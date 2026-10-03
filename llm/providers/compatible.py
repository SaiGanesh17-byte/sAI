import os
from typing import Dict, Iterator, List

from openai import OpenAI

from llm.providers.base import BaseProvider, stream_chat


class OpenAICompatibleProvider(BaseProvider):
    """
    Any provider with an OpenAI-style /chat/completions endpoint (Groq, Google's
    Gemini OpenAI endpoint, NVIDIA NIM). Each has its own free tier, so a fallback
    chain that crosses providers isn't limited by one account's daily cap. The key
    is read from `key_env`, which core.settings.load_settings fills from the keychain.
    """

    def __init__(self, base_url: str, key_env: str):
        self.base_url = base_url
        self.key_env = key_env
        self._client = None
        self._client_key = None

    def _get_client(self) -> OpenAI:
        api_key = os.getenv(self.key_env)
        if not api_key:
            raise ValueError(f"{self.key_env} is not configured.")
        if self._client is None or api_key != self._client_key:
            self._client = OpenAI(api_key=api_key, base_url=self.base_url)
            self._client_key = api_key
        return self._client

    def complete(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2, **kwargs) -> str:
        response = self._get_client().chat.completions.create(
            model=model, messages=messages, temperature=temperature, **kwargs
        )
        if getattr(response, "usage", None):
            from llm.tracker import token_tracker
            token_tracker.add_usage(response.usage)
        return response.choices[0].message.content

    def stream(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2, **kwargs) -> Iterator[str]:
        yield from stream_chat(self._get_client(), messages, model, temperature, **kwargs)

    def complete_with_tools(self, messages, model: str, tools, temperature: float = 0.2, **kwargs):
        import json
        response = self._get_client().chat.completions.create(
            model=model, messages=messages, temperature=temperature, tools=tools, **kwargs
        )
        if getattr(response, "usage", None):
            from llm.tracker import token_tracker
            token_tracker.add_usage(response.usage)
        message = response.choices[0].message
        tool_calls = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append({"name": tc.function.name, "arguments": args})
        return {"content": message.content, "tool_calls": tool_calls}

    def embed(self, text: str) -> List[float]:
        return [0.0] * 1536

    def health_check(self) -> bool:
        try:
            self._get_client().models.list()
            return True
        except Exception:
            return False


# name -> (base URL, env var holding the key, keychain entry)
FREE_TIER_PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "groq_key"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini_key"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "nvidia_key"),
}
