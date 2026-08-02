from dataclasses import dataclass, field
from typing import List

from core.message_bus import Message


@dataclass
class ConversationHistory:
    """
    Stores the conversation between
    the user and the AI agents.
    """

    messages: List[Message] = field(default_factory=list)

    def add(self, message: Message):
        self.messages.append(message)

    def latest(self):

        if not self.messages:
            return None

        return self.messages[-1]

    def all(self):

        return self.messages