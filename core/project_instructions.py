"""
Per-project instructions, like Claude Code's CLAUDE.md.

Loaded into every agent prompt:
  1. ~/.sai/SAI.md             -- your personal instructions, for every project
  2. <workspace>/SAI.md        -- the project's instructions (falls back to an
                                  existing CLAUDE.md, so projects already set up
                                  for Claude Code work unchanged)
"""
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_FILENAMES = ("SAI.md", "CLAUDE.md")
USER_FILE = Path.home() / ".sai" / "SAI.md"
MAX_CHARS = 16000


def find_instruction_files(workspace: Path) -> List[Path]:
    found = []
    if USER_FILE.is_file():
        found.append(USER_FILE)
    project = next((workspace / n for n in PROJECT_FILENAMES if (workspace / n).is_file()), None)
    if project:
        found.append(project)
    return found


def load_project_instructions(workspace: Optional[Path] = None) -> Tuple[str, List[Path]]:
    """Returns (combined text, files used). Empty text when there are none."""
    if workspace is None:
        from core.security import get_current_workspace
        workspace = get_current_workspace()
    files = find_instruction_files(Path(workspace))
    parts = []
    for f in files:
        try:
            parts.append(f"# From {f}\n{f.read_text(encoding='utf-8', errors='ignore').strip()}")
        except Exception:
            continue
    text = "\n\n".join(parts)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n...[instructions truncated at {MAX_CHARS} chars]"
    return text, files
