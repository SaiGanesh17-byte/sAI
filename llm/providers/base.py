from abc import ABC, abstractmethod
from typing import List, Dict, Iterator, Any

class BaseProvider(ABC):
    @abstractmethod
    def complete(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2, **kwargs) -> str:
        """
        Send a completion request.
        """
        pass

    @abstractmethod
    def complete_with_tools(
        self, messages: List[Dict[str, str]], model: str, tools: List[Dict[str, Any]], temperature: float = 0.2, **kwargs
    ) -> Dict[str, Any]:
        """
        Send a completion request with OpenAI-style function-calling tools attached.
        Returns {"content": str|None, "tool_calls": [{"name": str, "arguments": dict}]}.
        """
        pass

    @abstractmethod
    def stream(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2, **kwargs) -> Iterator[str]:
        """
        Stream back content tokens.
        """
        pass

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """
        Compute embedding vector.
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check connection health.
        """
        pass


def stream_chat(client, messages, model: str, temperature: float = 0.2, **kwargs) -> Iterator[str]:
    """
    Shared streaming for the OpenAI-compatible providers. Asks for a final usage
    chunk (stream_options.include_usage) so token counts stay accurate; if a
    server rejects that option, retries without it and estimates usage
    (~4 chars/token) instead of silently recording zero.
    """
    from llm.tracker import token_tracker

    try:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, stream=True,
            stream_options={"include_usage": True}, **kwargs
        )
    except Exception:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, stream=True, **kwargs
        )

    produced = []
    usage = None
    for chunk in response:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta.content:
            produced.append(chunk.choices[0].delta.content)
            yield chunk.choices[0].delta.content

    if usage:
        token_tracker.add_usage(usage)
    else:
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        token_tracker.add(prompt_chars // 4, len("".join(produced)) // 4)

