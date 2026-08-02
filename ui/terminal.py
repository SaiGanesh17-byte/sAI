import sys
import threading
from typing import Optional, List
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual.widgets import Header, Footer, Input, Static, Label
from textual.reactive import reactive

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.orchestrator import Orchestrator
from core.task import Task
from core.events import event_bus, Event, EventType
from core.protocol import Message, MessageType


class AgentCard(Static):
    """
    A premium styled chat bubble representing an agent's summary in the conversation.
    """
    def __init__(self, agent_name: str, summary: str, reasoning: List[str], memory_snapshot: str, confidence: float):
        self.agent_name = agent_name
        self.summary = summary
        self.reasoning = reasoning
        self.memory_snapshot = memory_snapshot
        self.confidence = confidence
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(classes="agent-card-container"):
            yield Label(f"🤖 {self.agent_name.upper()} (Confidence: {self.confidence:.2f}) [dim](click to inspect)[/dim]", classes="agent-badge")
            yield Label(self.summary, classes="agent-summary")

    def on_click(self) -> None:
        self.app.update_inspector(
            self.agent_name,
            self.summary,
            self.reasoning,
            self.memory_snapshot,
            self.confidence
        )


class UserCard(Static):
    """
    A widget representing a user's prompt in the chat history.
    """
    def __init__(self, user_message: str):
        super().__init__()
        self.user_message = user_message

    def compose(self) -> ComposeResult:
        with Vertical(classes="user-card-container"):
            yield Label("👤 YOU [dim](click to inspect)[/dim]", classes="user-badge")
            yield Label(self.user_message, classes="user-content")

    def on_click(self) -> None:
        self.app.update_user_inspector(self.user_message)


class SaiApp(App):
    """
    sAI Terminal User Interface.
    """
    
    CSS = """
    Screen {
        background: #090E1A;
        color: #F8FAFC;
    }
    
    Header {
        background: #111827;
        color: #818CF8;
        text-align: center;
        text-style: bold;
        border-bottom: round #312E81;
    }
    
    Footer {
        background: #111827;
        color: #94A3B8;
    }
    
    #main-body {
        height: 1fr;
    }
    
    #chat-container {
        width: 60%;
        height: 100%;
        padding: 1 2;
        scrollbar-size: 1 1;
    }
    
    #inspector-container {
        width: 40%;
        height: 100%;
        background: #111827;
        border: round #312E81;
        margin: 1 2 1 0;
        padding: 1 2;
        scrollbar-size: 1 1;
    }
    
    .agent-card-container {
        background: #111827;
        border: round #1F2937;
        margin: 1 1;
        padding: 1 2;
    }
    
    .agent-card-container:hover {
        border: round #34D399;
        background: #1F2937;
    }
    
    .agent-badge {
        color: #34D399;
        text-style: bold;
        margin-bottom: 1;
    }
    
    .agent-summary {
        color: #F1F5F9;
        margin-bottom: 1;
    }
    
    .user-card-container {
        background: #1E1B4B;
        border: round #312E81;
        margin: 1 1;
        padding: 1 2;
    }
    
    .user-card-container:hover {
        border: round #FB7185;
        background: #312E81;
    }
    
    .user-badge {
        color: #FB7185;
        text-style: bold;
        margin-bottom: 1;
    }
    
    .user-content {
        color: #F8FAFC;
    }
    
    .inspector-title {
        color: #818CF8;
        text-style: bold;
        margin-bottom: 1;
        border-bottom: round #312E81;
    }
    
    .inspector-section {
        color: #38BDF8;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 1;
    }
    
    .inspector-text {
        color: #CBD5E1;
        margin-bottom: 1;
    }
    
    .inspector-bullets {
        color: #CBD5E1;
        margin-left: 2;
        margin-bottom: 1;
    }
    
    .inspector-memory {
        color: #94A3B8;
        background: #090E1A;
        padding: 1;
        margin-top: 1;
        border: round #1F2937;
    }
    
    #inspector-placeholder {
        color: #4B5563;
        text-align: center;
        margin-top: 12;
        text-style: italic;
    }
    
    #input-container {
        height: auto;
        border-top: round #312E81;
        background: #111827;
        padding: 1 2;
    }
    
    Input {
        background: #090E1A;
        border: round #312E81;
        color: #F8FAFC;
    }
    
    Input:focus {
        border: round #818CF8;
    }
    
    #status-label {
        color: #94A3B8;
        padding-left: 1;
        margin-top: 1;
    }
    """

    TITLE = "sAI Operating System"
    SUB_TITLE = "Multi-Agent Engine (Kernel Architecture)"

    status_message = reactive("System Idle. Enter a task below.")

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-body"):
            with ScrollableContainer(id="chat-container"):
                pass
            with ScrollableContainer(id="inspector-container"):
                yield Label("🔍 SELECT AN AGENT STEP OR USER PROMPT TO INSPECT DETAILS", id="inspector-placeholder")
        with Vertical(id="input-container"):
            yield Input(placeholder="Type your goal and press Enter...", id="user-input")
            yield Label(self.status_message, id="status-label")
        yield Footer()

    def on_mount(self) -> None:
        event_bus.subscribe(EventType.TASK_STARTED, self.on_task_started)
        event_bus.subscribe(EventType.AGENT_STARTED, self.on_agent_started)
        event_bus.subscribe(EventType.AGENT_FINISHED, self.on_agent_finished)
        event_bus.subscribe(EventType.TASK_FINISHED, self.on_task_finished)
        self.query_one("#user-input").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        goal = event.value.strip()
        if not goal:
            return

        input_widget = self.query_one("#user-input")
        input_widget.value = ""
        input_widget.disabled = True

        chat_container = self.query_one("#chat-container")
        chat_container.mount(UserCard(goal))
        chat_container.scroll_end(animate=False)

        self.run_worker(self.execute_task, thread=True)

    def execute_task(self) -> None:
        goal = self.query("UserCard").last().user_message
        task = Task(goal=goal)
        orchestrator = Orchestrator()
        try:
            orchestrator.run(task)
        except Exception as e:
            self.call_from_thread(self.set_status, f"Error: {e}")
            self.call_from_thread(self.enable_input)

    def on_task_started(self, event: Event) -> None:
        self.call_from_thread(self.set_status, f"Starting orchestrator (Task ID: {event.data['task_id']})...")

    def on_agent_started(self, event: Event) -> None:
        self.call_from_thread(self.set_status, f"🤖 Agent '{event.data['agent']}' (Turn {event.data.get('turn', 1)}) is running...")

    def on_agent_finished(self, event: Event) -> None:
        agent_name = event.data["agent"]
        message = event.data["msg"]
        
        response = message.metadata.get("response") if hasattr(message, "metadata") else getattr(message, "response", None)
        if not response and isinstance(message, dict):
            response = message.get("response")

        if response:
            summary = getattr(response, "summary", response.get("summary", ""))
            reasoning = getattr(response, "reasoning", response.get("reasoning", []))
            confidence = getattr(response, "confidence", response.get("confidence", 0.95))
            
            content = getattr(message, "content", "")
            if not content and hasattr(message, "payload"):
                content = str(message.payload)

            self.call_from_thread(
                self.mount_agent_card,
                agent_name,
                summary,
                reasoning,
                content,
                confidence
            )

    def on_task_finished(self, event: Event) -> None:
        self.call_from_thread(self.set_status, "Task execution completed successfully!")
        self.call_from_thread(self.enable_input)

    def set_status(self, msg: str) -> None:
        self.status_message = msg
        self.query_one("#status-label").update(msg)

    def mount_agent_card(self, name: str, summary: str, reasoning: List[str], snapshot: str, confidence: float) -> None:
        chat_container = self.query_one("#chat-container")
        chat_container.mount(AgentCard(name, summary, reasoning, snapshot, confidence))
        chat_container.scroll_end(animate=True)

    def enable_input(self) -> None:
        input_widget = self.query_one("#user-input")
        input_widget.disabled = False
        input_widget.focus()

    def update_inspector(self, name: str, summary: str, reasoning: List[str], snapshot: str, confidence: float) -> None:
        inspector = self.query_one("#inspector-container")
        for child in list(inspector.children):
            child.remove()
            
        reasoning_bullets = "\n".join([f" • {item}" for item in reasoning])
        
        inspector.mount(
            Label(f"🤖 {name.upper()} EXECUTION VIEW", classes="inspector-title"),
            Label(f"[bold]Summary:[/bold] {summary}", classes="inspector-text"),
            Label(f"[bold]Confidence Metric:[/bold] {confidence:.2f}", classes="inspector-text"),
            Label("[bold #38BDF8]Reasoning Chain:[/bold #38BDF8]", classes="inspector-section"),
            Label(reasoning_bullets, classes="inspector-bullets"),
            Label("[bold #38BDF8]Working Memory Update:[/bold #38BDF8]", classes="inspector-section"),
            Label(snapshot, classes="inspector-memory")
        )

    def update_user_inspector(self, content: str) -> None:
        inspector = self.query_one("#inspector-container")
        for child in list(inspector.children):
            child.remove()
            
        inspector.mount(
            Label("👤 USER INITIATION VIEW", classes="inspector-title"),
            Label("[bold]Submitted Task Goal:[/bold]", classes="inspector-section"),
            Label(content, classes="inspector-text")
        )


if __name__ == "__main__":
    app = SaiApp()
    app.run()
