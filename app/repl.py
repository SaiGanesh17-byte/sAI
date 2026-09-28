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

from core.orchestrator import Orchestrator
from core.task import Task
from core.protocol import Message, MessageType
from core.events import event_bus, EventType
from core.security import approve_path
from core.settings import load_settings
from execution.permissions import PermissionRequestRequired
from tools.terminal import TerminalTool
from jev.decision import JevRouter
from llm.tracker import token_tracker
from ui.banner import render_banner, TEAL, VIOLET, DIM

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


class SaiRepl:
    def __init__(self):
        self.console = Console()
        self.orchestrator = Orchestrator()
        self.jev = JevRouter()
        # One Task/TaskContext lives for the whole session so conversation
        # history and working memory persist across turns.
        self.task = Task(goal="")
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

    def _on_agent_started(self, event):
        agent = event.data.get("agent", "")
        self.console.print(f"\n[{DIM}]· [{VIOLET}]{agent}[/{VIOLET}] thinking...[/{DIM}]")

    def _on_tool_started(self, event):
        self.console.print(f"  [{TEAL}]→[/{TEAL}] {event.data.get('tool', '')}", style=DIM)

    def _on_tool_finished(self, event):
        ok = event.data.get("success", True)
        mark = f"[green]✓[/green]" if ok else "[red]✗[/red]"
        self.console.print(f"  {mark} {event.data.get('tool', '')}")

    def _on_agent_finished(self, event):
        agent = event.data.get("agent", "")
        msg = event.data.get("msg")
        summary = ""
        if msg is not None:
            payload = getattr(msg, "payload", {}) or {}
            summary = payload.get("summary") or payload.get("content") or ""
        if summary:
            self.console.print(f"[bold {VIOLET}]{agent}[/bold {VIOLET}] {summary}")

    def _on_error(self, event):
        self.console.print(f"  [bold red]⚠ error:[/bold red] {event.data.get('msg', '')}")

    def _prompt_approval(self, preq: PermissionRequestRequired) -> bool:
        self.console.print(f"\n[bold yellow]⚠ {preq}[/bold yellow]")
        answer = self.console.input("[yellow]Allow this action? [y/N] [/yellow]").strip().lower()
        return answer in ("y", "yes")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self):
        settings = load_settings()
        render_banner(
            self.console,
            cwd=Path.cwd(),
            provider=settings.get("provider", "openrouter"),
            coder_model=settings.get("coder_model", ""),
            reasoner_model=settings.get("reasoner_model", ""),
            agent_count=len(self.orchestrator.agents),
        )

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
                self._handle_turn(user_input)
            except Exception as e:
                self.console.print(f"  [bold red]⚠ Unexpected error:[/bold red] {e}")

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
            user_input = self.console.input(f"[{TEAL}]│[/{TEAL}] [bold {TEAL}]❯[/bold {TEAL}] ")
        finally:
            self.console.print(f"[{TEAL}]{bottom}[/{TEAL}]")

        settings = load_settings()
        total = token_tracker.input_tokens + token_tracker.output_tokens
        self.console.print(
            f"  [{DIM}]{len(self.orchestrator.agents)} agents · "
            f"{settings.get('provider', 'openrouter')} · session {total:,} tok · /help[/{DIM}]"
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

        before = self._token_snapshot()

        agent_names = [a.name for a in self.orchestrator.agents]
        agent_roles = {a.name: a.role for a in self.orchestrator.agents}
        decision = self.jev.decide(stripped, agent_names, self._recent_turn_summaries(), agent_roles=agent_roles)

        if decision.fallback:
            self.console.print(f"[{DIM}]jev: routing to full orchestrator ({decision.reasoning})[/{DIM}]")

        if decision.route == "direct_answer":
            self.console.print(f"[bold {VIOLET}]sAI[/bold {VIOLET}] {decision.answer}")
            msg = Message(
                sender="sAI",
                receiver="User",
                type=MessageType.SUMMARY,
                payload={"summary": decision.answer, "content": decision.answer},
            )
            self.task.context.conversation.add(msg)
        else:
            self.task.goal = stripped

            if decision.route == "single_agent" and decision.agent:
                self._run_single_agent(decision.agent)
            else:
                self._run_full_orchestrator()

        self._print_token_footer(before, decision.route)

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
            f"[{DIM}]· jev:{route_label} · +{din:,} in / +{dout:,} out "
            f"({dcalls} call{'s' if dcalls != 1 else ''}) · "
            f"session {token_tracker.input_tokens:,} in / {token_tracker.output_tokens:,} out[/{DIM}]"
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
  [{TEAL}]/clear[/{TEAL}]    reset this session's conversation and working memory
  [{TEAL}]/help[/{TEAL}]     show this message
  [{TEAL}]!<cmd>[/{TEAL}]    run a shell command directly (e.g. !ls, !pytest)
  [{TEAL}]exit[/{TEAL}]      leave sAI
""")
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

        if cmd == "/clear":
            self.task = Task(goal="")
            self.console.print(f"[{DIM}]Session memory cleared (token usage above is unaffected).[/{DIM}]")
            return

        self.console.print(f"[{DIM}]Unknown command '{cmd}'. Try /help.[/{DIM}]")

    # ------------------------------------------------------------------
    # `!<cmd>` / `git ...` bypass — mirrors app/main.py's shell bypass.
    # ------------------------------------------------------------------
    def _run_terminal_bypass(self, user_input: str):
        command = user_input[1:].strip() if user_input.startswith("!") else user_input
        tool = TerminalTool()
        try:
            result = tool.execute({"command": command})
        except PermissionRequestRequired as preq:
            if self._prompt_approval(preq):
                approve_path(preq.path)
                result = tool.execute({"command": command})
            else:
                self.console.print("  [red]✗ Declined.[/red]")
                return
        self.console.print(result)

    # ------------------------------------------------------------------
    # Jev route: single_agent — one agent turn, actions executed once
    # (mirrors app/main.py's fast_chat heuristic, but Jev-routed).
    # ------------------------------------------------------------------
    def _run_single_agent(self, agent_name: str):
        agent = next((a for a in self.orchestrator.agents if a.name == agent_name), None)
        if not agent:
            self._run_full_orchestrator()
            return

        self.task.context.current_agent = agent.name
        event_bus.publish(EventType.AGENT_STARTED, {"agent": agent.name, "turn": 1}, source="Jev")

        user_msg = Message(
            sender="User", receiver=agent.name, type=MessageType.TASK, payload={"content": self.task.goal}
        )
        self.task.context.conversation.add(user_msg)

        message = agent.run(self.task.context)
        response = message.metadata.get("response")

        if response and response.actions:
            for action in response.actions:
                self._execute_with_approval(action, agent)

        self.task.context.conversation.add(message)
        event_bus.publish(EventType.AGENT_FINISHED, {"agent": agent.name, "msg": message}, source="Jev")

    def _execute_with_approval(self, action, agent):
        tool_name = action.get("tool", "")
        try:
            result = self.orchestrator.execution_engine.execute(action)
        except PermissionRequestRequired as preq:
            if self._prompt_approval(preq):
                approve_path(preq.path)
                try:
                    result = self.orchestrator.execution_engine.execute(action)
                except PermissionRequestRequired:
                    self.console.print(f"  [red]✗ Still not permitted: {tool_name}[/red]")
                    return
            else:
                self.console.print(f"  [red]✗ Declined: {tool_name}[/red]")
                return

        content = result.stdout if result.success else result.stderr
        result_msg = Message(
            sender="System", receiver=agent.name, type=MessageType.TOOL_RESULT, payload={"content": content}
        )
        self.task.context.conversation.add(result_msg)

    # ------------------------------------------------------------------
    # Jev route: full_orchestrator — the existing multi-agent loop, with
    # inline permission-approval-and-resume on PermissionRequestRequired.
    # ------------------------------------------------------------------
    def _run_full_orchestrator(self):
        self._run_with_permission_retry()

    def _run_with_permission_retry(self, max_retries: int = 5):
        start_agent: Optional[str] = None
        for _ in range(max_retries):
            try:
                self.orchestrator.run(self.task, start_agent=start_agent)
                return
            except PermissionRequestRequired as preq:
                resume_agent = self.task.context.current_agent
                if self._prompt_approval(preq):
                    approve_path(preq.path)
                    start_agent = resume_agent
                    continue
                self.console.print("  [red]✗ Permission declined — aborting this task.[/red]")
                return
        self.console.print("  [red]✗ Too many permission retries — aborting.[/red]")
