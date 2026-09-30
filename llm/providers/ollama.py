import os
from typing import List, Dict, Iterator
from llm.providers.base import BaseProvider, stream_chat
from openai import OpenAI

class OllamaProvider(BaseProvider):
    def __init__(self):
        self._client = None

    def _get_client(self) -> OpenAI:
        if not self._client:
            base_url = os.getenv("OLLAMA_API_BASE_URL", "http://localhost:11434/v1")
            self._client = OpenAI(
                api_key="ollama",
                base_url=base_url
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
        return [0.0] * 768

    def health_check(self) -> bool:
        try:
            client = self._get_client()
            client.models.list()
            return True
        except Exception:
            return False
