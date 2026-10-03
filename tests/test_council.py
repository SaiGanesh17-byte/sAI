import pytest

from core.kernel import kernel
from llm import runtime as llm_runtime
from llm.attribution import ModelLog, short_model
from llm.council import CouncilError, run_council


class FakeProvider:
    def __init__(self, answers):
        self.answers = answers  # model -> answer text, or an Exception to raise
        self.seen = []

    def complete(self, messages, model, temperature=0.2, **kw):
        self.seen.append((model, messages[-1]["content"]))
        answer = self.answers[model]
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeRuntime:
    def __init__(self):
        self.prompts = []

    def query(self, prompt, task_kind, model=None, **kw):
        from core.events import event_bus, EventType
        self.prompts.append(prompt)
        event_bus.publish(EventType.LLM_RESPONSE, {"model": model, "agent": task_kind}, source="test")
        return "merged answer\nAgreement: yes"


@pytest.fixture
def council(monkeypatch):
    monkeypatch.setattr("llm.router.ModelRouter.route", staticmethod(lambda kind: ("fake", "unused")))
    monkeypatch.setattr(llm_runtime, "free_models_paused", lambda: False)
    llm_runtime._MODEL_COOLDOWN.clear()
    old = kernel._services.get("llm_runtime")
    judge = FakeRuntime()
    kernel._services["llm_runtime"] = judge

    def setup(answers):
        provider = FakeProvider(answers)
        kernel.register_provider("fake", provider)
        return provider, judge
    yield setup
    kernel._providers.pop("fake", None)
    kernel._services["llm_runtime"] = old
    llm_runtime._MODEL_COOLDOWN.clear()


SETTINGS = {"council_models": ["a:free", "b:free", "c:free"], "council_judge": "j:free"}


def test_judge_merges_member_answers_anonymously(council):
    provider, judge = council({"a:free": "four", "b:free": "4", "c:free": "five"})
    result = run_council("2+2?", SETTINGS)
    assert result.answer.startswith("merged answer")
    assert result.judge_model == "j:free"
    assert [m.model for m in result.members] == ["a:free", "b:free", "c:free"]
    assert "### Answer A\nfour" in judge.prompts[0] and "### Answer C\nfive" in judge.prompts[0]
    assert "a:free" not in judge.prompts[0]  # the judge doesn't see model names


def test_failed_member_is_reported_and_cooled_down(council):
    provider, judge = council({"a:free": RuntimeError("429 rate limited"), "b:free": "x", "c:free": "y"})
    seen = []
    result = run_council("q", SETTINGS, on_member=seen.append)
    assert {m.model for m in seen} == {"a:free", "b:free", "c:free"}
    assert result.members[0].error.startswith("429")
    assert llm_runtime.cooling_down("a:free")


def test_single_survivor_skips_the_judge(council):
    provider, judge = council({"a:free": RuntimeError("down"), "b:free": "only one", "c:free": RuntimeError("down")})
    result = run_council("q", SETTINGS)
    assert result.answer == "only one" and result.judge_model == "" and judge.prompts == []


def test_all_members_failing_raises(council):
    council({"a:free": RuntimeError("x"), "b:free": RuntimeError("y"), "c:free": RuntimeError("z")})
    with pytest.raises(CouncilError):
        run_council("q", SETTINGS)


def test_context_reaches_members(council):
    provider, _ = council({"a:free": "1", "b:free": "2", "c:free": "3"})
    run_council("and in Go?", SETTINGS, context_lines=["User: how do I read a file in Python?"])
    assert all("read a file in Python" in prompt and "and in Go?" in prompt for _, prompt in provider.seen)


def test_model_log_attribution(council):
    council({"a:free": "1", "b:free": "2", "c:free": "3"})
    log = ModelLog()
    mark = log.mark()
    run_council("q", SETTINGS)
    summary = ModelLog.summarize(log.since(mark))
    assert "Council → a (free)" in summary and "Council → j (free)" in summary
    assert short_model("openai/gpt-4o-mini") == "gpt-4o-mini"
