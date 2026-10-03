from jev.decision import JevRouter, SYSTEM_PROMPT


def test_system_prompt_forbids_direct_answer_for_time_sensitive_questions():
    # Regression guard: Jev was observed routing "what is the latest <product>
    # model" to direct_answer, which answers from stale training-data memory
    # instead of live search -- confidently wrong rather than merely slow.
    normalized = " ".join(SYSTEM_PROMPT.lower().split())
    assert "never use this route" in normalized
    assert "latest" in normalized
    assert "researcher" in normalized  # the live-web research agent


def test_parse_clean_json():
    router = JevRouter()
    assert router._parse('{"route": "direct_answer", "answer": "hi"}') == {
        "route": "direct_answer",
        "answer": "hi",
    }


def test_parse_fenced_json():
    router = JevRouter()
    content = '```json\n{"route": "single_agent", "agent": "Coder"}\n```'
    assert router._parse(content) == {"route": "single_agent", "agent": "Coder"}


def test_parse_json_buried_in_prose():
    router = JevRouter()
    content = 'Sure! Here you go: {"route": "full_orchestrator"} thanks'
    assert router._parse(content) == {"route": "full_orchestrator"}


def test_parse_garbage_returns_none():
    router = JevRouter()
    assert router._parse("not json at all") is None


def test_decide_empty_input_falls_back():
    router = JevRouter()
    decision = router.decide("", ["Coder", "Planner"])
    assert decision.route == "full_orchestrator"
    assert decision.fallback is True


def test_decide_unknown_route_falls_back(fake_llm_runtime):
    fake_llm_runtime.response = '{"route": "do_something_weird", "reasoning": "x", "confidence": 0.9}'
    router = JevRouter()
    decision = router.decide("do a thing", ["Coder", "Planner"])
    assert decision.route == "full_orchestrator"
    assert decision.fallback is True


def test_decide_unknown_agent_name_falls_back(fake_llm_runtime):
    fake_llm_runtime.response = '{"route": "single_agent", "agent": "NotARealAgent", "confidence": 0.9}'
    router = JevRouter()
    decision = router.decide("do a narrow task", ["Coder", "Planner"])
    assert decision.route == "full_orchestrator"
    assert decision.fallback is True


def test_decide_single_agent_matches_case_insensitively(fake_llm_runtime):
    fake_llm_runtime.response = '{"route": "single_agent", "agent": "coder", "confidence": 0.8, "reasoning": "narrow task"}'
    router = JevRouter()
    decision = router.decide("write a small function", ["Coder", "Planner"])
    assert decision.route == "single_agent"
    assert decision.agent == "Coder"
    assert decision.fallback is False


def test_decide_direct_answer_requires_answer_text(fake_llm_runtime):
    fake_llm_runtime.response = '{"route": "direct_answer", "answer": null}'
    router = JevRouter()
    decision = router.decide("hi", ["Coder", "Planner"])
    assert decision.route == "full_orchestrator"
    assert decision.fallback is True


def test_decide_llm_exception_falls_back(fake_llm_runtime):
    fake_llm_runtime.raises = RuntimeError("provider is down")
    router = JevRouter()
    decision = router.decide("hello", ["Coder", "Planner"])
    assert decision.route == "full_orchestrator"
    assert decision.fallback is True
    assert "provider is down" in decision.reasoning


import pytest
from jev.decision import quick_math


@pytest.mark.parametrize("text,expected", [
    ("What is 17 * 23 * 41? Just the number.", "16031"),
    ("17*23", "391"),
    ("calculate (2^10)/4", "256"),
    ("what is 10 / 4?", "5/2 ≈ 2.5"),
    ("what is 1,000 x 3", "3000"),
    ("17 - 20", "-3"),
])
def test_quick_math_answers_plain_arithmetic_exactly(text, expected):
    assert quick_math(text).endswith(f"**{expected}**")


@pytest.mark.parametrize("text", [
    "what is a monad?", "what is python 3.12?", "what is 7 * 8 in binary?",
    "how many 3s in 333", "what is 2026-10-01", "42", "explain 2 + 2 to a child",
])
def test_quick_math_leaves_everything_else_to_the_llm(text):
    assert quick_math(text) is None


def test_jev_answers_arithmetic_without_an_llm_call(fake_llm_runtime):
    decision = JevRouter().decide("what is 17 * 23 * 41?", ["Researcher"])
    assert decision.route == "direct_answer" and "16031" in decision.answer
    assert fake_llm_runtime.calls == []


def test_jev_sees_what_each_agent_can_do(monkeypatch):
    from core.kernel import kernel
    from agents.runtime import AgentRuntime
    from jev.decision import JevRouter, agent_capabilities
    runner = AgentRuntime("Runner", "runs things", "", "m", tools=["read_file", "execute_command"])
    reader = AgentRuntime("Reader", "reads things", "", "m", tools=["read_file", "web_search", "mcp__context7__*"])
    monkeypatch.setattr(kernel, "_agents", {"Runner": runner, "Reader": reader})
    assert "runs commands" in agent_capabilities("Runner") and "can NOT edit files" in agent_capabilities("Runner")
    assert "can NOT run commands" in agent_capabilities("Reader") and "uses context7 tools" in agent_capabilities("Reader")
    prompt = JevRouter()._build_prompt("run it", ["Runner", "Reader"], None, {"Runner": "runs things", "Reader": "reads things"})
    assert "- Reader: reads things [reads code, searches the web, uses context7 tools; can NOT run commands, edit files]" in prompt


def test_direct_answer_about_a_workspace_file_goes_to_an_agent(tmp_workspace, fake_llm_runtime):
    from jev.decision import JevRouter
    (tmp_workspace / "error.png").write_bytes(b"png")
    fake_llm_runtime.response = '{"route": "direct_answer", "answer": "I don\'t have access to that image.", "reasoning": "x", "confidence": 0.9}'
    decision = JevRouter().decide("what does error.png say?", ["Researcher", "Coder"])
    assert decision.route == "single_agent" and decision.agent == "Researcher"
    # ...but a file name that isn't in the workspace is just conversation.
    decision = JevRouter().decide("what is a .png file?", ["Researcher", "Coder"])
    assert decision.route == "direct_answer"
