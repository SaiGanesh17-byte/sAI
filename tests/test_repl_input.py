import os
import threading
import time

import pytest

from ui import esc_watcher
from ui.input import InputReader, expand_file_mentions, list_workspace_files, rank_paths


def test_list_workspace_files_skips_noise(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "i.js").write_text("")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("")
    assert list_workspace_files(tmp_path) == [os.path.join("src", "app.py")]


def test_rank_paths_prefers_filename_matches():
    paths = ["docs/app_notes.md", "src/app.py", "tests/test_app.py", "zapp/other.py"]
    ranked = rank_paths("app", paths)
    assert ranked[0] == "src/app.py"
    assert set(ranked) == set(paths)
    assert rank_paths("nomatch", paths) == []


def test_expand_file_mentions_attaches_contents(tmp_workspace):
    (tmp_workspace / "api.py").write_text("def handler(): ...")
    text, attached = expand_file_mentions("fix the bug in @api.py please", tmp_workspace)
    assert attached == [(tmp_workspace / "api.py").resolve()]
    assert text.startswith("fix the bug in @api.py please")
    assert "[Attached file: api.py]" in text and "def handler(): ..." in text


def test_expand_file_mentions_ignores_unknown_and_outside(tmp_workspace):
    text, attached = expand_file_mentions("ping @team and look at @/etc/hosts", tmp_workspace)
    assert attached == [] and text == "ping @team and look at @/etc/hosts"


def test_expand_directory_and_trailing_punctuation(tmp_workspace):
    (tmp_workspace / "pkg").mkdir()
    (tmp_workspace / "pkg" / "a.py").write_text("")
    text, attached = expand_file_mentions("what's in @pkg?", tmp_workspace)
    assert attached == [(tmp_workspace / "pkg").resolve()]
    assert "[Attached directory listing: pkg]" in text and "a.py" in text


def test_completer_offers_commands_and_files(tmp_path):
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.document import Document
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    (tmp_path / "server.py").write_text("")
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        reader = InputReader.__new__(InputReader)
        reader.workspace, reader.commands = tmp_path, {"/compact": "summarize", "/clear": "fresh"}
        reader._files, reader._files_at = [], 0.0
        completer = reader._build_session().completer

        slash = [c.text for c in completer.get_completions(Document("/co"), None)]
        files = [c.text for c in completer.get_completions(Document("look at @serv"), None)]
    assert slash == ["/compact"]
    assert files == ["@server.py"]


@pytest.fixture
def pipe_watch(monkeypatch):
    kills = []
    monkeypatch.setattr(esc_watcher.os, "kill", lambda pid, sig: kills.append(sig))
    r, w = os.pipe()
    watcher = esc_watcher.EscWatcher()
    t = threading.Thread(target=watcher._watch, args=(r,), daemon=True)
    t.start()
    yield w, kills, watcher, t
    watcher._stop.set()
    t.join(timeout=1)
    os.close(r)
    os.close(w)


def test_lone_escape_interrupts(pipe_watch):
    w, kills, _, t = pipe_watch
    os.write(w, b"\x1b")
    t.join(timeout=1)
    assert kills == [esc_watcher.signal.SIGINT]


def test_arrow_keys_and_typing_do_not_interrupt(pipe_watch):
    w, kills, watcher, _ = pipe_watch
    os.write(w, b"\x1b[A")  # up arrow
    os.write(w, b"hello")
    time.sleep(0.3)
    assert kills == []
