import os
from pathlib import Path
from typing import Optional
from core.kernel import kernel
from core.security import get_current_workspace
from llm.providers.nvidia import NvidiaProvider
from llm.providers.openai import OpenAIProvider
from llm.providers.ollama import OllamaProvider
from llm.providers.openrouter import OpenRouterProvider
from llm.runtime import LLMRuntime
from tools.registry import ToolRegistry
from tools.filesystem import ReadFileTool, WriteFileTool, PatchFileTool, ListDirectoryTool
from tools.terminal import TerminalTool
from tools.git import GitTool
from tools.search import SearchTool, GrepAstTool, CodebaseSearchTool
from tools.python import PythonTool
from tools.memory import MemoryTool
from tools.math import MathTool
from tools.filesystem import EditFileTool
from tools.find import GlobTool, GrepTool
from tools.web_fetch import WebFetchTool
from tools.terminal import BashOutputTool, KillShellTool
from tools.todo import TodoWriteTool
from repository.context import RepositoryContext
from execution.engine import ExecutionEngine
from agents.loop import ActionOutcome, ApproveFn, DEFAULT_MAX_STEPS, execute_with_approval, run_agent_loop
from core.events import event_bus, EventType
from core.scheduler import DAGScheduler
from core.protocol import Message, MessageType, AgentResponse
from core.task import Task
from agents.registry import AgentRegistry


class Orchestrator:
    def __init__(self):
        self._init_kernel()
        self.agents = AgentRegistry().get_agents()
        self.execution_engine = ExecutionEngine()

    def _init_kernel(self) -> None:
        try:
            kernel.register_provider("nvidia", NvidiaProvider())
            kernel.register_provider("openai", OpenAIProvider())
            kernel.register_provider("ollama", OllamaProvider())
            kernel.register_provider("openrouter", OpenRouterProvider())
        except Exception:
            pass

        try:
            kernel.register_service("llm_runtime", LLMRuntime())
            
            tool_reg = ToolRegistry()
            tool_reg.register(ReadFileTool())
            tool_reg.register(WriteFileTool())
            tool_reg.register(PatchFileTool())
            tool_reg.register(ListDirectoryTool())
            tool_reg.register(TerminalTool())
            tool_reg.register(GitTool())
            tool_reg.register(SearchTool())
            tool_reg.register(GrepAstTool())
            tool_reg.register(CodebaseSearchTool())
            tool_reg.register(PythonTool())
            tool_reg.register(MemoryTool())
            tool_reg.register(MathTool())
            tool_reg.register(EditFileTool())
            tool_reg.register(GlobTool())
            tool_reg.register(GrepTool())
            tool_reg.register(WebFetchTool())
            tool_reg.register(BashOutputTool())
            tool_reg.register(KillShellTool())
            tool_reg.register(TodoWriteTool())
            kernel.register_service("tool_registry", tool_reg)

            repo_ctx = RepositoryContext.get_cached_context(get_current_workspace())
            kernel.register_service("repository", repo_ctx)
        except Exception:
            pass

    def _max_steps(self) -> int:
        from core.settings import load_settings
        try:
            return int(load_settings().get("agent_max_steps", DEFAULT_MAX_STEPS))
        except (TypeError, ValueError):
            return DEFAULT_MAX_STEPS

    def execute_action(self, task: Task, agent, action: dict, approve: Optional[ApproveFn] = None) -> ActionOutcome:
        """
        Runs one agent action: activity tracking, inline approval, and the
        post-write hooks (repo-map cache invalidation + auto-linter feedback).
        Shared by the full-team loop and the single-agent front ends.
        """
        from core.security import update_current_activity
        tool_name = action.get("tool", "")
        tool_args = action.get("args", {}) or {}
        target_path = tool_args.get("path", tool_args.get("target_file", tool_args.get("TargetFile", "")))
        target_cmd = tool_args.get("command", tool_args.get("CommandLine", ""))
        update_current_activity({"status": "executing", "tool": tool_name, "path": str(target_path), "command": str(target_cmd)})
        try:
            outcome = execute_with_approval(self.execution_engine, action, approve)
        finally:
            update_current_activity({"status": "thinking", "agent": agent.name, "tool": "", "path": "", "command": ""})

        if outcome.status == "ok" and tool_name in ["write_file", "patch_file", "edit_file"] and target_path:
            workspace = get_current_workspace()
            RepositoryContext.invalidate_cache(workspace)
            file_path = Path(target_path)
            if not file_path.is_absolute():
                file_path = workspace / file_path
            if file_path.exists():
                if not hasattr(task, "linter_attempts"):
                    task.linter_attempts = {}
                tech_stack = ""
                for note in task.context.memory.notes:
                    if "Framework Context:" in note:
                        tech_stack = note.split("Framework Context:")[-1].split(".")[0].strip().lower()
                linter_err = run_linter_checks(workspace, tech_stack, file_path)
                if linter_err:
                    attempts = task.linter_attempts.get(str(file_path), 0) + 1
                    task.linter_attempts[str(file_path)] = attempts
                    if attempts > 3:
                        outcome.content += f"\n\n❌ [AUTO-LINTER ABORTED]: Linter checks failed {attempts} times on {file_path.name}. Syntax verification aborted to prevent infinite loops. Error:\n{linter_err}"
                    else:
                        outcome.content += f"\n\n⚠️ AUTO-LINTER COMPILATION WARNING:\n{linter_err}\nYour code has syntax or compile errors. You MUST edit the file to fix this error immediately."
        return outcome

    def run(self, task: Task, start_agent: Optional[str] = None, approve: Optional[ApproveFn] = None):
        """
        With `approve`, permission requests are answered inline and the turn
        continues; without it they propagate as PermissionRequestRequired
        (the web UI's approve-then-resume flow).
        """
        event_bus.publish(EventType.TASK_STARTED, {"task_id": task.id}, source="Orchestrator")

        if start_agent:
            # Resuming an interrupted turn (e.g. after an inline permission approval) —
            # skip the greeting short-circuit and initial planning message, and
            # continue the loop from the agent whose action was interrupted.
            current_agent_name = start_agent
        else:
            cleaned_goal = task.goal.strip().lower().rstrip(".!?")
            if cleaned_goal in ["hi", "hello", "hey", "greetings", "yo"]:
                response_msg = Message(
                    sender="sAI",
                    receiver="User",
                    type=MessageType.SUMMARY,
                    payload={"summary": "Hello! I am sAI, your AI Operating System. How can I help you today?"}
                )
                response_msg.metadata["response"] = AgentResponse(
                    agent="sAI",
                    summary="Greeting processed locally.",
                    reasoning=["Simple greeting detected."],
                    confidence=1.0,
                    finished=True
                )
                task.context.conversation.add(response_msg)
                event_bus.publish(EventType.AGENT_FINISHED, {"agent": "sAI", "msg": response_msg}, source="Orchestrator")
                event_bus.publish(EventType.TASK_FINISHED, {"task_id": task.id}, source="Orchestrator")
                return

            first = Message(
                sender="User",
                receiver="Planner",
                type=MessageType.TASK,
                payload={"content": task.goal}
            )
            task.context.conversation.add(first)

            scheduler = DAGScheduler()
            scheduler.add_task("task_planning", "Planner decomposes user goal into checklist of sub-tasks")
            scheduler.mark_completed("task_planning")

            current_agent_name = "Planner"

        turns = 0
        max_turns = 12

        from core.security import update_current_activity

        while current_agent_name and turns < max_turns:
            turns += 1

            agent = next((a for a in self.agents if a.name == current_agent_name), None)
            if not agent:
                break

            task.context.current_agent = agent.name
            update_current_activity({
                "status": "thinking",
                "agent": agent.name,
                "tool": "",
                "path": "",
                "command": ""
            })
            from core.security import check_and_reset_halt
            loop = run_agent_loop(
                agent,
                task.context,
                lambda action, agent=agent: self.execute_action(task, agent, action, approve),
                max_steps=self._max_steps(),
                source="Orchestrator",
                is_halted=check_and_reset_halt,
            )
            message = loop.final_message
            response = message.metadata.get("response") if message else None

            if loop.stop_reason == "declined":
                # The user said no -- hand control back rather than moving on
                # to the next agent as if the step had happened.
                break

            if agent.name == "Planner" and message:
                planner_content = message.payload.get("content", "")
                import re
                tasks_found = re.findall(r'(?:-|\*|\d+\.)\s*\[\s*\]\s*(.+)', planner_content)
                if tasks_found:
                    update_current_activity({
                        "tasks": [t.strip() for t in tasks_found]
                    })
                    scheduler = DAGScheduler()
                    for idx, task_desc in enumerate(tasks_found):
                        task_id = f"task_{idx+1}"
                        dependencies = [f"task_{idx}"] if idx > 0 else []
                        scheduler.add_task(task_id, task_desc.strip(), dependencies)

            if response and response.finished:
                break

            requested_next = str(response.next_agent).strip() if (response and response.next_agent) else ""
            valid_agent_names = {a.name for a in self.agents}
            if requested_next and requested_next in valid_agent_names:
                current_agent_name = requested_next
            else:
                try:
                    curr_idx = next(i for i, a in enumerate(self.agents) if a.name == current_agent_name)
                    if curr_idx < len(self.agents) - 1:
                        current_agent_name = self.agents[curr_idx + 1].name
                    else:
                        current_agent_name = None
                except StopIteration:
                    current_agent_name = None

        update_current_activity({
            "status": "idle",
            "agent": "",
            "tool": "",
            "path": "",
            "command": ""
        })
        event_bus.publish(EventType.TASK_FINISHED, {"task_id": task.id}, source="Orchestrator")


def run_linter_checks(workspace_path: Path, tech_stack: str, file_path: Path) -> str:
    ext = file_path.suffix.lower()
    import subprocess
    
    # 1. Python compilation validation check
    if ext == ".py":
        res = subprocess.run(["python3", "-m", "py_compile", str(file_path)], capture_output=True, text=True)
        if res.returncode != 0:
            return f"Python Compile Error:\n{res.stderr or res.stdout}"
            
    # 2. Node/JS syntax check
    elif ext == ".js":
        res = subprocess.run(["node", "--check", str(file_path)], capture_output=True, text=True)
        if res.returncode != 0:
            return f"Javascript Syntax Error:\n{res.stderr or res.stdout}"
            
    # 3. Stack checks if configuration commands are available
    if "fastapi" in tech_stack or "flask" in tech_stack:
        if (workspace_path / "tests").exists() or (workspace_path / "test_main.py").exists():
            res = subprocess.run(["pytest"], cwd=str(workspace_path), capture_output=True, text=True)
            if res.returncode != 0:
                return f"pytest failures:\n{res.stdout or res.stderr}"
                
    elif "node" in tech_stack or "express" in tech_stack:
        if (workspace_path / "package.json").exists():
            res = subprocess.run(["npm", "test"], cwd=str(workspace_path), capture_output=True, text=True)
            if res.returncode != 0:
                return f"npm test failures:\n{res.stdout or res.stderr}"
                
    return ""