import pytest

from core.kernel import kernel
from core import security as security_module


@pytest.fixture
def tmp_workspace(tmp_path):
    """
    Points core.security.CURRENT_WORKSPACE at a pytest tmp_path for the
    duration of a test, so filesystem-tool tests never touch the real
    project directory. Restores the previous workspace afterward.
    """
    previous = security_module.CURRENT_WORKSPACE
    security_module.set_current_workspace(str(tmp_path))
    try:
        yield tmp_path
    finally:
        security_module.CURRENT_WORKSPACE = previous


class FakeLLMRuntime:
    """
    Minimal stand-in for llm.runtime.LLMRuntime. Returns a canned response,
    or raises a canned exception, without making any real network call.
    """

    def __init__(self, response: str = "", raises: Exception = None):
        self.response = response
        self.raises = raises
        self.calls = []

    def query(self, prompt: str, task_kind: str, temperature: float = 0.2, **kwargs):
        self.calls.append({"prompt": prompt, "task_kind": task_kind, "temperature": temperature, **kwargs})
        if self.raises:
            raise self.raises
        return self.response


@pytest.fixture
def fake_llm_runtime():
    """
    Registers a FakeLLMRuntime into the process-wide kernel singleton for the
    duration of a test, restoring whatever was previously registered (or
    unregistering it) afterward, so tests stay isolated from each other.
    """
    had_previous = "llm_runtime" in kernel._services
    previous = kernel._services.get("llm_runtime")

    fake = FakeLLMRuntime()
    kernel.register_service("llm_runtime", fake)
    try:
        yield fake
    finally:
        if had_previous:
            kernel.register_service("llm_runtime", previous)
        else:
            kernel._services.pop("llm_runtime", None)


@pytest.fixture(autouse=True)
def _isolate_free_model_pause(tmp_path, monkeypatch):
    """Keep the real .sai/free_models_paused_until (written when the OpenRouter
    daily free cap is hit) from changing test behaviour."""
    import llm.runtime as rt
    monkeypatch.setattr(rt, "FREE_PAUSE_FILE", tmp_path / "free_models_paused_until")
    monkeypatch.setitem(rt._FREE_MODELS_PAUSED_UNTIL, "t", -1.0)


@pytest.fixture(autouse=True)
def _isolate_spend_file(tmp_path, monkeypatch):
    """Keep tests from writing to the real .sai/spend.json (daily budget tracking)."""
    import llm.tracker as tracker
    monkeypatch.setattr(tracker, "SPEND_FILE", tmp_path / "spend.json")


@pytest.fixture(autouse=True)
def _isolate_project_memory(tmp_path, monkeypatch):
    """Keep tests from reading or writing the real per-project memory stores."""
    import memory.graphiti as graphiti
    monkeypatch.setattr(graphiti, "PROJECTS_ROOT", tmp_path / "projects")


@pytest.fixture(autouse=True)
def _no_real_mcp_servers(monkeypatch):
    """Agents start MCP servers lazily from the user's real settings; tests never should.
    (tests/test_mcp.py builds its own MCPManager against a fake server.)"""
    from core.mcp import mcp_manager
    monkeypatch.setattr(mcp_manager, "ensure_for_patterns", lambda patterns, config=None: [])


@pytest.fixture(autouse=True)
def _no_os_sandbox(monkeypatch):
    """Commands in tests run unsandboxed, the same on macOS and Linux CI.
    tests/test_sandbox.py turns the real sandbox back on where it's supported."""
    import core.sandbox as sandbox
    monkeypatch.setattr(sandbox, "sandbox_supported", lambda: False)
