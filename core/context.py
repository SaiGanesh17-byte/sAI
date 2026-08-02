from dataclasses import dataclass

from memory.conversation import ConversationHistory
from memory.working import WorkingMemory


@dataclass
class TaskContext:

    workspace: str

    current_agent: str

    conversation: ConversationHistory

    memory: WorkingMemory