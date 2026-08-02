import time
from dataclasses import dataclass, field
from typing import List, Dict, Any
from core.kernel import kernel
from core.events import event_bus, EventType
from execution.permissions import PermissionChecker, PermissionRequestRequired

@dataclass
class ToolResult:
    tool: str
    success: bool
    stdout: str
    stderr: str
    duration_ms: float = 0.0
    artifacts: List[str] = field(default_factory=list)

class ExecutionEngine:
    """
    Coordinates tool lookups from the registry and runs validation checks.
    """
    def __init__(self):
        pass

    def execute(self, action: Dict[str, Any]) -> ToolResult:
        tool_name = action.get("tool")
        args = action.get("args", {})
        
        if not tool_name:
            return ToolResult(tool="unknown", success=False, stdout="", stderr="Error: Tool name is missing in action.")

        event_bus.publish(
            EventType.TOOL_REQUEST,
            {"tool": tool_name, "args": args},
            source="ExecutionEngine"
        )

        try:
            tool_registry = kernel.get_service("tool_registry")
            tool = tool_registry.get_tool(tool_name)
        except KeyError as e:
            err_msg = str(e)
            event_bus.publish(EventType.ERROR, {"msg": err_msg}, source="ExecutionEngine")
            return ToolResult(tool=tool_name, success=False, stdout="", stderr=err_msg)

        path_arg = args.get("path") or args.get("script_path")
        if path_arg:
            if not PermissionChecker.validate_path(str(path_arg)):
                # Instead of returning a soft error, raise the interactive permission exception
                raise PermissionRequestRequired(
                    path=str(path_arg),
                    reason=f"Agent wants to execute '{tool_name}' tool on path outside sandbox."
                )

        event_bus.publish(
            EventType.TOOL_STARTED,
            {"tool": tool_name},
            source="ExecutionEngine"
        )

        start_time = time.time()
        try:
            stdout_content = tool.execute(args)
            duration_ms = (time.time() - start_time) * 1000
            
            success = not stdout_content.startswith("Error")
            stderr_content = "" if success else stdout_content

            result = ToolResult(
                tool=tool_name,
                success=success,
                stdout=stdout_content if success else "",
                stderr=stderr_content,
                duration_ms=duration_ms,
                artifacts=[str(path_arg)] if path_arg and success else []
            )

            event_bus.publish(
                EventType.TOOL_FINISHED,
                {"tool": tool_name, "success": success, "duration_ms": duration_ms},
                source="ExecutionEngine"
            )
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            err_msg = f"Error during tool execution: {e}"
            event_bus.publish(EventType.ERROR, {"msg": err_msg}, source="ExecutionEngine")
            return ToolResult(
                tool=tool_name,
                success=False,
                stdout="",
                stderr=err_msg,
                duration_ms=duration_ms
            )
