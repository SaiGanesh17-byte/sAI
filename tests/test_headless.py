import json

import pytest

from app.headless import run_headless
from core import security


@pytest.fixture
def headless_env(tmp_path, monkeypatch):
    saved_ws, saved_roots = security.CURRENT_WORKSPACE, list(security._EXTRA_ALLOWED_ROOTS)
    monkeypatch.chdir(tmp_path)
    prompts = []

    def install(responder):
        def fake_query(self, prompt, task_kind, temperature=0.2, model=None, **kw):
            prompts.append((task_kind, prompt))
            return json.dumps(responder(task_kind, prompt))
        monkeypatch.setattr("llm.runtime.LLMRuntime.query", fake_query)

    yield tmp_path, install, prompts
    security.CURRENT_WORKSPACE = saved_ws
    security._EXTRA_ALLOWED_ROOTS[:] = saved_roots
    security.set_session_auto_edits(False)


def _run(capsys, *args, **kwargs):
    code = run_headless(*args, quiet=True, **kwargs)
    out = capsys.readouterr().out
    return code, out


def test_direct_answer_text_and_json(headless_env, capsys):
    _, install, _ = headless_env
    install(lambda kind, p: {"route": "direct_answer", "answer": "Hi there!", "reasoning": "", "confidence": 1})
    code, out = _run(capsys, "hello")
    assert code == 0 and out.strip() == "Hi there!"

    code, out = _run(capsys, "hello", output_format="json")
    data = json.loads(out)
    assert data["result"] == "Hi there!" and data["route"] == "direct_answer" and data["is_error"] is False


def _coder(kind, prompt):
    if kind == "Jev":
        return {"route": "single_agent", "agent": "Coder", "reasoning": "", "confidence": 1}
    if "write_file(notes.txt) -> ok" in prompt:
        return {"summary": "Wrote notes.txt.", "actions": []}
    return {"summary": "Writing notes.txt", "actions": [{"tool": "write_file", "args": {"path": "notes.txt", "content": "hi\n"}}]}


def test_edits_are_declined_without_accept_edits(headless_env, capsys):
    ws, install, _ = headless_env
    install(_coder)
    code, out = _run(capsys, "write notes", output_format="json")
    data = json.loads(out)
    assert code == 0
    assert data["stop_reason"] == "declined" and data["permission_denials"]
    assert not (ws / "notes.txt").exists()


def test_accept_edits_applies_them(headless_env, capsys):
    ws, install, _ = headless_env
    install(_coder)
    code, out = _run(capsys, "write notes", accept_edits=True)
    assert code == 0 and out.strip() == "Wrote notes.txt."
    assert (ws / "notes.txt").read_text() == "hi\n"


def test_piped_stdin_is_included(headless_env, capsys):
    _, install, prompts = headless_env
    install(lambda kind, p: {"route": "direct_answer", "answer": "ok", "reasoning": "", "confidence": 1})
    _run(capsys, "explain this", stdin_text="Traceback: ZeroDivisionError")
    assert "ZeroDivisionError" in prompts[0][1]
