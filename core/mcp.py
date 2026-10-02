"""
MCP (Model Context Protocol) client for stdio servers -- connect external
tool servers the way Claude Code does. Configure in sAI's settings.json:

    "mcp_servers": {
      "github":   {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."}},
      "sqlite":   {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "app.db"]},
      "disabled": {"command": "...", "disabled": true}
    }

Each server tool becomes an agent tool named mcp__<server>__<tool>. Running
one asks permission first (allow_mcp_tools in settings, or "a" at the
prompt, skips that). Speaks JSON-RPC 2.0, one message per line, over the
server's stdin/stdout; a small reader thread routes responses by id.
"""
import atexit
import json
import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.base import BaseTool

PROTOCOL_VERSION = "2025-06-18"
STARTUP_TIMEOUT = 15.0
CALL_TIMEOUT = 120.0
MAX_RESULT_CHARS = 30000


class MCPError(Exception):
    pass


class MCPServer:
    def __init__(self, name: str, command: str, args: Optional[List[str]] = None,
                 env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.proc: Optional[subprocess.Popen] = None
        self.server_info: Dict[str, Any] = {}
        self._next_id = 0
        self._pending: Dict[int, "queue.Queue"] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stderr_tail: List[str] = []

    # -- lifecycle ----------------------------------------------------------------
    def start(self, timeout: float = STARTUP_TIMEOUT) -> None:
        self.proc = subprocess.Popen(
            [self.command, *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, cwd=self.cwd, env={**os.environ, **self.env},
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sai", "version": "1.0"},
        }, timeout=timeout)
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        self.notify("notifications/initialized")

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- JSON-RPC -------------------------------------------------------------------
    def _send(self, message: dict) -> None:
        if not self.alive:
            raise MCPError(f"server '{self.name}' is not running{self._stderr_hint()}")
        with self._write_lock:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def request(self, method: str, params: Optional[dict] = None, timeout: float = CALL_TIMEOUT) -> Any:
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            box: "queue.Queue" = queue.Queue(maxsize=1)
            self._pending[req_id] = box
        try:
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
            try:
                reply = box.get(timeout=timeout)
            except queue.Empty:
                raise MCPError(f"'{self.name}' did not answer {method} within {timeout:.0f}s{self._stderr_hint()}")
        finally:
            with self._lock:
                self._pending.pop(req_id, None)
        if reply is None:
            raise MCPError(f"server '{self.name}' exited{self._stderr_hint()}")
        if "error" in reply:
            err = reply["error"] or {}
            raise MCPError(f"{method} failed: {err.get('message', err)}")
        return reply.get("result")

    def _read_stdout(self) -> None:
        for line in iter(self.proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray log output on stdout
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._lock:
                    box = self._pending.get(msg["id"])
                if box is not None:
                    box.put(msg)
            elif "id" in msg and "method" in msg:
                # Server-to-client request (roots/list, sampling, ...): not supported.
                try:
                    self._send({"jsonrpc": "2.0", "id": msg["id"],
                                "error": {"code": -32601, "message": f"sAI does not support {msg['method']}"}})
                except MCPError:
                    pass
            # notifications (logging, progress, list_changed) are ignored
        # Server exited: wake every waiting request.
        with self._lock:
            for box in self._pending.values():
                try:
                    box.put_nowait(None)
                except queue.Full:
                    pass

    def _read_stderr(self) -> None:
        for line in iter(self.proc.stderr.readline, ""):
            self._stderr_tail = (self._stderr_tail + [line.rstrip()])[-20:]

    def _stderr_hint(self) -> str:
        tail = "\n".join(self._stderr_tail[-5:])
        return f"\n{tail}" if tail else ""

    # -- MCP methods ----------------------------------------------------------------
    def list_tools(self) -> List[dict]:
        tools, cursor = [], None
        for _ in range(50):  # pagination guard
            result = self.request("tools/list", {"cursor": cursor} if cursor else {}) or {}
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, tool: str, arguments: dict) -> str:
        result = self.request("tools/call", {"name": tool, "arguments": arguments or {}}) or {}
        parts = []
        for item in result.get("content", []):
            kind = item.get("type")
            if kind == "text":
                parts.append(item.get("text", ""))
            elif kind == "resource":
                res = item.get("resource", {})
                parts.append(res.get("text") or f"[resource {res.get('uri', '')}]")
            elif kind == "resource_link":
                parts.append(f"[resource link {item.get('uri', '')}]")
            else:
                parts.append(f"[{kind} content omitted]")
        if result.get("structuredContent") is not None and not parts:
            parts.append(json.dumps(result["structuredContent"]))
        text = "\n".join(parts) or "(no output)"
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"\n...[truncated, {len(text) - MAX_RESULT_CHARS} more chars]"
        return f"Error: {text}" if result.get("isError") else text


def resolve_env(env: Dict[str, str]) -> Dict[str, str]:
    """
    Env values may be "$(command)" -- run at startup, e.g. "$(gh auth token)" -- or
    "${VAR}" from sAI's own environment, so secrets never need to sit in settings.json.
    """
    resolved = {}
    for key, value in env.items():
        value = str(value)
        command = re.fullmatch(r"\$\((.+)\)", value.strip())
        if command:
            try:
                out = subprocess.run(command.group(1), shell=True, capture_output=True, text=True, timeout=15)
            except subprocess.TimeoutExpired:
                raise MCPError(f"env {key}: `{command.group(1)}` timed out")
            if out.returncode != 0 or not out.stdout.strip():
                raise MCPError(f"env {key}: `{command.group(1)}` failed: {(out.stderr or out.stdout).strip()[:200]}")
            value = out.stdout.strip()
        else:
            value = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
        resolved[key] = value
    return resolved


def mcp_tool_name(server: str, tool: str) -> str:
    clean = lambda s: re.sub(r"[^A-Za-z0-9_-]", "_", s)
    return f"mcp__{clean(server)}__{clean(tool)}"


class MCPTool(BaseTool):
    """Adapter exposing one MCP server tool as an sAI tool."""

    def __init__(self, server: MCPServer, spec: dict):
        self.server = server
        self.tool = spec["name"]
        self._name = mcp_tool_name(server.name, self.tool)
        self._description = (spec.get("description") or "").strip()
        self._schema = spec.get("inputSchema") or {"type": "object", "properties": {}}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        # Agent prompts show only a short prefix of this (see AgentRuntime); the
        # tool_schema tool returns it in full.
        return f"[MCP {self.server.name}] {self._description}"

    @property
    def permissions(self) -> list:
        return ["mcp"]

    @property
    def schema(self) -> Dict[str, Any]:
        return self._schema

    def execute(self, args: Dict[str, Any]) -> str:
        from core.security import consume_approved_mcp_tool, is_mcp_tool_allowed
        from execution.permissions import PermissionRequestRequired

        if not is_mcp_tool_allowed(self._name) and not consume_approved_mcp_tool(self._name):
            raise PermissionRequestRequired(
                path=self._name,
                reason=f"Use '{self.tool}' from MCP server '{self.server.name}'",
                kind="mcp",
            )
        try:
            return self.server.call_tool(self.tool, args)
        except MCPError as e:
            return f"Error: {e}"


@dataclass
class ServerStatus:
    name: str
    ok: bool
    tools: List[str] = field(default_factory=list)
    error: str = ""


class MCPManager:
    """Starts configured servers once per process and hands out their tools."""

    def __init__(self):
        self.servers: Dict[str, MCPServer] = {}
        self.status: Dict[str, ServerStatus] = {}
        self.tools: List[MCPTool] = []
        self._started = False
        self._lock = threading.Lock()

    def ensure_for_patterns(self, patterns: List[str], config: Optional[Dict[str, dict]] = None) -> List[MCPTool]:
        """
        Starts (once) only the servers these mcp__<server>__ patterns refer to, and
        returns their tools. Launch stays fast: a plain question starts no server,
        the GitHub server starts the first time the GitHub agent runs.
        """
        import fnmatch
        if not patterns:
            return []
        if config is None:
            from core.settings import load_settings
            config = load_settings().get("mcp_servers") or {}
        from core.security import get_current_workspace
        names = [n for n in config if any(fnmatch.fnmatch(f"mcp__{n}__x", p) or p.startswith(f"mcp__{n}__")
                                           for p in patterns)]
        with self._lock:
            todo = [(n, config[n]) for n in names if n not in self.status
                    and isinstance(config[n], dict) and not config[n].get("disabled") and config[n].get("command")]
            if todo:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                    results = list(pool.map(lambda item: self._start_one(item[0], item[1], str(get_current_workspace())), todo))
                for name, server, tools, error in results:
                    if error:
                        self.status[name] = ServerStatus(name, ok=False, error=error)
                        continue
                    self.servers[name] = server
                    self.tools.extend(tools)
                    self.status[name] = ServerStatus(name, ok=True, tools=[t.tool for t in tools])
            return [t for t in self.tools if t.server.name in names]

    def ensure_started(self, config: Optional[Dict[str, dict]] = None, cwd: Optional[str] = None) -> List[MCPTool]:
        with self._lock:
            if self._started:
                return self.tools
            self._started = True
            if config is None:
                from core.settings import load_settings
                config = load_settings().get("mcp_servers") or {}
            wanted = [(name, spec) for name, spec in config.items()
                      if isinstance(spec, dict) and not spec.get("disabled") and spec.get("command")]
            # Start servers in parallel: npx-launched servers take seconds each, and
            # sequential startup would add them all up on every launch.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=max(1, len(wanted))) as pool:
                results = list(pool.map(lambda item: self._start_one(item[0], item[1], cwd), wanted))
            for name, server, tools, error in results:
                if error:
                    self.status[name] = ServerStatus(name, ok=False, error=error)
                    continue
                self.servers[name] = server
                self.tools.extend(tools)
                self.status[name] = ServerStatus(name, ok=True, tools=[t.tool for t in tools])
            return self.tools

    @staticmethod
    def _start_one(name: str, spec: dict, cwd: Optional[str]):
        try:
            env = resolve_env(spec.get("env") or {})
        except MCPError as e:
            return name, None, [], str(e)
        server = MCPServer(name, spec["command"], spec.get("args"), env, cwd=cwd)
        try:
            server.start(timeout=float(spec.get("startup_timeout", STARTUP_TIMEOUT)))
            specs = server.list_tools()
        except (MCPError, OSError) as e:
            server.close()
            return name, None, [], str(e).strip()
        return name, server, [MCPTool(server, s) for s in specs if s.get("name")], ""

    def shutdown(self) -> None:
        for server in self.servers.values():
            server.close()


mcp_manager = MCPManager()
atexit.register(mcp_manager.shutdown)
