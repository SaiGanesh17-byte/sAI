"""
Custom slash commands, like Claude Code's .claude/commands:

    <workspace>/.sai/commands/review.md   -> /review   (project; shared via git)
    ~/.sai/commands/standup.md            -> /standup  (yours, in every project)

The file's text becomes the request. `$ARGUMENTS` is replaced by whatever
follows the command (`/review auth.py` -> "auth.py"); without the
placeholder, arguments are appended. Optional front matter:

    ---
    description: Review a file for bugs
    ---
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

USER_COMMANDS_DIR = Path.home() / ".sai" / "commands"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass
class CustomCommand:
    name: str
    description: str
    body: str
    path: Path

    def render(self, arguments: str) -> str:
        arguments = arguments.strip()
        if "$ARGUMENTS" in self.body:
            return self.body.replace("$ARGUMENTS", arguments)
        return f"{self.body}\n\n{arguments}".strip() if arguments else self.body


def _parse(path: Path) -> CustomCommand:
    text = path.read_text(encoding="utf-8", errors="ignore")
    description = ""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "description":
                description = value.strip()
        text = text[match.end():]
    body = text.strip()
    if not description:
        description = (body.splitlines()[0] if body else "")[:60]
    return CustomCommand(name="/" + path.stem.lower(), description=description, body=body, path=path)


def load_custom_commands(workspace: Path, user_dir: Path = None) -> Dict[str, CustomCommand]:
    """Project commands override personal ones with the same name."""
    commands: Dict[str, CustomCommand] = {}
    for directory in (user_dir or USER_COMMANDS_DIR, Path(workspace) / ".sai" / "commands"):
        if not directory.is_dir():
            continue
        for f in sorted(directory.glob("*.md")):
            if _NAME.match(f.stem.lower()):
                try:
                    commands["/" + f.stem.lower()] = _parse(f)
                except OSError:
                    continue
    return commands
