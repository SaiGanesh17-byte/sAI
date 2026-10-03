import os
import platform
import socket
from pathlib import Path

import pytest

import core.sandbox as sandbox
from core import security
from execution.permissions import PermissionRequestRequired
from tools.terminal import TerminalTool, check_command

REAL_SANDBOX = platform.system() == "Darwin" and os.path.exists(sandbox.SANDBOX_EXEC)


def test_domain_allowlist():
    allowed = ["pypi.org", "*.githubusercontent.com"]
    assert sandbox.domain_allowed("pypi.org", allowed)
    assert sandbox.domain_allowed("files.pypi.org", allowed)
    assert sandbox.domain_allowed("raw.githubusercontent.com", allowed)
    assert not sandbox.domain_allowed("evilpypi.org", allowed)
    assert not sandbox.domain_allowed("example.com", allowed)
    assert sandbox.domain_allowed("anything.example", ["*"])


def test_profile_limits_writes_reads_and_network(tmp_path):
    profile = sandbox.build_profile(str(tmp_path), {}, proxy_port=4321)
    home = os.path.realpath(Path.home())
    assert f'(deny file-read* (subpath "{home}")' in profile
    assert f'(subpath "{os.path.realpath(tmp_path)}")' in profile
    assert "(deny file-write*)" in profile
    assert '(deny process-exec (literal "/usr/bin/security"))' in profile
    assert '(global-name "com.apple.SecurityServer")' in profile
    assert "(deny network-outbound)" in profile
    assert '(remote ip "localhost:*")' in profile
    strict = sandbox.build_profile(str(tmp_path), {"sandbox_allow_localhost": False}, proxy_port=4321)
    assert '(remote ip "localhost:4321")' in strict and '"localhost:*"' not in strict


def test_sandbox_env_hides_api_keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    monkeypatch.setenv("GROQ_API_KEY", "secret")
    env = sandbox.sandbox_env({})
    assert "OPENROUTER_API_KEY" not in env and "GROQ_API_KEY" not in env
    assert env["HTTPS_PROXY"] == f"http://127.0.0.1:{sandbox.proxy.port}"


def test_proxy_refuses_unlisted_host():
    port = sandbox.proxy.ensure_started(["pypi.org"])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        reply = s.recv(4096)
    assert reply.startswith(b"HTTP/1.1 403") and b"sAI sandbox blocked" in reply
    assert sandbox.proxy.blocked[-1] == "example.com"


def test_sandbox_drops_escape_only_prompts(tmp_workspace):
    # Spawning programs is harmless when they're sandboxed too...
    assert check_command("python3 -c 'import subprocess; subprocess.run([\"ls\"])'", sandboxed=True) is None
    with pytest.raises(PermissionRequestRequired):
        check_command("python3 -c 'import subprocess; subprocess.run([\"ls\"])'", sandboxed=False)
    # ...but deleting files and pushing code still ask.
    for command in ("rm -rf build", "git push origin main"):
        with pytest.raises(PermissionRequestRequired):
            check_command(command, sandboxed=True)


def test_scripts_with_subprocess_run_without_a_prompt_when_sandboxed(tmp_workspace):
    (tmp_workspace / "runner.py").write_text("import subprocess\nsubprocess.run(['pytest'])\n")
    assert check_command("python3 runner.py", sandboxed=True) is None
    (tmp_workspace / "cleanup.py").write_text("import shutil\nshutil.rmtree('build')\n")
    with pytest.raises(PermissionRequestRequired):
        check_command("python3 cleanup.py", sandboxed=True)


def test_leaving_the_sandbox_always_needs_approval(tmp_workspace, monkeypatch):
    monkeypatch.setattr("core.security.is_command_allowed_by_rule", lambda c: True)  # even with an allow rule
    with pytest.raises(PermissionRequestRequired) as info:
        check_command("pytest", sandboxed=False, leave_sandbox=True)
    assert "OUTSIDE the sandbox" in info.value.reason
    security.approve_command("pytest")
    assert check_command("pytest", sandboxed=False, leave_sandbox=True) is None  # approved once
    with pytest.raises(PermissionRequestRequired):
        check_command("pytest", sandboxed=False, leave_sandbox=True)  # ...and only once


def test_flag_is_ignored_without_a_sandbox(tmp_workspace, monkeypatch):
    started = []
    monkeypatch.setattr("tools.terminal.async_process_manager.start_process",
                        lambda cmd, cwd, **kw: started.append(kw))
    monkeypatch.setattr("tools.terminal.async_process_manager.is_running", False, raising=False)
    TerminalTool().execute({"command": "echo hi", "dangerously_disable_sandbox": True})
    assert started == [{"sandbox": False}]  # no sandbox here (conftest), so nothing to leave or approve


# ---------------------------------------------------------------- real sandbox (macOS)

@pytest.fixture
def real_sandbox(monkeypatch, tmp_workspace):
    if not REAL_SANDBOX:
        pytest.skip("sandbox-exec is macOS-only")
    monkeypatch.setattr(sandbox, "sandbox_supported", lambda: True)
    monkeypatch.setattr("core.settings.load_settings", lambda: {"sandbox": "auto"})
    return tmp_workspace


def run(command: str) -> str:
    return TerminalTool().execute({"command": command, "timeout": 30})


def test_real_workspace_is_writable(real_sandbox):
    assert "hi" in run("echo hi > note.txt && cat note.txt")
    assert (real_sandbox / "note.txt").read_text().strip() == "hi"


def test_real_home_is_not_writable_or_readable(real_sandbox):
    target = Path.home() / "sai_sandbox_probe.txt"
    out = run(f"touch {target}")
    assert "Operation not permitted" in out and not target.exists()
    assert "Operation not permitted" in run("ls $HOME/Library/Keychains")
    assert "sAI sandbox:" in out  # the agent is told what happened and how to ask for more


def test_real_keychain_tool_is_blocked(real_sandbox):
    assert "Operation not permitted" in run("/usr/bin/security list-keychains")


def test_real_network_only_through_allowlist(real_sandbox):
    out = run("curl -sS -m 5 https://example.com -o /dev/null")
    assert "403" in out and "example.com" in out
    direct = run("curl -sS -m 5 --noproxy '*' http://1.1.1.1 -o /dev/null")
    assert "exited with code" in direct


def test_real_subprocess_scripts_run_without_prompt(real_sandbox):
    (real_sandbox / "t.py").write_text("import subprocess\nprint(subprocess.run(['echo', 'nested'], "
                                       "capture_output=True, text=True).stdout)\n")
    assert "nested" in run("python3 t.py")


def test_profile_is_passed_inline_not_as_a_file(tmp_path):
    argv, _ = sandbox.sandboxed_argv(["true"], str(tmp_path), {})
    assert argv[1] == "-p" and argv[2].startswith("(version 1)")  # nothing on disk to tamper with


def test_real_git_credentials_unreachable(real_sandbox):
    out = run("printf 'protocol=https\\nhost=github.com\\n\\n' | git credential-osxkeychain get; echo rc=$?")
    assert "password=" not in out
