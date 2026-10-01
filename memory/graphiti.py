"""
Persistent facts and decisions, one store per workspace:

    ~/sAI/.sai/projects/<workspace-slug>/memory.json

It used to be a single global file, so facts about one project (e.g. a React
Native app's fonts and visual style) were injected into every other project's
prompts -- an agent titled a calculator's README after the other app and
guessed the wrong GitHub repository from it.
"""
import json
import re
from pathlib import Path
from typing import Optional

PROJECTS_ROOT = Path(__file__).resolve().parent.parent / ".sai" / "projects"
LEGACY_FILE = Path(__file__).resolve().parent.parent / ".sai" / "memory.json"


def workspace_memory_file(workspace: Optional[Path] = None) -> Path:
    if workspace is None:
        from core.security import get_current_workspace
        workspace = get_current_workspace()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(Path(workspace).resolve())).strip("-") or "root"
    return PROJECTS_ROOT / slug / "memory.json"


class GraphitiMemory:
    def __init__(self, workspace: Optional[Path] = None):
        self.path = workspace_memory_file(workspace)
        self.facts = []
        self.decisions = []
        self.load()

    def load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.facts = data.get("facts", [])
                self.decisions = data.get("decisions", [])
        except Exception:
            pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "facts": self.facts,
                "decisions": self.decisions
            }
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def add_fact(self, fact: str):
        if fact not in self.facts:
            self.facts.append(fact)
            self.save()

    def add_decision(self, decision: str):
        if decision not in self.decisions:
            self.decisions.append(decision)
            self.save()

    def remove_fact(self, fact: str):
        if fact in self.facts:
            self.facts.remove(fact)
            self.save()

    def remove_decision(self, decision: str):
        if decision in self.decisions:
            self.decisions.remove(decision)
            self.save()

    def clear_all(self):
        self.facts = []
        self.decisions = []
        self.save()
