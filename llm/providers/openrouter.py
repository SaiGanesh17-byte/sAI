import os
from typing import List, Dict, Iterator
from llm.providers.base import BaseProvider
from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(BaseProvider):
    """
    OpenRouter is OpenAI-chat-completions-compatible, so this mirrors
    OpenAIProvider/NvidiaProvider but points at OpenRouter's base URL and
    reads OPENROUTER_API_KEY (injected into the environment by
    core.settings.load_settings, same pattern as NVIDIA_API_KEY/OPENAI_API_KEY).
    """

    def __init__(self):
        self._client = None

    def _get_client(self) -> OpenAI:
        if not self._client:
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY is not configured.")
            self._client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
        return self._client

    def complete(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2, **kwargs) -> str:
        client = self._get_client()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            **kwargs
        )
        if hasattr(response, "usage") and response.usage:
            from llm.tracker import token_tracker
            token_tracker.add(response.usage.prompt_tokens, response.usage.completion_tokens)
        return response.choices[0].message.content

    def stream(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2, **kwargs) -> Iterator[str]:
        client = self._get_client()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
            **kwargs
        )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def complete_with_tools(self, messages, model: str, tools, temperature: float = 0.2, **kwargs):
        client = self._get_client()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            tools=tools,
            **kwargs
        )
        if hasattr(response, "usage") and response.usage:
            from llm.tracker import token_tracker
            token_tracker.add(response.usage.prompt_tokens, response.usage.completion_tokens)
        message = response.choices[0].message
        tool_calls = []
        if message.tool_calls:
            import json
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append({"name": tc.function.name, "arguments": args})
        return {"content": message.content, "tool_calls": tool_calls}

    def embed(self, text: str) -> List[float]:
        # OpenRouter has no unified embeddings endpoint across its models.
        return [0.0] * 1536

    def health_check(self) -> bool:
        try:
            client = self._get_client()
            client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1
            )
            return True
        except Exception:
            return False
