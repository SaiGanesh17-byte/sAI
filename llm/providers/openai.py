import os
from typing import List, Dict, Iterator
from llm.providers.base import BaseProvider
from openai import OpenAI

class OpenAIProvider(BaseProvider):
    def __init__(self):
        self._client = None

    def _get_client(self) -> OpenAI:
        if not self._client:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY is not configured.")
            self._client = OpenAI(api_key=api_key)
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
        client = self._get_client()
        response = client.embeddings.create(
            input=[text],
            model="text-embedding-3-small"
        )
        return response.data[0].embedding

    def health_check(self) -> bool:
        try:
            client = self._get_client()
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1
            )
            return True
        except Exception:
            return False
