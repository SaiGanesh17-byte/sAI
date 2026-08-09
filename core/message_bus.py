from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid

from core.protocol import AgentResponse


@dataclass
class Message:
    """
    Message exchanged between agents.

    A Message is simply an envelope that transports
    an AgentResponse.
    """

    sender: str

    receiver: str

    content: str

    response: Optional[AgentResponse] = None

    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))