# System Architecture - sAI

This document details the core architectural design of the sAI platform.

## High-Level Overview
sAI operates as an **Event-Driven Orchestrator** managing specialized, multi-agent workflows. The system is designed to minimize local resource footprint by offloading execution to cloud-based LLM APIs (initially NVIDIA API) while keeping directory tracking, memory indices, and codebase execution local.

```
                  +-----------------------------------+
                  |           Terminal UI             |
                  +-----------------+-----------------+
                                    | Events / Streams
                                    v
                  +-----------------------------------+
                  |        sAI Orchestrator           |
                  +--------+-----------------+--------+
                           |                 |
            +--------------v---+         +---v--------------+
            |    Agent Hub     |         |   Event Stream   |
            | (Planner, Coder) |         |  (Observability) |
            +--------+---------+         +------------------+
                     |
         +-----------+-----------+
         |                       |
         v                       v
+--------+--------+     +--------+--------+
| Memory System   |     | Tool Ecosystem  |
| (Short / Long)  |     | (Git, Parser,   |
| (Graphiti)      |     |  Aider, DDG)    |
+-----------------+     +-----------------+
```

---

## Project Workspace Structure
A single workspace represents a single project context.
Multiple chat conversations share the same repository definition, memory database, and settings.

* **Chat**: Active conversational interfaces and history tracking.
* **Repository**: Path mappings, tree-sitter indices, and file metadata.
* **Files**: Access interfaces for file loading, parsing, and modification.
* **Memory**: Local SQLite or Neo4j databases and Graphiti schemas.
* **Tasks**: Current task queues and execution boards.
* **Terminal**: PTY/Subprocess shell handles.
* **Git**: Repository version control hooks.
* **Settings**: Workspace-specific configuration parameters.

---

## Multi-Agent Collaboration Protocol
Agents do not communicate using arbitrary text chat. They follow a **structured protocol** to pass context, tasks, and state updates:

1. **Information Viewports**: Every active agent has access to a structured payload containing:
   * Current Goal/Sub-goal.
   * Working Memory snippet.
   * Conversation context.
   * Artifacts (code modifications/reports).
   * Global Project State.
   * Repository Map (classes/functions/files list).
   * Long-term Memory associations.
   * Previous Tool Results.
2. **State Transitions**: The Orchestrator manages transitions between agent states (e.g., Planner -> Researcher -> Coder -> Reviewer) through event emissions.

---

## Event-Driven Orchestration
The backbone of sAI is an event loop that logs and distributes operations. All key actions emit structured events:
```json
{
  "event_id": "evt_9823f9823h",
  "timestamp": "2026-07-20T21:26:00Z",
  "type": "tool_started",
  "source": "CoderAgent",
  "data": {
    "tool": "edit_file",
    "params": {
      "path": "src/main.py",
      "line_range": [10, 20]
    }
  }
}
```
This enables real-time rendering in the Terminal UI and builds an audit-trail for debugging and security verification.
