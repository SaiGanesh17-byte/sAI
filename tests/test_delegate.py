import json

from core.events import event_bus, EventType
from core.orchestrator import Orchestrator
from core.protocol import AgentResponse, Message, MessageType
from core.task import Task


class Scripted:
    """Agent stand-in: returns scripted (actions, response) pairs and records what it saw."""

    def __init__(self, name, steps, tools=("*",)):
        self.name, self.role, self.priority = name, f"{name} role", 0
        self.steps = list(steps)
        self.tools = list(tools)
        self.seen = []

    def allows_tool(self, tool):
        import fnmatch
        return any(fnmatch.fnmatch(tool, p) for p in self.tools)

    def run(self, context):
        self.seen.append([m.payload.get("content", "") for m in context.conversation.all()])
        actions, response = self.steps.pop(0) if self.steps else ([], "done")
        r = AgentResponse(agent=self.name, summary="working", actions=actions)
        msg = Message(sender=self.name, receiver="X", type=MessageType.SUMMARY,
                      payload={"summary": "working", "actions": actions, "response": response if not actions else ""})
        msg.metadata["response"] = r
        return msg


def _orch(*agents):
    o = Orchestrator()
    o.agents = list(agents)
    return o


def test_delegate_runs_helper_in_fresh_context_and_returns_only_its_answer():
    helper = Scripted("Researcher", [([], "save_settings is called from app/repl.py and core/settings.py")])
    caller = Scripted("Coder", [])
    task = Task(goal="x")
    task.context.conversation.add(Message(sender="User", receiver="Coder", type=MessageType.TASK,
                                          payload={"content": "SECRET CALLER HISTORY"}))
    o = _orch(caller, helper)

    out = o.execute_action(task, caller, {"tool": "delegate", "args": {"agent": "researcher", "task": "find callers of save_settings"}})

    assert out.status == "ok"
    assert out.content == "Researcher reports:\nsave_settings is called from app/repl.py and core/settings.py"
    first_view = helper.seen[0]
    assert len(first_view) == 1 and "find callers of save_settings" in first_view[0]
    assert not any("SECRET CALLER HISTORY" in m for m in first_view)  # fresh context
    assert len(task.context.conversation.all()) == 1  # caller history untouched by the helper's steps


def test_helper_uses_its_own_tools_and_cannot_delegate_again():
    helper = Scripted("Researcher", [
        ([{"tool": "delegate", "args": {"agent": "Coder", "task": "x"}}], ""),
        ([], "could not delegate, answered myself"),
    ])
    caller = Scripted("Coder", [])
    out = _orch(caller, helper).execute_action(Task(goal="x"), caller,
                                               {"tool": "delegate", "args": {"agent": "Researcher", "task": "t"}})
    assert out.content.endswith("could not delegate, answered myself")
    assert any("can't delegate again" in m for m in helper.seen[1])


def test_bad_delegate_targets():
    caller = Scripted("Coder", [])
    o = _orch(caller, Scripted("Reviewer", []))
    assert "Choose one of: Reviewer" in o.execute_action(Task(goal="x"), caller, {"tool": "delegate", "args": {"agent": "Nobody", "task": "t"}}).content
    assert "can't delegate to 'Coder'" in o.execute_action(Task(goal="x"), caller, {"tool": "delegate", "args": {"agent": "Coder", "task": "t"}}).content
    assert "'task' is required" in o.execute_action(Task(goal="x"), caller, {"tool": "delegate", "args": {"agent": "Reviewer"}}).content


def test_delegate_respects_the_callers_tool_list():
    caller = Scripted("Reviewer", [], tools=("read_file",))
    out = _orch(caller, Scripted("Coder", [])).execute_action(Task(goal="x"), caller, {"tool": "delegate", "args": {"agent": "Coder", "task": "t"}})
    assert out.status == "failed" and "can't use 'delegate'" in out.content


def test_delegate_publishes_events_for_the_ui():
    seen = []
    cb = lambda e: seen.append((e.event_type.value, e.source, e.data.get("tool")))
    for et in (EventType.TOOL_STARTED, EventType.TOOL_FINISHED, EventType.AGENT_STARTED):
        event_bus.subscribe(et, cb)
    try:
        caller, helper = Scripted("Coder", []), Scripted("Researcher", [([], "answer")])
        _orch(caller, helper).execute_action(Task(goal="x"), caller, {"tool": "delegate", "args": {"agent": "Researcher", "task": "t"}})
    finally:
        for et in (EventType.TOOL_STARTED, EventType.TOOL_FINISHED, EventType.AGENT_STARTED):
            event_bus._listeners[et.value].remove(cb)
    assert ("TOOL_STARTED", "Delegate", "delegate") in seen
    assert ("AGENT_STARTED", "Delegate", None) in seen
    assert seen[-1] == ("TOOL_FINISHED", "Delegate", "delegate")
