import json
from typing import List, Dict, Iterator, Any, Optional
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from core.kernel import kernel
from core.events import event_bus, EventType
from llm.router import ModelRouter

logger = logging.getLogger("sai.llm.runtime")

def is_free_model(model: str) -> bool:
    """OpenRouter's free models: '<id>:free', or the 'openrouter/free' auto-router."""
    return bool(model) and (model.endswith(":free") or model == "openrouter/free")


def looks_like_json_object(text: str) -> bool:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[4:] if cleaned.lower().startswith("json") else cleaned
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return False
    try:
        return isinstance(json.loads(cleaned[start:end + 1]), dict)
    except json.JSONDecodeError:
        return False


# OpenRouter caps free-model requests per account per day (50 without credits). Once
# that cap is hit, every free call is a guaranteed 429, so stop trying until the
# reset time OpenRouter reports instead of paying a wasted round-trip per call.
_FREE_MODELS_PAUSED_UNTIL = {"t": 0.0}


def note_free_model_failure(model: str, error: Exception) -> None:
    if not is_free_model(model):
        return
    text = str(error)
    if "free-models-per-day" not in text and "free_tier_daily" not in text:
        return
    import re
    import time
    match = re.search(r"X-RateLimit-Reset'?:\s*'?(\d{10,13})", text)
    if match:
        reset = int(match.group(1))
        _FREE_MODELS_PAUSED_UNTIL["t"] = reset / 1000 if reset > 10**11 else float(reset)
    else:
        _FREE_MODELS_PAUSED_UNTIL["t"] = time.time() + 3600


def free_models_paused() -> bool:
    import time
    return time.time() < _FREE_MODELS_PAUSED_UNTIL["t"]


def model_candidates(model: str, settings: dict) -> List[str]:
    """
    Models to try, in order. A free model is followed by the other configured
    free models (free_model_chain) and then the paid free_fallback_model; with
    use_free_models off, free models are skipped entirely.
    """
    if not is_free_model(model):
        return [model]
    fallback = settings.get("free_fallback_model") or settings.get("reasoner_model") or ""
    if not settings.get("use_free_models", True) or (free_models_paused() and fallback):
        return [fallback or model]
    chain = [model] + [m for m in (settings.get("free_model_chain") or []) if m != model]
    chain = chain[:1 + max(0, int(settings.get("free_model_retries", 1)))]
    if fallback and fallback not in chain:
        chain.append(fallback)
    return chain


class ContextCompressor:
    """
    Trims the [CONVERSATION HISTORY LOGS] section of a compiled prompt (the only
    genuinely unbounded part -- repo map/memory snapshot are already bounded
    elsewhere) when the whole prompt is estimated to exceed a token budget.
    Token count is estimated at ~4 chars/token (no tokenizer dependency in this
    project) -- approximate by design, not exact.
    """
    CHARS_PER_TOKEN = 4
    TRIM_MARKER = "...[older history trimmed to fit context budget]..."
    HISTORY_HEADER = "[CONVERSATION HISTORY LOGS]"

    @staticmethod
    def compress(prompt: str, max_tokens: int = 32000) -> str:
        max_chars = max_tokens * ContextCompressor.CHARS_PER_TOKEN
        if len(prompt) <= max_chars:
            return prompt

        header_idx = prompt.find(ContextCompressor.HISTORY_HEADER)
        if header_idx == -1:
            # No identifiable history section to trim -- keep the most recent
            # content rather than exceed the budget with no trimming at all.
            return prompt[-max_chars:]

        before = prompt[:header_idx]
        history_section = prompt[header_idx:]
        budget_for_history = max_chars - len(before)

        if budget_for_history <= len(ContextCompressor.TRIM_MARKER):
            # Even everything before the history section alone blows the budget --
            # nothing sensible left to keep from history; prefer the most recent
            # non-history content.
            return before[-max_chars:]

        lines = history_section.splitlines()
        header_line = lines[0] if lines else ContextCompressor.HISTORY_HEADER
        body_lines = lines[1:]

        kept = []
        running_len = len(header_line) + 1 + len(ContextCompressor.TRIM_MARKER) + 1
        for line in reversed(body_lines):
            running_len += len(line) + 1
            if running_len > budget_for_history:
                break
            kept.append(line)
        kept.reverse()

        trimmed_history = header_line + "\n" + ContextCompressor.TRIM_MARKER + "\n" + "\n".join(kept)
        return before + trimmed_history

class ResponseValidator:
    @staticmethod
    def validate(response: str) -> bool:
        """
        Validates response structure.
        """
        return len(response.strip()) > 0

class LLMRuntime:
    """
    LLM Runtime Pipeline manager.
    """
    def __init__(self):
        pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def query(self, prompt: str, task_kind: str, temperature: float = 0.2, model: Optional[str] = None, **kwargs) -> str:
        from core.settings import load_settings
        settings = load_settings()
        token_budget = settings.get("context_token_budget", 32000)
        compressed_prompt = ContextCompressor.compress(prompt, max_tokens=token_budget)
        # Only an explicit override replaces the caller's temperature (Jev routes at
        # 0.0, agents use their YAML value). The old "temperature" key always held
        # 0.2, which silently overrode every call.
        user_temp = settings.get("temperature_override")
        if user_temp is not None:
            temperature = float(user_temp)

        provider_name, routed_model = ModelRouter.route(task_kind)
        # An explicit model (e.g. an agent's own configured model) overrides the
        # task_kind-based guess; task_kind is still used to pick the provider.
        model_name = model or routed_model
        
        event_bus.publish(
            EventType.LLM_REQUEST, 
            {"model": model_name, "provider": provider_name, "prompt_len": len(compressed_prompt)},
            source="LLMRuntime"
        )

        messages = [
            {"role": "user", "content": compressed_prompt}
        ]

        provider = kernel.get_provider(provider_name)

        from core.security import CURRENT_ACTIVITY
        old_status = CURRENT_ACTIVITY.get("status", "thinking")
        CURRENT_ACTIVITY.update({
            "status": "llm_call",
            "agent": task_kind,
            "tool": f"API call to {provider_name}",
            "path": "",
            "command": ""
        })

        try:
            candidates = model_candidates(model_name, settings)
            for attempt, candidate in enumerate(candidates):
                if attempt:
                    # Free models are often rate-limited or briefly unavailable; fall
                    # straight back to the paid model instead of failing the turn.
                    event_bus.publish(
                        EventType.LLM_FALLBACK,
                        {"from": candidates[attempt - 1], "to": candidate, "agent": task_kind},
                        source="LLMRuntime",
                    )
                    event_bus.publish(
                        EventType.LLM_REQUEST,
                        {"model": candidate, "provider": provider_name, "prompt_len": len(compressed_prompt)},
                        source="LLMRuntime",
                    )
                call_kwargs = dict(kwargs)
                if is_free_model(candidate):
                    # Several free models reject structured outputs outright (400). The
                    # JSON check below covers what response_format would have enforced.
                    call_kwargs.pop("response_format", None)
                if is_free_model(candidate) and len(candidates) > 1:
                    # The OpenAI client waits up to 10 minutes by default; a stalled free
                    # model must give way to the fallback quickly. (Applies between
                    # streamed chunks, so a slow-but-progressing answer isn't cut off.)
                    call_kwargs.setdefault("timeout", float(settings.get("free_model_timeout", 30)))
                try:
                    response_content = self._complete(provider, messages, candidate, temperature, task_kind,
                                                      settings.get("stream_responses", True), **call_kwargs)
                    if not ResponseValidator.validate(response_content):
                        raise ValueError("Response failed structure validation checks.")
                    # A free model can "succeed" with junk -- OpenRouter has served a
                    # safety classifier's "User Safety: safe" for a JSON request. When
                    # JSON was asked for and another model is left to try, require it.
                    if (kwargs.get("response_format") and attempt < len(candidates) - 1
                            and not looks_like_json_object(response_content)):
                        raise ValueError(f"expected a JSON object, got: {response_content[:80]!r}")
                    model_name = candidate
                    break
                except Exception as e:
                    note_free_model_failure(candidate, e)
                    if attempt == len(candidates) - 1:
                        raise
                    logger.info(f"{candidate} failed ({str(e)[:120]}); falling back to {candidates[attempt + 1]}")

            event_bus.publish(
                EventType.LLM_RESPONSE,
                {"model": model_name, "provider": provider_name, "response_len": len(response_content)},
                source="LLMRuntime"
            )
            return response_content
        except Exception as e:
            logger.error(f"LLM request error on {provider_name}/{model_name}: {e}")
            event_bus.publish(EventType.ERROR, {"msg": f"LLM request failed: {e}"}, source="LLMRuntime")
            raise e
        finally:
            CURRENT_ACTIVITY.update({
                "status": old_status,
                "agent": task_kind,
                "tool": "",
                "path": "",
                "command": ""
            })

    @staticmethod
    def _complete(provider, messages, model: str, temperature: float, task_kind: str, stream: bool, **kwargs) -> str:
        if not stream:
            return provider.complete(messages, model=model, temperature=temperature, **kwargs)
        # Stream so UIs can show text as it's generated (LLM_DELTA events);
        # the caller still gets the complete string back.
        chunks = []
        for delta in provider.stream(messages, model=model, temperature=temperature, **kwargs):
            chunks.append(delta)
            event_bus.publish(EventType.LLM_DELTA, {"agent": task_kind, "delta": delta}, source="LLMRuntime")
        return "".join(chunks)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def query_with_tools(
        self,
        prompt: str,
        task_kind: str,
        tools: List[Dict[str, Any]],
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Same pipeline as query(), but for the optional real function-calling path
        (agents/runtime.py, gated by settings["use_tool_calling"]). Returns
        {"content": str|None, "tool_calls": [...]} instead of a plain string.
        """
        from core.settings import load_settings
        settings = load_settings()
        token_budget = settings.get("context_token_budget", 32000)
        compressed_prompt = ContextCompressor.compress(prompt, max_tokens=token_budget)
        # Only an explicit override replaces the caller's temperature (Jev routes at
        # 0.0, agents use their YAML value). The old "temperature" key always held
        # 0.2, which silently overrode every call.
        user_temp = settings.get("temperature_override")
        if user_temp is not None:
            temperature = float(user_temp)

        provider_name, routed_model = ModelRouter.route(task_kind)
        model_name = model or routed_model

        event_bus.publish(
            EventType.LLM_REQUEST,
            {"model": model_name, "provider": provider_name, "prompt_len": len(compressed_prompt)},
            source="LLMRuntime"
        )

        messages = [{"role": "user", "content": compressed_prompt}]
        provider = kernel.get_provider(provider_name)

        from core.security import CURRENT_ACTIVITY
        old_status = CURRENT_ACTIVITY.get("status", "thinking")
        CURRENT_ACTIVITY.update({
            "status": "llm_call",
            "agent": task_kind,
            "tool": f"API call to {provider_name}",
            "path": "",
            "command": ""
        })

        try:
            result = provider.complete_with_tools(messages, model=model_name, tools=tools, temperature=temperature)
            event_bus.publish(
                EventType.LLM_RESPONSE,
                {"model": model_name, "provider": provider_name, "response_len": len(result.get("content") or "")},
                source="LLMRuntime"
            )
            return result
        except Exception as e:
            logger.error(f"LLM tool-calling request error on {provider_name}/{model_name}: {e}")
            event_bus.publish(EventType.ERROR, {"msg": f"LLM tool-calling request failed: {e}"}, source="LLMRuntime")
            raise e
        finally:
            CURRENT_ACTIVITY.update({
                "status": old_status,
                "agent": task_kind,
                "tool": "",
                "path": "",
                "command": ""
            })
