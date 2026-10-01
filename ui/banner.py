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
    tips = [
        ("@file", "attach a file to your request"),
        ("!<cmd>", "run a shell command yourself"),
        ("/init", "write SAI.md -- project rules every agent follows"),
        ("/compact", "summarize the conversation to free context"),
        ("/resume", "pick up an earlier session (or launch with sai -c)"),
        ("/help", "all commands · Esc stops a turn · \\⏎ new line"),
    ]
    for i, (cmd, desc) in enumerate(tips):
        body.append(f"  {cmd:<11}", style=f"bold {TEAL}")
        body.append(desc + ("\n" if i < len(tips) - 1 else ""), style=DIM)

    console.print(
        Panel(
            Text.assemble(header, "\n", body),
            border_style=TEAL,
            padding=(1, 2),
            expand=False,
        )
    )
