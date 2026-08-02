from typing import List, Dict, Iterator, Any
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from core.kernel import kernel
from core.events import event_bus, EventType
from llm.router import ModelRouter

logger = logging.getLogger("sai.llm.runtime")

class ContextCompressor:
    @staticmethod
    def compress(prompt: str, max_tokens: int = 32000) -> str:
        """
        Stub context compressor. Trims or shrinks prompt if exceeding token limits.
        """
        return prompt

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
    def query(self, prompt: str, task_kind: str, temperature: float = 0.2) -> str:
        compressed_prompt = ContextCompressor.compress(prompt)
        from core.settings import load_settings
        settings = load_settings()
        user_temp = settings.get("temperature")
        if user_temp is not None:
            temperature = float(user_temp)

        provider_name, model_name = ModelRouter.route(task_kind)
        
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
            response_content = provider.complete(messages, model=model_name, temperature=temperature)
            
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
