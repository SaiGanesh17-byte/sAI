import pytest

from core import security
from execution.permissions import PermissionRequestRequired
from tools.terminal import TerminalTool


@pytest.fixture(autouse=True)
def _isolate_command_approvals():
    before = set(security.APPROVED_COMMANDS)
    yield
    security.APPROVED_COMMANDS.clear()
    security.APPROVED_COMMANDS.update(before)


@pytest.mark.parametrize("command", ["sudo echo hi", "chmod 777 x", "echo hi | sh"])
def test_approved_risky_command_is_not_reblocked(tmp_workspace, command):
    # Regression: approve_path() only routed a few hard-coded prefixes
    # ("rm ", "git ", ...) to command approval, so approving e.g. "sudo ..."
    # landed in APPROVED_PATHS and the command was blocked again forever.
    tool = TerminalTool()
    with pytest.raises(PermissionRequestRequired) as exc:
        tool.execute({"command": command})
    assert exc.value.kind == "command"

    security.approve_request(exc.value.path, exc.value.kind)
    assert security.is_command_approved(command)


@pytest.mark.parametrize("command", [
    "cd ~ && cat .zshrc",
    "cat $HOME/.zshrc",
    "cat ${HOME}/.zshrc",
    "cd .. && ls",
    "ls /",
])
def test_commands_escaping_the_workspace_are_rejected(tmp_workspace, command):
    result = TerminalTool().execute({"command": command})
    assert result.startswith("Security Error")


def test_urls_are_not_mistaken_for_paths(tmp_workspace, monkeypatch):
    # Only the sandbox check is under test -- don't actually hit the network.
    from tools import terminal
    started = []
    monkeypatch.setattr(terminal.async_process_manager, "start_process", lambda cmd, cwd: started.append(cmd))
    monkeypatch.setattr(terminal.async_process_manager, "is_running", False)
    TerminalTool().execute({"command": "curl -s https://example.com/some/page"})
    assert started == ["curl -s https://example.com/some/page"]


def test_running_a_destructive_script_needs_approval_for_its_content(tmp_workspace, monkeypatch):
    # Regression: with `rm` gated, an agent wrote delete_files.py and ran `python3 delete_files.py`.
    from tools import terminal
    started = []
    monkeypatch.setattr(terminal.async_process_manager, "start_process", lambda cmd, cwd: started.append(cmd))
    monkeypatch.setattr(terminal.async_process_manager, "is_running", False)
    script = tmp_workspace / "delete_files.py"
    script.write_text("import os\nfor f in os.listdir('.'):\n    os.remove(f)\n")

    for command in ("python3 delete_files.py", "cd . && python3 -u delete_files.py", "bash -c 'true'; python delete_files.py"):
        with pytest.raises(PermissionRequestRequired) as exc:
            TerminalTool().execute({"command": command})
        assert exc.value.kind == "script" and exc.value.path == str(script.resolve())
    assert started == []

    security.approve_request(str(script.resolve()), "script")
    TerminalTool().execute({"command": "python3 delete_files.py"})
    assert started == ["python3 delete_files.py"]

    script.write_text(script.read_text() + "# edited after approval\n")  # approval is for that exact content
    with pytest.raises(PermissionRequestRequired):
        TerminalTool().execute({"command": "python3 delete_files.py"})


def test_harmless_scripts_and_executables_still_run(tmp_workspace, monkeypatch):
    from tools import terminal
    started = []
    monkeypatch.setattr(terminal.async_process_manager, "start_process", lambda cmd, cwd: started.append(cmd))
    monkeypatch.setattr(terminal.async_process_manager, "is_running", False)
    (tmp_workspace / "hello.py").write_text("print('hi')\n")
    (tmp_workspace / "wipe.sh").write_text("rm -rf ./build\n")
    TerminalTool().execute({"command": "python3 hello.py"})
    with pytest.raises(PermissionRequestRequired):
        TerminalTool().execute({"command": "./wipe.sh"})
    assert started == ["python3 hello.py"]


@pytest.mark.parametrize("script", ["fs.unlinkSync('a')", "require('rimraf')", "FileUtils.rm_rf('x')", "Path('a').unlink()"])
def test_js_ruby_pathlib_deletes_are_risky(script):
    assert security.find_risky_pattern(script) is not None


def test_failed_commands_report_their_exit_code(tmp_workspace):
    out = TerminalTool().execute({"command": "python3 -c 'import sys; print(\"boom\"); sys.exit(3)'"})
    assert out.startswith("Error: command exited with code 3") and "boom" in out
    ok = TerminalTool().execute({"command": "echo fine"})
    assert ok.strip() == "fine"
