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
from core.security import approve_request
from execution.permissions import PermissionRequestRequired
from jev.decision import JevRouter



def _describe_action(action) -> str:
    args = action.get("args", {}) or {}
    target = args.get("path") or args.get("script_path") or args.get("command") or ""
    return f"{action.get('tool', '?')}({target})" if target else str(action.get("tool", "?"))

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


class ToolCallLine(Static):
    """
    A lightweight, single-line indicator for a tool call in progress/finished --
    mirrors app/repl.py's live tool-streaming lines, adapted to a mounted widget.
    """
    def __init__(self, text: str, success: Optional[bool] = None):
        super().__init__()
        self.text = text
        self.success = success

    def compose(self) -> ComposeResult:
        marker = "⚙" if self.success is None else ("✓" if self.success else "✗")
        yield Label(f"{marker} {self.text}", classes="tool-call-line")


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
        background: #1C1C1E;
        color: #F5F5F7;
    }

    Header {
        background: #232326;
        color: #C96442;
        text-align: center;
        text-style: bold;
        border-bottom: round #3A3A3D;
    }

    Footer {
        background: #232326;
        color: #8E8E93;
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
        background: #232326;
        border: round #3A3A3D;
        margin: 1 2 1 0;
        padding: 1 2;
        scrollbar-size: 1 1;
    }

    .agent-card-container {
        background: #232326;
        border: round #3A3A3D;
        margin: 1 1;
        padding: 1 2;
    }

    .agent-card-container:hover {
        border: round #C96442;
        background: #2A2A2D;
    }

    .agent-badge {
        color: #34C759;
        text-style: bold;
        margin-bottom: 1;
    }

    .agent-summary {
        color: #F5F5F7;
        margin-bottom: 1;
    }

    .tool-call-line {
        color: #8E8E93;
        margin: 0 1 0 3;
    }

    .user-card-container {
        background: #2A2A2D;
        border: round #3A3A3D;
        margin: 1 1;
        padding: 1 2;
    }

    .user-card-container:hover {
        border: round #C96442;
        background: #2F2F32;
    }

    .user-badge {
        color: #C96442;
        text-style: bold;
        margin-bottom: 1;
    }

    .user-content {
        color: #F5F5F7;
    }

    .inspector-title {
        color: #C96442;
        text-style: bold;
        margin-bottom: 1;
        border-bottom: round #3A3A3D;
    }

    .inspector-section {
        color: #6FAEE7;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 1;
    }

    .inspector-text {
        color: #D1D1D6;
        margin-bottom: 1;
    }

    .inspector-bullets {
        color: #D1D1D6;
        margin-left: 2;
        margin-bottom: 1;
    }

    .inspector-memory {
        color: #8E8E93;
        background: #1C1C1E;
        padding: 1;
        margin-top: 1;
        border: round #3A3A3D;
    }

    #inspector-placeholder {
        color: #6E6E73;
        text-align: center;
        margin-top: 12;
        text-style: italic;
    }

    #input-container {
        height: auto;
        border-top: round #3A3A3D;
        background: #232326;
        padding: 1 2;
    }

    Input {
        background: #1C1C1E;
        border: round #3A3A3D;
        color: #F5F5F7;
    }

    Input:focus {
        border: round #C96442;
    }

    #status-label {
        color: #8E8E93;
        padding-left: 1;
        margin-top: 1;
    }
    """

    TITLE = "sAI Operating System"
    SUB_TITLE = "Multi-Agent Engine (Kernel Architecture)"

    status_message = reactive("System Idle. Enter a task below.")

    def __init__(self):
        super().__init__()
        # One Orchestrator/Task lives for the whole session so conversation history
        # and working memory persist across submissions, instead of a fresh Task
        # being built on every single input (the earlier behavior here).
        self.orchestrator = Orchestrator()
        self.jev = JevRouter()
        # Named sai_task (not "task") -- Textual's App base class already defines a
        # read-only "task" property internally, which would otherwise be shadowed.
        self.sai_task = Task(goal="")
        self._pending_permission: Optional[PermissionRequestRequired] = None
        self._task_running = False
        # Lets a worker thread block on a permission answer without a separate
        # resume-from-where-we-left-off mechanism per route -- the thread just
        # waits here while the main thread shows the prompt and sets the event.
        self._permission_answer_event = threading.Event()
        self._permission_answer_value = False

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
        event_bus.subscribe(EventType.TOOL_STARTED, self.on_tool_started)
        event_bus.subscribe(EventType.TOOL_FINISHED, self.on_tool_finished)
        event_bus.subscribe(EventType.AGENT_FINISHED, self.on_agent_finished)
        event_bus.subscribe(EventType.TASK_FINISHED, self.on_task_finished)
        self.query_one("#user-input").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return

        input_widget = self.query_one("#user-input")
        input_widget.value = ""

        if self._pending_permission is not None:
            self._handle_permission_answer(value)
            return

        if self._task_running:
            # Defensive guard: the input is disabled while a turn is in progress, but
            # this protects against any path that could still deliver a submission
            # (e.g. a synthetic/queued event) while sai_task is being mutated by the
            # in-flight worker thread.
            return

        self._task_running = True
        input_widget.disabled = True

        chat_container = self.query_one("#chat-container")
        chat_container.mount(UserCard(value))
        chat_container.scroll_end(animate=False)

        self.run_worker(lambda: self.route_turn(value), thread=True)

    # ------------------------------------------------------------------
    # Every turn goes through Jev first, same as app/repl.py, instead of
    # always invoking the full Planner -> ... -> Reviewer loop -- this is
    # what previously let a trivial question get misrouted into a full
    # "build a new codebase" chain that wrote real files.
    # ------------------------------------------------------------------
    def route_turn(self, goal: str) -> None:
        agent_names = [a.name for a in self.orchestrator.agents]
        agent_roles = {a.name: a.role for a in self.orchestrator.agents}
        decision = self.jev.decide(goal, agent_names, self._recent_turn_summaries(), agent_roles=agent_roles)

        if decision.route == "direct_answer" and decision.answer:
            msg = Message(
                sender="sAI", receiver="User", type=MessageType.SUMMARY,
                payload={"summary": decision.answer, "content": decision.answer},
            )
            self.sai_task.context.conversation.add(msg)
            self._task_running = False
            self.call_from_thread(self.mount_direct_answer, decision.answer)
            self.call_from_thread(self.set_status, "System Idle. Enter a task below.")
            self.call_from_thread(self.enable_input)
            return

        self.sai_task.goal = goal

        try:
            if decision.route == "single_agent" and decision.agent:
                self._run_single_agent_turn(decision.agent)
            else:
                self._run_full_orchestrator_with_retry()
        except Exception as e:
            self._task_running = False
            self.call_from_thread(self.set_status, f"Error: {e}")
            self.call_from_thread(self.enable_input)
            return

        self._task_running = False
        self.call_from_thread(self.set_status, "Task execution completed successfully!")
        self.call_from_thread(self.enable_input)

    def _recent_turn_summaries(self) -> List[str]:
        turns = []
        for msg in self.sai_task.context.conversation.all()[-6:]:
            payload = getattr(msg, "payload", {}) or {}
            text = payload.get("content") or payload.get("summary") or ""
            sender = getattr(msg, "sender", "")
            if text:
                turns.append(f"{sender}: {text}"[:200])
        return turns

    def mount_direct_answer(self, answer: str) -> None:
        self.mount_agent_card("sAI", answer, [], "", 1.0)

    # ------------------------------------------------------------------
    # Jev route: single_agent -- one agent turn, actions executed once
    # (mirrors app/repl.py::SaiRepl._run_single_agent). AGENT_STARTED/
    # AGENT_FINISHED are published manually since AgentRuntime.execute_turn
    # only fires them when driven by Orchestrator.run().
    # ------------------------------------------------------------------
    def _run_single_agent_turn(self, agent_name: str) -> None:
        agent = next((a for a in self.orchestrator.agents if a.name == agent_name), None)
        if not agent:
            self._run_full_orchestrator_with_retry()
            return

        self.sai_task.context.current_agent = agent.name
        event_bus.publish(EventType.AGENT_STARTED, {"agent": agent.name, "turn": 1}, source="Jev")

        user_msg = Message(
            sender="User", receiver=agent.name, type=MessageType.TASK, payload={"content": self.sai_task.goal}
        )
        self.sai_task.context.conversation.add(user_msg)

        message = agent.run(self.sai_task.context)
        response = message.metadata.get("response")

        not_done = []
        if response and response.actions:
            for action in response.actions:
                if not self._execute_action_with_approval(action, agent):
                    not_done.append(_describe_action(action))

        self.sai_task.context.conversation.add(message)
        event_bus.publish(EventType.AGENT_FINISHED, {"agent": agent.name, "msg": message}, source="Jev")

        # The agent's summary is written before its actions run, so it reads as
        # if everything succeeded -- say plainly what didn't happen.
        if not_done:
            self.call_from_thread(
                self.mount_tool_line, f"Not completed: {', '.join(not_done)} (the summary above describes what was attempted)", False
            )

    def _execute_action_with_approval(self, action, agent) -> bool:
        """Runs one action; returns True only if it actually succeeded."""
        tool_name = action.get("tool", "")
        try:
            result = self.orchestrator.execution_engine.execute(action)
        except PermissionRequestRequired as preq:
            if self._ask_permission_sync(preq):
                try:
                    result = self.orchestrator.execution_engine.execute(action)
                except PermissionRequestRequired:
                    self.call_from_thread(self.mount_tool_line, f"Still not permitted: {tool_name}", False)
                    self._record_tool_result(agent, f"NOT PERMITTED: {_describe_action(action)} -- it did not run.")
                    return False
            else:
                self.call_from_thread(self.mount_tool_line, f"Declined: {tool_name}", False)
                # Record it, or later turns only see the agent's optimistic summary.
                self._record_tool_result(agent, f"DECLINED by user: {_describe_action(action)} -- it did not run.")
                return False

        content = result.stdout if result.success else result.stderr
        self._record_tool_result(agent, content)
        return result.success

    def _record_tool_result(self, agent, content: str) -> None:
        result_msg = Message(
            sender="System", receiver=agent.name, type=MessageType.TOOL_RESULT, payload={"content": content}
        )
        self.sai_task.context.conversation.add(result_msg)

    # ------------------------------------------------------------------
    # Jev route: full_orchestrator -- the existing multi-agent loop, with
    # inline permission-approval-and-resume on PermissionRequestRequired
    # (mirrors app/repl.py::SaiRepl._run_with_permission_retry).
    # ------------------------------------------------------------------
    def _run_full_orchestrator_with_retry(self, max_retries: int = 5) -> None:
        start_agent: Optional[str] = None
        for _ in range(max_retries):
            try:
                self.orchestrator.run(self.sai_task, start_agent=start_agent)
                return
            except PermissionRequestRequired as preq:
                resume_agent = self.sai_task.context.current_agent
                if self._ask_permission_sync(preq):
                    approve_request(preq.path, preq.kind)
                    start_agent = resume_agent
                    continue
                self.call_from_thread(self.mount_tool_line, "Permission declined -- aborting this task.", False)
                return
        self.call_from_thread(self.mount_tool_line, "Too many permission retries -- aborting.", False)

    # ------------------------------------------------------------------
    # Inline permission approval. Blocks the calling worker thread (never
    # the UI thread) on a threading.Event while the main thread shows the
    # prompt and waits for the next input submission to answer it -- this
    # lets both routes above share one simple "ask and wait" primitive
    # instead of a separate resume-from-where-we-left-off path each.
    # ------------------------------------------------------------------
    def _ask_permission_sync(self, preq: PermissionRequestRequired) -> bool:
        self._permission_answer_event.clear()
        self.call_from_thread(self.prompt_permission, preq)
        self._permission_answer_event.wait()
        return self._permission_answer_value

    def prompt_permission(self, preq: PermissionRequestRequired) -> None:
        self._pending_permission = preq
        chat_container = self.query_one("#chat-container")
        chat_container.mount(Static(f"⚠ Permission required: {preq}\nType 'y' to approve, anything else to decline.", classes="tool-call-line"))
        chat_container.scroll_end(animate=True)

        input_widget = self.query_one("#user-input")
        input_widget.placeholder = "Approve this action? [y/N]"
        input_widget.disabled = False
        input_widget.focus()
        self.set_status(f"⚠ Awaiting approval: {preq.reason}")

    def _handle_permission_answer(self, value: str) -> None:
        preq = self._pending_permission
        self._pending_permission = None
        input_widget = self.query_one("#user-input")
        input_widget.placeholder = "Type your goal and press Enter..."
        input_widget.disabled = True

        approved = value.strip().lower() in ("y", "yes")
        chat_container = self.query_one("#chat-container")
        if approved:
            approve_request(preq.path, preq.kind)
            chat_container.mount(Static("✓ Approved. Resuming...", classes="tool-call-line"))
        else:
            chat_container.mount(Static("✗ Declined.", classes="tool-call-line"))
        chat_container.scroll_end(animate=True)

        # Wakes up whichever worker thread is blocked in _ask_permission_sync; it
        # resumes and will call enable_input/set_status itself once the turn
        # actually finishes (route_turn's try/except, same as a normal completion).
        self._permission_answer_value = approved
        self._permission_answer_event.set()

    def on_task_started(self, event: Event) -> None:
        self.call_from_thread(self.set_status, f"Starting orchestrator (Task ID: {event.data['task_id']})...")

    def on_agent_started(self, event: Event) -> None:
        self.call_from_thread(self.set_status, f"🤖 Agent '{event.data['agent']}' (Turn {event.data.get('turn', 1)}) is running...")

    def on_tool_started(self, event: Event) -> None:
        self.call_from_thread(self.mount_tool_line, f"Running {event.data.get('tool', '')}...", None)

    def on_tool_finished(self, event: Event) -> None:
        self.call_from_thread(
            self.mount_tool_line, event.data.get("tool", ""), event.data.get("success", True)
        )

    def on_agent_finished(self, event: Event) -> None:
        agent_name = event.data["agent"]
        message = event.data["msg"]

        response = message.metadata.get("response") if hasattr(message, "metadata") else getattr(message, "response", None)
        if not response and isinstance(message, dict):
            response = message.get("response")

        if response:
            is_dict = isinstance(response, dict)
            summary = getattr(response, "summary", response.get("summary", "") if is_dict else "")
            reasoning = getattr(response, "reasoning", response.get("reasoning", []) if is_dict else [])
            confidence = getattr(response, "confidence", response.get("confidence", 0.95) if is_dict else 0.95)

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
        # Completion (status/_task_running/enable_input) is handled centrally in
        # route_turn once it returns, uniformly across all three Jev routes --
        # TASK_FINISHED only fires for the full_orchestrator route and would
        # otherwise race with that single completion point for no benefit.
        pass

    def set_status(self, msg: str) -> None:
        self.status_message = msg
        self.query_one("#status-label").update(msg)

    def mount_tool_line(self, tool_name: str, success: Optional[bool]) -> None:
        chat_container = self.query_one("#chat-container")
        chat_container.mount(ToolCallLine(tool_name, success))
        chat_container.scroll_end(animate=False)

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
            Label("[bold #6FAEE7]Reasoning Chain:[/bold #6FAEE7]", classes="inspector-section"),
            Label(reasoning_bullets, classes="inspector-bullets"),
            Label("[bold #6FAEE7]Working Memory Update:[/bold #6FAEE7]", classes="inspector-section"),
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
