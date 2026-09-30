from types import SimpleNamespace

import pytest

from core.events import event_bus, EventType
from llm.providers.base import stream_chat
from llm.tracker import token_tracker
from ui.activity import partial_json_string


def _chunk(text=None, usage=None):
    choices = [SimpleNamespace(delta=SimpleNamespace(content=text))] if text is not None else []
    return SimpleNamespace(choices=choices, usage=usage)


class FakeClient:
    def __init__(self, chunks, reject_stream_options=False):
        self.chunks = chunks
        self.reject = reject_stream_options
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject and "stream_options" in kwargs:
            raise ValueError("unsupported parameter: stream_options")
        return iter(self.chunks)


@pytest.fixture
def tracker_snapshot():
    before = (token_tracker.input_tokens, token_tracker.output_tokens)
    yield lambda: (token_tracker.input_tokens - before[0], token_tracker.output_tokens - before[1])


def test_stream_chat_yields_text_and_records_real_usage(tracker_snapshot):
    client = FakeClient([_chunk("Hel"), _chunk("lo"), _chunk(usage=SimpleNamespace(prompt_tokens=50, completion_tokens=2))])
    out = list(stream_chat(client, [{"role": "user", "content": "hi"}], "m"))
    assert out == ["Hel", "lo"]
    assert client.calls[0]["stream_options"] == {"include_usage": True}
    assert tracker_snapshot() == (50, 2)


def test_stream_chat_falls_back_and_estimates_when_usage_unsupported(tracker_snapshot):
    client = FakeClient([_chunk("x" * 40)], reject_stream_options=True)
    out = list(stream_chat(client, [{"role": "user", "content": "y" * 400}], "m"))
    assert out == ["x" * 40]
    assert "stream_options" not in client.calls[-1]
    assert tracker_snapshot() == (100, 10)  # ~4 chars/token, not silently zero


def test_llm_runtime_publishes_deltas_and_returns_full_text(monkeypatch):
    from core.kernel import kernel
    from llm.runtime import LLMRuntime

    class StreamingProvider:
        def stream(self, messages, model, temperature=0.2, **kw):
            yield from ['{"summary": ', '"hi"}']

    monkeypatch.setattr("llm.router.ModelRouter.route", staticmethod(lambda kind: ("fakeprov", "m")))
    previous = kernel._providers.get("fakeprov") if hasattr(kernel, "_providers") else None
    kernel.register_provider("fakeprov", StreamingProvider())
    deltas = []
    cb = lambda e: deltas.append(e.data["delta"])
    event_bus.subscribe(EventType.LLM_DELTA, cb)
    try:
        result = LLMRuntime().query("prompt", task_kind="Coder")
    finally:
        event_bus._listeners[EventType.LLM_DELTA.value].remove(cb)
        if hasattr(kernel, "_providers"):
            kernel._providers.pop("fakeprov", None)
            if previous is not None:
                kernel._providers["fakeprov"] = previous

    assert result == '{"summary": "hi"}'
    assert deltas == ['{"summary": ', '"hi"}']


@pytest.mark.parametrize("text,expected", [
    ('{"summary": "Reading app', "Reading app"),
    ('{"a": 1, "summary": "x\\ny \\"q\\" done", "b"', 'x\ny "q" done'),
    ('{"summary": "trailing escape \\', "trailing escape "),
    ('{"reasoning": ["x"]', None),
    ('{"summary": "caf\\u00e9"}', "café"),
    ('{"summary": "half unicode \\u00', "half unicode "),
])
def test_partial_json_string(text, expected):
    assert partial_json_string(text, "summary") == expected
