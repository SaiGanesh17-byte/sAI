"""
Session persistence and conversation compaction for the REPL.

Sessions are saved after every turn to
    ~/sAI/.sai/sessions/<workspace-slug>/<session-id>.json
so `sai --continue` / `sai --resume` (and /resume inside the REPL) can pick
up where you left off, per project -- like Claude Code's --continue/--resume.

Compaction replaces older history with an LLM-written summary (Claude Code's
/compact, and its automatic compaction near the context limit), instead of
only cutting old messages off.
"""
import json
import re
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from core.protocol import Message, MessageType
from memory.working import WorkingMemory
from tools.todo import get_todos, set_todos

SESSIONS_ROOT = Path(__file__).resolve().parent.parent / ".sai" / "sessions"
CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# (De)serialization
# ---------------------------------------------------------------------------

def message_to_dict(msg) -> dict:
    mtype = getattr(msg, "type", "")
    return {
        "sender": getattr(msg, "sender", ""),
        "receiver": getattr(msg, "receiver", ""),
        "type": mtype.value if isinstance(mtype, MessageType) else str(mtype),
        "payload": getattr(msg, "payload", {}) or {},
        "timestamp": getattr(msg, "timestamp", datetime.now(timezone.utc)).isoformat(),
    }


def message_from_dict(data: dict) -> Message:
    try:
        mtype = MessageType(data.get("type", "SUMMARY"))
    except ValueError:
        mtype = MessageType.SUMMARY
    msg = Message(sender=data.get("sender", ""), receiver=data.get("receiver", ""), type=mtype,
                  payload=data.get("payload") or {})
    try:
        msg.timestamp = datetime.fromisoformat(data["timestamp"])
    except (KeyError, ValueError):
        pass
    return msg


def _memory_to_dict(memory: WorkingMemory) -> dict:
    return asdict(memory)


def _memory_from_dict(data: dict, fallback_goal: str) -> WorkingMemory:
    known = {f.name for f in fields(WorkingMemory)}
    kwargs = {k: v for k, v in (data or {}).items() if k in known}
    kwargs.setdefault("goal", fallback_goal)
    return WorkingMemory(**kwargs)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    id: str
    path: Path
    updated: datetime
    title: str
    message_count: int


def workspace_slug(workspace: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(Path(workspace).resolve())).strip("-") or "root"


class SessionStore:
    def __init__(self, workspace: Path, root: Optional[Path] = None):
        self.dir = (root or SESSIONS_ROOT) / workspace_slug(workspace)

    @staticmethod
    def new_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def save(self, session_id: str, task) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        messages = [message_to_dict(m) for m in task.context.conversation.all()]
        title = next((str(m["payload"].get("content", ""))[:80] for m in messages
                      if m["sender"] == "User" and m["payload"].get("content")), "(empty session)")
        data = {
            "id": session_id,
            "updated": datetime.now(timezone.utc).isoformat(),
            "title": title,
            "goal": task.goal,
            "messages": messages,
            "memory": _memory_to_dict(task.context.memory),
            "todos": get_todos(),
        }
        path = self.dir / f"{session_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
        tmp.replace(path)  # atomic: a crash mid-write never corrupts the session
        return path

    def list(self) -> List[SessionInfo]:
        infos = []
        for f in self.dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                infos.append(SessionInfo(
                    id=data["id"], path=f, updated=datetime.fromisoformat(data["updated"]),
                    title=data.get("title", ""), message_count=len(data.get("messages", [])),
                ))
            except Exception:
                continue
        return sorted(infos, key=lambda i: i.updated, reverse=True)

    def load_into(self, session_id: str, task) -> bool:
        """Replaces task's conversation and working memory with a saved session's."""
        path = self.dir / f"{session_id}.json"
        if not path.is_file():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        conversation = task.context.conversation
        conversation.messages[:] = [message_from_dict(m) for m in data.get("messages", [])]
        task.goal = data.get("goal", "")
        task.context.memory = _memory_from_dict(data.get("memory", {}), task.goal)
        set_todos(data.get("todos") or [])
        return True


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

COMPACT_PROMPT = """You are compacting the history of a coding session so the work can continue
with a much shorter context. Write a summary that someone picking the work up would need:

- What the user asked for, in their words where it matters, and any preferences they stated.
- What has been done: files created or changed (exact paths), commands run and their outcomes.
- Decisions made and why; approaches that failed and should not be retried.
- Current state and what remains to be done (open todos, unanswered questions).
- Anything the user declined or asked not to do.

Be specific and concise (under 400 words). Plain text, no JSON.
{focus}
CONVERSATION:
{history}
"""


def estimate_conversation_tokens(conversation) -> int:
    total = 0
    for m in conversation.all():
        payload = getattr(m, "payload", {}) or {}
        total += len(str(payload.get("content") or payload.get("summary") or payload))
    return total // CHARS_PER_TOKEN


def _render_history(messages) -> str:
    lines = []
    for m in messages:
        payload = getattr(m, "payload", {}) or {}
        text = str(payload.get("content") or payload.get("summary") or "")
        if len(text) > 3000:
            text = text[:3000] + " ...[truncated]"
        lines.append(f"{getattr(m, 'sender', '?')}: {text}")
    return "\n".join(lines)


def compact_conversation(task, llm_runtime, keep_last: int = 4, focus: str = "") -> Optional[tuple]:
    """
    Summarizes all but the last `keep_last` messages into one message.
    Returns (tokens_before, tokens_after), or None if there was nothing to compact.
    """
    conversation = task.context.conversation
    messages = conversation.all()
    if len(messages) <= keep_last + 1:
        return None
    before = estimate_conversation_tokens(conversation)
    older, recent = messages[:-keep_last] if keep_last else messages, messages[-keep_last:] if keep_last else []

    focus_line = f"\nPay particular attention to: {focus}\n" if focus else ""
    summary = llm_runtime.query(
        COMPACT_PROMPT.format(focus=focus_line, history=_render_history(older)),
        task_kind="Compactor",
        temperature=0.0,
    ).strip()

    summary_msg = Message(
        sender="System", receiver="All", type=MessageType.SUMMARY,
        payload={"content": f"[Summary of the earlier conversation]\n{summary}", "compacted": len(older)},
    )
    conversation.messages[:] = [summary_msg, *recent]
    return before, estimate_conversation_tokens(conversation)
