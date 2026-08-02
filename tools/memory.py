from typing import Dict, Any
from tools.base import BaseTool
from memory.graphiti import GraphitiMemory

# Global graphiti instance for session persistence
_graphiti_instance = GraphitiMemory()

class MemoryTool(BaseTool):
    @property
    def name(self) -> str:
        return "memory_operation"

    @property
    def description(self) -> str:
        return "Queries or updates persistent graph memory (facts, decisions, project notes)."

    @property
    def permissions(self) -> list:
        return ["memory"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["query", "add_fact", "add_decision"],
                    "description": "Action to perform: query current graphs, add a new fact, or log a design decision."
                },
                "text": {
                    "type": "string",
                    "description": "The fact, decision, or query string."
                }
            },
            "required": ["action"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        action = args.get("action")
        text = args.get("text", "")
        
        if action == "query":
            facts = "\n".join(f" • {f}" for f in _graphiti_instance.facts)
            decisions = "\n".join(f" • {d}" for d in _graphiti_instance.decisions)
            
            output = []
            output.append("=== Persistent Memory Snapshot ===")
            output.append("Facts Learned:")
            output.append(facts if facts else " • None")
            output.append("Design Decisions:")
            output.append(decisions if decisions else " • None")
            return "\n".join(output)
            
        elif action == "add_fact":
            if not text:
                return "Error: 'text' argument is required to add a fact."
            _graphiti_instance.add_fact(text)
            return f"Success: Fact registered: '{text}'"
            
        elif action == "add_decision":
            if not text:
                return "Error: 'text' argument is required to add a decision."
            _graphiti_instance.add_decision(text)
            return f"Success: Decision logged: '{text}'"
            
        else:
            return f"Error: Unsupported action '{action}'."
