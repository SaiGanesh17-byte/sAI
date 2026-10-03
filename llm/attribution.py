"""
Which models actually answered. Every completed LLM call publishes LLM_RESPONSE with
the model that served it -- after any free-model fallback -- so a turn can end by
saying exactly which models produced it, instead of the user guessing from settings.
"""
from typing import List, Tuple

from core.events import event_bus, EventType


def short_model(model: str) -> str:
    """'nvidia/nemotron-3-super-120b-a12b:free' -> 'nemotron-3-super-120b-a12b (free)';
    'groq:openai/gpt-oss-120b' -> 'gpt-oss-120b (groq, free)'."""
    from llm.runtime import FREE_TIER_PREFIXES, split_model
    provider, rest = split_model(model, "")
    name = rest.split("/", 1)[-1]
    if provider in FREE_TIER_PREFIXES:
        return f"{name} ({provider}, free)"
    return name[:-len(":free")] + " (free)" if name.endswith(":free") else name


class ModelLog:
    def __init__(self):
        self.calls: List[Tuple[str, str]] = []  # (agent / task kind, model), in call order
        event_bus.subscribe(EventType.LLM_RESPONSE, self._on_response)

    def _on_response(self, event):
        self.calls.append((str(event.data.get("agent") or ""), str(event.data.get("model") or "?")))

    def mark(self) -> int:
        return len(self.calls)

    def since(self, mark: int) -> List[Tuple[str, str]]:
        return self.calls[mark:]

    @staticmethod
    def summarize(calls: List[Tuple[str, str]]) -> str:
        """'Jev → ling-3.0-flash-sante (free) · Coder → nemotron-3-super-120b-a12b (free) ×3'."""
        counts = {}
        for agent, model in calls:
            counts[(agent, model)] = counts.get((agent, model), 0) + 1
        parts = []
        for (agent, model), n in counts.items():
            label = f"{agent} → {short_model(model)}" if agent else short_model(model)
            parts.append(label + (f" ×{n}" if n > 1 else ""))
        return " · ".join(parts)
