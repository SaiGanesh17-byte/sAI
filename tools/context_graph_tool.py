"""context_graph: query the code + work graph (definitions, references, imports,
importers, tests, recent edits/errors) for a file or symbol."""
from typing import Any, Dict

from tools.base import BaseTool


class ContextGraphTool(BaseTool):
    @property
    def name(self) -> str:
        return "context_graph"

    @property
    def description(self) -> str:
        return ("Looks up a file or symbol in the project's context graph: where a function/class is defined, "
                "every place it is referenced, what a file imports, what imports it, its tests, and recent "
                "edits/errors on it. Faster and more complete than grepping by hand.")

    @property
    def permissions(self) -> list:
        return ["read"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {"type": "object",
                "properties": {"query": {"type": "string", "description": "A file path (calc.py) or a symbol name (divide)."}},
                "required": ["query"]}

    def execute(self, args: Dict[str, Any]) -> str:
        from core.context_graph import graph_for
        query = str(args.get("query", "")).strip()
        if not query:
            return "Error: 'query' is required."
        return graph_for().query(query)
