from types import SimpleNamespace

import pytest

from llm import tracker as tracker_mod
from llm.runtime import BudgetExceeded, check_budget
from llm.tracker import format_usd, spent_today, token_tracker


def test_add_usage_reads_openrouter_cost():
    before = (token_tracker.cost_usd, token_tracker.unpriced_calls)
    token_tracker.add_usage(SimpleNamespace(prompt_tokens=100, completion_tokens=20, cost=0.0042))
    assert token_tracker.cost_usd - before[0] == pytest.approx(0.0042)
    token_tracker.add_usage(SimpleNamespace(prompt_tokens=1, completion_tokens=1))  # no price reported
    assert token_tracker.unpriced_calls - before[1] == 1
    assert spent_today() == pytest.approx(0.0042)


def test_cost_in_pydantic_extra_fields():
    from openai.types import CompletionUsage
    usage = CompletionUsage.model_validate({"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11, "cost": 2.55e-06})
    before = token_tracker.cost_usd
    token_tracker.add_usage(usage)
    assert token_tracker.cost_usd - before == pytest.approx(2.55e-06)


def test_format_usd():
    assert format_usd(0) == "$0"
    assert format_usd(0.00255) == "$0.0026"
    assert format_usd(1.5) == "$1.50"


def test_budget_blocks_paid_calls_but_not_free(monkeypatch):
    tracker_mod._record_spend(2.0)
    check_budget({"daily_budget_usd": 0})          # no cap
    check_budget({"daily_budget_usd": 5})          # under the cap
    with pytest.raises(BudgetExceeded, match="Daily budget reached"):
        check_budget({"daily_budget_usd": 1.5})

    from core.kernel import kernel
    from llm.runtime import LLMRuntime
    calls = []

    class P:
        def stream(self, messages, model, temperature=0.2, **kw):
            calls.append(model)
            yield "{}"

    monkeypatch.setattr("core.settings.load_settings", lambda: {"daily_budget_usd": 1.0, "free_fallback_model": "paid"})
    monkeypatch.setattr("llm.router.ModelRouter.route", staticmethod(lambda kind: ("budgetprov", "x")))
    kernel.register_provider("budgetprov", P())
    try:
        assert LLMRuntime().query("p", task_kind="Jev", model="a:free") == "{}"  # free still works
        with pytest.raises(BudgetExceeded):
            LLMRuntime().query("p", task_kind="Coder", model="paid/model")
        assert calls == ["a:free"]
    finally:
        kernel._providers.pop("budgetprov", None)
