from typing import Any

# Per-message cap and how many recent messages stay uncompacted in the prompt.
MAX_MESSAGE_CHARS = 8000
RAW_TAIL_MESSAGES = 10


class PromptBuilder:
    @staticmethod
    def build(context: Any, repository: Any, memory: Any, conversation: Any) -> str:
        """
        Builds a compiled, clean context prompt for the agent.
        """
        memory_snapshot = memory.snapshot() if hasattr(memory, "snapshot") else str(memory)
        
        # Load and serialize persistent memory graph
        graphiti_str = "No active historical facts mapped in memory graph."
        aider_directive = ""
        settings = {}
        try:
            from core.settings import load_settings
            settings = load_settings()
            
            # Graphiti on/off check
            if settings.get("graphiti_mode", True):
                from memory.graphiti import GraphitiMemory
                graphiti = GraphitiMemory()
                parts = []
                
                # Extract search keywords from the current working goal
                goal_str = getattr(memory, "goal", "")
                goal_words = set(str(goal_str).lower().split()) if goal_str else set()
                
                def rank_items(items):
                    scored = []
                    for item in items:
                        item_words = set(item.lower().split())
                        overlap = len(goal_words.intersection(item_words)) if goal_words else 1
                        scored.append((overlap, item))
                    # Sort descending by relevance overlap count
                    scored.sort(key=lambda x: x[0], reverse=True)
                    return [item for _, item in scored[:5]]
                
                relevant_facts = rank_items(graphiti.facts)
                relevant_decisions = rank_items(graphiti.decisions)
                
                if relevant_facts:
                    parts.append("Relevant Facts Mapped:\n" + "\n".join(f" - {f}" for f in relevant_facts))
                if relevant_decisions:
                    parts.append("Relevant Logged Decisions:\n" + "\n".join(f" - {d}" for d in relevant_decisions))
                if parts:
                    graphiti_str = "\n\n".join(parts)
            else:
                graphiti_str = "Persistent Memory Graph (Graphiti) is disabled."
                
            # Aider mode check
            if not settings.get("aider_mode", True):
                aider_directive = "\n⚠️ CRITICAL WARNING: Aider patch application is disabled. Do NOT use patch_file or search-and-replace block formatting tools. You must write complete file updates using write_file.\n"
        except Exception:
            pass
        
        repo_snapshot = ""
        if repository and hasattr(repository, "get_repo_map"):
            # ~4 chars/token, same estimate as llm/runtime.py::ContextCompressor.
            repo_budget_tokens = settings.get("repo_map_token_budget", 1500)
            repo_snapshot = repository.get_repo_map(max_chars=int(repo_budget_tokens) * 4)
        else:
            repo_snapshot = "No active repository symbols mapped."

        conv_text = ""
        if conversation and hasattr(conversation, "all"):
            msgs = conversation.all()
            num_msgs = len(msgs)
            for idx, msg in enumerate(msgs):
                sender = getattr(msg, "sender", "Unknown")
                receiver = getattr(msg, "receiver", "Unknown")
                payload = getattr(msg, "payload", {})
                if hasattr(msg, "content"):
                    content = msg.content
                elif isinstance(payload, dict):
                    content = payload.get("content", payload.get("summary", str(payload)))
                    # Show which tools an agent asked for, so in the tool-use loop
                    # it can line its own requests up with the results that follow.
                    requested = payload.get("actions") or []
                    if requested:
                        from agents.loop import describe_action
                        content = f"{content}\n  (requested: {', '.join(describe_action(a) for a in requested)})"
                else:
                    content = str(payload)

                content = str(content)
                if len(content) > MAX_MESSAGE_CHARS:
                    content = content[:MAX_MESSAGE_CHARS] + f"\n...[{len(content) - MAX_MESSAGE_CHARS} more chars truncated]"

                # Structural context compaction: keep the most recent messages raw, compact
                # older ones. The window is wide enough to hold a few loop steps' worth of
                # tool results (a read_file result must survive until the edit that uses it).
                if num_msgs > RAW_TAIL_MESSAGES + 6 and idx < num_msgs - RAW_TAIL_MESSAGES:
                    if isinstance(payload, dict) and "summary" in payload:
                        content = f"[Summary of Action]: {payload['summary']}"
                    else:
                        lines = str(content).splitlines()
                        first_line = lines[0] if lines else ""
                        content = first_line[:120] + " ... [Tool Output Compacted to Save Tokens] ..."
                
                conv_text += f"{sender} to {receiver}: {content}\n"
        else:
            conv_text = "No history."

        from core.project_instructions import load_project_instructions
        instructions, _ = load_project_instructions()
        instructions_section = ""
        if instructions:
            instructions_section = (
                "[PROJECT INSTRUCTIONS (SAI.md) -- follow these; they override defaults]\n"
                f"{instructions}\n\n"
            )

        from tools.todo import get_todos, render_todos
        todos = get_todos()
        todo_section = f"[CURRENT TODO LIST -- keep it updated with todo_write]\n{render_todos(todos)}\n\n" if todos else ""

        prompt = f"""{instructions_section}{todo_section}[SYSTEM CONTEXT & SERVICE REGISTRIES]{aider_directive}

[PERSISTENT MEMORY GRAPH (GRAPHITI)]
{graphiti_str}

[REPOSITORY FILE STRUCTURE & SYMBOL GRAPH]
{repo_snapshot}

[SHARED WORKING MEMORY]
{memory_snapshot}

[CONVERSATION HISTORY LOGS]
{conv_text}"""
        return prompt
