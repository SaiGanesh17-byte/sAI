import pytest

from core.events import event_bus, EventType
from core.kernel import kernel
from llm.runtime import LLMRuntime, is_free_model, model_candidates


def test_is_free_model():
    assert is_free_model("qwen/qwen3.8-27b:free")
    assert is_free_model("openrouter/free")
    assert not is_free_model("openai/gpt-4o-mini")
    assert not is_free_model("")


def test_candidates():
    s = {"free_fallback_model": "openai/gpt-4o-mini", "use_free_models": True}
    assert model_candidates("openrouter/free", s) == ["openrouter/free", "openai/gpt-4o-mini"]
    assert model_candidates("qwen/qwen3-coder-plus", s) == ["qwen/qwen3-coder-plus"]  # paid: no fallback
    assert model_candidates("x:free", {**s, "use_free_models": False}) == ["openai/gpt-4o-mini"]


class FlakyProvider:
    def __init__(self, fail_models):
        self.fail_models = set(fail_models)
        self.calls = []

    def stream(self, messages, model, temperature=0.2, **kw):
        self.calls.append(model)
        if model in self.fail_models:
            raise RuntimeError("Error code: 429 - rate limited")
        yield '{"ok": "'
        yield model + '"}'


@pytest.fixture
def runtime_with(monkeypatch):
    def setup(provider, **settings):
        base = {"use_free_models": True, "free_fallback_model": "openai/gpt-4o-mini", "stream_responses": True}
        monkeypatch.setattr("core.settings.load_settings", lambda: {**base, **settings})
        monkeypatch.setattr("llm.router.ModelRouter.route", staticmethod(lambda kind: ("flaky", "unused")))
        kernel.register_provider("flaky", provider)
        return LLMRuntime()
    yield setup
    kernel._providers.pop("flaky", None)


def test_rate_limited_free_model_falls_back_once(runtime_with):
    provider = FlakyProvider({"openrouter/free"})
    fallbacks = []
    cb = lambda e: fallbacks.append((e.data["from"], e.data["to"]))
    event_bus.subscribe(EventType.LLM_FALLBACK, cb)
    try:
        out = runtime_with(provider).query("p", task_kind="Jev", model="openrouter/free")
    finally:
        event_bus._listeners[EventType.LLM_FALLBACK.value].remove(cb)
    assert out == '{"ok": "openai/gpt-4o-mini"}'
    assert provider.calls == ["openrouter/free", "openai/gpt-4o-mini"]  # no slow retry loop on the free model
    assert fallbacks == [("openrouter/free", "openai/gpt-4o-mini")]


def test_working_free_model_is_used(runtime_with):
    provider = FlakyProvider(set())
    assert runtime_with(provider).query("p", task_kind="Jev", model="openrouter/free") == '{"ok": "openrouter/free"}'
    assert provider.calls == ["openrouter/free"]


def test_master_switch_skips_free_models(runtime_with):
    provider = FlakyProvider(set())
    runtime_with(provider, use_free_models=False).query("p", task_kind="Writer", model="openrouter/free")
    assert provider.calls == ["openai/gpt-4o-mini"]


def test_router_uses_compact_and_jev_models(monkeypatch):
    from llm.router import ModelRouter
    monkeypatch.setattr("llm.router.load_settings", lambda: {"provider": "openrouter", "jev_model": "openrouter/free",
                                                              "compact_model": "x:free", "reasoner_model": "openai/gpt-4o-mini"})
    assert ModelRouter.route("Jev") == ("openrouter", "openrouter/free")
    assert ModelRouter.route("Compactor") == ("openrouter", "x:free")
    assert ModelRouter.route("Researcher") == ("openrouter", "openai/gpt-4o-mini")


def test_chain_tries_another_free_model_before_paying():
    s = {"use_free_models": True, "free_fallback_model": "paid", "free_model_retries": 1,
         "free_model_chain": ["a:free", "b:free", "c:free"]}
    assert model_candidates("a:free", s) == ["a:free", "b:free", "paid"]
    assert model_candidates("b:free", s) == ["b:free", "a:free", "paid"]
    assert model_candidates("a:free", {**s, "free_model_retries": 0}) == ["a:free", "paid"]


class JunkProvider(FlakyProvider):
    """The first model "succeeds" with non-JSON junk, as OpenRouter's safety classifier did."""

    def stream(self, messages, model, temperature=0.2, **kw):
        self.calls.append((model, kw.get("timeout")))
        if model == "openrouter/free":
            yield "User Safety: safe"
        else:
            yield '{"route": "direct_answer"}'


def test_junk_from_free_model_falls_back_when_json_expected(runtime_with):
    provider = JunkProvider(set())
    out = runtime_with(provider, free_model_timeout=12).query(
        "p", task_kind="Jev", model="openrouter/free", response_format={"type": "json_object"})
    assert out == '{"route": "direct_answer"}'
    assert provider.calls == [("openrouter/free", 12.0), ("openai/gpt-4o-mini", None)]  # timeout only on the free try


def test_junk_is_returned_when_no_json_was_requested(runtime_with):
    provider = JunkProvider(set())
    assert runtime_with(provider).query("p", task_kind="Writer", model="openrouter/free") == "User Safety: safe"


def test_free_models_never_get_response_format(runtime_with):
    seen = []

    class Recorder(FlakyProvider):
        def stream(self, messages, model, temperature=0.2, **kw):
            seen.append((model, "response_format" in kw))
            yield '{"ok": 1}'

    runtime_with(Recorder(set())).query("p", task_kind="Jev", model="x:free", response_format={"type": "json_object"})
    runtime_with(Recorder(set())).query("p", task_kind="Coder", model="paid/model", response_format={"type": "json_object"})
    assert seen == [("x:free", False), ("paid/model", True)]


def test_daily_free_limit_pauses_free_models(runtime_with, monkeypatch, tmp_path):
    import time
    import llm.runtime as rt
    monkeypatch.setitem(rt._FREE_MODELS_PAUSED_UNTIL, "t", -1.0)
    monkeypatch.setattr(rt, "FREE_PAUSE_FILE", tmp_path / "pause")
    reset_ms = int((time.time() + 3600) * 1000)

    class DailyCap(FlakyProvider):
        def stream(self, messages, model, temperature=0.2, **kw):
            self.calls.append(model)
            if model.endswith(":free"):
                raise RuntimeError("Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day', "
                                   f"'metadata': {{'headers': {{'X-RateLimit-Reset': '{reset_ms}'}}}}}}}}")
            yield '{"ok": 1}'

    provider = DailyCap(set())
    runtime = runtime_with(provider)
    runtime.query("p", task_kind="Jev", model="a:free")
    assert provider.calls == ["a:free", "openai/gpt-4o-mini"]
    assert rt.free_models_paused()

    provider.calls.clear()
    runtime.query("p", task_kind="Jev", model="a:free")
    assert provider.calls == ["openai/gpt-4o-mini"]  # no wasted free attempt until the reset

    # A new process (fresh in-memory state) reads the pause back from disk.
    monkeypatch.setitem(rt._FREE_MODELS_PAUSED_UNTIL, "t", 0.0)
    assert rt.free_models_paused()


def test_paused_free_models_are_skipped_mid_chain(runtime_with, monkeypatch, tmp_path):
    import time
    import llm.runtime as rt
    monkeypatch.setattr(rt, "FREE_PAUSE_FILE", tmp_path / "pause")
    monkeypatch.setitem(rt._FREE_MODELS_PAUSED_UNTIL, "t", time.time() + 600)
    provider = FlakyProvider(set())
    # Even if a chain still lists free models, a paused cap skips straight to the paid one.
    runtime_with(provider, free_model_chain=["a:free", "b:free"]).query("p", task_kind="Jev", model="a:free")
    assert provider.calls == ["openai/gpt-4o-mini"]


def test_callers_temperature_is_used_unless_overridden(runtime_with):
    seen = []

    class Recorder(FlakyProvider):
        def stream(self, messages, model, temperature=0.2, **kw):
            seen.append(temperature)
            yield "{}"

    runtime_with(Recorder(set()), temperature=0.2).query("p", task_kind="Jev", model="paid", temperature=0.0)
    runtime_with(Recorder(set()), temperature_override=0.7).query("p", task_kind="Jev", model="paid", temperature=0.0)
    assert seen == [0.0, 0.7]  # the legacy 'temperature' key no longer overrides
