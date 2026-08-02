import subprocess
import threading
import queue
import time
import re
from pathlib import Path
from typing import Dict, Any, List
from tools.base import BaseTool
from core.security import get_current_workspace, validate_path

class AsyncProcessManager:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(AsyncProcessManager, cls).__new__(cls, *args, **kwargs)
            cls._instance.process = None
            cls._instance.output_queue = queue.Queue()
            cls._instance.history = []
            cls._instance.is_running = False
        return cls._instance

    def start_process(self, command: str, cwd: str):
        if self.is_running:
            self.terminate()
            
        self.is_running = True
        self.history = []
        self.output_queue = queue.Queue()
        
        def run_thread():
            try:
                import os
                from core.settings import load_settings
                settings = load_settings()
                run_cmd = command
                
                if settings.get("docker_sandbox", False):
                    try:
                        check = subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2.0)
                        if check.returncode == 0:
                            run_cmd = f'docker run --rm -v "{cwd}":/workspace -w /workspace alpine sh -c {repr(command)}'
                    except Exception:
                        pass
                
                self.process = subprocess.Popen(
                    run_cmd,
                    shell=True,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    preexec_fn=os.setsid
                )
                
                # Non-blocking line reading thread helper
                for line in iter(self.process.stdout.readline, ""):
                    if not self.is_running:
                        break
                    self.output_queue.put(line)
                    self.history.append(line)
                    
                self.process.stdout.close()
                self.process.wait()
            except Exception as e:
                err_msg = f"Process error: {e}\n"
                self.output_queue.put(err_msg)
                self.history.append(err_msg)
            finally:
                self.is_running = False
                self.output_queue.put("[PROCESS_FINISHED]\n")

        self.thread = threading.Thread(target=run_thread, daemon=True)
        self.thread.start()

    def write_stdin(self, text: str):
        if self.is_running and self.process and self.process.stdin:
            try:
                self.process.stdin.write(text + "\n")
                self.process.stdin.flush()
                return True
            except Exception:
                pass
        return False

    def terminate(self):
        self.is_running = False
        if self.process:
            import os
            import signal
            try:
                pgid = os.getpgid(self.process.pid)
                os.killpg(pgid, signal.SIGTERM)
                self.process.wait(timeout=1.5)
            except Exception:
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
            self.process = None

    def get_new_output(self) -> List[str]:
        lines = []
        while not self.output_queue.empty():
            try:
                lines.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return lines

async_process_manager = AsyncProcessManager()

class TerminalTool(BaseTool):
    @property
    def name(self) -> str:
        return "execute_command"

    @property
    def description(self) -> str:
        return "Executes shell command strictly inside the workspace."

    @property
    def permissions(self) -> list:
        return ["execute"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."}
            },
            "required": ["command"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        command = args.get("command")
        if not command:
            return "Error: 'command' argument is required."

        # Check for destructive/risky actions and prompt permission gate
        from core.security import consume_approved_command
        from execution.permissions import PermissionRequestRequired
        
        command_lower = command.strip().lower()
        destructive_keywords = ["rm ", "git push", "git clean", "npm publish", "docker run", "deploy", "delete"]
        if any(kw in command_lower for kw in destructive_keywords):
            if not consume_approved_command(command):
                raise PermissionRequestRequired(
                    path=command,
                    reason="Destructive command execution approval."
                )

        # Command Path Travel Sanitization Check
        paths = re.findall(r'(?:/[a-zA-Z0-9_\-\.]+)+|(?:\.\./)+[a-zA-Z0-9_\-\./]*', command)
        for p in paths:
            target_path = Path(p)
            if not target_path.is_absolute():
                target_path = get_current_workspace() / target_path
            
            if not validate_path(target_path):
                return f"Security Error: Command references path '{p}' outside sandbox constraints. Access denied."

        # Start process asynchronously
        active_cwd = str(get_current_workspace())
        async_process_manager.start_process(command, active_cwd)

        # For Agent reasoning loop context: wait for execution to finish (max 30s)
        timeout = 30
        start_time = time.time()
        while async_process_manager.is_running and (time.time() - start_time) < timeout:
            time.sleep(0.1)

        output = "".join(async_process_manager.history)
        if not output:
            output = "Command started and running in background."
        return output
