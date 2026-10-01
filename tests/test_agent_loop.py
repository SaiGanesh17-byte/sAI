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
        payload = {"summary": response.summary, "actions": actions}
        if not actions:
            payload["response"] = "final answer"
        msg = Message(sender=self.name, receiver="Orchestrator", type=MessageType.SUMMARY, payload=payload)
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



class _NoAnswerAgent(ScriptedAgent):
    """Finishes without a 'response' unless it's the wrap-up call."""

    def run(self, context):
        msg = super().run(context)
        history = context.conversation.all()
        wrap_up = bool(history) and "STOP using tools now" in history[-1].payload.get("content", "")
        msg.payload.pop("response", None)
        if wrap_up:
            msg.payload["response"] = "wrapped-up answer"
            msg.payload["actions"] = [{"tool": "read_file", "args": {"path": "ignored"}}]
            msg.metadata["response"].actions = list(msg.payload["actions"])
        return msg


def test_wrap_up_when_the_agent_stops_without_an_answer():
    agent = _NoAnswerAgent([[_read("a.py")], []])
    executed = []
    result = run_agent_loop(agent, Task(goal="x").context, lambda a: executed.append(a) or ActionOutcome("ok", "A"))
    assert result.stop_reason == "done"
    assert result.final_message.payload["response"] == "wrapped-up answer"
    assert result.final_message.payload["actions"] == []  # wrap-up never runs tools
    assert len(executed) == 1


def test_wrap_up_after_repeat_and_step_limit():
    repeat = _NoAnswerAgent([[_read("a.py")]] * 5)
    r1 = run_agent_loop(repeat, Task(goal="x").context, lambda a: ActionOutcome("ok", ""))
    assert r1.stop_reason == "repeating" and r1.final_message.payload["response"] == "wrapped-up answer"

    many = _NoAnswerAgent([[_read(f"{i}.py")] for i in range(10)])
    r2 = run_agent_loop(many, Task(goal="x").context, lambda a: ActionOutcome("ok", ""), max_steps=3)
    assert r2.stop_reason == "max_steps" and r2.final_message.payload["response"] == "wrapped-up answer"


def test_decline_wraps_up_only_when_asked():
    declined = lambda a: ActionOutcome("declined", "DECLINED")
    interactive = _NoAnswerAgent([[_read("a.py")]])
    r1 = run_agent_loop(interactive, Task(goal="x").context, declined)
    assert len(interactive.seen_histories) == 1  # hands control back to the user

    headless = _NoAnswerAgent([[_read("a.py")]])
    r2 = run_agent_loop(headless, Task(goal="x").context, declined, wrap_up_on_decline=True)
    assert r2.stop_reason == "declined" and r2.final_message.payload["response"] == "wrapped-up answer"


def test_work_ledger_reports_only_what_actually_happened():
    from agents.loop import work_ledger

    def result(text):
        return Message(sender="System", receiver="Coder", type=MessageType.TOOL_RESULT, payload={"content": text})

    msgs = [
        result("[read_file(calc.py) -> ok]\ndef add..."),
        result("[write_file(manual_test.py) -> ok]\nSuccess"),
        result("[edit_file(calc.py) -> failed]\nError: 'old_string' was not found"),
        result("[execute_command(python3 -m pytest) -> ok]\nNo module named pytest"),
        Message(sender="Coder", receiver="X", type=MessageType.SUMMARY, payload={"summary": "Fixed calc.py!"}),
    ]
    ledger = work_ledger(msgs)
    assert "Files you actually changed this turn: manual_test.py" in ledger
    assert "Edits that did NOT apply: calc.py" in ledger
    assert "`python3 -m pytest` -> ok" in ledger
    assert work_ledger([]).startswith("Files you actually changed this turn: NONE")


def test_sources_appended_when_web_answer_cites_none():
    class WebAgent(ScriptedAgent):
        def run(self, context):
            msg = super().run(context)
            if not msg.payload["actions"]:
                msg.payload["response"] = "Python 3.14.8 is the latest."
            return msg

    agent = WebAgent([[{"tool": "web_search", "args": {"query": "python"}}], []])
    out = "Title: Python\nURL: https://www.python.org/downloads/\nBody: 3.14.8\n---\nURL: https://docs.python.org/3/whatsnew/"
    result = run_agent_loop(agent, Task(goal="x").context, lambda a: ActionOutcome("ok", out))
    response = result.final_message.payload["response"]
    assert response.endswith("Sources consulted:\n- https://www.python.org/downloads/\n- https://docs.python.org/3/whatsnew/")


def test_sources_not_added_when_already_cited_or_no_web_used():
    class Agent(ScriptedAgent):
        def __init__(self, steps, text):
            super().__init__(steps)
            self.text = text

        def run(self, context):
            msg = super().run(context)
            if not msg.payload["actions"]:
                msg.payload["response"] = self.text
            return msg

    cited = Agent([[{"tool": "web_search", "args": {"query": "q"}}], []], "See https://nodejs.org")
    r1 = run_agent_loop(cited, Task(goal="x").context, lambda a: ActionOutcome("ok", "URL: https://other.example"))
    assert "Sources consulted" not in r1.final_message.payload["response"]

    local = Agent([[_read("a.py")], []], "a.py defines add()")
    r2 = run_agent_loop(local, Task(goal="x").context, lambda a: ActionOutcome("ok", "see https://x.example in a comment"))
    assert "Sources consulted" not in r2.final_message.payload["response"]


def test_nudge_when_code_shown_but_not_applied(tmp_workspace):
    (tmp_workspace / "calc.py").write_text("def divide(a, b): ...")

    class LazyThenApplies(ScriptedAgent):
        def __init__(self):
            super().__init__([[], [{"tool": "edit_file", "args": {"path": "t.py"}}], []])
            self.tools = ["*"]

        def allows_tool(self, tool):
            return True

        def run(self, context):
            msg = super().run(context)
            if not msg.payload["actions"]:
                msg.payload["response"] = "Add these tests:\n```python\ndef test_x(): ...\n```" if len(self.seen_histories) == 1 else "Done."
            return msg

    task = Task(goal="x")
    task.context.conversation.add(Message(sender="User", receiver="Debugger", type=MessageType.TASK,
                                          payload={"content": "what edge cases should I test for calc.py? then write those as tests"}))
    agent = LazyThenApplies()
    result = run_agent_loop(agent, task.context, lambda a: ActionOutcome("ok", "Success"))
    assert len(agent.seen_histories) == 3  # nudged once, then it applied and finished
    assert any("changed no files" in h for h in agent.seen_histories[1])
    assert result.final_message.payload["response"] == "Done."


def test_no_nudge_for_questions_or_no_edit_requests(tmp_workspace):
    class ShowsCode(ScriptedAgent):
        def allows_tool(self, tool):
            return True

        def run(self, context):
            msg = super().run(context)
            msg.payload["response"] = "```python\nx = 1\n```"
            return msg

    (tmp_workspace / "calc.py").write_text("x = 1")
    for request in ("how do I reverse a list?", "rename add in calc.py to plus. Don't edit anything, just tell me",
                    "explain this error from app.py and how to fix it"):  # app.py isn't in the workspace
        task = Task(goal="x")
        task.context.conversation.add(Message(sender="User", receiver="Coder", type=MessageType.TASK, payload={"content": request}))
        agent = ShowsCode([[]])
        run_agent_loop(agent, task.context, lambda a: ActionOutcome("ok", ""))
        assert len(agent.seen_histories) == 1, request
