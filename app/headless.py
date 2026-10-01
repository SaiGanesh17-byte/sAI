"""
Headless mode: `sai -p "prompt"` runs one request without the interactive UI
and prints the result -- for scripts, CI and pipes (Claude Code's -p).

    sai -p "summarize what this repo does"
    cat error.log | sai -p "why is this failing?"
    sai -p "add a docstring to utils.py" --accept-edits --output-format json

Progress goes to stderr, the answer to stdout. Nobody is there to answer
permission prompts, so they're declined unless allowed up front
(--accept-edits, allow_commands rules, edit_approval: "auto").
"""
import json
import sys
from pathlib import Path
from typing import Optional

from agents.loop import run_agent_loop
from core.custom_commands import load_custom_commands
from core.events import event_bus, EventType
from core.hooks import run_hooks
from core.orchestrator import Orchestrator
from core.protocol import Message, MessageType
from core.security import set_current_workspace
from core.task import Task
from execution.permissions import PermissionRequestRequired
from jev.decision import JevRouter
from llm.tracker import token_tracker
from ui.activity import tool_call_label
from ui.input import expand_file_mentions, is_image

MAX_STDIN_CHARS = 100_000


def _final_answer(task: Task, since_index: int) -> str:
    for msg in reversed(task.context.conversation.all()[since_index:]):
        if getattr(msg, "sender", "") in ("User", "System"):
            continue
        payload = getattr(msg, "payload", {}) or {}
        text = payload.get("response") or payload.get("summary") or payload.get("content")
        if text:
            return str(text)
    return ""


def run_headless(prompt: str, accept_edits: bool = False, output_format: str = "text",
                 stdin_text: Optional[str] = None, quiet: bool = False) -> int:
    workspace = Path.cwd().resolve()
    set_current_workspace(str(workspace), exclusive=True)

    def progress(line: str):
        if not quiet:
            print(line, file=sys.stderr, flush=True)

    if prompt.startswith("/"):
        name, _, arguments = prompt.partition(" ")
        custom = load_custom_commands(workspace).get(name.lower())
        if custom is None:
            print(f"Error: unknown command '{name}' (custom commands live in .sai/commands/*.md)", file=sys.stderr)
            return 2
        prompt = custom.render(arguments)

    if stdin_text:
        prompt = f"{prompt}\n\n[Piped input]\n{stdin_text[:MAX_STDIN_CHARS]}"

    submit = run_hooks("UserPromptSubmit", {"prompt": prompt})
    for warning in submit.warnings:
        progress(f"warning: {warning}")
    if submit.blocked:
        print(f"Blocked by a UserPromptSubmit hook: {submit.reason}", file=sys.stderr)
        return 2

    orchestrator = Orchestrator()
    task = Task(goal="")
    goal, attached = expand_file_mentions(prompt, workspace)
    images = [str(p) for p in attached if is_image(p)]
    task.images = images
    if submit.context:
        goal += f"\n\n[Context added by a UserPromptSubmit hook]\n{submit.context}"
    for path in attached:
        if path.is_file():
            orchestrator.execution_engine.file_versions[str(path)] = path.stat().st_mtime_ns

    denied = []

    def approve(preq: PermissionRequestRequired, action: dict) -> bool:
        if accept_edits and preq.kind == "edit":
            return True
        denied.append(preq.reason if preq.kind != "command" else f"command: {preq.path}")
        progress(f"  ✗ permission needed, declined (non-interactive): {preq.reason}")
        return False

    event_bus.subscribe(EventType.TOOL_STARTED, lambda e: progress(f"⏺ {tool_call_label(e.data.get('tool', ''), e.data.get('args'))}"))
    event_bus.subscribe(EventType.LLM_FALLBACK, lambda e: progress(f"  (free model busy: {e.data.get('from')} -> {e.data.get('to')})"))

    tokens_before = (token_tracker.input_tokens, token_tracker.output_tokens, token_tracker.cost_usd)
    agents = orchestrator.agents
    decision = JevRouter().decide(prompt, [a.name for a in agents], [], agent_roles={a.name: a.role for a in agents})
    start_index = len(task.context.conversation.all())
    stop_reason = "done"
    agent_name = None

    try:
        if decision.route == "direct_answer":
            result = decision.answer or ""
        else:
            task.goal = goal
            if decision.route == "single_agent" and decision.agent:
                agent = next((a for a in agents if a.name == decision.agent), None)
            else:
                agent = None
            if agent is not None:
                agent_name = agent.name
                progress(f"sAI → {agent.name}")
                task.context.conversation.add(Message(sender="User", receiver=agent.name, type=MessageType.TASK,
                                                      payload={"content": goal, "images": images}))
                loop = run_agent_loop(
                    agent, task.context,
                    lambda action: orchestrator.execute_action(task, agent, action, approve),
                    max_steps=orchestrator._max_steps(), source="Headless",
                    wrap_up_on_decline=True,  # nobody here to redirect it -- answer without that action
                )
                stop_reason = loop.stop_reason
            else:
                progress("sAI → full team")
                orchestrator.run(task, approve=approve, wrap_up_on_decline=True)
            result = _final_answer(task, start_index)
    except KeyboardInterrupt:
        progress("Interrupted.")
        return 130
    except Exception as e:
        if output_format == "json":
            print(json.dumps({"is_error": True, "error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1

    for warning in run_hooks("Stop", {"prompt": prompt, "route": decision.route}).warnings:
        progress(f"warning: {warning}")
    if denied and stop_reason == "done":
        stop_reason = "declined"
    usage = {"input_tokens": token_tracker.input_tokens - tokens_before[0],
             "output_tokens": token_tracker.output_tokens - tokens_before[1],
             "cost_usd": round(token_tracker.cost_usd - tokens_before[2], 6)}

    if output_format == "json":
        print(json.dumps({
            "result": result, "route": decision.route, "agent": agent_name,
            "stop_reason": stop_reason, "permission_denials": denied, "usage": usage, "is_error": False,
        }))
    else:
        print(result)
        if denied:
            progress("\nSome actions needed permission and were declined. Re-run with --accept-edits, "
                     "or allow commands with `/permissions allow <prefix>` / allow_commands in settings.")
    return 0
