from typing import Any

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
            repo_snapshot = repository.get_repo_map()
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
                else:
                    content = str(payload)
                
                # Structural context compaction: keep last 4 messages raw, compact older ones
                if num_msgs > 10 and idx < num_msgs - 4:
                    if isinstance(payload, dict) and "summary" in payload:
                        content = f"[Summary of Action]: {payload['summary']}"
                    else:
                        lines = str(content).splitlines()
                        first_line = lines[0] if lines else ""
                        content = first_line[:120] + " ... [Tool Output Compacted to Save Tokens] ..."
                
                conv_text += f"{sender} to {receiver}: {content}\n"
        else:
            conv_text = "No history."

        prompt = f"""[SYSTEM CONTEXT & SERVICE REGISTRIES]{aider_directive}

[PERSISTENT MEMORY GRAPH (GRAPHITI)]
{graphiti_str}

[REPOSITORY FILE STRUCTURE & SYMBOL GRAPH]
{repo_snapshot}

[SHARED WORKING MEMORY]
{memory_snapshot}

[CONVERSATION HISTORY LOGS]
{conv_text}"""
        return prompt
