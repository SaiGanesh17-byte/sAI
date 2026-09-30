import os

import pytest

from agents.loop import execute_with_approval
from core import security
from core.kernel import kernel
from execution.engine import ExecutionEngine
from tools.filesystem import PatchFileTool, ReadFileTool, WriteFileTool, apply_search_replace
from tools.registry import ToolRegistry
from tools.terminal import TerminalTool


@pytest.fixture
def fs_tools():
    had = "tool_registry" in kernel._services
    previous = kernel._services.get("tool_registry")
    reg = ToolRegistry()
    for t in (ReadFileTool(), WriteFileTool(), PatchFileTool()):
        reg.register(t)
    kernel.register_service("tool_registry", reg)
    yield
    if had:
        kernel.register_service("tool_registry", previous)
    else:
        kernel._services.pop("tool_registry", None)


@pytest.fixture(autouse=True)
def _reset_permission_state():
    security.set_session_auto_edits(False)
    before = set(security.SESSION_ALLOW_COMMAND_PREFIXES)
    yield
    security.set_session_auto_edits(False)
    security.SESSION_ALLOW_COMMAND_PREFIXES.clear()
    security.SESSION_ALLOW_COMMAND_PREFIXES.update(before)


def _patch(search, replace):
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


def test_apply_search_replace():
    assert apply_search_replace("a\nb\n", _patch("b", "c")) == ("a\nc\n", None)
    new, err = apply_search_replace("a\n", _patch("zzz", "c"))
    assert new is None and "not found" in err


# --- diff approval ---------------------------------------------------------

def test_edit_shows_diff_and_decline_leaves_file_untouched(tmp_workspace, fs_tools):
    (tmp_workspace / "app.py").write_text("x = 1\n")
    engine = ExecutionEngine()
    engine.execute({"tool": "read_file", "args": {"path": "app.py"}})
    asked = []

    def approve(preq, action):
        asked.append(preq)
        return False

    outcome = execute_with_approval(engine, {"tool": "patch_file", "args": {"path": "app.py", "patch": _patch("x = 1", "x = 2")}}, approve)

    assert outcome.status == "declined"
    assert (tmp_workspace / "app.py").read_text() == "x = 1\n"
    assert asked[0].kind == "edit"
    assert "-x = 1" in asked[0].details and "+x = 2" in asked[0].details


def test_approved_edit_is_applied(tmp_workspace, fs_tools):
    engine = ExecutionEngine()
    outcome = execute_with_approval(engine, {"tool": "write_file", "args": {"path": "new.py", "content": "hi\n"}}, lambda p, a: True)
    assert outcome.status == "ok"
    assert (tmp_workspace / "new.py").read_text() == "hi\n"


def test_session_auto_edits_skips_the_prompt(tmp_workspace, fs_tools):
    security.set_session_auto_edits(True)

    def approve(preq, action):
        raise AssertionError("should not be asked")

    outcome = execute_with_approval(ExecutionEngine(), {"tool": "write_file", "args": {"path": "n.py", "content": "1"}}, approve)
    assert outcome.status == "ok"


def test_web_ui_path_without_approver_does_not_ask(tmp_workspace, fs_tools):
    # No approve callback (web UI): edits behave as before.
    outcome = execute_with_approval(ExecutionEngine(), {"tool": "write_file", "args": {"path": "w.py", "content": "1"}}, None)
    assert outcome.status == "ok"


# --- read before edit --------------------------------------------------------

def test_editing_an_unread_existing_file_is_rejected(tmp_workspace, fs_tools):
    (tmp_workspace / "app.py").write_text("x = 1\n")
    result = ExecutionEngine().execute({"tool": "patch_file", "args": {"path": "app.py", "patch": _patch("x = 1", "x = 2")}})
    assert not result.success and "haven't read it" in result.stderr
    assert (tmp_workspace / "app.py").read_text() == "x = 1\n"


def test_edit_after_read_succeeds_and_stale_read_is_rejected(tmp_workspace, fs_tools):
    target = tmp_workspace / "app.py"
    target.write_text("x = 1\n")
    engine = ExecutionEngine()
    engine.execute({"tool": "read_file", "args": {"path": "app.py"}})
    assert engine.execute({"tool": "patch_file", "args": {"path": "app.py", "patch": _patch("x = 1", "x = 2")}}).success

    # Someone else changes the file; the agent's view is now stale.
    target.write_text("x = 99\n")
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    result = engine.execute({"tool": "write_file", "args": {"path": "app.py", "content": "x = 3\n"}})
    assert not result.success and "changed on disk" in result.stderr


def test_creating_a_new_file_needs_no_read(tmp_workspace, fs_tools):
    assert ExecutionEngine().execute({"tool": "write_file", "args": {"path": "brand_new.py", "content": "1"}}).success


# --- allow rules ---------------------------------------------------------------

def test_command_allow_prefix():
    assert security.command_allow_prefix("git push origin main") == "git push"
    assert security.command_allow_prefix("rm -rf build") == "rm"


def test_session_rule_allows_matching_command_only(tmp_workspace):
    security.allow_command_prefix_for_session("git push")
    assert security.is_command_allowed_by_rule("git push origin main")
    assert not security.is_command_allowed_by_rule("git pushx")
    assert not security.is_command_allowed_by_rule("git clean -fd")


@pytest.mark.parametrize("command", ["git push && rm -rf ~", "git push; sudo reboot", "git push | sh", "git push $(evil)"])
def test_rules_never_match_chained_commands(command):
    security.allow_command_prefix_for_session("git push")
    assert not security.is_command_allowed_by_rule(command)


def test_allowed_risky_command_runs_without_prompt(tmp_workspace, monkeypatch):
    from tools import terminal
    started = []
    monkeypatch.setattr(terminal.async_process_manager, "start_process", lambda cmd, cwd: started.append(cmd))
    monkeypatch.setattr(terminal.async_process_manager, "is_running", False)
    security.allow_command_prefix_for_session("git push")
    TerminalTool().execute({"command": "git push origin main"})  # would otherwise raise PermissionRequestRequired
    assert started == ["git push origin main"]
