import os
import subprocess
from pathlib import Path
from typing import Dict, Any
from core.security import validate_path, mask_secrets

WORKSPACE_ROOT = Path("/Users/saiganeshongolu/sAI").resolve()

def read_file(path_str: str) -> str:
    """Reads the contents of a local file safely within the sandbox."""
    # Resolve relative path to workspace root if needed
    target_path = Path(path_str)
    if not target_path.is_absolute():
        target_path = WORKSPACE_ROOT / target_path
    
    if not validate_path(target_path):
        return f"Error: Access denied. Path '{path_str}' is outside the workspace sandbox."
    
    if not target_path.exists():
        return f"Error: File '{path_str}' does not exist."
        
    try:
        content = target_path.read_text(encoding="utf-8")
        return content
    except Exception as e:
        return f"Error reading file '{path_str}': {e}"

def write_file(path_str: str, content: str) -> str:
    """Writes content to a file safely within the sandbox. Creates folders if needed."""
    target_path = Path(path_str)
    if not target_path.is_absolute():
        target_path = WORKSPACE_ROOT / target_path
        
    if not validate_path(target_path):
        return f"Error: Access denied. Path '{path_str}' is outside the workspace sandbox."
        
    try:
        # Ensure parent directories exist
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        return f"Success: Wrote file '{path_str}'."
    except Exception as e:
        return f"Error writing file '{path_str}': {e}"

def execute_command(command: str) -> str:
    """Runs a shell command asynchronously inside the workspace and returns output."""
    # We validate command parameter sanity and run in the workspace root
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(WORKSPACE_ROOT),
            text=True,
            capture_output=True,
            timeout=20 # 20 second safety guard timeout
        )
        output = ""
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"
        if not output:
            output = f"Command executed successfully (Exit Code {result.returncode})."
        return mask_secrets(output)
    except subprocess.TimeoutExpired:
        return "Error: Command execution timed out (limit: 20 seconds)."
    except Exception as e:
        return f"Error executing command '{command}': {e}"

def execute_tool(action: Dict[str, Any]) -> str:
    """
    Main dispatcher which parses action details and runs the corresponding tool.
    """
    tool_name = action.get("tool")
    args = action.get("args", {})
    
    if not tool_name:
        return "Error: Tool name is missing from action."
        
    if tool_name == "read_file":
        path = args.get("path")
        if not path:
            return "Error: 'path' argument is required for read_file."
        return read_file(path)
        
    elif tool_name == "write_file":
        path = args.get("path")
        content = args.get("content", "")
        if not path:
            return "Error: 'path' argument is required for write_file."
        return write_file(path, content)
        
    elif tool_name == "execute_command":
        command = args.get("command")
        if not command:
            return "Error: 'command' argument is required for execute_command."
        return execute_command(command)
        
    else:
        return f"Error: Tool '{tool_name}' is not supported."
