from typing import List, Dict, Iterator, Any
from llm.providers.base import BaseProvider
from openai import OpenAI
from sai.config import settings

class NvidiaProvider(BaseProvider):
    def __init__(self):
        self._client = None

    def _get_client(self) -> OpenAI:
        if not self._client:
            if not settings.has_nvidia_key:
                raise ValueError("NVIDIA_API_KEY is not configured in sAI environment.")
            self._client = OpenAI(
                api_key=settings.nvidia_api_key,
                base_url=settings.nvidia_api_base_url
            )
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

    def embed(self, text: str) -> List[float]:
        return [0.0] * 1536

    def health_check(self) -> bool:
        try:
            client = self._get_client()
            client.chat.completions.create(
                model="qwen/qwen-2.5-coder-32b-instruct",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1
            )
            return True
        except Exception:
            return False
