import pytest

from core.kernel import kernel
from execution.engine import ExecutionEngine
from execution.permissions import PermissionRequestRequired
from tools.base import BaseTool
from tools.registry import ToolRegistry


class RaisingTool(BaseTool):
    """A stub tool that raises PermissionRequestRequired inside execute(),
    the way TerminalTool/PythonTool do for risky commands/scripts."""

    @property
    def name(self):
        return "raising_tool"

    @property
    def description(self):
        return "stub"

    @property
    def permissions(self):
        return []

    @property
    def schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, args):
        raise PermissionRequestRequired(path="some-command", reason="stub risky action")


class NoOpTool(BaseTool):
    """A stub tool whose execute() should never actually be called in the
    pre-flight-path-check test below."""

    @property
    def name(self):
        return "noop_tool"

    @property
    def description(self):
        return "stub"

    @property
    def permissions(self):
        return []

    @property
    def schema(self):
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    def execute(self, args):
        return "should not have been called"


@pytest.fixture
def stub_tool_registry():
    had_previous = "tool_registry" in kernel._services
    previous = kernel._services.get("tool_registry")

    registry = ToolRegistry()
    registry.register(RaisingTool())
    registry.register(NoOpTool())
    kernel.register_service("tool_registry", registry)
    try:
        yield registry
    finally:
        if had_previous:
            kernel.register_service("tool_registry", previous)
        else:
            kernel._services.pop("tool_registry", None)


def test_permission_request_required_propagates_from_tool_execute(stub_tool_registry):
    """
    Regression test: ExecutionEngine.execute() must not swallow a
    PermissionRequestRequired raised inside tool.execute() into a generic
    failed ToolResult -- it must propagate to the caller.
    """
    engine = ExecutionEngine()
    with pytest.raises(PermissionRequestRequired):
        engine.execute({"tool": "raising_tool", "args": {}})


def test_permission_request_required_from_preflight_path_check(stub_tool_registry, tmp_workspace):
    engine = ExecutionEngine()
    outside_path = "/definitely/outside/the/sandbox/file.txt"
    with pytest.raises(PermissionRequestRequired):
        engine.execute({"tool": "noop_tool", "args": {"path": outside_path}})


def test_relative_path_is_checked_against_workspace_not_cwd(stub_tool_registry, tmp_workspace, tmp_path_factory, monkeypatch):
    """
    Regression: launching `sai` from ~ made the pre-flight check resolve a
    relative path like "query_api.py" against the process cwd, so every
    relative write was flagged as outside the sandbox.
    """
    monkeypatch.chdir(tmp_path_factory.mktemp("launched_from_elsewhere"))
    result = ExecutionEngine().execute({"tool": "noop_tool", "args": {"path": "query_api.py"}})
    assert result.stdout == "should not have been called"  # i.e. it ran, no permission prompt


def test_outside_path_request_carries_absolute_path(stub_tool_registry, tmp_workspace):
    with pytest.raises(PermissionRequestRequired) as exc:
        ExecutionEngine().execute({"tool": "noop_tool", "args": {"path": "../escape.txt"}})
    assert exc.value.path == str((tmp_workspace / ".." / "escape.txt").resolve())
    assert exc.value.kind == "path"


def test_tool_output_has_secrets_masked(stub_tool_registry, tmp_workspace, monkeypatch):
    # load_settings() re-exports the real Keychain keys into os.environ; stub it
    # so the fake key set below is the one in effect.
    monkeypatch.setattr("core.settings.load_settings", lambda: {})
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value-123")

    class LeakyTool(NoOpTool):
        @property
        def name(self):
            return "leaky_tool"

        def execute(self, args):
            return "OPENROUTER_API_KEY=sk-or-secret-value-123"

    stub_tool_registry.register(LeakyTool())
    result = ExecutionEngine().execute({"tool": "leaky_tool", "args": {}})
    assert "sk-or-secret-value-123" not in result.stdout
