from agents.loop import ActionOutcome, execute_with_approval, run_agent_loop
from core.protocol import AgentResponse, Message, MessageType
from core.task import Task
from execution.permissions import PermissionRequestRequired


class ScriptedAgent:
    """Returns one scripted list of actions per call and records the history it saw."""

    def __init__(self, steps):
        self.name = "Coder"
        self.steps = list(steps)
        self.seen_histories = []

    def run(self, context):
        self.seen_histories.append([m.payload.get("content", "") for m in context.conversation.all()])
        actions = self.steps.pop(0) if self.steps else []
        response = AgentResponse(agent=self.name, summary=f"step with {len(actions)} actions", actions=actions)
        msg = Message(sender=self.name, receiver="Orchestrator", type=MessageType.SUMMARY,
                      payload={"summary": response.summary, "actions": actions})
        msg.metadata["response"] = response
        return msg


def _read(path):
    return {"tool": "read_file", "args": {"path": path}}


def test_agent_sees_tool_results_before_responding_again():
    agent = ScriptedAgent([[_read("a.py")], []])
    task = Task(goal="x")

    result = run_agent_loop(agent, task.context, lambda a: ActionOutcome("ok", "CONTENTS-OF-A"))

    assert result.stop_reason == "done"
    assert result.steps == 2
    # The second call saw the first call's tool result.
    assert any("CONTENTS-OF-A" in h and "read_file(a.py) -> ok" in h for h in agent.seen_histories[1])


def test_loop_stops_when_user_declines():
    agent = ScriptedAgent([[_read("a.py"), _read("b.py")], []])
    executed = []

    def execute(action):
        executed.append(action)
        return ActionOutcome("declined", "DECLINED by user")

    result = run_agent_loop(agent, Task(goal="x").context, execute)

    assert result.stop_reason == "declined"
    assert result.declined == ["read_file(a.py)"]
    assert len(executed) == 1  # b.py was never attempted
    assert len(agent.seen_histories) == 1  # no further agent call after "no"


def test_loop_stops_on_repeated_identical_actions():
    agent = ScriptedAgent([[_read("a.py")]] * 5)
    result = run_agent_loop(agent, Task(goal="x").context, lambda a: ActionOutcome("ok", ""))
    assert result.stop_reason == "repeating"
    assert result.steps == 2


def test_loop_respects_max_steps():
    agent = ScriptedAgent([[_read(f"{i}.py")] for i in range(10)])
    result = run_agent_loop(agent, Task(goal="x").context, lambda a: ActionOutcome("ok", ""), max_steps=3)
    assert result.stop_reason == "max_steps"
    assert result.steps == 3


def test_history_order_is_chronological():
    agent = ScriptedAgent([[_read("a.py")], []])
    task = Task(goal="x")
    run_agent_loop(agent, task.context, lambda a: ActionOutcome("ok", "A"))
    senders = [m.sender for m in task.context.conversation.all()]
    assert senders == ["Coder", "System", "Coder"]


class _AskingEngine:
    def __init__(self):
        self.calls = 0

    def execute(self, action):
        self.calls += 1
        if self.calls == 1:
            raise PermissionRequestRequired(path="sudo ls", reason="risky", kind="command")
        from execution.engine import ToolResult
        return ToolResult(tool="execute_command", success=True, stdout="ran", stderr="")


def test_execute_with_approval_retries_after_yes():
    engine = _AskingEngine()
    outcome = execute_with_approval(engine, {"tool": "execute_command", "args": {"command": "sudo ls"}}, lambda p, a: True)
    assert outcome.status == "ok" and outcome.content == "ran"
    assert engine.calls == 2


def test_execute_with_approval_without_approver_propagates():
    import pytest
    with pytest.raises(PermissionRequestRequired):
        execute_with_approval(_AskingEngine(), {"tool": "execute_command", "args": {}}, None)
