"""
todo_write: the agent's visible task list, like Claude Code's TodoWrite. The
agent sends the whole list each time; the REPL renders it as a checklist and
every agent prompt includes the current list so work stays on track.
"""
from typing import Any, Dict, List

from tools.base import BaseTool

VALID_STATUSES = ("pending", "in_progress", "completed")
MARKS = {"pending": "☐", "in_progress": "◼", "completed": "☒"}

_TODOS: List[Dict[str, str]] = []


def get_todos() -> List[Dict[str, str]]:
    return [dict(t) for t in _TODOS]


def set_todos(todos: List[Dict[str, str]]) -> None:
    _TODOS[:] = [dict(t) for t in todos]


def render_todos(todos: List[Dict[str, str]]) -> str:
    return "\n".join(f"{MARKS.get(t['status'], '☐')} {t['content']}" for t in todos)


class TodoWriteTool(BaseTool):
    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def description(self) -> str:
        return ("Creates/updates your task list for multi-step work. Send the FULL list every time, each "
                "item {content, status: pending|in_progress|completed}. Keep exactly one item in_progress "
                "while working, and mark items completed as soon as they're done. Skip it for trivial tasks.")

    @property
    def permissions(self) -> list:
        return []

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string", "enum": list(VALID_STATUSES)},
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["todos"],
        }

    def execute(self, args: Dict[str, Any]) -> str:
        todos = args.get("todos")
        if not isinstance(todos, list):
            return "Error: 'todos' must be a list of {content, status} items."
        cleaned = []
        for i, item in enumerate(todos, 1):
            if not isinstance(item, dict) or not str(item.get("content", "")).strip():
                return f"Error: todo #{i} needs a non-empty 'content'."
            status = item.get("status", "pending")
            if status not in VALID_STATUSES:
                return f"Error: todo #{i} has invalid status '{status}' (use {', '.join(VALID_STATUSES)})."
            cleaned.append({"content": str(item["content"]).strip(), "status": status})
        if sum(t["status"] == "in_progress" for t in cleaned) > 1:
            return "Error: only one todo can be in_progress at a time."
        set_todos(cleaned)
        return render_todos(cleaned) if cleaned else "Todo list cleared."
