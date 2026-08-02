from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: pass

    @property
    @abstractmethod
    def description(self) -> str: pass

    @property
    @abstractmethod
    def permissions(self) -> list: pass

    @property
    @abstractmethod
    def schema(self) -> Dict[str, Any]: pass

    @abstractmethod
    def execute(self, args: Dict[str, Any]) -> str:
        """
        Execute the tool with structured arguments and return output text.
        """
        pass
