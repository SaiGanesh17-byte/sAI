"""
Console-script entry point for the `sai` command (see [project.scripts] in
pyproject.toml). Thin dispatch layer only -- all real behavior lives in
app/main.py::main(), which this module drives by setting sys.argv the way a
user invoking `python3 app/main.py <flags>` would.
"""
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import typer

app = typer.Typer(
    name="sai",
    help="sAI: a modular, event-driven multi-agent AI Operating System.",
    add_completion=False,
    invoke_without_command=True,
)


def _read_piped_stdin() -> Optional[str]:
    """
    Only read stdin when something was actually piped or redirected in
    (`cat log | sai -p ...`, `sai -p ... < file`). Under cron, CI, or a parent
    process that leaves stdin open as a socket, a blind read() never returns.
    """
    import os
    import stat

    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (OSError, ValueError):
        return None
    if stat.S_ISFIFO(mode) or stat.S_ISREG(mode):
        return sys.stdin.read()
    return None


def _run_main(argv: list[str]) -> None:
    from app.main import main as app_main

    sys.argv = ["sai", *argv]
    app_main()


@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    continue_: bool = typer.Option(False, "--continue", "-c", help="Continue the most recent session in this folder."),
    resume: bool = typer.Option(False, "--resume", "-r", help="Pick an earlier session in this folder to resume."),
    print_: Optional[str] = typer.Option(None, "--print", "-p", help="Run one request non-interactively and print the result."),
    output_format: str = typer.Option("text", "--output-format", help="With -p: 'text' or 'json'."),
    accept_edits: bool = typer.Option(False, "--accept-edits", help="With -p: apply file edits without asking."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="With -p: no progress on stderr."),
):
    """With no subcommand, drops straight into the terminal REPL (same as `sai repl`)."""
    if ctx.invoked_subcommand is not None:
        return
    if print_ is not None:
        from app.headless import run_headless

        stdin_text = _read_piped_stdin()
        raise typer.Exit(run_headless(print_, accept_edits=accept_edits, output_format=output_format,
                                      stdin_text=stdin_text, quiet=quiet))

    from app.repl import SaiRepl

    SaiRepl(resume="continue" if continue_ else "pick" if resume else None).run()


@app.command()
def web(port: int = typer.Option(None, help="Port to bind the web UI to.")):
    """Start the sAI web UI."""
    argv = []
    if port is not None:
        argv += ["--port", str(port)]
    _run_main(argv)


@app.command()
def tui():
    """Start the sAI terminal UI (Textual)."""
    _run_main(["--tui"])


@app.command()
def repl():
    """Start the sAI interactive CLI REPL."""
    _run_main(["--cli"])


@app.command()
def run(goal: str = typer.Argument(..., help="Task goal for the multi-agent orchestrator to execute.")):
    """Run a single multi-agent task from the command line and exit."""
    _run_main(["--run", goal])


if __name__ == "__main__":
    app()
