import atexit
import subprocess
import threading
import queue
import time
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from tools.base import BaseTool
from core.security import get_current_workspace, validate_path

def wrap_for_sandbox(command: str, cwd: str, settings: dict) -> str:
    """Runs the command inside docker when docker_sandbox is on and docker is available."""
    if not settings.get("docker_sandbox", False):
        return command
    try:
        check = subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2.0)
        if check.returncode == 0:
            # shlex.quote, not repr(): repr() can emit a double-quoted
            # string, which the *host* shell expands ($(...), $VAR)
            # before docker ever runs -- escaping the sandbox.
            import shlex
            return (
                f"docker run --rm -v {shlex.quote(cwd + ':/workspace')} -w /workspace "
                f"alpine sh -c {shlex.quote(command)}"
            )
    except Exception:
        pass
    return command


_INTERPRETERS = {"python", "python3", "bash", "sh", "zsh", "node", "ruby", "perl", "php", "deno", "bun", "tsx", "ts-node"}


def _scripts_in(command: str) -> List[Path]:
    """Workspace files a command would execute: `python3 x.py`, `bash -e x.sh`, `./x`."""
    import shlex
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    found = []
    expect_script = False
    for tok in tokens:
        if tok in ("&&", "||", ";", "|"):
            expect_script = False
            continue
        if expect_script:
            if tok.startswith("-"):
                if tok in ("-c", "-e", "-m"):
                    expect_script = False  # inline code/module: the command text itself is scanned
                continue
            found.append(tok)
            expect_script = False
            continue
        if Path(tok).name in _INTERPRETERS:
            expect_script = True
        elif tok.startswith("./"):
            found.append(tok)
    paths = []
    for raw in found:
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else get_current_workspace() / p
        if p.is_file():
            paths.append(p.resolve())
    return paths


def _check_scripts_run_by(command: str) -> None:
    """
    The command text can be harmless while the script it runs is not: with `rm`
    gated, an agent wrote delete_files.py and ran `python3 delete_files.py`,
    wiping the workspace without a single prompt. Scripts are scanned like
    run_python_script's, and approval is tied to their exact content.
    """
    from core.security import find_risky_pattern, is_script_approved
    from execution.permissions import PermissionRequestRequired

    for script in _scripts_in(command):
        try:
            content = script.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        pattern = find_risky_pattern(content)
        if pattern and not is_script_approved(script):
            raise PermissionRequestRequired(
                path=str(script),
                reason=f"`{command[:80]}` runs {script.name}, which contains '{pattern}'.",
                kind="script",
            )


def check_command(command: str) -> Optional[str]:
    """
    Shared safety gate for foreground and background commands. Raises
    PermissionRequestRequired for risky commands that aren't approved or
    allowed by a rule; returns an error string for sandbox escapes; None if OK.
    """
    from core.security import consume_approved_command, find_risky_pattern, is_command_allowed_by_rule
    from execution.permissions import PermissionRequestRequired

    _check_scripts_run_by(command)

    matched_pattern = find_risky_pattern(command)
    if matched_pattern and not is_command_allowed_by_rule(command):
        if not consume_approved_command(command):
            raise PermissionRequestRequired(
                path=command,
                reason=f"Destructive command execution approval (matched pattern: '{matched_pattern}').",
                kind="command",
            )

    # Command Path Travel Sanitization Check. Still a heuristic (e.g. it can't
    # see paths built at runtime) -- docker_sandbox is the real boundary.
    # Home-directory references never resolve inside the workspace; the
    # path regex below misses bare "~" / "$HOME" (e.g. "cd ~ && cat .zshrc").
    if re.search(r'(^|[\s=:;&|(`])~|\$\{?HOME\b', command):
        return "Security Error: Command references the home directory outside sandbox constraints. Access denied."
    # URLs contain "/segment" runs that aren't filesystem paths.
    path_scan = re.sub(r'[a-zA-Z][a-zA-Z0-9+.\-]*://\S+', ' ', command)
    paths = re.findall(r'(?:/[a-zA-Z0-9_\-\.]+)+|(?:\.\./)+[a-zA-Z0-9_\-\./]*', path_scan)
    # Bare "/" and ".." tokens (e.g. "cd .. && ls", "ls /") slip past the regex above.
    paths += re.findall(r'(?:^|(?<=[\s;&|(]))(\.\.|/)(?=$|[\s;&|)])', path_scan)
    for p in paths:
        target_path = Path(p)
        if not target_path.is_absolute():
            target_path = get_current_workspace() / target_path
        if not validate_path(target_path):
            return f"Security Error: Command references path '{p}' outside sandbox constraints. Access denied."
    return None


class BackgroundShells:
    """Long-running commands (dev servers, watchers, slow test suites) the agent
    starts, then polls with bash_output and stops with kill_shell."""

    MAX_BUFFER_LINES = 5000

    def __init__(self):
        self._shells: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def start(self, command: str, cwd: str) -> str:
        import os
        from core.settings import load_settings

        with self._lock:
            self._counter += 1
            shell_id = f"bg{self._counter}"
        proc = subprocess.Popen(
            wrap_for_sandbox(command, cwd, load_settings()), shell=True, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, bufsize=1, preexec_fn=os.setsid,
        )
        entry = {"command": command, "proc": proc, "lines": [], "read_upto": 0}

        def pump():
            for line in iter(proc.stdout.readline, ""):
                with self._lock:
                    entry["lines"].append(line)
                    if len(entry["lines"]) > self.MAX_BUFFER_LINES:
                        drop = len(entry["lines"]) - self.MAX_BUFFER_LINES
                        del entry["lines"][:drop]
                        entry["read_upto"] = max(0, entry["read_upto"] - drop)
            proc.stdout.close()

        threading.Thread(target=pump, daemon=True).start()
        with self._lock:
            self._shells[shell_id] = entry
        return shell_id

    def read_new(self, shell_id: str) -> Optional[tuple]:
        """(new_output, status) since the last read, or None for an unknown id."""
        with self._lock:
            entry = self._shells.get(shell_id)
            if entry is None:
                return None
            new = "".join(entry["lines"][entry["read_upto"]:])
            entry["read_upto"] = len(entry["lines"])
        code = entry["proc"].poll()
        status = "running" if code is None else f"exited with code {code}"
        return new, status

    def kill(self, shell_id: str) -> bool:
        import os
        import signal
        with self._lock:
            entry = self._shells.get(shell_id)
        if entry is None:
            return False
        if entry["proc"].poll() is None:
            try:
                os.killpg(os.getpgid(entry["proc"].pid), signal.SIGTERM)
                entry["proc"].wait(timeout=2)
            except Exception:
                try:
                    os.killpg(os.getpgid(entry["proc"].pid), signal.SIGKILL)
                except Exception:
                    pass
        return True

    def kill_all(self):
        for shell_id in list(self._shells):
            self.kill(shell_id)


background_shells = BackgroundShells()
# Don't leave dev servers running after sAI exits.
atexit.register(background_shells.kill_all)


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
        self.returncode = None
        self.output_queue = queue.Queue()
        
        def run_thread():
            try:
                import os
                from core.settings import load_settings
                settings = load_settings()
                run_cmd = wrap_for_sandbox(command, cwd, settings)
                
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
                self.returncode = self.process.wait()
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
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout": {"type": "integer", "description": "Seconds to wait for it to finish (default 30, max 600)."},
                "run_in_background": {"type": "boolean", "description": "Start it and return immediately (dev servers, watchers); read output later with bash_output."},
            },
            "required": ["command"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        command = args.get("command")
        if not command:
            return "Error: 'command' argument is required."

        error = check_command(command)
        if error:
            return error

        active_cwd = str(get_current_workspace())
        if args.get("run_in_background"):
            shell_id = background_shells.start(command, active_cwd)
            return (f"Started in background as shell '{shell_id}'. Use bash_output with shell_id "
                    f"'{shell_id}' to read its output, and kill_shell to stop it.")

        # Start process asynchronously
        async_process_manager.start_process(command, active_cwd)

        # For Agent reasoning loop context: wait for execution to finish.
        try:
            timeout = max(1, min(int(args.get("timeout") or 30), 600))
        except (TypeError, ValueError):
            timeout = 30
        start_time = time.time()
        while async_process_manager.is_running and (time.time() - start_time) < timeout:
            time.sleep(0.1)

        output = "".join(async_process_manager.history)
        if async_process_manager.is_running:
            output += (f"\n[still running after {timeout}s -- it keeps running; for long commands pass a larger "
                       f"'timeout' or use run_in_background]")
            return output
        code = getattr(async_process_manager, "returncode", None)
        if code not in (0, None):
            # Without the exit status, "No module named pytest" looked like a successful
            # run and an agent reported "all 7 tests passed".
            return f"Error: command exited with code {code}\n{output or '(no output)'}"
        return output or "Command completed with no output (exit code 0)."


class BashOutputTool(BaseTool):
    @property
    def name(self) -> str:
        return "bash_output"

    @property
    def description(self) -> str:
        return "Returns new output (since the last check) and status of a background shell started with run_in_background."

    @property
    def permissions(self) -> list:
        return ["execute"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"shell_id": {"type": "string"}}, "required": ["shell_id"]}

    def execute(self, args: Dict[str, Any]) -> str:
        result = background_shells.read_new(str(args.get("shell_id", "")))
        if result is None:
            return f"Error: no background shell '{args.get('shell_id')}'."
        new, status = result
        return f"[{status}]\n{new if new else '(no new output)'}"


class KillShellTool(BaseTool):
    @property
    def name(self) -> str:
        return "kill_shell"

    @property
    def description(self) -> str:
        return "Stops a background shell started with run_in_background."

    @property
    def permissions(self) -> list:
        return ["execute"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"shell_id": {"type": "string"}}, "required": ["shell_id"]}

    def execute(self, args: Dict[str, Any]) -> str:
        shell_id = str(args.get("shell_id", ""))
        return f"Stopped shell '{shell_id}'." if background_shells.kill(shell_id) else f"Error: no background shell '{shell_id}'."
