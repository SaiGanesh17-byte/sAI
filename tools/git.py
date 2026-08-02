from pathlib import Path
from typing import Dict, Any
from git import Repo
from tools.base import BaseTool

from core.security import get_current_workspace

class GitTool(BaseTool):
    @property
    def name(self) -> str:
        return "git_operation"

    @property
    def description(self) -> str:
        return "Handles git commands like status, diff, and commit."

    @property
    def permissions(self) -> list:
        return ["git"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "diff", "commit"],
                    "description": "The git action to perform."
                },
                "message": {
                    "type": "string",
                    "description": "Commit message (required for 'commit' action)."
                }
            },
            "required": ["action"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        action = args.get("action")
        try:
            repo = Repo(get_current_workspace(), search_parent_directories=True)
        except Exception as e:
            return f"Error opening git repository: {e}"

        if action == "status":
            try:
                status_text = repo.git.status()
                return f"Git Status:\n{status_text}"
            except Exception as e:
                return f"Error executing git status: {e}"
        elif action == "diff":
            try:
                diff_text = repo.git.diff()
                if not diff_text:
                    return "No uncommitted changes."
                return f"Git Diff:\n{diff_text}"
            except Exception as e:
                return f"Error executing git diff: {e}"
        elif action == "commit":
            message = args.get("message")
            if not message:
                return "Error: 'message' argument is required for commit action."
            try:
                repo.git.add(A=True)
                commit_info = repo.git.commit(m=message)
                return f"Successfully committed changes:\n{commit_info}"
            except Exception as e:
                return f"Error executing git commit: {e}"
        else:
            return f"Error: Unsupported git action '{action}'."
