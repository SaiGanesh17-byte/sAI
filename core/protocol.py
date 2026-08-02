from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

class MessageType(Enum):
    TASK = "TASK"
    QUESTION = "QUESTION"
    TOOL_REQUEST = "TOOL_REQUEST"
    TOOL_RESULT = "TOOL_RESULT"
    REVIEW = "REVIEW"
    PLAN = "PLAN"
    SUMMARY = "SUMMARY"
    ERROR = "ERROR"
    EVENT = "EVENT"

@dataclass
class Message:
    sender: str
    receiver: str
    type: MessageType
    payload: Dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

class ArtifactKind(Enum):
    CODE = "CODE"
    PATCH = "PATCH"
    FILE = "FILE"
    DIAGRAM = "DIAGRAM"
    SEARCH = "SEARCH"
    TERMINAL = "TERMINAL"
    PLAN = "PLAN"
    REPORT = "REPORT"
    DOCUMENT = "DOCUMENT"
    TEST_RESULT = "TEST_RESULT"

@dataclass
class Artifact:
    kind: ArtifactKind
    title: str
    author: str
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created: datetime = field(default_factory=datetime.utcnow)
    path: Optional[str] = None
    version: int = 1

@dataclass
class AgentResponse:
    """
    Standard response structure returned by Agent runtime.
    """
    agent: str
    summary: str
    reasoning: List[str] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    confidence: float = 1.0
    next_agent: Optional[str] = None
    finished: bool = False
    findings: List[Dict[str, Any]] = field(default_factory=list)