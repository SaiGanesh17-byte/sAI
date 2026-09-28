import json
import re
from typing import Dict, Any, List
from core.context import TaskContext
from core.protocol import AgentResponse, Message, MessageType, Artifact
from core.kernel import kernel
from core.prompt_builder import PromptBuilder

def parse_json_response(content: str, agent_name: str) -> dict:
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

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {
        "memory_update": content,
        "summary": f"{agent_name} processing completed.",
        "reasoning": ["Failed to parse structured JSON from LLM output. Raw output saved."],
        "confidence": 0.5,
        "finished": False,
        "actions": []
    }

class AgentRuntime:
    """
    Generic Agent Runtime. Executes reasoning loops by compiling context 
    prompts and dispatching requests to the LLM Runtime.
    """
    def __init__(self, name: str, role: str, system_prompt: str, model: str, temperature: float = 0.2):
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.model = model
        self.temperature = temperature

    def execute_turn(self, context: TaskContext) -> Message:
        repository = kernel.get_service("repository")
        memory = context.memory
        conversation = context.conversation

        compiled_context = PromptBuilder.build(context, repository, memory, conversation)

        tool_reg = kernel.get_service("tool_registry")
        tool_desc = []
        if tool_reg:
            for idx, (name, details) in enumerate(tool_reg.list_tools().items(), 1):
                desc = details.get("description", "")
                schema = details.get("schema", {})
                tool_desc.append(f"{idx}. {name}: {desc}\n   Usage schema: {json.dumps(schema)}")
        else:
            tool_desc = [
                "1. read_file: Read files inside the workspace sandbox.",
                "2. write_file: Write text content to a file.",
                "3. execute_command: Run a console command."
            ]
        tools_instruction = "\n".join(tool_desc)

        roster = kernel.list_agents()
        roster_lines = []
        for agent_name, agent_obj in roster.items():
            if agent_name == self.name:
                continue
            role = getattr(agent_obj, "role", "")
            roster_lines.append(f"- {agent_name}: {role}")
        roster_instruction = "\n".join(roster_lines) if roster_lines else "(no other agents registered)"

        system_instruction = f"""{self.system_prompt}

You are the '{self.name}' agent in a collaborative multi-agent loop.
Your role is: {self.role}

AVAILABLE AGENTS YOU CAN HAND OFF TO (set "next_agent" to one of these EXACT names, or null if you are finished):
{roster_instruction}

AVAILABLE TOOLS:
{tools_instruction}

HOW YOUR TURN WORKS (a tool-use loop):
- Put the tool calls you need in "actions". They run after you respond, and each result comes
  back to you as a "System to {self.name}: [tool(target) -> ok|failed|declined]" line in the
  conversation history below, and you respond again.
- Work in small steps: e.g. read or list before you edit, then check the result.
- While you are requesting actions, "summary" says what you are about to do ("Reading app.py to
  find the router."). Never claim an action succeeded before you have seen its "-> ok" result.
- When the work is done (or you cannot proceed), return "actions": [] and make "summary" your
  final answer to the user: what was actually done, based on the tool results -- including any
  failures -- plus file paths the user will want.
- If a result says "declined", do not retry that action.

Your response MUST be a JSON object containing these keys:
- "memory_update": overwrite string content updates for your section of shared memory.
- "summary": see above -- what you are about to do, or your final answer when "actions" is empty.
- "reasoning": list of short bulleted thought steps.
- "confidence": float 0.0 to 1.0.
- "finished": boolean indicating if overall goal is fully achieved.
- "next_agent": target next agent name (e.g., 'Coder', 'Reviewer') or null if complete.
- "actions": list of tool actions to run now (empty when you are done). Format: [{{"tool": "read_file", "args": {{"path": "..."}}}}, ...]

Respond ONLY with the JSON block. Do not include markdown wraps or conversational greetings outside of JSON.
"""

        full_prompt = f"{system_instruction}\n\n{compiled_context}"

        from core.settings import load_settings
        settings = load_settings()
        query_kwargs = {}
        if settings.get("agents_json_mode"):
            query_kwargs["response_format"] = {"type": "json_object"}

        llm_runtime = kernel.get_service("llm_runtime")

        # Optional real function-calling path (off by default -- see docs/MODELS.md).
        # Additive: when tool_calls come back, they're merged into the same
        # `actions` list shape the rest of the pipeline already expects, so
        # Orchestrator/ExecutionEngine need no changes either way.
        if settings.get("use_tool_calling") and tool_reg:
            tools_schema = [
                {
                    "type": "function",
                    "function": {
                        "name": t_name,
                        "description": details.get("description", ""),
                        "parameters": details.get("schema") or {"type": "object", "properties": {}},
                    },
                }
                for t_name, details in tool_reg.list_tools().items()
            ]
            result = llm_runtime.query_with_tools(
                full_prompt, task_kind=self.name, tools=tools_schema, temperature=self.temperature, model=self.model
            )
            raw_response = result.get("content") or ""
            tool_calls = result.get("tool_calls") or []

            if raw_response.strip():
                parsed = parse_json_response(raw_response, self.name)
            else:
                # The model called tools without also returning a JSON body -- fall
                # back to sensible defaults for the fields tool_calls doesn't carry.
                parsed = {
                    "memory_update": "",
                    "summary": f"{self.name} invoked {len(tool_calls)} tool(s)." if tool_calls else f"{self.name} produced no output.",
                    "reasoning": [],
                    "confidence": 0.9,
                    "finished": False,
                    "next_agent": None,
                    "actions": [],
                    "findings": [],
                }

            if tool_calls:
                parsed["actions"] = [{"tool": tc["name"], "args": tc["arguments"]} for tc in tool_calls]
        else:
            raw_response = llm_runtime.query(
                full_prompt, task_kind=self.name, temperature=self.temperature, model=self.model, **query_kwargs
            )
            parsed = parse_json_response(raw_response, self.name)
        
        # Schema validation & single-turn retry loop
        if self.name == "Reviewer":
            is_valid = True
            error_msg = ""
            # Check if default parse envelope was returned due to JSON Decode Error
            if parsed.get("summary") == "Reviewer processing completed." and "Failed to parse structured JSON" in "".join(parsed.get("reasoning", [])):
                is_valid = False
                error_msg = "Invalid JSON structure (failed to parse JSON from LLM output)."
            elif "findings" not in parsed:
                is_valid = False
                error_msg = "Missing key: 'findings'."
            elif not isinstance(parsed.get("findings"), list):
                is_valid = False
                error_msg = "Key 'findings' must be a list."
                
            if not is_valid:
                retry_prompt = f"{full_prompt}\n\n⚠️ Error: The last response failed validation checks: {error_msg}. Please regenerate your response as a valid JSON block containing all keys."
                try:
                    raw_response = llm_runtime.query(
                        retry_prompt, task_kind=self.name, temperature=self.temperature, model=self.model, **query_kwargs
                    )
                    parsed = parse_json_response(raw_response, self.name)
                    
                    is_valid = True
                    if parsed.get("summary") == "Reviewer processing completed." and "Failed to parse structured JSON" in "".join(parsed.get("reasoning", [])):
                        is_valid = False
                        error_msg = "Invalid JSON structure on retry."
                    elif "findings" not in parsed:
                        is_valid = False
                        error_msg = "Missing key 'findings' on retry."
                    elif not isinstance(parsed.get("findings"), list):
                        is_valid = False
                        error_msg = "Key 'findings' must be a list on retry."
                except Exception as e:
                    is_valid = False
                    error_msg = f"Exception during retry query: {e}"
                    
            if not is_valid:
                raise ValueError(f"Reviewer output failed schema validation check after retry. Error: {error_msg}. Raw Response: {raw_response[:300]}")
        
        memory_update = parsed.get("memory_update", "")
        summary = parsed.get("summary", "Processed.")
        reasoning = parsed.get("reasoning", [])
        confidence = parsed.get("confidence", 0.95)
        finished = parsed.get("finished", False)
        next_agent = parsed.get("next_agent", None)
        actions = parsed.get("actions", [])

        name_lower = self.name.lower()
        if "architect" in name_lower:
            memory.architecture = memory_update
        elif "researcher" in name_lower:
            memory.research = memory_update
        elif "coder" in name_lower:
            memory.implementation = memory_update
        elif "reviewer" in name_lower:
            memory.review = memory_update

        findings = parsed.get("findings", [])
        payload = {
            "summary": summary,
            "reasoning": reasoning,
            "confidence": confidence,
            "finished": finished,
            "next_agent": next_agent,
            "actions": actions,
            "memory_update": memory_update,
            "findings": findings
        }

        # Build message envelope
        msg = Message(
            sender=self.name,
            receiver="Orchestrator",
            type=MessageType.SUMMARY,
            payload=payload
        )
        # Create an AgentResponse inside metadata/property for orchestrator turns
        msg.metadata["response"] = AgentResponse(
            agent=self.name,
            summary=summary,
            reasoning=reasoning,
            actions=actions,
            confidence=confidence,
            next_agent=next_agent,
            finished=finished,
            findings=findings
        )
        return msg

    def run(self, context: TaskContext) -> Message:
        return self.execute_turn(context)

