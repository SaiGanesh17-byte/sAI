from dataclasses import dataclass
import json
import re
from typing import Dict, Any

from core.context import TaskContext
from core.message_bus import Message
from core.protocol import AgentResponse
from llm.router import query_model


def parse_json_response(content: str, agent_name: str) -> dict:
    """
    Parses JSON from the model's response. Falls back to a standard
    structure if JSON parsing fails.
    """
    # Clean up markdown formatting if present
    cleaned = content.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt to locate any JSON object in the string
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Return fallback structure
    return {
        "memory_update": content,
        "summary": f"{agent_name} processing completed.",
        "reasoning": ["Failed to parse structured JSON from LLM output. Raw output saved to memory."],
        "confidence": 0.5,
        "finished": False
    }


@dataclass
class Agent:
    name: str
    role: str
    model: str
    temperature: float
    priority: int
    system_prompt: str

    def run(self, context: TaskContext) -> Message:
        """
        Execute the agent using the NVIDIA API.
        """
        memory = context.memory

        # -----------------------------------------------------
        # 1. Build Agent-specific Context & Messages
        # -----------------------------------------------------
        conversation_history_text = ""
        for msg in context.conversation.all():
            conversation_history_text += f"{msg.sender} to {msg.receiver}: {msg.content}\n"

        system_instruction = f"""{self.system_prompt}

You are the '{self.name}' agent in a collaborative multi-agent loop.
Your role is: {self.role}

You have access to the shared working memory, conversation history, and local environment tools.

AVAILABLE TOOLS:
You can request local tool executions by adding objects to the "actions" array in your JSON output. The orchestrator will run them and return the outcomes to your conversation history context on the next turn.

1. read_file: Reads file contents inside the workspace path.
   Usage: {{"tool": "read_file", "args": {{"path": "relative/path/to/file.py"}}}}
2. write_file: Writes complete contents to a file (creates directories automatically).
   Usage: {{"tool": "write_file", "args": {{"path": "relative/path/to/file.py", "content": "file contents..."}}}}
3. execute_command: Runs a console terminal command (e.g., compiling, running tests, pip commands).
   Usage: {{"tool": "execute_command", "args": {{"command": "python3 -m unittest discover"}}}}

Your response MUST be a JSON object containing the following keys:
- "memory_update": A detailed string containing your actual work/updates that should overwrite your section of the shared memory.
- "summary": A brief 1-line summary of what you did.
- "reasoning": A list of short strings outlining your step-by-step thinking or findings.
- "confidence": A float between 0.0 and 1.0 representing your confidence in this solution.
- "finished": A boolean indicating if the overall goal has been completely achieved. Set this to true once files are written, tests pass, and you are finished.
- "next_agent": A string representing the name of the next agent that should run (e.g., 'Researcher', 'Coder', 'Reviewer', or null if you want to complete the task).
- "actions": A list of tool actions you want to run. If you don't need any tool, this is an empty list []. Format: [{{"tool": "read_file", "args": {{"path": "..."}}}}, ...]

IMPORTANT: Respond ONLY with the JSON object. Do not include introductory or concluding conversational text outside the JSON.
"""

        user_content = f"""Goal:
{memory.goal}

Current Shared Working Memory:
{memory.snapshot()}

Conversation History:
{conversation_history_text}
"""

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_content}
        ]

        # -----------------------------------------------------
        # 2. Query the LLM model
        # -----------------------------------------------------
        raw_response = query_model(
            model=self.model,
            messages=messages,
            temperature=self.temperature
        )

        # -----------------------------------------------------
        # 3. Parse Response & Update Working Memory
        # -----------------------------------------------------
        parsed = parse_json_response(raw_response, self.name)
        memory_val = parsed.get("memory_update", "")

        if self.name == "Architect":
            memory.architecture = memory_val
        elif self.name == "Researcher":
            memory.research = memory_val
        elif self.name == "Coder":
            memory.implementation = memory_val
        elif self.name == "Reviewer":
            memory.review = memory_val

        # -----------------------------------------------------
        # 4. Build structured response & Message
        # -----------------------------------------------------
        response = AgentResponse(
            agent=self.name,
            summary=parsed.get("summary", "Task processed."),
            reasoning=parsed.get("reasoning", []),
            confidence=parsed.get("confidence", 0.95),
            finished=parsed.get("finished", False),
            next_agent=parsed.get("next_agent", None),
            actions=parsed.get("actions", []),
            findings=parsed.get("findings", [])
        )

        return Message(
            sender=self.name,
            receiver="Orchestrator",
            content=memory.snapshot(),
            response=response
        )