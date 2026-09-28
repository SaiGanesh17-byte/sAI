"""
The agent tool-use loop, shared by every front end (REPL, TUI, orchestrator).

    agent turn -> run its actions -> record results -> agent turn -> ...

until the agent returns no actions. This is Claude Code's core loop: the agent
sees what its tools actually returned before it says what happened, instead
of writing a summary up front and having its actions run blind afterwards
(which let an agent report "Created query_api.py" for a write the user had
declined).
"""
import json
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core.events import event_bus, EventType
from core.protocol import Message, MessageType
from core.security import approve_request
from execution.permissions import PermissionRequestRequired

DEFAULT_MAX_STEPS = 8

# Called when an action needs permission: (request, action) -> approved?
ApproveFn = Callable[[PermissionRequestRequired, dict], bool]


@dataclass
class ActionOutcome:
    status: str  # "ok" | "failed" | "declined"
    content: str


@dataclass
class LoopResult:
    final_message: Optional[Message]
    steps: int
    stop_reason: str  # "done" | "declined" | "repeating" | "max_steps"
    declined: List[str] = field(default_factory=list)


def describe_action(action: dict) -> str:
    args = action.get("args", {}) or {}
    target = args.get("path") or args.get("script_path") or args.get("command") or args.get("query") or ""
    return f"{action.get('tool', '?')}({target})" if target else str(action.get("tool", "?"))


def execute_with_approval(engine, action: dict, approve: Optional[ApproveFn]) -> ActionOutcome:
    """
    Runs one action. With `approve`, a PermissionRequestRequired is resolved
    inline (ask, approve by kind, retry once); without it, the request
    propagates to the caller as before (the web UI relies on that).
    """
    try:
        result = engine.execute(action)
    except PermissionRequestRequired as preq:
        if approve is None:
            raise
        if not approve(preq, action):
            return ActionOutcome(
                "declined",
                f"DECLINED by user: {describe_action(action)} -- it did not run. "
                "Do not retry it; tell the user what you were trying to do and ask how to proceed.",
            )
        approve_request(preq.path, preq.kind)
        try:
            result = engine.execute(action)
        except PermissionRequestRequired:
            return ActionOutcome("failed", f"NOT PERMITTED: {describe_action(action)} -- it did not run.")

    content = result.stdout if result.success else result.stderr
    return ActionOutcome("ok" if result.success else "failed", content)


def run_agent_loop(
    agent,
    context,
    execute_action: Callable[[dict], ActionOutcome],
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    source: str = "AgentLoop",
    is_halted: Optional[Callable[[], bool]] = None,
) -> LoopResult:
    """
    Drives one agent until it stops requesting actions. Every agent message and
    tool result is appended to context.conversation in the order it happened,
    and AGENT_STARTED / AGENT_FINISHED fire once per step so UIs can show
    each intermediate message.
    """
    last_signature = None
    message = None

    for step in range(1, max_steps + 1):
        if is_halted and is_halted():
            raise InterruptedError("Agent loop execution halted by user interrupt.")

        context.current_agent = agent.name
        event_bus.publish(EventType.AGENT_STARTED, {"agent": agent.name, "turn": step}, source=source)

        message = agent.run(context)
        response = message.metadata.get("response")
        actions = list(getattr(response, "actions", None) or [])

        context.conversation.add(message)
        event_bus.publish(
            EventType.AGENT_FINISHED,
            {"agent": agent.name, "msg": message, "step": step, "final": not actions},
            source=source,
        )

        if not actions:
            return LoopResult(message, step, "done")

        # A model that keeps asking for exactly the same thing isn't making progress.
        signature = json.dumps(actions, sort_keys=True, default=str)
        if signature == last_signature:
            return LoopResult(message, step, "repeating")
        last_signature = signature

        for action in actions:
            if is_halted and is_halted():
                raise InterruptedError("Agent loop execution halted by user interrupt.")
            outcome = execute_action(action)
            context.conversation.add(Message(
                sender="System",
                receiver=agent.name,
                type=MessageType.TOOL_RESULT,
                payload={"content": f"[{describe_action(action)} -> {outcome.status}]\n{outcome.content}"},
            ))
            if outcome.status == "declined":
                # Like Claude Code: a "no" hands control back to the user.
                return LoopResult(message, step, "declined", [describe_action(action)])

    return LoopResult(message, max_steps, "max_steps")
