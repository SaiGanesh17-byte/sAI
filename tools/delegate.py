"""
delegate: hand a self-contained sub-task to another agent that works in a
fresh context and returns only its answer -- Claude Code's Task tool.

The sub-agent's file reads, searches and intermediate steps stay in its own
throwaway history, so the caller's context (re-sent on every one of its
steps) grows by one answer instead of by everything the sub-agent looked at.

Execution is handled by Orchestrator.execute_action, which has the approval
callback and runs the sub-agent's tool-use loop; this class only supplies the
tool's name, description and schema.
"""
from typing import Any, Dict

from tools.base import BaseTool

MAX_RESULT_CHARS = 6000


class DelegateTool(BaseTool):
    @property
    def name(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        return ("Hands a self-contained sub-task to another agent, which works in a fresh context with its own "
                "tools and returns only its final answer. Use it for side questions whose details you don't need "
                "to keep -- e.g. Researcher: 'find every caller of save_settings and summarize how each uses it'; "
                "Reviewer: 'review app/auth.py for security bugs'. Write the task so it stands alone: the other "
                "agent does NOT see this conversation.")

    @property
    def permissions(self) -> list:
        return []

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Name of the agent to delegate to (from the AVAILABLE AGENTS list)."},
                "task": {"type": "string", "description": "Complete, standalone instructions: what to find or do, and what to report back."},
            },
            "required": ["agent", "task"],
        }

    def execute(self, args: Dict[str, Any]) -> str:
        return "Error: delegate must be run by the orchestrator (it needs the agent roster and approvals)."
