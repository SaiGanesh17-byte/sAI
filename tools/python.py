import subprocess
from pathlib import Path
from typing import Dict, Any
from tools.base import BaseTool

WORKSPACE_ROOT = Path("/Users/saiganeshongolu/sAI").resolve()

class PythonTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_python_script"

    @property
    def description(self) -> str:
        return "Runs a python script using python3 inside the workspace sandbox."

    @property
    def permissions(self) -> list:
        return ["execute"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "script_path": {"type": "string", "description": "Relative path of python script file to execute."}
            },
            "required": ["script_path"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        script_path_str = args.get("script_path")
        if not script_path_str:
            return "Error: 'script_path' argument is required."

        target_path = Path(script_path_str)
        if not target_path.is_absolute():
            target_path = WORKSPACE_ROOT / target_path

        try:
            resolved_target = target_path.resolve()
            in_sandbox = resolved_target.parts[:len(WORKSPACE_ROOT.parts)] == WORKSPACE_ROOT.parts
        except Exception:
            in_sandbox = False

        if not in_sandbox:
            return f"Error: Path '{script_path_str}' is outside the workspace sandbox."

        if not target_path.exists():
            return f"Error: File '{script_path_str}' does not exist."

        from core.security import find_risky_pattern, is_script_approved
        from execution.permissions import PermissionRequestRequired

        try:
            script_content = target_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            script_content = ""

        matched_pattern = find_risky_pattern(script_content)
        if matched_pattern and not is_script_approved(target_path):
            # Absolute path: approvers resolve it against their own cwd, so a
            # relative path approved the wrong file whenever sAI was launched
            # from outside the workspace, re-prompting forever.
            raise PermissionRequestRequired(
                path=str(target_path.resolve()),
                reason=f"Script contains a potentially risky pattern: '{matched_pattern}'.",
                kind="script",
            )

        try:
            result = subprocess.run(
                ["python3", str(target_path)],
                cwd=str(WORKSPACE_ROOT),
                text=True,
                capture_output=True,
                timeout=15
            )
            output = ""
            if result.stdout:
                output += f"STDOUT:\n{result.stdout}\n"
            if result.stderr:
                output += f"STDERR:\n{result.stderr}\n"
            if not output:
                output = f"Python execution completed successfully with exit code {result.returncode}."
            return output
        except subprocess.TimeoutExpired:
            return "Error: Python execution timed out after 15 seconds."
        except Exception as e:
            return f"Error running python script: {e}"
