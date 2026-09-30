from pathlib import Path

import pytest
from rich.console import Console

from core import security
from core import project_instructions as pi


@pytest.fixture
def no_user_file(monkeypatch, tmp_path):
    monkeypatch.setattr(pi, "USER_FILE", tmp_path / "no-such-user-file.md")


def test_sai_md_is_loaded(tmp_path, no_user_file):
    (tmp_path / "SAI.md").write_text("Run tests with: make test")
    text, files = pi.load_project_instructions(tmp_path)
    assert "make test" in text and files == [tmp_path / "SAI.md"]


def test_claude_md_is_used_when_there_is_no_sai_md(tmp_path, no_user_file):
    (tmp_path / "CLAUDE.md").write_text("Use pnpm")
    text, _ = pi.load_project_instructions(tmp_path)
    assert "Use pnpm" in text


def test_sai_md_wins_over_claude_md(tmp_path, no_user_file):
    (tmp_path / "SAI.md").write_text("from sai")
    (tmp_path / "CLAUDE.md").write_text("from claude")
    text, _ = pi.load_project_instructions(tmp_path)
    assert "from sai" in text and "from claude" not in text


def test_user_file_is_included_first(tmp_path, monkeypatch):
    user = tmp_path / "user.md"
    user.write_text("always use type hints")
    monkeypatch.setattr(pi, "USER_FILE", user)
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "SAI.md").write_text("project rule")
    text, files = pi.load_project_instructions(ws)
    assert text.index("type hints") < text.index("project rule")
    assert files == [user, ws / "SAI.md"]


def test_instructions_appear_in_agent_prompt(tmp_workspace, no_user_file):
    from core.prompt_builder import PromptBuilder
    from core.task import Task
    (tmp_workspace / "SAI.md").write_text("NEVER touch legacy/")
    ctx = Task(goal="x").context
    prompt = PromptBuilder.build(ctx, None, ctx.memory, ctx.conversation)
    assert prompt.startswith("[PROJECT INSTRUCTIONS")
    assert "NEVER touch legacy/" in prompt


def test_exclusive_workspace_drops_sai_root(tmp_path):
    saved_ws, saved_roots = security.CURRENT_WORKSPACE, list(security._EXTRA_ALLOWED_ROOTS)
    try:
        security.set_current_workspace(str(tmp_path))
        assert security.validate_path(security.WORKSPACE_ROOT / "core" / "x.py")  # legacy default
        security.set_current_workspace(str(tmp_path), exclusive=True)
        assert not security.validate_path(security.WORKSPACE_ROOT / "core" / "x.py")
        assert security.validate_path(tmp_path / "x.py")
    finally:
        security.CURRENT_WORKSPACE = saved_ws
        security._EXTRA_ALLOWED_ROOTS[:] = saved_roots


def _trust_repl(monkeypatch, tmp_path, answer, settings):
    from app import repl as repl_mod
    saved = {}
    monkeypatch.setattr(repl_mod, "load_settings", lambda: dict(settings))
    monkeypatch.setattr(repl_mod, "save_settings", lambda s: saved.update(s))
    monkeypatch.chdir(tmp_path)
    r = repl_mod.SaiRepl.__new__(repl_mod.SaiRepl)
    r.console = Console(record=True)
    r.console.input = lambda prompt="": answer
    return r, saved


def test_untrusted_folder_declined_exits(monkeypatch, tmp_path):
    r, saved = _trust_repl(monkeypatch, tmp_path, "n", {})
    with pytest.raises(SystemExit):
        r._choose_workspace()
    assert saved == {}


def test_trusted_folder_is_remembered_and_becomes_workspace(monkeypatch, tmp_path):
    saved_ws, saved_roots = security.CURRENT_WORKSPACE, list(security._EXTRA_ALLOWED_ROOTS)
    try:
        r, saved = _trust_repl(monkeypatch, tmp_path, "y", {})
        assert r._choose_workspace() == tmp_path.resolve()
        assert saved["trusted_folders"] == [str(tmp_path.resolve())]
        assert security.get_current_workspace() == tmp_path.resolve()

        # Second launch: no question asked.
        r2, _ = _trust_repl(monkeypatch, tmp_path, "SHOULD-NOT-BE-READ", {"trusted_folders": [str(tmp_path.resolve())]})
        r2.console.input = lambda prompt="": (_ for _ in ()).throw(AssertionError("asked twice"))
        assert r2._choose_workspace() == tmp_path.resolve()
    finally:
        security.CURRENT_WORKSPACE = saved_ws
        security._EXTRA_ALLOWED_ROOTS[:] = saved_roots
