from typing import Dict, Any
from tools.base import BaseTool

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool:
        tool = self._tools.get(name)
        if not tool:
            raise KeyError(f"Tool '{name}' is not registered in ToolRegistry.")
        return tool

    def list_tools(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "description": tool.description,
                "schema": tool.schema,
                "permissions": tool.permissions
            }
            for name, tool in self._tools.items()
        }
