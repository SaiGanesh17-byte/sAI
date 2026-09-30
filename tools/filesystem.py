import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from tools.base import BaseTool
from core.security import validate_path, get_current_workspace

_PATCH_BLOCK = re.compile(r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE", re.DOTALL)


def apply_search_replace(content: str, patch: str) -> Tuple[Optional[str], Optional[str]]:
    """Applies Aider-style SEARCH/REPLACE blocks. Returns (new_content, None) or (None, error)."""
    matches = _PATCH_BLOCK.findall(patch or "")
    if not matches:
        return None, "Error: No valid SEARCH/REPLACE blocks found in patch."
    new_content = content
    for search, replace in matches:
        if search not in new_content:
            return None, f"Error: Search block not found in file:\n{search}"
        new_content = new_content.replace(search, replace, 1)
    return new_content, None


def resolve_in_workspace(path_str: str) -> Path:
    target = Path(path_str).expanduser()
    if not target.is_absolute():
        target = get_current_workspace() / target
    return target


def preview_edit(tool_name: str, args: Dict[str, Any]) -> Tuple[Optional[Path], str, Optional[str], Optional[str]]:
    """
    Computes what a write_file/patch_file call *would* do, without writing:
    (target_path, old_content, new_content, error). Used to show a diff
    before asking the user to approve an edit.
    """
    path_str = args.get("path")
    if not path_str:
        return None, "", None, "Error: 'path' argument is required."
    target = resolve_in_workspace(path_str)
    old = ""
    if target.exists() and target.is_file():
        try:
            old = target.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return target, "", None, f"Error reading file '{path_str}': {e}"
    if tool_name == "write_file":
        return target, old, args.get("content", ""), None
    if not target.exists():
        return target, "", None, f"Error: File '{path_str}' does not exist. Use write_file to create it first."
    new, err = apply_search_replace(old, args.get("patch", ""))
    return target, old, new, err


class ReadFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Reads the text contents of a file safely inside the workspace sandbox."

    @property
    def permissions(self) -> list:
        return ["read"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path inside the workspace."}
            },
            "required": ["path"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        path_str = args.get("path")
        if not path_str:
            return "Error: 'path' argument is required."
        
        target_path = Path(path_str)
        if not target_path.is_absolute():
            target_path = get_current_workspace() / target_path

        if not validate_path(target_path):
            return f"Error: Path '{path_str}' is outside the workspace sandbox."

        if not target_path.exists():
            return f"Error: File '{path_str}' does not exist."

        try:
            return target_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file '{path_str}': {e}"

class WriteFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Writes text content to a file inside the workspace sandbox, creating directories as needed."

    @property
    def permissions(self) -> list:
        return ["write"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to target file."},
                "content": {"type": "string", "description": "Complete file content."}
            },
            "required": ["path", "content"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        path_str = args.get("path")
        content = args.get("content", "")
        if not path_str:
            return "Error: 'path' argument is required."

        target_path = Path(path_str)
        if not target_path.is_absolute():
            target_path = get_current_workspace() / target_path

        if not validate_path(target_path):
            return f"Error: Path '{path_str}' is outside the workspace sandbox."

        try:
            from core.security import save_transaction_snapshot
            import core.security
            session_id = getattr(core.security, "CURRENT_SESSION_ID", "default_session")
            abs_path_str = str(target_path.resolve())
            before_content = None
            if target_path.exists():
                before_content = target_path.read_text(encoding="utf-8", errors="ignore")
            save_transaction_snapshot(session_id, abs_path_str, before_content, content)
        except Exception:
            pass

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            try:
                from core.security import POST_PATCH_BUFFERS
                POST_PATCH_BUFFERS[str(target_path.resolve())] = content
            except Exception:
                pass
            return f"Success: Wrote to file '{path_str}'."
        except Exception as e:
            return f"Error writing file '{path_str}': {e}"

class PatchFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "patch_file"

    @property
    def description(self) -> str:
        return "Applies an Aider-style Search/Replace block patch to an existing file."

    @property
    def permissions(self) -> list:
        return ["write"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to target file."},
                "patch": {"type": "string", "description": "Search/Replace patch block with <<<<<<< SEARCH, =======, >>>>>>> REPLACE structures."}
            },
            "required": ["path", "patch"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        path_str = args.get("path")
        patch = args.get("patch", "")
        if not path_str:
            return "Error: 'path' argument is required."
        if not patch:
            return "Error: 'patch' argument is required."

        target_path = Path(path_str)
        if not target_path.is_absolute():
            target_path = get_current_workspace() / target_path

        if not validate_path(target_path):
            return f"Error: Path '{path_str}' is outside the workspace sandbox."

        if not target_path.exists():
            return f"Error: File '{path_str}' does not exist. Use write_file to create it first."

        try:
            from core.security import save_transaction_snapshot
            import core.security
            session_id = getattr(core.security, "CURRENT_SESSION_ID", "default_session")
            abs_path_str = str(target_path.resolve())
            before_content = None
            if target_path.exists():
                before_content = target_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            before_content = None

        try:
            content = target_path.read_text(encoding="utf-8")
            new_content, patch_error = apply_search_replace(content, patch)
            if patch_error:
                return patch_error

            try:
                save_transaction_snapshot(session_id, abs_path_str, before_content, new_content)
            except Exception:
                pass

            target_path.write_text(new_content, encoding="utf-8")
            try:
                from core.security import POST_PATCH_BUFFERS
                POST_PATCH_BUFFERS[str(target_path.resolve())] = new_content
            except Exception:
                pass
            return f"Success: Applied patch to '{path_str}'."
        except Exception as e:
            return f"Error patching file '{path_str}': {e}"

class ListDirectoryTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_directory"

    @property
    def description(self) -> str:
        return "Lists files and subdirectories inside a directory path."

    @property
    def permissions(self) -> list:
        return ["read"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative directory path. Defaults to active workspace root if empty."}
            }
        }

    def execute(self, args: Dict[str, Any]) -> str:
        path_str = args.get("path", "").strip()
        target_path = get_current_workspace()
        if path_str:
            target_path = Path(path_str)
            if not target_path.is_absolute():
                target_path = get_current_workspace() / target_path
                
        if not validate_path(target_path):
            return f"Error: Path '{path_str}' is outside the workspace sandbox."
            
        if not target_path.exists():
            return f"Error: Directory '{path_str}' does not exist."
            
        if not target_path.is_dir():
            return f"Error: Path '{path_str}' is a file, not a directory. Use read_file to inspect it."
            
        try:
            output = []
            for p in sorted(target_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if p.name in {".git", "venv", "node_modules", "__pycache__", ".sai"}:
                    continue
                prefix = "📁" if p.is_dir() else "📄"
                output.append(f"{prefix} {p.name}")
            
            if not output:
                return "Directory is empty."
            return "\n".join(output)
        except Exception as e:
            return f"Error listing directory '{path_str}': {e}"
