import os
from pathlib import Path
from core.kernel import kernel
from llm.providers.nvidia import NvidiaProvider
from llm.providers.openai import OpenAIProvider
from llm.providers.ollama import OllamaProvider
from llm.runtime import LLMRuntime
from tools.registry import ToolRegistry
from tools.filesystem import ReadFileTool, WriteFileTool, PatchFileTool, ListDirectoryTool
from tools.terminal import TerminalTool
from tools.git import GitTool
from tools.search import SearchTool, GrepAstTool, CodebaseSearchTool
from tools.python import PythonTool
from tools.memory import MemoryTool
from repository.context import RepositoryContext
from execution.engine import ExecutionEngine
from core.events import event_bus, EventType
from core.scheduler import DAGScheduler
from core.protocol import Message, MessageType, AgentResponse
from core.task import Task
from agents.registry import AgentRegistry

WORKSPACE_ROOT = Path("/Users/saiganeshongolu/sAI").resolve()

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
            kernel.register_service("tool_registry", tool_reg)

            repo_ctx = RepositoryContext.get_cached_context(WORKSPACE_ROOT)
            kernel.register_service("repository", repo_ctx)
        except Exception:
            pass

    def run(self, task: Task):
        event_bus.publish(EventType.TASK_STARTED, {"task_id": task.id}, source="Orchestrator")

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
            event_bus.publish(EventType.AGENT_STARTED, {"agent": agent.name, "turn": turns}, source="Orchestrator")

            from core.security import check_and_reset_halt
            if check_and_reset_halt():
                raise InterruptedError("Agent loop execution halted by user interrupt.")

            message = agent.run(task.context)
            response = message.metadata.get("response")

            if not hasattr(task, "linter_attempts"):
                task.linter_attempts = {}
                
            if response and response.actions:
                action_turns = 0
                while response.actions and action_turns < 5:
                    action_turns += 1
                    for action in response.actions:
                        if check_and_reset_halt():
                            raise InterruptedError("Agent loop execution halted by user interrupt.")
                        tool_name = action.get("tool", "")
                        tool_args = action.get("args", {})
                        
                        # Support path or target_file arguments
                        target_path = tool_args.get("path", tool_args.get("target_file", tool_args.get("TargetFile", "")))
                        target_cmd = tool_args.get("command", tool_args.get("CommandLine", ""))
                        
                        update_current_activity({
                            "status": "executing",
                            "tool": tool_name,
                            "path": str(target_path),
                            "command": str(target_cmd)
                        })
                        
                        tool_result = self.execution_engine.execute(action)
                        
                        update_current_activity({
                            "status": "thinking",
                            "agent": agent.name,
                            "tool": "",
                            "path": "",
                            "command": ""
                        })
                        
                        tool_content = tool_result.stdout if tool_result.success else tool_result.stderr
                        
                        if tool_result.success and tool_name in ["write_file", "patch_file"]:
                            RepositoryContext.invalidate_cache(WORKSPACE_ROOT)
                            file_arg = tool_args.get("path", tool_args.get("target_file", tool_args.get("TargetFile", "")))
                            if file_arg:
                                file_path = Path(file_arg)
                                if not file_path.is_absolute():
                                    file_path = WORKSPACE_ROOT / file_path
                                if file_path.exists():
                                    tech_stack = ""
                                    for note in task.context.memory.notes:
                                        if "Framework Context:" in note:
                                            tech_stack = note.split("Framework Context:")[-1].split(".")[0].strip().lower()
                                            
                                    linter_err = run_linter_checks(WORKSPACE_ROOT, tech_stack, file_path)
                                    if linter_err:
                                        attempts = task.linter_attempts.get(str(file_path), 0) + 1
                                        task.linter_attempts[str(file_path)] = attempts
                                        if attempts > 3:
                                            tool_content += f"\n\n❌ [AUTO-LINTER ABORTED]: Linter checks failed {attempts} times on {file_path.name}. Syntax verification aborted to prevent infinite loops. Error:\n{linter_err}"
                                        else:
                                            tool_content += f"\n\n⚠️ AUTO-LINTER COMPILATION WARNING:\n{linter_err}\nYour code has syntax or compile errors. You MUST edit the file to fix this error immediately."
                        result_msg = Message(
                            sender="System",
                            receiver=agent.name,
                            type=MessageType.TOOL_RESULT,
                            payload={"content": tool_content}
                        )
                        task.context.conversation.add(result_msg)

                    message = agent.run(task.context)
                    response = message.metadata.get("response")

            if agent.name == "Planner":
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

            task.context.conversation.add(message)
            event_bus.publish(EventType.AGENT_FINISHED, {"agent": agent.name, "msg": message}, source="Orchestrator")

            if response and response.finished:
                break

            if response and response.next_agent and str(response.next_agent).strip():
                current_agent_name = str(response.next_agent).strip()
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