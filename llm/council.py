"""
/council: ask several models the same question in parallel, then have a judge model
compare their answers and write one best answer.

Different free models get different things wrong, so where they agree an answer is
more likely right, and where they disagree the judge has to check the claim instead
of passing on one model's guess. Costs one request per member plus one for the judge
-- with OpenRouter's 50 free requests a day, that's why it's a command and not the
default for every turn. Members have no tools: this is for questions answered from
knowledge and the conversation (design choices, explanations, reviews of pasted code),
not for work in the workspace.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core.events import event_bus, EventType
from core.kernel import kernel

MEMBER_SYSTEM = (
    "Answer the user's question accurately and completely, in Markdown. Think it through before "
    "answering. If you are unsure of a fact, say so instead of guessing."
)

JUDGE_INSTRUCTIONS = """You are the judge of a council of AI models. Each answered the question below
independently. Write the single best answer to the question for the user:
- Where the answers agree, that is likely right -- but still check it.
- Where they disagree, work out which is correct and say why in a sentence.
- Drop anything wrong, unsupported or off-topic; keep the best explanation and examples.
- Answer the user directly. Don't describe the council or refer to "Answer A" in the body.
End with one line starting "Agreement:" saying whether the answers agreed, and on what they differed."""


@dataclass
class MemberAnswer:
    model: str
    answer: str = ""
    error: str = ""
    seconds: float = 0.0


@dataclass
class CouncilResult:
    answer: str
    judge_model: str = ""
    members: List[MemberAnswer] = field(default_factory=list)


class CouncilError(RuntimeError):
    pass


def _ask_member(default_provider: str, model: str, prompt: str, timeout: float) -> MemberAnswer:
    from llm.runtime import note_free_model_failure, split_model
    start = time.time()
    try:
        provider_name, provider_model = split_model(model, default_provider)
        text = kernel.get_provider(provider_name).complete(
            [{"role": "system", "content": MEMBER_SYSTEM}, {"role": "user", "content": prompt}],
            model=provider_model, temperature=0.3, timeout=timeout,
        ) or ""
        if not text.strip():
            raise ValueError("empty answer")
        event_bus.publish(EventType.LLM_RESPONSE,
                          {"model": model, "provider": "council", "agent": "Council", "response_len": len(text)},
                          source="Council")
        return MemberAnswer(model, answer=text.strip(), seconds=time.time() - start)
    except Exception as e:
        note_free_model_failure(model, e)
        return MemberAnswer(model, error=str(e)[:160], seconds=time.time() - start)


def run_council(
    question: str,
    settings: dict,
    context_lines: Optional[List[str]] = None,
    on_member: Optional[Callable[[MemberAnswer], None]] = None,
) -> CouncilResult:
    from llm.router import ModelRouter
    from llm.runtime import cooling_down, free_models_paused, is_openrouter_free, provider_configured

    members = [m for m in (settings.get("council_models") or []) if not cooling_down(m) and provider_configured(m)]
    if free_models_paused():
        members = [m for m in members if not is_openrouter_free(m)]
    if not members:
        raise CouncilError("No council model is available right now (today's free requests are used up, "
                           "or every council model just failed). Set council_models in .sai/settings.json.")

    prompt = question
    if context_lines:
        prompt = "Recent conversation, for context:\n" + "\n".join(context_lines) + f"\n\nQuestion: {question}"

    provider_name, _ = ModelRouter.route("Council")
    timeout = float(settings.get("council_timeout", 90))

    results: List[MemberAnswer] = []
    with ThreadPoolExecutor(max_workers=len(members)) as pool:
        futures = [pool.submit(_ask_member, provider_name, m, prompt, timeout) for m in members]
        for future in as_completed(futures):
            member = future.result()
            results.append(member)
            if on_member:
                on_member(member)
    results.sort(key=lambda m: members.index(m.model))

    answered = [m for m in results if m.answer]
    if not answered:
        raise CouncilError("Every council model failed: " + "; ".join(f"{m.model}: {m.error}" for m in results))
    if len(answered) == 1:
        return CouncilResult(answered[0].answer, judge_model="", members=results)

    # Anonymous labels, so the judge weighs the answers rather than the model names.
    labeled = "\n\n".join(f"### Answer {chr(65 + i)}\n{m.answer}" for i, m in enumerate(answered))
    judge_prompt = f"{JUDGE_INSTRUCTIONS}\n\n## Question\n{prompt}\n\n## Answers\n{labeled}"
    judge = settings.get("council_judge") or settings.get("reasoner_model") or answered[0].model

    seen = []
    capture = lambda e: seen.append(e.data.get("model"))
    event_bus.subscribe(EventType.LLM_RESPONSE, capture)
    try:
        # Through LLMRuntime, so a busy free judge falls back like any other call (and streams).
        final = kernel.get_service("llm_runtime").query(judge_prompt, task_kind="Council", model=judge,
                                                        temperature=0.1)
    finally:
        event_bus.unsubscribe(EventType.LLM_RESPONSE, capture)
    return CouncilResult(final.strip(), judge_model=seen[-1] if seen else judge, members=results)
