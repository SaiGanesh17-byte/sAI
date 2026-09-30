"""
sAI terminal branding: startup banner + shared color palette for app/repl.py.

Deliberately distinct from Claude Code's orange/rust accent -- sAI uses a
teal/violet pair -- and uses a plain unicode glyph as a logo mark rather than
an image, since terminals can't reliably render one.
"""
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

TEAL = "#2DD4BF"
VIOLET = "#A78BFA"
DIM = "grey62"
ICON = "◆"


def render_banner(console: Console, cwd: Path, provider: str, coder_model: str, reasoner_model: str, agent_count: int, instructions: str = "") -> None:
    header = Text()
    header.append(f"{ICON} ", style=f"bold {VIOLET}")
    header.append("sAI", style=f"bold {TEAL}")
    header.append("  ·  Multi-Agent AI Operating System", style=DIM)

    body = Text()
    body.append("\nHey, I'm sAI. Tell me what to build, fix, or explain.\n\n", style="bold")

    body.append("workspace ", style=DIM)
    body.append(f"{cwd}\n")
    body.append("rules     ", style=DIM)
    if instructions:
        body.append(f"{instructions}\n")
    else:
        body.append("no SAI.md yet -- run /init to create one\n", style=DIM)
    body.append("provider  ", style=DIM)
    body.append(f"{provider}\n")
    body.append("models    ", style=DIM)
    body.append(f"{coder_model} (code) · {reasoner_model} (reason)\n")
    body.append("agents    ", style=DIM)
    body.append(f"{agent_count} specialists loaded\n\n")

    body.append("Tips\n", style=f"bold {VIOLET}")
    body.append("  Just type a request, e.g. ", style=DIM)
    body.append('"fix the failing test in tools/math.py"\n', style="italic")
    body.append("  !<command>", style=f"bold {TEAL}")
    body.append("   run a shell command directly\n", style=DIM)
    body.append("  /agents", style=f"bold {TEAL}")
    body.append("      list all specialist agents\n", style=DIM)
    body.append("  /tokens", style=f"bold {TEAL}")
    body.append("      show session token usage (Jev's savings, made visible)\n", style=DIM)
    body.append("  /help", style=f"bold {TEAL}")
    body.append("        show all commands\n", style=DIM)
    body.append("  /clear", style=f"bold {TEAL}")
    body.append("       reset this session's memory\n", style=DIM)
    body.append("  exit", style=f"bold {TEAL}")
    body.append("         leave sAI", style=DIM)

    console.print(
        Panel(
            Text.assemble(header, "\n", body),
            border_style=TEAL,
            padding=(1, 2),
            expand=False,
        )
    )
