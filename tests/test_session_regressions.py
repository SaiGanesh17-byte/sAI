"""Unit-level guards for bugs seen in a real interactive session (2026-10-01)."""
from types import SimpleNamespace

from rich.console import Console


def test_step_with_pending_actions_shows_its_status_not_its_claims():
    from app import repl as repl_mod
    from core.protocol import Message, MessageType
    from ui.activity import ActivityIndicator, ActivityPrinter

    r = repl_mod.SaiRepl.__new__(repl_mod.SaiRepl)
    r.console = Console(record=True, width=120)
    r.printer, r.activity = ActivityPrinter(r.console), ActivityIndicator(r.console)
    msg = Message(sender="Coder", receiver="X", type=MessageType.SUMMARY, payload={
        "summary": "Running the tests", "response": "All tests are passing!",
        "actions": [{"tool": "execute_command", "args": {"command": "pytest"}}]})
    r._on_agent_finished(SimpleNamespace(source="Jev", data={"agent": "Coder", "msg": msg}))
    out = r.console.export_text()
    assert "Running the tests" in out and "All tests are passing" not in out


def test_reading_an_image_attaches_it_for_the_next_call(tmp_workspace):
    import base64
    from agents.loop import ActionOutcome, run_agent_loop
    from core.protocol import AgentResponse, Message, MessageType
    from core.task import Task
    from tools.filesystem import ReadFileTool

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    (tmp_workspace / "error.png").write_bytes(png)
    result = ReadFileTool().execute({"path": "error.png"})
    assert result.startswith("[Image file 'error.png'")

    class Agent:
        name = "Researcher"
        def __init__(self):
            self.calls = 0
        def run(self, context):
            self.calls += 1
            actions = [{"tool": "read_file", "args": {"path": "error.png"}}] if self.calls == 1 else []
            m = Message(sender="Researcher", receiver="X", type=MessageType.SUMMARY,
                        payload={"summary": "s", "response": "" if actions else "it says hi", "actions": actions})
            m.metadata["response"] = AgentResponse(agent="Researcher", summary="s", actions=actions)
            return m

    task = Task(goal="x")
    run_agent_loop(Agent(), task.context, lambda a: ActionOutcome("ok", ReadFileTool().execute(a["args"])))
    tool_msgs = [m for m in task.context.conversation.all() if m.type is MessageType.TOOL_RESULT]
    assert tool_msgs[0].payload["images"] == [str((tmp_workspace / "error.png").resolve())]


def test_reviewer_without_findings_does_not_crash(fake_llm_runtime, tmp_workspace):
    from core.kernel import kernel
    from core.orchestrator import Orchestrator
    from core.task import Task

    orch = Orchestrator()
    kernel.register_service("llm_runtime", fake_llm_runtime)
    fake_llm_runtime.response = '{"summary": "Reviewing", "response": "add() subtracts.", "actions": []}'  # no findings
    reviewer = next(a for a in orch.agents if a.name == "Reviewer")
    msg = reviewer.run(Task(goal="review").context)
    assert msg.payload["response"] == "add() subtracts." and msg.payload["findings"] == []


def test_web_search_falls_back_when_duckduckgo_fails(monkeypatch):
    from tools import search
    monkeypatch.setattr(search.SearchTool, "_search", lambda self, q, max_results: (_ for _ in ()).throw(RuntimeError("No results found.")))
    monkeypatch.setattr(search, "openrouter_web_search", lambda q: f"[web search via OpenRouter]\nTitle: Node {q}")
    assert search.SearchTool().execute({"query": "node"}).startswith("[web search via OpenRouter]")

    monkeypatch.setattr(search, "openrouter_web_search", lambda q: "")
    out = search.SearchTool().execute({"query": "node"})
    assert out.startswith("Error performing web search") and "web_fetch an official source" in out


def test_environment_note_names_the_python_command():
    from agents.runtime import environment_note
    note = environment_note()
    assert note.startswith("ENVIRONMENT:") and "Python: use `python" in note and "pytest:" in note
