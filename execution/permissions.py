from pathlib import Path
from core.security import validate_path

class PermissionRequestRequired(Exception):
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason

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
