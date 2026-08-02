from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class WorkingMemory:
    """
    Shared workspace for all agents.

    Every agent can read the entire memory but should only
    update the section it owns.
    """

    goal: str

    architecture: str = ""

    research: str = ""

    implementation: str = ""

    review: str = ""

    notes: List[str] = field(default_factory=list)

    artifacts: List[str] = field(default_factory=list)

    metadata: Dict[str, str] = field(default_factory=dict)

    def snapshot(self) -> str:
        """
        Returns a formatted snapshot of the current working memory.
        """
        notes_str = "\n".join(f" - {n}" for n in self.notes) if self.notes else "None"
        artifacts_str = "\n".join(f" - {a}" for a in self.artifacts) if self.artifacts else "None"
        
        return f"""
Goal:
{self.goal}

Notes & Guidelines:
{notes_str}

Active Artifacts:
{artifacts_str}

Architecture:
{self.architecture}

Research:
{self.research}

Implementation:
{self.implementation}

Review:
{self.review}
"""