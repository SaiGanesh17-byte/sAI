import pytest
from rich.console import Console

from core import checkpoints, security
from tools.filesystem import EditFileTool, WriteFileTool


@pytest.fixture
def tx_db(tmp_path, monkeypatch):
    db = tmp_path / "tx.db"
    monkeypatch.setattr(security, "TRANSACTIONS_DB", db)
    monkeypatch.setattr(checkpoints, "TRANSACTIONS_DB", db)
    security.init_transactions_db()
    monkeypatch.setattr(security, "CURRENT_SESSION_ID", "s1", raising=False)
    return db


def test_undo_restores_edits_and_removes_new_files(tmp_workspace, tx_db):
    (tmp_workspace / "app.py").write_text("x = 1\n")
    mark = checkpoints.checkpoint()

    EditFileTool().execute({"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})
    EditFileTool().execute({"path": "app.py", "old_string": "x = 2", "new_string": "x = 3"})
    WriteFileTool().execute({"path": "new.py", "content": "print('hi')\n"})

    plan = checkpoints.plan_undo("s1", mark)
    assert sorted(p.split("/")[-1] for p in (r.path for r in plan.safe)) == ["app.py", "new.py"]
    restored = checkpoints.apply_undo(plan)

    assert len(restored) == 2
    assert (tmp_workspace / "app.py").read_text() == "x = 1\n"  # back to before the turn, not just one step
    assert not (tmp_workspace / "new.py").exists()
    assert checkpoints.plan_undo("s1", mark).restores == []  # transactions consumed


def test_files_changed_by_the_user_afterwards_are_left_alone(tmp_workspace, tx_db):
    (tmp_workspace / "a.py").write_text("a\n")
    (tmp_workspace / "b.py").write_text("b\n")
    mark = checkpoints.checkpoint()
    EditFileTool().execute({"path": "a.py", "old_string": "a", "new_string": "A"})
    EditFileTool().execute({"path": "b.py", "old_string": "b", "new_string": "B"})
    (tmp_workspace / "b.py").write_text("B plus my own work\n")  # user edits after sAI

    plan = checkpoints.plan_undo("s1", mark)
    assert [r.path.split("/")[-1] for r in plan.conflicts] == ["b.py"]
    checkpoints.apply_undo(plan)
    assert (tmp_workspace / "a.py").read_text() == "a\n"
    assert (tmp_workspace / "b.py").read_text() == "B plus my own work\n"


def test_other_sessions_and_earlier_turns_are_not_touched(tmp_workspace, tx_db, monkeypatch):
    (tmp_workspace / "f.py").write_text("0\n")
    EditFileTool().execute({"path": "f.py", "old_string": "0", "new_string": "1"})  # earlier turn
    mark = checkpoints.checkpoint()
    monkeypatch.setattr(security, "CURRENT_SESSION_ID", "other", raising=False)
    EditFileTool().execute({"path": "f.py", "old_string": "1", "new_string": "2"})  # another session
    assert checkpoints.plan_undo("s1", mark).restores == []


def test_repl_undo_walks_back_turn_by_turn(tmp_workspace, tx_db):
    from app import repl as repl_mod
    from core.task import Task
    from types import SimpleNamespace
    from ui.activity import ActivityIndicator, ActivityPrinter

    r = repl_mod.SaiRepl.__new__(repl_mod.SaiRepl)
    r.console = Console(record=True)
    r.console.input = lambda prompt="": "y"
    r.printer, r.activity = ActivityPrinter(r.console), ActivityIndicator(r.console)
    r.workspace, r.session_id, r.task = tmp_workspace, "s1", Task(goal="")
    r.orchestrator = SimpleNamespace(execution_engine=SimpleNamespace(file_versions={"x": 1}))
    r._turn_marks = []

    (tmp_workspace / "f.py").write_text("v0\n")
    r._turn_marks.append((checkpoints.checkpoint(), "turn 1"))
    EditFileTool().execute({"path": "f.py", "old_string": "v0", "new_string": "v1"})
    r._turn_marks.append((checkpoints.checkpoint(), "turn 2 (question, no edits)"))
    r._turn_marks.append((checkpoints.checkpoint(), "turn 3"))
    EditFileTool().execute({"path": "f.py", "old_string": "v1", "new_string": "v2"})

    r._undo_last_turn()
    assert (tmp_workspace / "f.py").read_text() == "v1\n"
    r._undo_last_turn()  # skips the turn without edits
    assert (tmp_workspace / "f.py").read_text() == "v0\n"
    r._undo_last_turn()
    assert "Nothing to undo" in r.console.export_text()
    assert r.orchestrator.execution_engine.file_versions == {}  # agents must re-read
    assert "undid the previous turn" in r.task.context.conversation.all()[-1].payload["content"]
