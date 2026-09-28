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
import time
from typing import Any, Dict, Optional

from rich.console import Console, Group
from rich.live import Live
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
}


def format_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def tool_call_label(tool: str, args: Optional[Dict[str, Any]]) -> str:
    """'write_file', {'path': 'a.py'} -> 'Write(a.py)'."""
    args = args or {}
    name = TOOL_LABELS.get(tool, tool or "?")
    target = next(
        (str(args[k]) for k in ("path", "script_path", "command", "query", "pattern", "action", "operation") if args.get(k)),
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
    if success and tool == "patch_file":
        return f"Updated {args.get('path', 'file')}"
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

    def _bulleted(self, bullet_style: str, head: Text, body: Optional[Text] = None) -> None:
        grid = Table.grid(padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column()
        grid.add_row(Text(BULLET, style=bullet_style), head)
        if body is not None and body.plain.strip():
            grid.add_row("", body)
        self.console.print(grid)

    def agent_message(self, agent: str, text: str) -> None:
        self.console.print()
        self._bulleted(VIOLET, Text(agent, style=f"bold {VIOLET}"), Text(text))

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

    def note(self, markup: str) -> None:
        """A ⎿ line built from Rich markup (caller escapes any untrusted parts)."""
        self._elbow(Text.from_markup(markup))


class _SpinnerLine:
    """Renderable re-evaluated on every Live refresh, so the clock and token
    count tick even while the main thread is blocked on an LLM call."""

    def __init__(self, label: str):
        self.label = label
        self.started = time.time()
        self.tokens_at_start = token_tracker.input_tokens + token_tracker.output_tokens
        self.spinner = Spinner("sai_star", style=f"bold {VIOLET}")

    def __rich__(self):
        secs = int(time.time() - self.started)
        spent = token_tracker.input_tokens + token_tracker.output_tokens - self.tokens_at_start
        stats = f"{secs}s"
        if spent:
            stats += f" · ↑ {format_tokens(spent)} tokens"
        self.spinner.update(
            text=Text.from_markup(
                f"[bold {VIOLET}]{escape(self.label)}…[/bold {VIOLET}] "
                f"[{DIM}]({stats} · ctrl+c to interrupt)[/{DIM}]"
            )
        )
        return Group(Text(""), self.spinner)


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
