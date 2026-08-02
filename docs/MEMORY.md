# Memory System - sAI

This document outlines the hierarchy and architecture of the memory systems implemented within sAI.

## Memory Classification

```
                  +-----------------------------------+
                  |            sAI Memory             |
                  +-----------------+-----------------+
                                    |
         +--------------------------+--------------------------+
         |                                                     |
         v                                                     v
+--------+--------+                                   +--------+--------+
|   Short-Term    |                                   |    Long-Term    |
+--------+--------+                                   +--------+--------+
         |                                                     |
         +--> Working Memory (current task/context)            +--> Conversation History
                                                               |
                                                               +--> Knowledge Memory (Graphiti)
                                                                    (Facts, Entities, Decisions)
```

---

## Short-Term Memory
### Working Memory
* **Scope**: Active execution context of the current task.
* **Storage**: In-memory ephemeral buffers or quick-serialize JSON files.
* **Function**: Keeps track of the immediate step-by-step goals, intermediate variables, shell command results, and recent code diffs. It ensures the active agent doesn't lose context between tool executions.

---

## Long-Term Memory
### Conversation History
* **Scope**: History of user prompts, orchestrator replies, and system events.
* **Storage**: Local JSON Lines (JSONL) files stored on disk.
* **Function**: Provides raw dialogue logs that can be summarized or retrieved for past discussions.

### Knowledge Memory (Graphiti-based)
* **Scope**: Structural facts, conceptual mappings, and developer decisions.
* **Storage**: Integrates with **Graphiti** (knowledge graph backend) utilizing relational mappings.
* **Concepts tracked**:
  * **Facts**: Project rules, coding guidelines, configuration settings.
  * **Entities**: Directories, packages, external dependencies, system modules.
  * **Relationships**: Module dependencies, tool mappings (e.g., `Module A uses Database B`).
  * **Decisions**: Technical debt choices, design trade-offs, architecture selections.
