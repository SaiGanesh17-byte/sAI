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
