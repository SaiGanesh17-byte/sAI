from rich.console import Console

from core.protocol import Message, MessageType
from core.session import SessionStore, compact_conversation, estimate_conversation_tokens, workspace_slug
from core.task import Task


def _msg(sender, text, **extra):
    return Message(sender=sender, receiver="X", type=MessageType.TASK if sender == "User" else MessageType.SUMMARY,
                   payload={"content": text, **extra})


def _task_with(n):
    task = Task(goal="build api")
    for i in range(n):
        task.context.conversation.add(_msg("User" if i % 2 == 0 else "Coder", f"message {i}"))
    return task


def test_save_and_load_round_trip(tmp_path):
    store = SessionStore(tmp_path / "proj", root=tmp_path / "sessions")
    task = _task_with(4)
    task.context.memory.implementation = "wrote api.py"
    task.context.conversation.add(_msg("Coder", "done", actions=[{"tool": "write_file", "args": {"path": "api.py"}}]))
    sid = SessionStore.new_id()
    store.save(sid, task)

    restored = Task(goal="")
    assert store.load_into(sid, restored)
    msgs = restored.context.conversation.all()
    assert [m.payload["content"] for m in msgs] == [m.payload["content"] for m in task.context.conversation.all()]
    assert msgs[0].type is MessageType.TASK
    assert msgs[-1].payload["actions"][0]["args"]["path"] == "api.py"
    assert restored.context.memory.implementation == "wrote api.py"
    assert restored.goal == "build api"


def test_list_is_newest_first_with_title(tmp_path):
    store = SessionStore(tmp_path, root=tmp_path / "s")
    older, newer = _task_with(2), _task_with(2)
    older.context.conversation.messages[0].payload["content"] = "first question"
    newer.context.conversation.messages[0].payload["content"] = "second question"
    store.save("a", older)
    store.save("b", newer)
    infos = store.list()
    assert [i.id for i in infos] == ["b", "a"]
    assert infos[0].title == "second question"


def test_sessions_are_separate_per_workspace(tmp_path):
    root = tmp_path / "s"
    SessionStore(tmp_path / "one", root=root).save("x", _task_with(2))
    assert SessionStore(tmp_path / "two", root=root).list() == []
    assert workspace_slug(tmp_path / "one") != workspace_slug(tmp_path / "two")


class FakeRuntime:
    def __init__(self):
        self.prompts = []

    def query(self, prompt, task_kind, temperature=0.2, **kw):
        self.prompts.append(prompt)
        return "User wants an API. api.py created. Next: add tests."


def test_compaction_replaces_older_messages_with_summary():
    task = _task_with(12)
    runtime = FakeRuntime()
    before, after = compact_conversation(task, runtime, keep_last=4, focus="the API design")

    msgs = task.context.conversation.all()
    assert len(msgs) == 5
    assert msgs[0].payload["content"].startswith("[Summary of the earlier conversation]")
    assert "api.py created" in msgs[0].payload["content"]
    assert [m.payload["content"] for m in msgs[1:]] == [f"message {i}" for i in range(8, 12)]
    assert "message 0" in runtime.prompts[0] and "message 8" not in runtime.prompts[0]
    assert "the API design" in runtime.prompts[0]
    assert isinstance(before, int) and isinstance(after, int)


def test_compaction_skips_short_conversations():
    assert compact_conversation(_task_with(3), FakeRuntime(), keep_last=4) is None


def test_auto_compact_triggers_over_threshold(monkeypatch):
    from app import repl as repl_mod
    from ui.activity import ActivityIndicator, ActivityPrinter

    r = repl_mod.SaiRepl.__new__(repl_mod.SaiRepl)
    r.console = Console(record=True)
    r.printer, r.activity = ActivityPrinter(r.console), ActivityIndicator(r.console)
    r.task = _task_with(10)
    r.task.context.conversation.messages[0].payload["content"] = "x" * 4000  # ~1000 tokens
    calls = []
    monkeypatch.setattr(repl_mod, "load_settings", lambda: {"auto_compact_tokens": 500})
    monkeypatch.setattr(r, "_compact", lambda focus="", automatic=False: calls.append(automatic))

    r._auto_compact_if_needed()
    assert calls == [True]

    calls.clear()
    monkeypatch.setattr(repl_mod, "load_settings", lambda: {"auto_compact_tokens": 10_000})
    r._auto_compact_if_needed()
    assert calls == []
    assert estimate_conversation_tokens(r.task.context.conversation) > 500
