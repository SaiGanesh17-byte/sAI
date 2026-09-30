import json

from core.custom_commands import load_custom_commands


def _write(d, name, text):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def test_loads_project_and_user_commands_with_override(tmp_path):
    user, ws = tmp_path / "user", tmp_path / "proj"
    _write(user, "standup.md", "Summarize yesterday's commits.")
    _write(user, "review.md", "USER review")
    _write(ws / ".sai" / "commands", "review.md", "---\ndescription: Review a file for bugs\n---\nReview $ARGUMENTS for bugs.")
    cmds = load_custom_commands(ws, user_dir=user)
    assert set(cmds) == {"/standup", "/review"}
    assert cmds["/review"].description == "Review a file for bugs"
    assert cmds["/review"].render("auth.py") == "Review auth.py for bugs."
    assert cmds["/standup"].description == "Summarize yesterday's commits."


def test_arguments_appended_without_placeholder(tmp_path):
    _write(tmp_path / ".sai" / "commands", "explain.md", "Explain this code simply.")
    cmd = load_custom_commands(tmp_path, user_dir=tmp_path / "none")["/explain"]
    assert cmd.render("") == "Explain this code simply."
    assert cmd.render("utils.py") == "Explain this code simply.\n\nutils.py"


def test_invalid_names_are_ignored(tmp_path):
    _write(tmp_path / ".sai" / "commands", "Bad Name!.md", "x")
    assert load_custom_commands(tmp_path, user_dir=tmp_path / "none") == {}


def test_repl_runs_custom_command_as_a_turn(tmp_path, monkeypatch):
    from rich.console import Console
    from app import repl as repl_mod
    from ui.activity import ActivityIndicator, ActivityPrinter

    _write(tmp_path / ".sai" / "commands", "review.md", "Review $ARGUMENTS carefully.")
    r = repl_mod.SaiRepl.__new__(repl_mod.SaiRepl)
    r.workspace, r.console = tmp_path, Console(record=True)
    r.printer, r.activity = ActivityPrinter(r.console), ActivityIndicator(r.console)
    turns = []
    monkeypatch.setattr(r, "_handle_turn", lambda text: turns.append(text))
    r._run_slash_command("/review api.py")
    assert turns == ["Review api.py carefully."]


def test_headless_expands_custom_command(tmp_path, monkeypatch, capsys):
    from app.headless import run_headless
    from core import security

    _write(tmp_path / ".sai" / "commands", "hello.md", "Say hello to $ARGUMENTS.")
    saved = security.CURRENT_WORKSPACE, list(security._EXTRA_ALLOWED_ROOTS)
    monkeypatch.chdir(tmp_path)
    seen = []

    def fake_query(self, prompt, task_kind, **kw):
        seen.append(prompt)
        return json.dumps({"route": "direct_answer", "answer": "Hello, Sai!", "reasoning": "", "confidence": 1})

    monkeypatch.setattr("llm.runtime.LLMRuntime.query", fake_query)
    try:
        assert run_headless("/hello Sai", quiet=True) == 0
        assert "Say hello to Sai." in seen[0]
        assert run_headless("/nope", quiet=True) == 2
    finally:
        security.CURRENT_WORKSPACE, security._EXTRA_ALLOWED_ROOTS[:] = saved[0], saved[1]
