"""
The agent tool-use loop, shared by every front end (REPL, TUI, orchestrator).

    agent turn -> run its actions -> record results -> agent turn -> ...

until the agent returns no actions. This is Claude Code's core loop: the agent
sees what its tools actually returned before it says what happened, instead
of writing a summary up front and having its actions run blind afterwards
(which let an agent report "Created query_api.py" for a write the user had
declined).
"""
import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core.events import event_bus, EventType
from core.protocol import Message, MessageType
from core.security import approve_request
from execution.permissions import PermissionRequestRequired

DEFAULT_MAX_STEPS = 12

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


def _read_image_path(action: dict, outcome: "ActionOutcome") -> Optional[str]:
    if action.get("tool") != "read_file" or outcome.status != "ok" or not outcome.content.startswith("[Image file"):
        return None
    from tools.filesystem import resolve_in_workspace
    return str(resolve_in_workspace(str((action.get("args") or {}).get("path", ""))).resolve())


def _graph_note_request(context) -> None:
    try:
        from core.context_graph import graph_for
        request = next((str((m.payload or {}).get("content", "")) for m in reversed(context.conversation.all())
                        if getattr(m, "sender", "") == "User"), "")
        graph_for().note_request(request.split("\n\n[Attached")[0])
    except Exception:
        pass  # the graph is an aid; never let it break a turn


def _graph_record(action: dict, outcome, agent) -> None:
    try:
        from core.context_graph import graph_for
        graph_for().record_action(action, outcome, getattr(agent, "name", ""))
    except Exception:
        pass


def describe_action(action: dict) -> str:
    args = action.get("args", {}) or {}
    target = args.get("path") or args.get("script_path") or args.get("command") or args.get("query") or ""
    return f"{action.get('tool', '?')}({target})" if target else str(action.get("tool", "?"))


EDIT_TOOLS = {"write_file", "patch_file", "edit_file"}
DIFF_CONTEXT_LINES = 3


def unified_diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}", n=DIFF_CONTEXT_LINES,
    ))


def _confirm_edit(action: dict, approve: ApproveFn) -> Optional[ActionOutcome]:
    """
    Claude Code's edit gate: show the diff and ask before a file changes.
    Returns an outcome only when the user declines; otherwise the edit
    proceeds. Edits the tool itself would reject (bad patch, missing file)
    are left to the tool so the agent gets its normal error message.
    """
    from core.security import edits_need_approval
    from tools.filesystem import preview_edit

    if not edits_need_approval():
        return None
    target, old, new, error = preview_edit(action.get("tool"), action.get("args", {}) or {})
    if error or target is None or new is None or new == old:
        return None
    rel = action.get("args", {}).get("path", str(target))
    verb = "Create" if not target.exists() else "Edit"
    preq = PermissionRequestRequired(
        path=str(target), reason=f"{verb} {rel}", kind="edit", details=unified_diff(old, new, rel),
    )
    if approve(preq, action):
        return None
    return ActionOutcome(
        "declined",
        f"DECLINED by user: {describe_action(action)} -- the file was not changed. "
        "Do not retry it; tell the user what you were trying to do and ask how to proceed.",
    )


def execute_with_approval(engine, action: dict, approve: Optional[ApproveFn]) -> ActionOutcome:
    """
    Runs one action. With `approve`, a PermissionRequestRequired is resolved
    inline (ask, approve by kind, retry once); without it, the request
    propagates to the caller as before (the web UI relies on that).
    """
    if approve is not None and action.get("tool") in EDIT_TOOLS:
        declined = _confirm_edit(action, approve)
        if declined:
            return declined

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


WRAP_UP_NOTES = {
    "repeating": "You requested exactly the same actions again, so they were not run.",
    "max_steps": "You have used all the tool steps available for this request.",
    "declined": "The user declined your last action, so it did not run. Do not ask for it again.",
    "no_response": "You stopped without writing an answer in \"response\".",
}


def _has_answer(message) -> bool:
    payload = getattr(message, "payload", {}) or {}
    return bool(str(payload.get("response") or "").strip())


_RESULT_TAG = re.compile(r"^\[(\w+)\((.*?)\) -> (ok|failed|declined)\]")


def work_ledger(messages) -> str:
    """
    What actually happened this turn, from the tool results themselves. Given to
    the wrap-up call because a model asked to "summarize" after a messy turn
    will happily report a fix it never made.
    """
    changed, failed_edits, commands = [], [], []
    for m in messages:
        if getattr(m, "type", None) != MessageType.TOOL_RESULT:
            continue
        match = _RESULT_TAG.match(str((getattr(m, "payload", {}) or {}).get("content", "")))
        if not match:
            continue
        tool, target, status = match.groups()
        if tool in EDIT_TOOLS:
            (changed if status == "ok" else failed_edits).append(target)
        elif tool == "execute_command":
            commands.append(f"`{target[:80]}` -> {status}")
    lines = ["Files you actually changed this turn: " + (", ".join(dict.fromkeys(changed)) or "NONE")]
    if failed_edits:
        lines.append("Edits that did NOT apply: " + ", ".join(dict.fromkeys(failed_edits)))
    if commands:
        lines.append("Commands run: " + "; ".join(commands[-6:]))
    return "\n".join(lines)


def _wrap_up(agent, context, reason: str, source: str, since_index: int = 0):
    """
    One last call, no tools: answer from what's already in the history. Without
    it an early stop leaves the user with the agent's status line ("Searching
    for the latest Python version.") instead of an answer.
    """
    ledger = work_ledger(context.conversation.all()[since_index:])
    context.conversation.add(Message(
        sender="System", receiver=agent.name, type=MessageType.TOOL_RESULT,
        payload={"content": f"[{WRAP_UP_NOTES[reason]} STOP using tools now.\n{ledger}\n"
                            "Write your final answer to the user in \"response\" with \"actions\": []. "
                            "Report ONLY what the record above shows: do not say you changed, fixed or "
                            "verified anything that isn't listed there. If the task is not finished, say "
                            "so plainly, explain what you found and what still needs to be done.]"},
    ))
    event_bus.publish(EventType.AGENT_STARTED, {"agent": agent.name, "turn": "wrap-up"}, source=source)
    message = agent.run(context)
    response = message.metadata.get("response")
    if response is not None:
        response.actions = []  # a wrap-up never runs tools
    if isinstance(message.payload, dict):
        message.payload["actions"] = []
    context.conversation.add(message)
    event_bus.publish(EventType.AGENT_FINISHED, {"agent": agent.name, "msg": message, "step": "wrap-up", "final": True},
                      source=source)
    return message


def run_agent_loop(
    agent,
    context,
    execute_action: Callable[[dict], ActionOutcome],
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    source: str = "AgentLoop",
    is_halted: Optional[Callable[[], bool]] = None,
    wrap_up_on_decline: bool = False,
) -> LoopResult:
    """
    Drives one agent until it stops requesting actions. Every agent message and
    tool result is appended to context.conversation in the order it happened,
    and AGENT_STARTED / AGENT_FINISHED fire once per step so UIs can show
    each intermediate message.

    If the loop ends without a real answer -- repeated actions, the step limit,
    or a final message with no "response" -- the agent gets one tool-free
    wrap-up call. After a decline that happens only with wrap_up_on_decline
    (headless mode, where nobody can redirect it); interactively a "no" hands
    control straight back to the user, like Claude Code.
    """
    since_index = len(context.conversation.all())
    _graph_note_request(context)
    result = _loop(agent, context, execute_action, max_steps, source, is_halted)
    if result.stop_reason == "done" and _shows_code_instead_of_applying(agent, context.conversation.all(), since_index,
                                                                         result.final_message):
        # Once: the user asked for a change, the agent printed code and changed nothing.
        context.conversation.add(Message(
            sender="System", receiver=agent.name, type=MessageType.TOOL_RESULT,
            payload={"content": "[You showed code but changed no files, and the user asked you to make the change. "
                                "Apply it now with edit_file/write_file (read the file first), then verify. If the "
                                "change doesn't belong in a workspace file, or you truly cannot, give your full "
                                "answer in \"response\" again and say why.]"},
        ))
        remaining = max(2, max_steps - result.steps)
        result = _loop(agent, context, execute_action, remaining, source, is_halted)
    reason = result.stop_reason
    if reason == "done" and not _has_answer(result.final_message):
        reason = "no_response"
    if reason in ("repeating", "max_steps", "no_response") or (reason == "declined" and wrap_up_on_decline):
        result.final_message = _wrap_up(agent, context, reason, source, since_index)
    _ensure_sources(result.final_message, context.conversation.all()[since_index:])
    return result


_ACTION_REQUEST = re.compile(r"\b(write|add|fix|create|implement|update|change|refactor|rename|apply|make)\b", re.I)
_NO_EDIT_REQUEST = re.compile(r"don'?t (edit|change|modify|touch)|do not (edit|change|modify)|just (tell|explain|show|list)|"
                              r"read[- ]only|without (editing|changing)", re.I)


def _shows_code_instead_of_applying(agent, messages, since_index: int, final_message) -> bool:
    if not (hasattr(agent, "allows_tool") and agent.allows_tool("edit_file")):
        return False
    response = str(((getattr(final_message, "payload", None) or {}).get("response")) or "")
    if "```" not in response:
        return False
    request = next((str((m.payload or {}).get("content", "")) for m in reversed(messages)
                    if getattr(m, "sender", "") == "User"), "")
    request = request.split("\n\n[Attached")[0]  # ignore attached file contents
    if not _ACTION_REQUEST.search(request) or _NO_EDIT_REQUEST.search(request):
        return False
    # Only when the change is about a file that actually exists here -- "how do I fix
    # this pasted error from app.py" (no app.py in the workspace) is a question.
    from core.security import get_current_workspace
    workspace = get_current_workspace()
    names = set(re.findall(r"[\w./-]+\.[A-Za-z]{1,5}\b", request + "\n" + response))
    if not any((workspace / n).is_file() for n in names if not n.startswith(("http", "/"))):
        return False
    changed = re.compile(r"^\[(write_file|edit_file|patch_file)\(.*?\) -> ok\]")
    return not any(changed.match(str((getattr(m, "payload", {}) or {}).get("content", "")))
                   for m in messages[since_index:])


_URL = re.compile(r"https?://[^\s)\]>\"'`]+")


def _ensure_sources(message, messages) -> None:
    """
    An answer built on web results must say where it came from. Models skip the
    'Sources:' list now and then despite instructions, so if this turn ran
    web_search/web_fetch and the answer cites no URL, append the URLs those tools
    actually returned -- labeled as consulted, since we can't know which were used.
    """
    payload = getattr(message, "payload", None)
    if not isinstance(payload, dict) or not str(payload.get("response") or "").strip():
        return
    if _URL.search(payload["response"]):
        return
    urls = []
    for m in messages:
        content = str((getattr(m, "payload", {}) or {}).get("content", ""))
        if getattr(m, "type", None) == MessageType.TOOL_RESULT and re.match(r"^\[web_(search|fetch)\(", content):
            urls.extend(u.rstrip(".,;:") for u in _URL.findall(content))
    urls = list(dict.fromkeys(urls))[:5]
    if urls:
        payload["response"] = payload["response"].rstrip() + "\n\nSources consulted:\n" + "\n".join(f"- {u}" for u in urls)
        payload["content"] = payload["response"]


def _loop(agent, context, execute_action, max_steps, source, is_halted) -> LoopResult:
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
            _graph_record(action, outcome, agent)
            payload = {"content": f"[{describe_action(action)} -> {outcome.status}]\n{outcome.content}"}
            image = _read_image_path(action, outcome)
            if image:
                payload["images"] = [image]  # the agent's next call sees the picture itself
            context.conversation.add(Message(
                sender="System",
                receiver=agent.name,
                type=MessageType.TOOL_RESULT,
                payload=payload,
            ))
            if outcome.status == "declined":
                # Like Claude Code: a "no" hands control back to the user.
                return LoopResult(message, step, "declined", [describe_action(action)])

    return LoopResult(message, max_steps, "max_steps")
