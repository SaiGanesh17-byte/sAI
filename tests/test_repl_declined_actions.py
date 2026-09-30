from types import SimpleNamespace

from rich.console import Console

from app.repl import SaiRepl
from ui.activity import ActivityIndicator, ActivityPrinter
from ui.esc_watcher import EscWatcher
from core.task import Task
from execution.permissions import PermissionRequestRequired


class _DenyingEngine:
    def execute(self, action):
        raise PermissionRequestRequired(path="/outside/query_api.py", reason="outside sandbox")


def _repl(approve: bool):
    repl = SaiRepl.__new__(SaiRepl)
    repl.console = Console(record=True, width=200)
    repl.task = Task(goal="write a query api")
    repl.orchestrator = SimpleNamespace(execution_engine=_DenyingEngine())
    repl.printer = ActivityPrinter(repl.console)
    repl.activity = ActivityIndicator(repl.console)
    repl._current_agent = ""
    repl._open_tool = None
    repl.esc = EscWatcher()
    repl._prompt_approval = lambda preq, action=None: approve
    return repl


def test_declined_action_is_recorded_in_history():
    """
    Regression: a declined write_file left no trace in the conversation, so the
    agent's pre-written summary ("Created query_api.py...") was the only record
    and the next turn confidently told the user the file existed.
    """
    from agents.loop import execute_with_approval

    repl = _repl(approve=False)
    outcome = execute_with_approval(
        _DenyingEngine(), {"tool": "write_file", "args": {"path": "query_api.py"}}, repl._prompt_approval
    )
    assert outcome.status == "declined"
    assert "DECLINED" in outcome.content and "write_file(query_api.py)" in outcome.content


def test_approval_prompt_shows_the_choices(monkeypatch):
    # Regression: "[y/N]" was parsed as Rich markup and silently disappeared.
    repl = _repl(approve=False)
    del repl._prompt_approval
    monkeypatch.setattr(repl.console, "input", lambda prompt: repl.console.print(prompt, end="") or "n")
    SaiRepl._prompt_approval(repl, PermissionRequestRequired(path="x", reason="y"))
    assert "[y/N]" in repl.console.export_text()
