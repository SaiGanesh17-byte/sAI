"""
Claude-Code-style live activity rendering for the sAI REPL (app/repl.py).

    ⏺ Coder
      Creating a query API module with GET/POST handlers.

    ⏺ Write(query_api.py)
      ⎿  Wrote 42 lines to query_api.py

    ✻ Coder thinking… (8s · ↑ 7.5k tokens · ctrl+c to interrupt)

The "✻" line is a transient spinner pinned below the transcript: it names the
agent currently working, and ticks elapsed time and the tokens spent in this
turn. Everything else is printed as permanent transcript lines above it.
"""
import re
import time
from typing import Any, Dict, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from rich._spinners import SPINNERS

from llm.tracker import token_tracker
from ui.banner import DIM, TEAL, VIOLET

# The pulsing star Claude Code uses for its "working" line.
SPINNERS.setdefault("sai_star", {"interval": 120, "frames": ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"]})

BULLET = "⏺"
ELBOW = "⎿"
PREVIEW_LINES = 3

# Tool registry names -> short, Claude-Code-like display names.
TOOL_LABELS = {
    "write_file": "Write",
    "read_file": "Read",
    "patch_file": "Update",
    "list_directory": "List",
    "execute_command": "Bash",
    "run_python_script": "Python",
    "web_search": "WebSearch",
    "grep_ast": "Search",
    "codebase_search": "Search",
    "git_operation": "Git",
    "memory_operation": "Memory",
    "math_solve": "Math",
    "edit_file": "Update",
    "glob": "Glob",
    "grep": "Grep",
    "web_fetch": "WebFetch",
    "bash_output": "BashOutput",
    "kill_shell": "KillShell",
    "todo_write": "Todos",
}


def format_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def tool_call_label(tool: str, args: Optional[Dict[str, Any]]) -> str:
    """'write_file', {'path': 'a.py'} -> 'Write(a.py)'."""
    args = args or {}
    name = TOOL_LABELS.get(tool, tool or "?")
    target = next(
        (str(args[k]) for k in ("path", "script_path", "command", "query", "pattern", "url", "shell_id", "action", "operation") if args.get(k)),
        "",
    )
    if len(target) > 80:
        target = target[:77] + "…"
    return f"{name}({target})" if target else name


def tool_result_summary(tool: str, args: Optional[Dict[str, Any]], success: bool, output: str) -> str:
    """One short line (or a few preview lines) describing what a tool call produced."""
    args = args or {}
    output = (output or "").strip()

    if success and tool == "write_file":
        return f"Wrote {args.get('content_lines', '?')} lines to {args.get('path', 'file')}"
    if success and tool in ("patch_file", "edit_file"):
        return f"Updated {args.get('path', 'file')}"
    if success and tool == "todo_write":
        return output  # the rendered checklist, shown in full
    if success and tool in ("glob", "grep") and not output.startswith("No "):
        n = len(output.splitlines())
        return f"Found {n} {'match' if n == 1 else 'matches'}" if tool == "grep" else f"Found {n} file{'s' if n != 1 else ''}"
    if success and tool == "read_file":
        return f"Read {len(output.splitlines())} lines"
    if not output:
        return "Done" if success else "Failed"

    lines = output.splitlines()
    shown = [ln[:120] for ln in lines[:PREVIEW_LINES]]
    if len(lines) > PREVIEW_LINES:
        shown.append(f"… +{len(lines) - PREVIEW_LINES} lines")
    return "\n".join(shown)


class ActivityPrinter:
    """Prints permanent transcript lines in the ⏺ / ⎿ style."""

    def __init__(self, console: Console):
        self.console = console

    def _bulleted(self, bullet_style: str, head: Text, body=None) -> None:
        grid = Table.grid(padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column()
        grid.add_row(Text(BULLET, style=bullet_style), head)
        if body is not None and (body.markup.strip() if isinstance(body, Markdown) else body.plain.strip()):
            grid.add_row("", body)
        self.console.print(grid)

    def agent_message(self, agent: str, text: str) -> None:
        self.console.print()
        # Rendered as Markdown (code blocks, lists, bold) like Claude Code's replies.
        self._bulleted(VIOLET, Text(agent, style=f"bold {VIOLET}"), Markdown(text or ""))

    def tool_call(self, label: str) -> None:
        self.console.print()
        self._bulleted(TEAL, Text(label, style="bold"))

    def _elbow(self, body: Text) -> None:
        # Grid, not a plain print, so wrapped lines stay indented under the text.
        grid = Table.grid(padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column()
        grid.add_row(Text(f"  {ELBOW} ", style=DIM), body)
        self.console.print(grid)

    def tool_result(self, summary: str, ok: bool = True) -> None:
        self._elbow(Text(summary, style=DIM if ok else "red"))

    DIFF_MAX_LINES = 60

    def diff(self, unified: str) -> None:
        """A unified diff under a ⎿, colored like Claude Code's edit previews."""
        body = Text()
        lines = [ln for ln in unified.splitlines() if not ln.startswith(("--- ", "+++ "))]
        added = sum(1 for ln in lines if ln.startswith("+"))
        removed = sum(1 for ln in lines if ln.startswith("-"))
        body.append(f"+{added} -{removed} lines\n", style=DIM)
        for ln in lines[:self.DIFF_MAX_LINES]:
            if ln.startswith("+"):
                body.append(ln + "\n", style="green")
            elif ln.startswith("-"):
                body.append(ln + "\n", style="red")
            elif ln.startswith("@@"):
                body.append(ln + "\n", style=f"{DIM} italic")
            else:
                body.append(ln + "\n", style=DIM)
        if len(lines) > self.DIFF_MAX_LINES:
            body.append(f"… +{len(lines) - self.DIFF_MAX_LINES} more diff lines", style=DIM)
        self._elbow(body)

    def note(self, markup: str) -> None:
        """A ⎿ line built from Rich markup (caller escapes any untrusted parts)."""
        self._elbow(Text.from_markup(markup))


_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def partial_json_string(text: str, key: str) -> Optional[str]:
    """
    Best-effort value of the string field `key` from a JSON object that may
    still be streaming in -- e.g. '{"summary": "Reading app.py to fi' ->
    'Reading app.py to fi'. None until the field has started.
    """
    marker = re.search(r'"%s"\s*:\s*"' % re.escape(key), text or "")
    if not marker:
        return None
    out = []
    i = marker.end()
    while i < len(text):
        ch = text[i]
        if ch == '"':
            break
        if ch == "\\":
            if i + 1 >= len(text):
                break  # escape sequence not fully streamed yet
            nxt = text[i + 1]
            if nxt == "u":
                hex_digits = text[i + 2:i + 6]
                if len(hex_digits) < 4:
                    break
                try:
                    out.append(chr(int(hex_digits, 16)))
                except ValueError:
                    pass
                i += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class _SpinnerLine:
    """Renderable re-evaluated on every Live refresh, so the clock and token
    count tick even while the main thread is blocked on an LLM call."""

    PREVIEW_MAX_LINES = 12

    def __init__(self, label: str):
        self.label = label
        self.started = time.time()
        self.tokens_at_start = token_tracker.input_tokens + token_tracker.output_tokens
        self.spinner = Spinner("sai_star", style=f"bold {VIOLET}")
        self.preview_title: Optional[str] = None
        self.preview_text = ""
        self.streamed_chars = 0

    def __rich__(self):
        secs = int(time.time() - self.started)
        spent = token_tracker.input_tokens + token_tracker.output_tokens - self.tokens_at_start
        stats = f"{secs}s"
        if spent:
            stats += f" · ↑ {format_tokens(spent)} tokens"
        if self.streamed_chars:
            # Live estimate while a response streams; real counts land in ↑ when it ends.
            stats += f" · ↓ {format_tokens(self.streamed_chars // 4)} tokens"
        self.spinner.update(
            text=Text.from_markup(
                f"[bold {VIOLET}]{escape(self.label)}…[/bold {VIOLET}] "
                f"[{DIM}]({stats} · ctrl+c to interrupt)[/{DIM}]"
            )
        )
        parts = []
        if self.preview_title and self.preview_text.strip():
            lines = self.preview_text.splitlines()[-self.PREVIEW_MAX_LINES:]
            grid = Table.grid(padding=(0, 1))
            grid.add_column(no_wrap=True)
            grid.add_column()
            grid.add_row(Text(BULLET, style=VIOLET), Text(self.preview_title, style=f"bold {VIOLET}"))
            grid.add_row("", Text("\n".join(lines)))
            parts += [Text(""), grid]
        return Group(*parts, Text(""), self.spinner)


class ActivityIndicator:
    """
    The transient '✻ <who> <doing>…' line. Rich's Live redraws it from its own
    thread and keeps it below anything printed with console.print().
    """

    def __init__(self, console: Console):
        self.console = console
        self._live: Optional[Live] = None
        self._line: Optional[_SpinnerLine] = None

    @property
    def active(self) -> bool:
        return self._live is not None

    def show(self, label: str) -> None:
        if self._live is None:
            self._line = _SpinnerLine(label)
            self._live = Live(self._line, console=self.console, refresh_per_second=10, transient=True)
            self._live.start()
        else:
            # Keep the turn's clock/token baseline; just change who/what.
            self._line.label = label

    def stream_preview(self, title: str, text: str, streamed_chars: int) -> None:
        """Show text that is still being generated above the spinner (not yet in the transcript)."""
        if self._line is not None:
            self._line.preview_title = title
            self._line.preview_text = text
            self._line.streamed_chars = streamed_chars

    def clear_preview(self) -> None:
        if self._line is not None:
            self._line.preview_title = None
            self._line.preview_text = ""
            self._line.streamed_chars = 0

    def hide(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
            self._line = None

    def paused(self):
        """Context manager: hide the spinner (e.g. to read a y/N answer), then restore it."""
        indicator = self

        class _Paused:
            def __enter__(self_inner):
                self_inner.label = indicator._line.label if indicator._line else None
                self_inner.started = indicator._line.started if indicator._line else None
                self_inner.baseline = indicator._line.tokens_at_start if indicator._line else None
                indicator.hide()

            def __exit__(self_inner, *exc):
                if self_inner.label is not None:
                    indicator.show(self_inner.label)
                    indicator._line.started = self_inner.started
                    indicator._line.tokens_at_start = self_inner.baseline
                return False

        return _Paused()
