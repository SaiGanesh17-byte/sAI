from typing import List, Dict, Iterator, Any, Optional
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from core.kernel import kernel
from core.events import event_bus, EventType
from llm.router import ModelRouter

logger = logging.getLogger("sai.llm.runtime")

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
        user_temp = settings.get("temperature")
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
            response_content = provider.complete(messages, model=model_name, temperature=temperature, **kwargs)
            
            if not ResponseValidator.validate(response_content):
                raise ValueError("Response failed structure validation checks.")

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
        user_temp = settings.get("temperature")
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
