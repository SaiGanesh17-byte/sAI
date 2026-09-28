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

# Output preview carried on TOOL_FINISHED events, for UIs to summarize.
OUTPUT_PREVIEW_CHARS = 2000

# Args worth showing in a UI next to the tool name. Bulk payloads (file
# content, patches) are reduced to a line count instead of being copied
# into every event.
_DISPLAY_ARG_KEYS = ("path", "script_path", "command", "query", "pattern", "action", "expression", "operation")


def _display_args(args: Dict[str, Any]) -> Dict[str, Any]:
    shown = {k: args[k] for k in _DISPLAY_ARG_KEYS if args.get(k)}
    for bulk in ("content", "patch"):
        if isinstance(args.get(bulk), str):
            shown[f"{bulk}_lines"] = args[bulk].count("\n") + 1
    return shown


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
            # Resolve relative paths against the workspace, exactly as the tools
            # themselves do. Validating the raw string resolved it against the
            # process cwd instead, so launching `sai` from ~ flagged every
            # relative write (e.g. "query_api.py") as "outside sandbox".
            from pathlib import Path
            from core.security import get_current_workspace
            target = Path(str(path_arg)).expanduser()
            if not target.is_absolute():
                target = get_current_workspace() / target
            if not PermissionChecker.validate_path(str(target)):
                # Instead of returning a soft error, raise the interactive permission exception
                raise PermissionRequestRequired(
                    path=str(target.resolve()),
                    reason=f"Agent wants to execute '{tool_name}' tool on path outside sandbox."
                )

        event_bus.publish(
            EventType.TOOL_STARTED,
            {"tool": tool_name, "args": _display_args(args)},
            source="ExecutionEngine"
        )

        start_time = time.time()
        try:
            from core.security import mask_secrets
            stdout_content = mask_secrets(tool.execute(args))
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
                {
                    "tool": tool_name,
                    "success": success,
                    "duration_ms": duration_ms,
                    "output": stdout_content[:OUTPUT_PREVIEW_CHARS],
                },
                source="ExecutionEngine"
            )
            return result

        except PermissionRequestRequired:
            # Let interactive permission prompts propagate to the caller instead of
            # being swallowed into a generic failed ToolResult below.
            raise
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            err_msg = f"Error during tool execution: {e}"
            event_bus.publish(EventType.ERROR, {"msg": err_msg}, source="ExecutionEngine")
            # Close out the TOOL_STARTED above so UIs don't leave it hanging.
            event_bus.publish(
                EventType.TOOL_FINISHED,
                {"tool": tool_name, "success": False, "duration_ms": duration_ms, "output": err_msg},
                source="ExecutionEngine"
            )
            return ToolResult(
                tool=tool_name,
                success=False,
                stdout="",
                stderr=err_msg,
                duration_ms=duration_ms
            )
