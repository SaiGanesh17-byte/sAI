from enum import Enum
from typing import Callable, Dict, List, Any
from datetime import datetime

class EventType(Enum):
    USER_MESSAGE = "USER_MESSAGE"
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_FINISHED = "TASK_FINISHED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_FINISHED = "AGENT_FINISHED"
    LLM_REQUEST = "LLM_REQUEST"
    LLM_RESPONSE = "LLM_RESPONSE"
    TOOL_REQUEST = "TOOL_REQUEST"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_FINISHED = "TOOL_FINISHED"
    MEMORY_READ = "MEMORY_READ"
    MEMORY_WRITE = "MEMORY_WRITE"
    REPOSITORY_UPDATED = "REPOSITORY_UPDATED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    ERROR = "ERROR"

class Event:
    def __init__(self, event_type: EventType, data: Dict[str, Any], source: str = "System"):
        self.event_type = event_type
        self.data = data
        self.source = source
        self.timestamp = datetime.utcnow()

class EventBus:
    """
    Event Bus supporting event-sourcing audits.
    """
    def __init__(self):
        self._listeners: Dict[str, List[Callable[[Event], None]]] = {}
        self.events_log: List[Event] = []

    def subscribe(self, event_type: EventType, callback: Callable[[Event], None]):
        event_name = event_type.value
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable[[Event], None]):
        event_name = event_type.value
        if event_name in self._listeners:
            try:
                self._listeners[event_name].remove(callback)
            except ValueError:
                pass

    def publish(self, event_type: EventType, data: Dict[str, Any], source: str = "System"):
        event = Event(event_type, data, source)
        self.events_log.append(event)
        
        event_name = event_type.value
        if event_name in self._listeners:
            for callback in self._listeners[event_name]:
                try:
                    callback(event)
                except Exception:
                    pass

# Shared global event bus instance
event_bus = EventBus()