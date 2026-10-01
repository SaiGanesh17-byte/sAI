import json
import sys

import pytest

from core.hooks import run_hooks
from core.kernel import kernel
from execution.engine import ExecutionEngine
from tools.filesystem import ReadFileTool, WriteFileTool
from tools.registry import ToolRegistry

PY = sys.executable


@pytest.fixture
def hooks(monkeypatch):
    config = {}
    monkeypatch.setattr("core.settings.load_settings", lambda: {"hooks": config})
    return config


@pytest.fixture
def fs_tools():
    had, prev = "tool_registry" in kernel._services, kernel._services.get("tool_registry")
    reg = ToolRegistry()
    reg.register(ReadFileTool())
    reg.register(WriteFileTool())
    kernel.register_service("tool_registry", reg)
    yield
    if had:
        kernel.register_service("tool_registry", prev)
    else:
        kernel._services.pop("tool_registry", None)


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return f'"{PY}" "{path}"'


def test_pre_tool_use_exit_2_blocks_the_tool(tmp_workspace, hooks, fs_tools):
    cmd = _script(tmp_workspace, "deny.py", "import sys, json\nd = json.load(sys.stdin)\nprint('no writes to ' + d['args']['path'], file=sys.stderr)\nsys.exit(2)\n")
    hooks["PreToolUse"] = [{"matcher": "write_file|edit_file", "command": cmd}]
    result = ExecutionEngine().execute({"tool": "write_file", "args": {"path": "x.txt", "content": "hi"}})
    assert not result.success
    assert "blocked by a PreToolUse hook: no writes to x.txt" in result.stderr
    assert not (tmp_workspace / "x.txt").exists()


def test_matcher_limits_which_tools_trigger(tmp_workspace, hooks, fs_tools):
    hooks["PreToolUse"] = [{"matcher": "edit_file", "command": f'"{PY}" -c "import sys; sys.exit(2)"'}]
    assert ExecutionEngine().execute({"tool": "write_file", "args": {"path": "y.txt", "content": "ok"}}).success


def test_post_tool_use_gets_env_and_feedback(tmp_workspace, hooks, fs_tools):
    marker = tmp_workspace / "seen.txt"
    cmd = _script(tmp_workspace, "post.py",
                  f"import os, sys\nopen({str(marker)!r}, 'w').write(os.environ['SAI_TOOL'] + ' ' + os.environ['SAI_FILE_PATH'])\n"
                  "print('formatting changed 2 lines', file=sys.stderr)\nsys.exit(2)\n")
    hooks["PostToolUse"] = [{"matcher": "*", "command": cmd}]
    result = ExecutionEngine().execute({"tool": "write_file", "args": {"path": "z.txt", "content": "ok"}})
    assert result.success  # PostToolUse can't undo; it adds feedback
    assert "[PostToolUse hook feedback]\nformatting changed 2 lines" in result.stdout
    assert marker.read_text() == "write_file z.txt"


def test_user_prompt_submit_context_and_block(tmp_workspace, hooks):
    hooks["UserPromptSubmit"] = [{"command": f'"{PY}" -c "print(\'branch: main\')"'}]
    res = run_hooks("UserPromptSubmit", {"prompt": "hi"})
    assert not res.blocked and res.context == "branch: main"

    hooks["UserPromptSubmit"] = [{"command": _script(tmp_workspace, "b.py", "import sys,json\nif 'password' in json.load(sys.stdin)['prompt']:\n    print('no secrets please', file=sys.stderr); sys.exit(2)\n")}]
    assert run_hooks("UserPromptSubmit", {"prompt": "my password is x"}).reason == "no secrets please"
    assert not run_hooks("UserPromptSubmit", {"prompt": "fine"}).blocked


def test_failures_and_timeouts_are_warnings_not_blocks(tmp_workspace, hooks):
    hooks["Stop"] = [{"command": f'"{PY}" -c "import sys; sys.exit(2)"'},  # Stop never blocks
                     {"command": f'"{PY}" -c "import sys; sys.exit(1)"'},
                     {"command": f'"{PY}" -c "import time; time.sleep(5)"', "timeout": 0.5}]
    res = run_hooks("Stop", {})
    assert not res.blocked
    assert len(res.warnings) == 3 and any("timed out" in w for w in res.warnings)


def test_no_hooks_configured_is_a_no_op(tmp_workspace, hooks):
    res = run_hooks("PreToolUse", {"args": {}}, tool="write_file")
    assert not res.blocked and res.warnings == [] and res.context == ""
