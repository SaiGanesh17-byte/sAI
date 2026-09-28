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
