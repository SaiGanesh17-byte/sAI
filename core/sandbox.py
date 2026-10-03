"""
OS-level sandbox for the commands agents run (execute_command, background shells,
run_python_script) -- the same approach Claude Code takes on macOS.

The checks in tools/terminal.py read the command *text*, so they can't see what a
command does once it runs (an agent once wrote delete_files.py to get around a gated
`rm`). Here the kernel enforces the limits instead, via macOS Seatbelt (sandbox-exec):

- writes: only the workspace, temp dirs and package caches;
- reads: everything outside your home folder (system toolchains), but inside it only
  the workspace and toolchain dirs -- not ~/.ssh, ~/.aws, ~/Documents, shell history,
  .netrc/.npmrc tokens, or the transcripts in ~/.claude;
- no running /usr/bin/security (it can print keychain passwords, sAI's keys included);
- network: only through a local proxy that allows sandbox_allowed_domains (package
  registries, GitHub). Everything else -- and unix sockets like docker.sock or the ssh
  agent -- is refused.

A command that genuinely needs more passes dangerously_disable_sandbox and runs
outside it only after the user approves. Linux (bubblewrap) isn't implemented yet;
there, and with sandbox "off", commands run as before under the text checks alone.
"""
import os
import platform
import select
import shutil
import socket
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Inside $HOME, commands may read only these (plus the workspace): where language
# toolchains and their caches live. Not dotfiles that hold tokens (.npmrc, .pypirc, .netrc).
HOME_READ_ALLOW = [
    ".cache", ".npm", ".nvm", ".pyenv", ".cargo", ".rustup", ".m2", ".gradle", ".sdkman", ".local",
    ".gem", ".rbenv", ".bun", ".deno", ".yarn", ".pnpm-store", ".volta", ".asdf", "go", ".gitconfig",
    ".config/git", "Library/Caches", "Library/Python", "Library/pnpm", "Library/Java", ".swiftpm",
]
# ...and write only these.
HOME_WRITE_ALLOW = [".cache", ".npm", ".gradle", ".m2", ".cargo/registry", ".cargo/git", "Library/Caches",
                    ".yarn", ".pnpm-store", "Library/pnpm", ".bun/install/cache"]

DEFAULT_ALLOWED_DOMAINS = [
    "pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "registry.yarnpkg.com",
    "github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com",
    "raw.githubusercontent.com", "repo.maven.apache.org", "repo1.maven.org", "plugins.gradle.org",
    "services.gradle.org", "crates.io", "static.crates.io", "index.crates.io", "proxy.golang.org",
    "sum.golang.org", "rubygems.org",
]

# What a sandbox refusal looks like in command output, so the agent can be told why.
BLOCK_MARKERS = ("Operation not permitted", "sAI sandbox blocked", "Could not resolve host",
                 "Temporary failure in name resolution", "nodename nor servname")


def sandbox_mode(settings: dict) -> str:
    mode = str(settings.get("sandbox", "auto")).lower()
    return mode if mode in ("auto", "on", "off") else "auto"


def sandbox_supported() -> bool:
    return platform.system() == "Darwin" and os.path.exists(SANDBOX_EXEC)


def sandbox_active(settings: dict) -> bool:
    """On unless turned off, and only where it can actually be enforced."""
    if settings.get("docker_sandbox"):
        return False  # the docker container is the boundary instead
    return sandbox_mode(settings) != "off" and sandbox_supported()


def _real(path) -> str:
    # Seatbelt matches resolved paths: /tmp is /private/tmp, /var is /private/var.
    return os.path.realpath(os.path.expanduser(str(path)))


def _sb(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_profile(workspace: str, settings: dict, proxy_port: Optional[int]) -> str:
    home = _real(Path.home())
    ws = _real(workspace)
    temp_dirs = {_real("/tmp"), _real(tempfile.gettempdir()), "/private/var/folders"}

    read_allow = [ws] + [os.path.join(home, p) for p in HOME_READ_ALLOW]
    read_allow += [_real(p) for p in settings.get("sandbox_allow_read") or []]
    write_allow = [ws] + sorted(temp_dirs) + [os.path.join(home, p) for p in HOME_WRITE_ALLOW]
    write_allow += [_real(p) for p in settings.get("sandbox_allow_write") or []]

    lines = [
        "(version 1)",
        "(allow default)",
        # Reads: all of $HOME and external volumes off, then the allowed parts back on.
        f"(deny file-read* (subpath {_sb(home)}) (subpath \"/Volumes\"))",
        "(allow file-read* " + " ".join(f"(subpath {_sb(p)})" for p in read_allow) + ")",
        # Listing the home folder itself (not its files) is harmless and some tools stat it.
        f"(allow file-read-metadata (literal {_sb(home)}))",
        "(deny file-write*)",
        "(allow file-write* " + " ".join(f"(subpath {_sb(p)})" for p in write_allow)
        + ' (literal "/dev/null") (literal "/dev/zero") (literal "/dev/stdout") (literal "/dev/stderr")'
        + ' (literal "/dev/dtracehelper") (regex #"^/dev/tty") (regex #"^/dev/fd/"))',
        # The sAI checkout's own state (sessions = conversation transcripts) stays private
        # even when the workspace is the sAI repo itself.
        f"(deny file-read* file-write* (subpath {_sb(_real(Path(__file__).resolve().parent.parent / '.sai'))}))",
        '(deny process-exec (literal "/usr/bin/security"))',
        # ...nor the keychain service itself: otherwise git's osxkeychain credential helper
        # would hand a sandboxed `git push` your GitHub credentials. TLS doesn't need it.
        '(deny mach-lookup (global-name "com.apple.SecurityServer") (global-name "com.apple.securityd"))',
        # Network: nothing leaves the machine except through the allowlisting proxy.
        "(deny network-outbound)",
    ]
    if settings.get("sandbox_allow_localhost", True):
        # The user's own dev servers and databases (and the proxy itself).
        lines.append('(allow network-outbound (remote ip "localhost:*"))')
    elif proxy_port:
        lines.append(f'(allow network-outbound (remote ip "localhost:{proxy_port}"))')
    return "\n".join(lines) + "\n"


def domain_allowed(host: str, allowed: List[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    for d in allowed:
        d = d.lower().strip()
        if d == "*":
            return True
        d = d.lstrip("*.")  # "*.example.com" means example.com and its subdomains
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


class AllowlistProxy:
    """
    A minimal HTTP proxy on 127.0.0.1: CONNECT for HTTPS, absolute-URI requests for
    HTTP. Sandboxed commands can only reach the network through it (pip, npm, git,
    curl and Node 24+ all honor HTTPS_PROXY), so it decides which hosts they reach.
    """

    def __init__(self):
        self.port: Optional[int] = None
        self.allowed: List[str] = []
        self.blocked: List[str] = []  # hosts refused, newest last
        self.blocked_total = 0
        self._server: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def ensure_started(self, allowed: List[str]) -> int:
        with self._lock:
            self.allowed = list(allowed)
            if self._server is None:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("127.0.0.1", 0))
                server.listen(64)
                self._server = server
                self.port = server.getsockname()[1]
                threading.Thread(target=self._accept_loop, daemon=True).start()
            return self.port

    def _accept_loop(self):
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _refuse(self, client, host: str):
        self.blocked_total += 1
        self.blocked.append(host)
        del self.blocked[:-20]
        body = (f"sAI sandbox blocked network access to {host}: not in sandbox_allowed_domains.\n").encode()
        client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n"
                       + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

    def _handle(self, client: socket.socket):
        upstream = None
        try:
            client.settimeout(30)
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _ = (request_line.split(" ") + ["", ""])[:3]
            if method.upper() == "CONNECT":
                host, _, port = target.rpartition(":")
                port = int(port or 443)
            else:
                from urllib.parse import urlsplit
                url = urlsplit(target)
                host, port = url.hostname or "", url.port or 80
            if not domain_allowed(host, self.allowed):
                self._refuse(client, host)
                return
            upstream = socket.create_connection((host, port), timeout=30)
            if method.upper() == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                rest = head.split(b"\r\n\r\n", 1)[1]
            else:
                # Origin-form request line for the upstream server.
                from urllib.parse import urlsplit
                url = urlsplit(target)
                path = (url.path or "/") + (f"?{url.query}" if url.query else "")
                rest = head.replace(target.encode("latin-1"), path.encode("latin-1"), 1)
            if rest:
                upstream.sendall(rest)
            self._pipe(client, upstream)
        except Exception:
            pass
        finally:
            for s in (client, upstream):
                try:
                    s and s.close()
                except OSError:
                    pass

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket):
        a.settimeout(None)
        b.settimeout(None)
        sockets = [a, b]
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 300)
            if errored or not readable:
                return
            for s in readable:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)


proxy = AllowlistProxy()


def sandbox_env(settings: dict) -> Dict[str, str]:
    port = proxy.ensure_started(settings.get("sandbox_allowed_domains") or DEFAULT_ALLOWED_DOMAINS)
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env[key] = url
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    env["NODE_USE_ENV_PROXY"] = "1"  # make Node's fetch() use HTTPS_PROXY too
    # ...without Node 22's "EnvHttpProxyAgent is experimental" warning on every command.
    env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " --disable-warning=UNDICI-EHPA").strip()
    env["SAI_SANDBOX"] = "1"
    # Secrets sAI put in its own environment never reach agent commands.
    for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
                "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        env.pop(key, None)
    return env


def sandboxed_argv(argv: List[str], workspace: str, settings: dict) -> Tuple[List[str], Dict[str, str]]:
    """Wraps an argv list; returns (argv, env)."""
    env = sandbox_env(settings)
    # Inline (-p), not a profile file: sandboxed commands can write the temp dir, and a
    # rewritten profile file would loosen the sandbox for every command after it.
    profile = build_profile(workspace, settings, proxy.port)
    return [SANDBOX_EXEC, "-p", profile] + list(argv), env


def sandboxed_shell(command: str, workspace: str, settings: dict) -> Tuple[str, Dict[str, str]]:
    """Wraps a shell command string (run with shell=True); returns (command, env)."""
    import shlex
    argv, env = sandboxed_argv(["/bin/sh", "-c", command], workspace, settings)
    return " ".join(shlex.quote(a) for a in argv), env


def explain_block(output: str, blocked_before: int = 0) -> str:
    """A hint appended to a sandboxed command's output when the sandbox likely stopped it.
    blocked_before: proxy.blocked_total when the command started (only its own blocks count)."""
    new_blocks = max(0, proxy.blocked_total - blocked_before)
    if not new_blocks and not any(marker in (output or "") for marker in BLOCK_MARKERS):
        return ""
    hosts = ", ".join(dict.fromkeys(proxy.blocked[-min(new_blocks, 3):])) if new_blocks else ""
    return ("\n[sAI sandbox: this command runs with writes limited to the workspace, no access to secrets "
            "in your home folder, and network only to allowed domains"
            + (f" (blocked: {hosts})" if hosts else "")
            + ". If it truly needs more, run it again with dangerously_disable_sandbox: true -- the user "
              "will be asked to approve. Don't work around the sandbox another way.]")


def sandbox_note(settings: dict) -> str:
    """One line for agent prompts, so they know the limits before they hit them."""
    if not sandbox_active(settings):
        return ""
    return ("SANDBOX: your commands run in an OS sandbox -- they can write only inside the workspace (and temp "
            "dirs), can't read secrets in the user's home folder, and reach the network only via package "
            "registries and GitHub. If a command fails because of this, retry it with "
            "dangerously_disable_sandbox: true (the user will be asked); never work around the sandbox.")
