"""
REPL input, Claude-Code-style:

  - ↑/↓ history, saved across sessions (.sai/repl_history)
  - multi-line: end a line with \\ or press Option/Alt+Enter for a newline
  - Tab completion for /commands and @file mentions
  - @path/to/file in a message attaches that file's contents for the agents

Falls back to plain input when stdin isn't a terminal (pipes, tests).
"""
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

HISTORY_FILE = Path(__file__).resolve().parent.parent / ".sai" / "repl_history"
IGNORED_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", ".sai", ".mypy_cache", ".pytest_cache", "dist", "build", ".idea", ".next"}
MAX_INDEXED_FILES = 5000
MAX_ATTACH_CHARS = 20000
MENTION = re.compile(r"(?<!\S)@([^\s]+)")


def list_workspace_files(workspace: Path, limit: int = MAX_INDEXED_FILES) -> List[str]:
    out = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(root, name), workspace)
            out.append(rel)
            if len(out) >= limit:
                return out
    return out


def rank_paths(query: str, paths: Iterable[str], limit: int = 30) -> List[str]:
    """Filename matches first, then path prefix, then anywhere in the path."""
    q = query.lower()
    scored = []
    for p in paths:
        low = p.lower()
        base = os.path.basename(low)
        if base.startswith(q):
            score = 0
        elif low.startswith(q):
            score = 1
        elif q in base:
            score = 2
        elif q in low:
            score = 3
        else:
            continue
        scored.append((score, len(p), p))
    return [p for _, _, p in sorted(scored)[:limit]]


def expand_file_mentions(text: str, workspace: Path) -> Tuple[str, List[Path]]:
    """
    Appends the contents of each @mentioned file (inside the workspace) to the
    message. Returns (expanded_text, attached_paths). Unknown mentions (e.g.
    an email-like "@team") are left alone.
    """
    from core.security import validate_path

    attached: List[Path] = []
    blocks = []
    for match in MENTION.finditer(text):
        raw = match.group(1).rstrip(".,;:!?)")
        target = (workspace / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if target in attached or not target.exists() or not validate_path(target):
            continue
        if target.is_dir():
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir() if p.name not in IGNORED_DIRS)
            blocks.append(f"[Attached directory listing: {raw}]\n" + "\n".join(entries[:200]))
        else:
            try:
                content = target.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if len(content) > MAX_ATTACH_CHARS:
                content = content[:MAX_ATTACH_CHARS] + f"\n...[truncated, {len(content) - MAX_ATTACH_CHARS} more chars -- use read_file for the rest]"
            blocks.append(f"[Attached file: {raw}]\n```\n{content}\n```")
        attached.append(target)
    if not blocks:
        return text, []
    return text + "\n\n" + "\n\n".join(blocks), attached


class InputReader:
    """Wraps a prompt_toolkit PromptSession; plain input() when not interactive."""

    FILE_INDEX_TTL = 10.0

    def __init__(self, workspace: Path, commands: Dict[str, str]):
        self.workspace = workspace
        self.commands = commands
        self._files: List[str] = []
        self._files_at = 0.0
        self.session = None
        if sys.stdin.isatty() and sys.stdout.isatty():
            self.session = self._build_session()

    def files(self) -> List[str]:
        if time.time() - self._files_at > self.FILE_INDEX_TTL:
            self._files = list_workspace_files(self.workspace)
            self._files_at = time.time()
        return self._files

    def _build_session(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings

        reader = self

        class SaiCompleter(Completer):
            def get_completions(self, document, complete_event):
                before = document.text_before_cursor
                if before.startswith("/") and " " not in before:
                    for cmd, meta in reader.commands.items():
                        if cmd.startswith(before):
                            yield Completion(cmd, start_position=-len(before), display_meta=meta)
                    return
                word = before.split()[-1] if before and not before[-1].isspace() else ""
                if word.startswith("@"):
                    for path in rank_paths(word[1:], reader.files()):
                        yield Completion("@" + path, start_position=-len(word), display=path)

        bindings = KeyBindings()

        @bindings.add("enter")
        def _(event):
            buf = event.current_buffer
            state = buf.complete_state
            if state and state.current_completion:
                buf.apply_completion(state.current_completion)
                return
            if buf.document.text_before_cursor.endswith("\\"):
                buf.delete_before_cursor(1)
                buf.insert_text("\n")
                return
            buf.validate_and_handle()

        @bindings.add("escape", "enter")  # Option/Alt+Enter
        def _(event):
            event.current_buffer.insert_text("\n")

        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        return PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
            completer=SaiCompleter(),
            complete_while_typing=True,
            key_bindings=bindings,
            multiline=True,  # Enter is handled above; this lets the buffer hold newlines
            prompt_continuation=lambda width, line_number, is_soft_wrap: "│   ",
        )

    def read(self, fallback_input) -> str:
        if self.session is None:
            return fallback_input()
        from prompt_toolkit.formatted_text import HTML
        return self.session.prompt(HTML('<style fg="#2DD4BF">│</style> <b><style fg="#2DD4BF">❯</style></b> '))
