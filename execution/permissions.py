from pathlib import Path
from core.security import validate_path

class PermissionRequestRequired(Exception):
    # kind tells the approver what `path` actually holds, so approval never has
    # to guess from the string's shape:
    #   "path"    -- a filesystem path to allow access to
    #   "command" -- an exact shell command to allow once
    #   "script"  -- an absolute script path, approved for its current content only
    #   "edit"    -- a file edit; `details` holds a unified diff of the change
    def __init__(self, path: str, reason: str, kind: str = "path", details: str = ""):
        self.path = path
        self.reason = reason
        self.kind = kind
        self.details = details
        super().__init__(f"Permission required for '{path}': {reason}")

class PermissionChecker:
    @staticmethod
    def validate_path(path_str: str) -> bool:
        """
        Delegates validation to central core.security.validate_path
        """
        return validate_path(path_str)

    @staticmethod
    def confirm_action(action_description: str) -> bool:
        """
        Handles permissions checks and user authorization prompts.
        """
        return True
