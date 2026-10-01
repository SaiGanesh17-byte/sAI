import os
from typing import List, Dict, Iterator
from llm.providers.base import BaseProvider, stream_chat
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
            token_tracker.add_usage(response.usage)
        return response.choices[0].message.content

    def stream(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2, **kwargs) -> Iterator[str]:
        yield from stream_chat(self._get_client(), messages, model, temperature, **kwargs)

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
            token_tracker.add_usage(response.usage)
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
