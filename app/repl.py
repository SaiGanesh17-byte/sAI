"""
sAI interactive REPL.

A persistent multi-turn terminal session, closer to Claude Code's CLI UX than
the old one-shot `--cli` stub: every user turn is routed through Jev (see
jev/decision.py) to either answer directly, hand off to a single specialist
agent, or run the full Planner -> ... -> Reviewer orchestrator loop. Agent and
tool activity streams live via the event bus as it happens, and risky actions
that raise PermissionRequestRequired are handled inline with a y/n prompt
instead of crashing or failing silently.
"""
import re
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.markup import escape

from core.orchestrator import Orchestrator
from core.task import Task
from core.protocol import Message, MessageType
from core.events import event_bus, EventType
from core.security import (
    SESSION_ALLOW_COMMAND_PREFIXES, allow_command_prefix_for_session, approve_request, command_allow_prefix,
    allow_mcp_tool_for_session, edits_need_approval, set_current_workspace, set_session_auto_edits,
)
from core.settings import load_settings, save_settings
from core.project_instructions import load_project_instructions
from core.custom_commands import load_custom_commands
from core.hooks import run_hooks
from core.session import SessionStore, compact_conversation, estimate_conversation_tokens
from tools.todo import set_todos
from core.kernel import kernel
from execution.permissions import PermissionRequestRequired
from tools.terminal import TerminalTool
from jev.decision import JevRouter
from llm.tracker import token_tracker
from ui.banner import render_banner, TEAL, VIOLET, DIM
from agents.loop import LoopResult, run_agent_loop
from ui.esc_watcher import EscWatcher
from ui.input import InputReader, expand_file_mentions
from ui.activity import (
    ActivityIndicator, ActivityPrinter, TOOL_LABELS, format_tokens, partial_json_string, tool_call_label, tool_result_summary,
)

ROUTE_LABELS = {"direct_answer": "direct", "single_agent": "1 agent", "full_orchestrator": "full team"}

# Deliberately tight: requires BOTH a known package-manager binary AND a
# subcommand verb, so it only matches unambiguous shell syntax (e.g.
# "pip install ddgs") and never a natural-language sentence that happens to
# start with a word like "go" or "run".
SHELL_COMMAND_PATTERN = re.compile(
    r"^(pip3?|npm|npx|yarn|pnpm|brew|apt(?:-get)?|cargo|gem|composer|docker)\s+"
    r"(install|uninstall|update|upgrade|remove|run|build|up|ps)\b",
    re.IGNORECASE,
)



SLASH_COMMANDS = {
    "/help": "show all commands",
    "/agents": "list specialist agents",
    "/init": "write SAI.md for this project",
    "/compact": "summarize history to free context",
    "/resume": "continue an earlier session",
    "/clear": "start a fresh session",
    "/permissions": "view or change approval rules",
    "/mcp": "connected MCP servers and tools",
    "/tokens": "session token usage",
    "/cost": "session token usage",
}


class SaiRepl:
    def __init__(self, resume: Optional[str] = None):
        """resume: None (fresh session), "continue" (latest in this folder), or "pick"."""
        self.console = Console()
        # Before Orchestrator(): it indexes the workspace when it's built.
        self.workspace = self._choose_workspace()
        self.orchestrator = Orchestrator()
        self.sessions = SessionStore(self.workspace)
        self.session_id = SessionStore.new_id()
        self._resume_mode = resume
        self.jev = JevRouter()
        # One Task/TaskContext lives for the whole session so conversation
        # history and working memory persist across turns.
        self.task = Task(goal="")
        self.printer = ActivityPrinter(self.console)
        self.activity = ActivityIndicator(self.console)
        self._current_agent = ""
        # (tool, display_args) between TOOL_STARTED and TOOL_FINISHED -- a tool
        # that raises PermissionRequestRequired mid-run never finishes, and the
        # approval prompt needs to know its ⏺ line is already on screen.
        self._open_tool: Optional[tuple] = None
        self._stream_buffer = ""
        self._announced_tool: Optional[str] = None
        self.esc = EscWatcher()
        self._register_events()

    # ------------------------------------------------------------------
    # Live streaming — EventBus.publish() calls subscribers synchronously,
    # and the REPL is single-threaded (blocked on input()), so printing
    # directly from these callbacks gives true as-it-happens streaming.
    # ------------------------------------------------------------------
    def _register_events(self):
        event_bus.subscribe(EventType.AGENT_STARTED, self._on_agent_started)
        event_bus.subscribe(EventType.TOOL_STARTED, self._on_tool_started)
        event_bus.subscribe(EventType.TOOL_FINISHED, self._on_tool_finished)
        event_bus.subscribe(EventType.AGENT_FINISHED, self._on_agent_finished)
        event_bus.subscribe(EventType.ERROR, self._on_error)
        event_bus.subscribe(EventType.LLM_REQUEST, self._on_llm_request)
        event_bus.subscribe(EventType.LLM_DELTA, self._on_llm_delta)
        event_bus.subscribe(EventType.LLM_FALLBACK, self._on_llm_fallback)

    # ------------------------------------------------------------------
    # Streaming: agents answer in JSON, so rather than echo raw JSON, pull
    # out the human-facing field as it arrives and show it above the spinner.
    # It moves into the permanent transcript once the response completes.
    # ------------------------------------------------------------------
    def _on_llm_fallback(self, event):
        self.printer.note(f"[{DIM}]free model busy ({escape(str(event.data.get('from')))}) → "
                          f"using {escape(str(event.data.get('to')))}[/{DIM}]")

    def _on_llm_request(self, event):
        self._stream_buffer = ""
        self.activity.clear_preview()

    def _on_llm_delta(self, event):
        self._stream_buffer += event.data.get("delta", "")
        agent = event.data.get("agent", "")
        is_jev = agent == "Jev"
        if is_jev:
            text = partial_json_string(self._stream_buffer, "answer")
        else:
            # Show the full reply once it starts streaming; until then the short status line.
            text = partial_json_string(self._stream_buffer, "response") or partial_json_string(self._stream_buffer, "summary")
        self.activity.stream_preview("sAI" if is_jev else agent, text or "", len(self._stream_buffer))

    def _on_agent_started(self, event):
        self._current_agent = event.data.get("agent", "")
        self.activity.show(f"{self._current_agent} thinking")

    def _on_tool_started(self, event):
        tool = event.data.get("tool", "")
        args = event.data.get("args") or {}
        self._open_tool = (tool, args)
        if self._announced_tool == tool:
            self._announced_tool = None  # shown with its diff at approval time
        else:
            self.printer.tool_call(tool_call_label(tool, args))
        who = self._current_agent or "sAI"
        self.activity.show(f"{who} running {TOOL_LABELS.get(tool, tool)}")

    def _on_tool_finished(self, event):
        tool = event.data.get("tool", "")
        args = self._open_tool[1] if self._open_tool and self._open_tool[0] == tool else {}
        ok = event.data.get("success", True)
        self.printer.tool_result(tool_result_summary(tool, args, ok, event.data.get("output", "")), ok)
        self._open_tool = None
        if self._current_agent:
            self.activity.show(f"{self._current_agent} thinking")

    def _on_agent_finished(self, event):
        self.activity.clear_preview()
        agent = event.data.get("agent", "")
        msg = event.data.get("msg")
        summary = ""
        if msg is not None:
            payload = getattr(msg, "payload", {}) or {}
            summary = payload.get("response") or payload.get("summary") or payload.get("content") or ""
        if summary:
            self.printer.agent_message(agent, summary)

    def _on_error(self, event):
        self.printer.note(f"[bold red]Error:[/bold red] [red]{escape(str(event.data.get('msg', '')))}[/red]")

    def _prompt_approval(self, preq: PermissionRequestRequired, action: Optional[dict] = None) -> bool:
        """
        Claude-Code-style permission prompt: y = once, a = don't ask again this
        session (for this kind of request), anything else = no. Edits show
        their diff first.
        """
        with self.activity.paused(), self.esc.paused():
            # Show which call is asking, unless its ⏺ line is already on screen
            # (tools like Bash raise this from inside execute(), after TOOL_STARTED).
            if self._open_tool is None:
                label = tool_call_label(action.get("tool", ""), action.get("args")) if action else f"Access({preq.path})"
                self.printer.tool_call(label)
            if preq.kind == "edit" and preq.details:
                self.printer.diff(preq.details)
            else:
                self.printer.note(f"[yellow]Permission needed:[/yellow] {escape(preq.reason)}")

            always = self._always_option(preq)
            choices = "y/N/a" if always else "y/N"
            if always:
                self.printer.note(f"[{DIM}]a = {escape(always)}[/{DIM}]")
            answer = self.console.input(f"     [bold yellow]Allow? \\[{choices}][/bold yellow] ").strip().lower()

        approved = answer in ("y", "yes") or bool(always and answer in ("a", "always"))
        if always and answer in ("a", "always"):
            self._apply_always(preq)
        if preq.kind == "edit" and approved:
            # The edit runs next and fires TOOL_STARTED; its ⏺ line is already shown.
            self._announced_tool = action.get("tool") if action else None
        self._open_tool = None
        if not approved:
            self.printer.tool_result("Declined -- nothing was changed" if preq.kind == "edit" else "Declined -- nothing was run", ok=False)
        return approved

    @staticmethod
    def _always_option(preq: PermissionRequestRequired) -> Optional[str]:
        if preq.kind == "edit":
            return "allow all edits for the rest of this session"
        if preq.kind == "command":
            prefix = command_allow_prefix(preq.path)
            return f"always allow `{prefix} …` this session" if prefix else None
        if preq.kind == "mcp":
            return f"always allow {preq.path} this session"
        return None

    @staticmethod
    def _apply_always(preq: PermissionRequestRequired) -> None:
        if preq.kind == "edit":
            set_session_auto_edits(True)
        elif preq.kind == "command":
            allow_command_prefix_for_session(command_allow_prefix(preq.path))
        elif preq.kind == "mcp":
            allow_mcp_tool_for_session(preq.path)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Workspace = the folder sAI was launched from, like Claude Code. The
    # first time in a folder, ask whether to trust it (agents can read and,
    # with approval, change everything under it).
    # ------------------------------------------------------------------
    def _choose_workspace(self) -> Path:
        cwd = Path.cwd().resolve()
        settings = load_settings()
        trusted = list(settings.get("trusted_folders") or [])
        if str(cwd) not in trusted:
            broad = cwd == Path.home().resolve() or cwd == Path(cwd.anchor)
            self.console.print(f"\n[bold {VIOLET}]Do you trust the files in this folder?[/bold {VIOLET}]")
            self.console.print(f"  [bold]{escape(str(cwd))}[/bold]")
            self.console.print(f"  [{DIM}]sAI's agents will be able to read files here, and change them or run commands with your approval.[/{DIM}]")
            if broad:
                self.console.print(
                    "  [bold yellow]⚠ This is your whole home folder (or the root of the disk).[/bold yellow] "
                    f"[{DIM}]Usually you want to cd into a project folder first.[/{DIM}]"
                )
            answer = self.console.input("  [bold yellow]Trust this folder? \\[y/N][/bold yellow] ").strip().lower()
            if answer not in ("y", "yes"):
                self.console.print(f"[{DIM}]No problem -- cd into the project you want to work on and run `hey sAI` again.[/{DIM}]")
                raise SystemExit(0)
            trusted.append(str(cwd))
            settings["trusted_folders"] = trusted
            save_settings(settings)
        set_current_workspace(str(cwd), exclusive=True)
        return cwd

    def run(self):
        commands = dict(SLASH_COMMANDS)
        for name, c in load_custom_commands(self.workspace).items():
            commands.setdefault(name, c.description)  # built-ins win on a name clash
        self.input = InputReader(self.workspace, commands)
        settings = load_settings()
        _, instruction_files = load_project_instructions(self.workspace)
        render_banner(
            self.console,
            cwd=self.workspace,
            instructions=", ".join(f.name if f.parent == self.workspace else str(f).replace(str(Path.home()), "~") for f in instruction_files),
            provider=settings.get("provider", "openrouter"),
            coder_model=settings.get("coder_model", ""),
            reasoner_model=settings.get("reasoner_model", ""),
            agent_count=len(self.orchestrator.agents),
        )
        if self._resume_mode == "continue":
            latest = self.sessions.list()
            if latest:
                self._resume_session(latest[0])
            else:
                self.printer.note(f"[{DIM}]No earlier session in this folder -- starting fresh.[/{DIM}]")
        elif self._resume_mode == "pick":
            self._pick_session()

        while True:
            try:
                user_input = self._read_user_input()
            except (EOFError, KeyboardInterrupt):
                self.console.print(f"\n[{DIM}]Goodbye.[/{DIM}]")
                break

            if user_input.strip().lower() in ("exit", "quit", ":q"):
                self.console.print(f"[{DIM}]Goodbye.[/{DIM}]")
                break

            try:
                self.esc.start()
                self._handle_turn(user_input)
            except KeyboardInterrupt:
                # Like Claude Code: ctrl+c stops the current turn, not the app.
                self.activity.hide()
                self._open_tool = None
                self.printer.note(f"[red]Interrupted[/red] [{DIM}]· tell sAI what to do instead[/{DIM}]")
            except Exception as e:
                self.activity.hide()
                self.printer.note(f"[bold red]Unexpected error:[/bold red] {escape(str(e))}")
            finally:
                self.esc.stop()
                self.activity.hide()
                self._save_session()

    # ------------------------------------------------------------------
    # Sessions (--continue / --resume / /resume) and compaction (/compact)
    # ------------------------------------------------------------------
    def _save_session(self):
        if not self.task.context.conversation.all():
            return
        try:
            self.sessions.save(self.session_id, self.task)
        except Exception as e:
            self.printer.note(f"[{DIM}]Couldn't save this session: {escape(str(e))}[/{DIM}]")

    def _resume_session(self, info):
        if self.sessions.load_into(info.id, self.task):
            self.session_id = info.id
            self.printer.note(
                f"Resumed [bold]{escape(info.title)}[/bold] "
                f"[{DIM}]· {info.message_count} messages · {info.updated.astimezone():%b %d %H:%M}[/{DIM}]"
            )

    def _pick_session(self):
        sessions = self.sessions.list()[:10]
        if not sessions:
            self.printer.note(f"[{DIM}]No saved sessions in this folder yet.[/{DIM}]")
            return
        self.console.print(f"\n[bold {VIOLET}]Resume a session[/bold {VIOLET}]")
        for i, info in enumerate(sessions, 1):
            self.console.print(
                f"  [{TEAL}]{i:>2}[/{TEAL}]  {escape(info.title[:70])} "
                f"[{DIM}]· {info.message_count} msgs · {info.updated.astimezone():%b %d %H:%M}[/{DIM}]"
            )
        choice = self.console.input(f"  [bold {TEAL}]Number (Enter to cancel):[/bold {TEAL}] ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(sessions):
            self._resume_session(sessions[int(choice) - 1])

    def _compact(self, focus: str = "", automatic: bool = False):
        self.activity.show("Compacting conversation")
        try:
            result = compact_conversation(self.task, kernel.get_service("llm_runtime"), focus=focus)
        finally:
            self.activity.hide()
        if result is None:
            if not automatic:
                self.printer.note(f"[{DIM}]Nothing to compact yet.[/{DIM}]")
            return
        before, after = result
        label = "Auto-compacted" if automatic else "Compacted"
        self.printer.note(f"{label} conversation [{DIM}]· ~{format_tokens(before)} → ~{format_tokens(after)} tokens · summary kept in history[/{DIM}]")

    def _auto_compact_if_needed(self):
        settings = load_settings()
        threshold = int(settings.get("auto_compact_tokens", 0) or 0) or int(settings.get("context_token_budget", 32000) * 0.6)
        if estimate_conversation_tokens(self.task.context.conversation) > threshold:
            self._compact(automatic=True)

    # ------------------------------------------------------------------
    # Boxed input prompt — a framed row instead of a bare arrow, plus a
    # status line underneath (agent count / provider / running session
    # token spend). Deliberately plain print/input rather than
    # prompt_toolkit's bottom_toolbar: that toolbar only paints once the
    # terminal renderer completes a cursor-position handshake
    # (`renderer_height_is_known` in prompt_toolkit's own source), which
    # never completed in testing across several real turns in a real
    # terminal -- a known fragility of that feature when interleaved with
    # plain stdout writes the way this REPL does. This always renders.
    # ------------------------------------------------------------------
    def _read_user_input(self) -> str:
        width = max(40, min(self.console.size.width - 2, 96))
        top = "╭" + "─" * (width - 2) + "╮"
        bottom = "╰" + "─" * (width - 2) + "╯"

        self.console.print(f"\n[{TEAL}]{top}[/{TEAL}]")
        try:
            user_input = self.input.read(
                lambda: self.console.input(f"[{TEAL}]│[/{TEAL}] [bold {TEAL}]❯[/bold {TEAL}] ")
            )
        finally:
            self.console.print(f"[{TEAL}]{bottom}[/{TEAL}]")

        settings = load_settings()
        total = token_tracker.input_tokens + token_tracker.output_tokens
        self.console.print(
            f"  [{DIM}]{len(self.orchestrator.agents)} agents · "
            f"{settings.get('provider', 'openrouter')} · session {total:,} tok · "
            f"@ file · \\⏎ newline · /help[/{DIM}]"
        )
        return user_input

    def _handle_turn(self, user_input: str):
        stripped = user_input.strip()
        if not stripped:
            return

        if stripped.startswith("/"):
            self._run_slash_command(stripped)
            return

        if stripped.startswith("!") or stripped.lower().startswith("git "):
            self._run_terminal_bypass(stripped)
            return

        if SHELL_COMMAND_PATTERN.match(stripped):
            # Regression guard: this exact shape ("pip install ddgs") was
            # previously falling through to Jev, which routed it to WebSearch
            # and got back an answer about a completely unrelated prior
            # question -- a confusing, wrong response, not just a slow one.
            # Package-manager commands are unambiguous enough to catch locally,
            # for free (no LLM call at all), rather than risk another agent
            # misinterpreting literal shell syntax as a research question.
            self.console.print(
                f"[{DIM}]That looks like a shell command, not a request for sAI — sAI never "
                f"auto-runs raw commands for safety. Prefix it with '!' to actually run it:[/{DIM}] "
                f"[bold {TEAL}]!{stripped}[/bold {TEAL}]"
            )
            return

        submit = run_hooks("UserPromptSubmit", {"prompt": stripped})
        for warning in submit.warnings:
            self.printer.note(f"[yellow]{escape(warning)}[/yellow]")
        if submit.blocked:
            self.printer.note(f"[red]Blocked by a UserPromptSubmit hook:[/red] {escape(submit.reason)}")
            return

        # @path mentions attach file contents for the agents. Jev only routes,
        # so it gets the short message plus the list of attached names.
        goal, attached = expand_file_mentions(stripped, self.workspace)
        if submit.context:
            goal += f"\n\n[Context added by a UserPromptSubmit hook]\n{submit.context}"
        for path in attached:
            if path.is_file():
                # Treat an attached file as read, like Claude Code's @-mentions,
                # so the agent can edit it without a redundant read_file.
                self.orchestrator.execution_engine.file_versions[str(path)] = path.stat().st_mtime_ns
        if attached:
            names = ", ".join(str(p.relative_to(self.workspace)) if p.is_relative_to(self.workspace) else str(p) for p in attached)
            self.printer.note(f"[{DIM}]attached {escape(names)}[/{DIM}]")
        routing_text = stripped + (f"\n(attached files: {names})" if attached else "")

        self._auto_compact_if_needed()
        before = self._token_snapshot()
        self._current_agent = ""
        self.activity.show("Jev routing")

        agent_names = [a.name for a in self.orchestrator.agents]
        agent_roles = {a.name: a.role for a in self.orchestrator.agents}
        decision = self.jev.decide(routing_text, agent_names, self._recent_turn_summaries(), agent_roles=agent_roles)

        if decision.fallback:
            self.printer.note(f"[{DIM}]jev: routing to full team ({escape(decision.reasoning)})[/{DIM}]")
        elif decision.route == "single_agent" and decision.agent:
            self.printer.note(f"[{DIM}]jev → {escape(decision.agent)}[/{DIM}]")
        elif decision.route == "full_orchestrator":
            self.printer.note(f"[{DIM}]jev → full team[/{DIM}]")

        if decision.route == "direct_answer":
            self.activity.clear_preview()
            self.activity.hide()
            self.printer.agent_message("sAI", decision.answer)
            msg = Message(
                sender="sAI",
                receiver="User",
                type=MessageType.SUMMARY,
                payload={"summary": decision.answer, "content": decision.answer},
            )
            self.task.context.conversation.add(msg)
        else:
            self.task.goal = goal

            if decision.route == "single_agent" and decision.agent:
                self._run_single_agent(decision.agent)
            else:
                self._run_full_orchestrator()

        self.activity.hide()
        self._print_token_footer(before, decision.route)
        for warning in run_hooks("Stop", {"prompt": stripped, "route": decision.route}).warnings:
            self.printer.note(f"[yellow]{escape(warning)}[/yellow]")

    # ------------------------------------------------------------------
    # Token usage footer — Jev exists specifically to keep most turns off
    # the full multi-agent loop, so make the savings visible per turn
    # instead of only in a hidden global counter.
    # ------------------------------------------------------------------
    def _token_snapshot(self):
        return (token_tracker.input_tokens, token_tracker.output_tokens, token_tracker.calls_count)

    def _print_token_footer(self, before, route: str):
        din = token_tracker.input_tokens - before[0]
        dout = token_tracker.output_tokens - before[1]
        dcalls = token_tracker.calls_count - before[2]
        route_label = ROUTE_LABELS.get(route, route)
        self.console.print(
            f"\n[{DIM}]  jev:{route_label} · ↑ {format_tokens(din)} in · ↓ {format_tokens(dout)} out · "
            f"{dcalls} call{'s' if dcalls != 1 else ''} · "
            f"session {format_tokens(token_tracker.input_tokens + token_tracker.output_tokens)} tokens[/{DIM}]"
        )

    def _recent_turn_summaries(self):
        turns = []
        for msg in self.task.context.conversation.all()[-6:]:
            payload = getattr(msg, "payload", {}) or {}
            text = payload.get("content") or payload.get("summary") or ""
            sender = getattr(msg, "sender", "")
            if text:
                turns.append(f"{sender}: {text}"[:200])
        return turns

    # ------------------------------------------------------------------
    # Slash commands — Claude-Code-style session controls.
    # ------------------------------------------------------------------
    def _run_slash_command(self, stripped: str):
        parts = stripped.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd in ("/help", "/?"):
            self.console.print(f"""
[bold {VIOLET}]Commands[/bold {VIOLET}]
  [{TEAL}]/agents[/{TEAL}]   list all specialist agents and their roles
  [{TEAL}]/tokens[/{TEAL}]   show cumulative session token usage (alias: /cost)
  [{TEAL}]/clear[/{TEAL}]    start a fresh session (the old one stays saved)
  [{TEAL}]/compact[/{TEAL}]  summarize the conversation so far to free up context · /compact <what to focus on>
  [{TEAL}]/resume[/{TEAL}]   pick an earlier session in this folder to continue
  [{TEAL}]/mcp[/{TEAL}]      show connected MCP servers and their tools
  [{TEAL}]/init[/{TEAL}]     have sAI study this project and write SAI.md (instructions every agent follows)
  [{TEAL}]/permissions[/{TEAL}]  show approval rules · /permissions edits auto|ask · /permissions allow <cmd prefix>
  [{TEAL}]/help[/{TEAL}]     show this message
  [{TEAL}]!<cmd>[/{TEAL}]    run a shell command directly (e.g. !ls, !pytest)
  [{TEAL}]exit[/{TEAL}]      leave sAI
""")
            custom = load_custom_commands(self.workspace)
            if custom:
                self.console.print(f"[bold {VIOLET}]Your commands[/bold {VIOLET}] [{DIM}](.sai/commands/*.md)[/{DIM}]")
                for c in custom.values():
                    self.console.print(f"  [{TEAL}]{escape(c.name)}[/{TEAL}]  {escape(c.description)}")
            else:
                self.console.print(f"[{DIM}]Add your own commands as Markdown files in .sai/commands/ (this project) or ~/.sai/commands/.[/{DIM}]")
            return

        if cmd == "/agents":
            lines = [f"  [{TEAL}]{a.name:<16}[/{TEAL}] {a.role}" for a in self.orchestrator.agents]
            self.console.print(f"\n[bold {VIOLET}]{len(self.orchestrator.agents)} agents loaded[/bold {VIOLET}]")
            self.console.print("\n".join(lines))
            return

        if cmd in ("/tokens", "/cost"):
            total = token_tracker.input_tokens + token_tracker.output_tokens
            self.console.print(f"""
[bold {VIOLET}]Session token usage[/bold {VIOLET}]
  input tokens    {token_tracker.input_tokens:,}
  output tokens   {token_tracker.output_tokens:,}
  total tokens    {total:,}
  LLM calls       {token_tracker.calls_count}
  elapsed         {token_tracker.elapsed_time:.0f}s
  throughput      {token_tracker.speed:.1f} tok/s (output)

  [{DIM}]Every "jev:direct" / "jev:1 agent" turn below is Jev keeping this off
  the full multi-agent loop -- compare its token delta to a "jev:full team" turn.[/{DIM}]
""")
            return

        if cmd == "/mcp":
            self._show_mcp()
            return

        if cmd == "/init":
            self._run_init()
            return

        if cmd == "/permissions":
            self._run_permissions_command(parts[1] if len(parts) > 1 else "")
            return

        if cmd == "/clear":
            self.task = Task(goal="")
            set_todos([])
            self.session_id = SessionStore.new_id()  # the old session stays saved for /resume
            self.console.print(f"[{DIM}]Started a fresh session (the previous one is saved -- /resume to go back).[/{DIM}]")
            return

        if cmd == "/compact":
            self._compact(focus=parts[1] if len(parts) > 1 else "")
            return

        if cmd == "/resume":
            self._pick_session()
            return

        custom = load_custom_commands(self.workspace).get(cmd)
        if custom:
            self.printer.note(f"[{DIM}]/{escape(custom.name[1:])} · {escape(str(custom.path).replace(str(Path.home()), '~'))}[/{DIM}]")
            self._handle_turn(custom.render(parts[1] if len(parts) > 1 else ""))
            return

        self.console.print(f"[{DIM}]Unknown command '{cmd}'. Try /help.[/{DIM}]")

    def _show_mcp(self):
        from core.mcp import mcp_manager
        if not mcp_manager.status:
            self.printer.note(f"[{DIM}]No MCP servers configured. Add them under \"mcp_servers\" in .sai/settings.json -- see core/mcp.py.[/{DIM}]")
            return
        self.console.print(f"\n[bold {VIOLET}]MCP servers[/bold {VIOLET}]")
        for st in mcp_manager.status.values():
            if st.ok:
                tools = ", ".join(st.tools[:8]) + (f" +{len(st.tools) - 8} more" if len(st.tools) > 8 else "")
                self.console.print(f"  [green]●[/green] [bold]{escape(st.name)}[/bold] [{DIM}]{len(st.tools)} tools · {escape(tools)}[/{DIM}]")
            else:
                self.console.print(f"  [red]●[/red] [bold]{escape(st.name)}[/bold] [red]failed:[/red] [{DIM}]{escape(st.error[:200])}[/{DIM}]")

    INIT_PROMPT = (
        "Create (or improve, if it exists) the file SAI.md at the root of this workspace. Every sAI agent "
        "reads it before working on this project, so it should hold what a new engineer needs on day one:\n"
        "1. One or two lines on what the project is.\n"
        "2. Exact commands to install, build, run, lint and test (only ones you can confirm from the files).\n"
        "3. A short map of the important directories and entry points.\n"
        "4. Conventions worth following (style, patterns, things to avoid).\n"
        "Start with list_directory and read the README and package/build manifests before writing. "
        "Keep it under 80 lines and don't invent commands you can't find evidence for."
    )

    def _run_init(self):
        self.task.goal = self.INIT_PROMPT
        before = self._token_snapshot()
        try:
            self._run_single_agent("Writer")
        finally:
            self.activity.hide()
        self._print_token_footer(before, "single_agent")

    def _run_permissions_command(self, arg: str):
        words = arg.split(maxsplit=1)
        settings = load_settings()
        if words and words[0] == "edits" and len(words) == 2 and words[1] in ("auto", "ask"):
            set_session_auto_edits(words[1] == "auto")
            self.printer.note(f"Edits this session: [bold]{'applied without asking' if words[1] == 'auto' else 'shown as a diff and confirmed'}[/bold]")
            return
        if words and words[0] == "allow" and len(words) == 2:
            rules = list(settings.get("allow_commands") or [])
            if words[1] not in rules:
                rules.append(words[1])
                settings["allow_commands"] = rules
                save_settings(settings)
            self.printer.note(f"Saved: commands starting with [bold]{escape(words[1])}[/bold] never ask (settings.json allow_commands)")
            return
        if words:
            self.printer.note(f"[{DIM}]Usage: /permissions · /permissions edits auto|ask · /permissions allow <command prefix>[/{DIM}]")
            return

        edits = "ask (diff + confirm)" if edits_need_approval() else "auto (no confirmation)"
        saved = ", ".join(settings.get("allow_commands") or []) or "none"
        session = ", ".join(sorted(SESSION_ALLOW_COMMAND_PREFIXES)) or "none"
        self.console.print(f"""
[bold {VIOLET}]Permissions[/bold {VIOLET}]
  edits                  {edits}
  always-allowed (saved) {escape(saved)}
  allowed this session   {escape(session)}
  read before edit       {'required' if settings.get('require_read_before_edit', True) else 'off'}

  [{DIM}]Allow rules never match commands containing ; & | ` > < or $(...).[/{DIM}]
""")

    # ------------------------------------------------------------------
    # `!<cmd>` / `git ...` bypass — mirrors app/main.py's shell bypass.
    # ------------------------------------------------------------------
    def _run_terminal_bypass(self, user_input: str):
        command = user_input[1:].strip() if user_input.startswith("!") else user_input
        tool = TerminalTool()
        action = {"tool": "execute_command", "args": {"command": command}}
        self.printer.tool_call(tool_call_label("execute_command", action["args"]))
        self._open_tool = ("execute_command", action["args"])
        try:
            self.activity.show("Running")
            try:
                result = tool.execute({"command": command})
            finally:
                self.activity.hide()
        except PermissionRequestRequired as preq:
            if not self._prompt_approval(preq, action):
                return
            approve_request(preq.path, preq.kind)
            result = tool.execute({"command": command})
        self._open_tool = None
        # You asked for this command directly -- show all of its output.
        self.printer.tool_result(result.rstrip() or "(no output)", ok=not result.startswith(("Error", "Security Error")))

    # ------------------------------------------------------------------
    # Jev route: single_agent — one agent turn, actions executed once
    # (mirrors app/main.py's fast_chat heuristic, but Jev-routed).
    # ------------------------------------------------------------------
    def _run_single_agent(self, agent_name: str):
        agent = next((a for a in self.orchestrator.agents if a.name == agent_name), None)
        if not agent:
            self._run_full_orchestrator()
            return

        self.task.context.conversation.add(Message(
            sender="User", receiver=agent.name, type=MessageType.TASK, payload={"content": self.task.goal}
        ))

        # Tool-use loop: the agent sees each result before it responds again,
        # so its final message reports what actually happened.
        loop = run_agent_loop(
            agent,
            self.task.context,
            lambda action: self.orchestrator.execute_action(self.task, agent, action, self._prompt_approval),
            max_steps=self.orchestrator._max_steps(),
            source="Jev",
        )
        self._report_loop_stop(loop)

    def _report_loop_stop(self, loop: LoopResult):
        if loop.stop_reason == "declined":
            self.console.print()
            self.printer.note(f"[yellow]Stopped[/yellow] [{DIM}]· you declined {escape(', '.join(loop.declined))} -- tell sAI what to do instead[/{DIM}]")
        elif loop.stop_reason == "max_steps":
            self.console.print()
            self.printer.note(f"[yellow]Paused[/yellow] [{DIM}]· hit the {loop.steps}-step limit (agent_max_steps) -- say \"continue\" to keep going[/{DIM}]")
        elif loop.stop_reason == "repeating":
            self.console.print()
            self.printer.note(f"[yellow]Stopped[/yellow] [{DIM}]· the agent repeated the same actions without progress[/{DIM}]")

    # ------------------------------------------------------------------
    # Jev route: full_orchestrator — the multi-agent loop. Permission
    # requests are answered inline and the turn continues in place.
    # ------------------------------------------------------------------
    def _run_full_orchestrator(self):
        self.orchestrator.run(self.task, approve=self._prompt_approval)
