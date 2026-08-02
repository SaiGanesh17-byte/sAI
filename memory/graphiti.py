import json
from pathlib import Path

MEMORY_FILE = Path("/Users/saiganeshongolu/sAI/.sai/memory.json")

class GraphitiMemory:
    def __init__(self):
        self.facts = []
        self.decisions = []
        self.load()

    def load(self):
        try:
            if MEMORY_FILE.exists():
                data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
                self.facts = data.get("facts", [])
                self.decisions = data.get("decisions", [])
        except Exception:
            pass

    def save(self):
        try:
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "facts": self.facts,
                "decisions": self.decisions
            }
            MEMORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
