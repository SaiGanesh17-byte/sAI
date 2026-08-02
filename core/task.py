from dataclasses import dataclass, field
from datetime import datetime
import uuid

from core.context import TaskContext

from memory.conversation import ConversationHistory
from memory.working import WorkingMemory


@dataclass
class Task:

    goal: str

    workspace: str = "default"

    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    status: str = "CREATED"

    created_at: datetime = field(default_factory=datetime.utcnow)

    context: TaskContext = field(init=False)

    def __post_init__(self):

        conversation = ConversationHistory()

        memory = WorkingMemory(
            goal=self.goal
        )

        self.context = TaskContext(

            workspace=self.workspace,

            current_agent="",

            conversation=conversation,

            memory=memory
        )