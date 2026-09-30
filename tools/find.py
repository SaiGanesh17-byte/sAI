"""
Fast file-finding tools, like Claude Code's Glob and Grep: agents locate code
directly instead of listing directories one at a time.
"""
import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List

from tools.base import BaseTool
from core.security import get_current_workspace, validate_path

IGNORED_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", ".sai", ".mypy_cache",
                ".pytest_cache", "dist", "build", ".next", ".idea", ".tox"}
MAX_RESULTS = 200
MAX_FILE_BYTES = 2_000_000


def _root(args: Dict[str, Any]) -> Path:
    base = get_current_workspace()
    raw = (args.get("path") or "").strip()
    if not raw:
        return base
    p = Path(raw).expanduser()
    return p if p.is_absolute() else base / p


def _walk_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for name in files:
            yield Path(dirpath) / name


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(get_current_workspace()))
    except ValueError:
        return str(path)


class GlobTool(BaseTool):
    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return ("Finds files by name pattern, e.g. '**/*.py' or 'src/**/test_*.ts'. Returns paths, most "
                "recently modified first. Use this instead of listing directories to locate files.")

    @property
    def permissions(self) -> list:
        return ["read"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'."},
                "path": {"type": "string", "description": "Directory to search (default: workspace root)."},
            },
            "required": ["pattern"],
        }

    def execute(self, args: Dict[str, Any]) -> str:
        pattern = (args.get("pattern") or "").strip()
        if not pattern:
            return "Error: 'pattern' argument is required."
        root = _root(args)
        if not validate_path(root):
            return f"Error: Path '{args.get('path')}' is outside the workspace sandbox."
        if not root.exists():
            return f"Error: Path '{args.get('path')}' does not exist."

        # "**/x" should also match "x" at the top level, as in most glob tools.
        patterns = {pattern, pattern[3:]} if pattern.startswith("**/") else {pattern}
        matches = []
        for f in _walk_files(root):
            rel = str(f.relative_to(root)) if root.is_dir() else f.name
            if any(fnmatch.fnmatch(rel, p) for p in patterns):
                matches.append(f)
        if not matches:
            return f"No files match '{pattern}'."
        matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        shown = [_rel(f) for f in matches[:MAX_RESULTS]]
        extra = f"\n... and {len(matches) - MAX_RESULTS} more (narrow the pattern)" if len(matches) > MAX_RESULTS else ""
        return "\n".join(shown) + extra


class GrepTool(BaseTool):
    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return ("Searches file contents with a regular expression. output_mode 'files' (default) lists "
                "matching files; 'content' shows matching lines with line numbers. Filter files with "
                "'glob' (e.g. '*.py').")

    @property
    def permissions(self) -> list:
        return ["read"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regular expression."},
                "path": {"type": "string", "description": "File or directory to search (default: workspace root)."},
                "glob": {"type": "string", "description": "Only search files whose name matches, e.g. '*.ts'."},
                "output_mode": {"type": "string", "enum": ["files", "content", "count"]},
                "case_insensitive": {"type": "boolean"},
            },
            "required": ["pattern"],
        }

    def execute(self, args: Dict[str, Any]) -> str:
        pattern = args.get("pattern") or ""
        if not pattern:
            return "Error: 'pattern' argument is required."
        try:
            regex = re.compile(pattern, re.IGNORECASE if args.get("case_insensitive") else 0)
        except re.error as e:
            return f"Error: invalid regular expression: {e}"
        root = _root(args)
        if not validate_path(root):
            return f"Error: Path '{args.get('path')}' is outside the workspace sandbox."
        if not root.exists():
            return f"Error: Path '{args.get('path')}' does not exist."
        name_filter = args.get("glob")
        mode = args.get("output_mode") or "files"

        files: List[str] = []
        lines: List[str] = []
        counts: List[str] = []
        for f in _walk_files(root):
            if name_filter and not fnmatch.fnmatch(f.name, name_filter) and not fnmatch.fnmatch(_rel(f), name_filter):
                continue
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable
            hits = [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if regex.search(ln)]
            if not hits:
                continue
            files.append(_rel(f))
            counts.append(f"{_rel(f)}: {len(hits)}")
            if mode == "content":
                lines.extend(f"{_rel(f)}:{i}: {ln.strip()[:200]}" for i, ln in hits)
            if len(files) >= MAX_RESULTS and mode != "content":
                break
            if mode == "content" and len(lines) >= MAX_RESULTS:
                break

        if not files:
            return f"No matches for /{pattern}/."
        out = {"files": files, "content": lines, "count": counts}.get(mode, files)
        truncated = "\n... (truncated -- narrow the search)" if len(out) >= MAX_RESULTS else ""
        return "\n".join(out[:MAX_RESULTS]) + truncated
