"""tool_schema: the full parameter schema of a tool whose prompt line only shows a
signature (MCP tools are listed compactly to keep prompts small)."""
import json
from typing import Any, Dict

from tools.base import BaseTool


class ToolSchemaTool(BaseTool):
    @property
    def name(self) -> str:
        return "tool_schema"

    @property
    def description(self) -> str:
        return "Returns the full description and JSON parameter schema of a tool (e.g. an mcp__ tool) before you call it."

    @property
    def permissions(self) -> list:
        return []

    @property
    def schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"tool": {"type": "string", "description": "Tool name."}}, "required": ["tool"]}

    def execute(self, args: Dict[str, Any]) -> str:
        from core.kernel import kernel
        name = str(args.get("tool", "")).strip()
        tools = kernel.get_service("tool_registry").list_tools()
        if name not in tools:
            return f"Error: no tool named '{name}'."
        details = tools[name]
        return f"{name}: {details.get('description', '')}\nSchema: {json.dumps(details.get('schema', {}), indent=1)}"
