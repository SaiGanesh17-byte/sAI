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
