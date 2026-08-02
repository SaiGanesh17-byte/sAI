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
