import sys
from pathlib import Path

import pytest

from core import security
from core.mcp import MCPManager, MCPServer, mcp_tool_name
from execution.permissions import PermissionRequestRequired

FAKE = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")


@pytest.fixture
def server():
    s = MCPServer("fake", sys.executable, [FAKE])
    s.start(timeout=10)
    yield s
    s.close()


@pytest.fixture
def manager():
    m = MCPManager()
    yield m
    m.shutdown()


@pytest.fixture(autouse=True)
def _reset_mcp_permissions(monkeypatch):
    monkeypatch.setattr("core.settings.load_settings", lambda: {})
    security.SESSION_ALLOWED_MCP_TOOLS.clear()
    security.APPROVED_MCP_TOOLS.clear()
    yield
    security.SESSION_ALLOWED_MCP_TOOLS.clear()
    security.APPROVED_MCP_TOOLS.clear()


def test_handshake_and_paginated_tool_list(server):
    assert server.server_info["name"] == "fake"
    assert [t["name"] for t in server.list_tools()] == ["echo", "add", "fail"]


def test_call_tool_text_and_error(server):
    assert server.call_tool("echo", {"text": "hello"}) == "hello"
    assert server.call_tool("add", {"a": 2, "b": 3}) == "5"
    assert server.call_tool("fail", {}) == "Error: something broke"


def test_manager_registers_tools_and_records_failures(manager, tmp_path):
    tools = manager.ensure_started({
        "fake": {"command": sys.executable, "args": [FAKE]},
        "broken": {"command": sys.executable, "args": ["-c", "import sys; print('boom', file=sys.stderr); sys.exit(1)"],
                   "startup_timeout": 5},
        "off": {"command": "nonexistent", "disabled": True},
    }, cwd=str(tmp_path))
    assert [t.name for t in tools] == ["mcp__fake__echo", "mcp__fake__add", "mcp__fake__fail"]
    assert tools[0].schema["required"] == ["text"]
    assert tools[0].description == "[MCP fake] Echo text back"
    assert manager.status["fake"].ok and not manager.status["broken"].ok
    assert "boom" in manager.status["broken"].error or "exited" in manager.status["broken"].error
    assert "off" not in manager.status
    # Started once per process: a second call doesn't spawn new servers.
    assert manager.ensure_started({"other": {"command": sys.executable, "args": [FAKE]}}) is tools


def test_mcp_tool_asks_permission_then_runs(manager, tmp_path):
    echo = manager.ensure_started({"fake": {"command": sys.executable, "args": [FAKE]}}, cwd=str(tmp_path))[0]
    with pytest.raises(PermissionRequestRequired) as exc:
        echo.execute({"text": "hi"})
    assert exc.value.kind == "mcp" and exc.value.path == "mcp__fake__echo"

    security.approve_request(exc.value.path, exc.value.kind)   # "y": once
    assert echo.execute({"text": "hi"}) == "hi"
    with pytest.raises(PermissionRequestRequired):
        echo.execute({"text": "again"})

    security.allow_mcp_tool_for_session("mcp__fake__echo")      # "a": session
    assert echo.execute({"text": "again"}) == "again"


def test_settings_rule_allows_a_whole_server(manager, tmp_path, monkeypatch):
    add = manager.ensure_started({"fake": {"command": sys.executable, "args": [FAKE]}}, cwd=str(tmp_path))[1]
    monkeypatch.setattr("core.settings.load_settings", lambda: {"allow_mcp_tools": ["mcp__fake__"]})
    assert add.execute({"a": 1, "b": 1}) == "2"


def test_dead_server_returns_error_instead_of_hanging(server):
    server.proc.kill()
    server.proc.wait()
    with pytest.raises(Exception) as exc:
        server.call_tool("echo", {"text": "x"})
    assert "not running" in str(exc.value) or "exited" in str(exc.value)


def test_tool_names_are_sanitized():
    assert mcp_tool_name("my server", "get.thing") == "mcp__my_server__get_thing"


def test_mcp_tool_through_engine_and_approval(manager, tmp_path):
    from agents.loop import execute_with_approval
    from core.kernel import kernel
    from execution.engine import ExecutionEngine
    from tools.registry import ToolRegistry
    from ui.activity import tool_call_label

    had, prev = "tool_registry" in kernel._services, kernel._services.get("tool_registry")
    reg = ToolRegistry()
    for t in manager.ensure_started({"fake": {"command": sys.executable, "args": [FAKE]}}, cwd=str(tmp_path)):
        reg.register(t)
    kernel.register_service("tool_registry", reg)
    try:
        asked = []
        action = {"tool": "mcp__fake__add", "args": {"a": 20, "b": 22}}
        outcome = execute_with_approval(ExecutionEngine(), action, lambda preq, a: asked.append(preq.kind) or True)
        assert outcome.status == "ok" and outcome.content == "42"
        assert asked == ["mcp"]
        assert tool_call_label("mcp__fake__add", {}) == "fake:add"
    finally:
        if had:
            kernel.register_service("tool_registry", prev)
        else:
            kernel._services.pop("tool_registry", None)



def test_env_values_from_commands_and_variables(monkeypatch):
    from core.mcp import MCPError, resolve_env
    monkeypatch.setenv("SAI_TEST_TOKEN", "abc123")
    env = resolve_env({"A": "$(echo from-command)", "B": "${SAI_TEST_TOKEN}", "C": "plain"})
    assert env == {"A": "from-command", "B": "abc123", "C": "plain"}
    with pytest.raises(MCPError, match="failed"):
        resolve_env({"T": "$(exit 3)"})


def test_failed_env_command_marks_server_failed(manager, tmp_path):
    manager.ensure_started({"gh": {"command": sys.executable, "args": [FAKE], "env": {"TOKEN": "$(exit 1)"}}}, cwd=str(tmp_path))
    assert not manager.status["gh"].ok and "TOKEN" in manager.status["gh"].error


def test_allow_rules_accept_globs(monkeypatch):
    monkeypatch.setattr("core.settings.load_settings", lambda: {"allow_mcp_tools": ["mcp__github__list_*", "mcp__github__issue_read"]})
    assert security.is_mcp_tool_allowed("mcp__github__list_issues")
    assert security.is_mcp_tool_allowed("mcp__github__issue_read")
    assert not security.is_mcp_tool_allowed("mcp__github__issue_write")


def test_servers_start_lazily_only_for_matching_patterns(tmp_path, monkeypatch):
    from core.mcp import MCPManager
    m = MCPManager()
    config = {"fake": {"command": sys.executable, "args": [FAKE]},
              "other": {"command": sys.executable, "args": [FAKE]}}
    try:
        assert m.ensure_for_patterns([], config) == [] and m.status == {}
        tools = m.ensure_for_patterns(["mcp__fake__echo"], config)
        assert [t.name for t in tools] == ["mcp__fake__echo", "mcp__fake__add", "mcp__fake__fail"]
        assert set(m.status) == {"fake"}  # "other" not started
        assert m.ensure_for_patterns(["mcp__fake__*"], config) == tools  # started once
    finally:
        m.shutdown()


def test_mcp_tools_are_listed_compactly(monkeypatch):
    from agents.runtime import compact_signature
    schema = {"type": "object", "properties": {"element": {}, "ref": {}, "button": {}}, "required": ["element", "ref"]}
    assert compact_signature(schema) == "(element*, ref*, button)"


def test_tool_schema_tool_returns_full_schema(manager, tmp_path):
    from core.kernel import kernel
    from tools.registry import ToolRegistry
    from tools.tool_schema import ToolSchemaTool
    had, prev = "tool_registry" in kernel._services, kernel._services.get("tool_registry")
    reg = ToolRegistry()
    for t in manager.ensure_started({"fake": {"command": sys.executable, "args": [FAKE]}}, cwd=str(tmp_path)):
        reg.register(t)
    kernel.register_service("tool_registry", reg)
    try:
        out = ToolSchemaTool().execute({"tool": "mcp__fake__echo"})
        assert '"required": [' in out and "text" in out
        assert ToolSchemaTool().execute({"tool": "nope"}).startswith("Error")
    finally:
        if had:
            kernel.register_service("tool_registry", prev)
        else:
            kernel._services.pop("tool_registry", None)
