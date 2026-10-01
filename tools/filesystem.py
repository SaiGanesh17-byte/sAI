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


def apply_exact_edit(content: str, old: str, new: str, replace_all: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """Claude Code's Edit semantics: old_string must match exactly, and uniquely unless replace_all."""
    if not old:
        return None, "Error: 'old_string' must not be empty (use write_file to create a file)."
    if old == new:
        return None, "Error: 'old_string' and 'new_string' are identical -- nothing to change."
    count = content.count(old)
    if count == 0:
        return None, "Error: 'old_string' was not found in the file. Re-read the file and copy the exact text, including indentation."
    if count > 1 and not replace_all:
        return None, (f"Error: 'old_string' appears {count} times. Include more surrounding lines to make it "
                      f"unique, or set replace_all to true.")
    return content.replace(old, new) if replace_all else content.replace(old, new, 1), None


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
    if tool_name == "edit_file":
        new, err = apply_exact_edit(old, args.get("old_string", ""), args.get("new_string", ""), bool(args.get("replace_all")))
        return target, old, new, err
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

        from ui.input import is_image, MAX_IMAGE_BYTES
        if is_image(target_path):
            # The agent loop attaches the image itself to the next model call
            # (agents/loop.py), so the model sees it rather than undecodable bytes.
            size = target_path.stat().st_size
            if size > MAX_IMAGE_BYTES:
                return f"Error: image '{path_str}' is {size // 1024} KB; the limit is {MAX_IMAGE_BYTES // 1024 // 1024} MB."
            return f"[Image file '{path_str}' ({size // 1024} KB) -- it is attached; look at it to answer.]"

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

class EditFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return ("Edits a file by exact string replacement: replaces 'old_string' with 'new_string'. "
                "old_string must match the file exactly (including indentation) and be unique unless "
                "replace_all is true. Preferred over patch_file/write_file for changing existing files.")

    @property
    def permissions(self) -> list:
        return ["write"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to the file."},
                "old_string": {"type": "string", "description": "Exact text to replace."},
                "new_string": {"type": "string", "description": "Replacement text."},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)."},
            },
            "required": ["path", "old_string", "new_string"],
        }

    def execute(self, args: Dict[str, Any]) -> str:
        path_str = args.get("path")
        if not path_str:
            return "Error: 'path' argument is required."
        target_path = resolve_in_workspace(path_str)
        if not validate_path(target_path):
            return f"Error: Path '{path_str}' is outside the workspace sandbox."
        if not target_path.is_file():
            return f"Error: File '{path_str}' does not exist. Use write_file to create it."
        try:
            content = target_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file '{path_str}': {e}"

        new_content, err = apply_exact_edit(content, args.get("old_string", ""), args.get("new_string", ""), bool(args.get("replace_all")))
        if err:
            return err
        try:
            import core.security
            from core.security import save_transaction_snapshot
            session_id = getattr(core.security, "CURRENT_SESSION_ID", "default_session")
            save_transaction_snapshot(session_id, str(target_path.resolve()), content, new_content)
        except Exception:
            pass
        try:
            target_path.write_text(new_content, encoding="utf-8")
            try:
                from core.security import POST_PATCH_BUFFERS
                POST_PATCH_BUFFERS[str(target_path.resolve())] = new_content
            except Exception:
                pass
        except Exception as e:
            return f"Error writing file '{path_str}': {e}"
        replaced = content.count(args.get("old_string", "")) if args.get("replace_all") else 1
        return f"Success: Edited '{path_str}' ({replaced} replacement{'s' if replaced != 1 else ''})."


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
