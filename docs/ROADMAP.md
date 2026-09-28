# Development Roadmap - sAI

This document outlines the six developmental phases of the sAI project. Status lines
were added retroactively — actual development did not follow this phase order exactly
(the agent roster, REPL, and UI work happened before Graphiti/consensus routing, for
instance), so treat the phases as a map of scope, not a literal timeline.

```
+-----------+     +-----------+     +-----------+     +-----------+     +-----------+     +-----------+
|  Phase 1  | --> |  Phase 2  | --> |  Phase 3  | --> |  Phase 4  | --> |  Phase 5  | --> |  Phase 6  |
| Core Engine|    |Integrations|    |Memory/Graph|    | Routing/AI|    |Terminal UI|    |Desktop/Web|
+-----------+     +-----------+     +-----------+     +-----------+     +-----------+     +-----------+
```

---

## Phase 1: Core Engine & Nvidia API
**Status: Done, provider since migrated.** The multi-agent coordination loop, working
memory, and conversation history all shipped as planned. The provider did not stay
NVIDIA — every NVIDIA model in use hit end-of-life on their API and the active provider
is now OpenRouter (see `docs/MODELS.md`); NVIDIA support remains in the codebase but
unused by default.
* **Objective**: Build the asynchronous multi-agent coordination loop.
* **Key Milestones**:
  * Multi-agent execution loop with Planner and Coder.
  * Integration with NVIDIA LLM API (Qwen3 Coder, Nemotron, Llama).
  * Short-term working memory data structures.
  * Local file-based conversation history logs.
  * Scaffolding project config (`pyproject.toml`, `.env`, `requirements.txt`).

## Phase 2: Local Integrations & Searching
**Status: Done.** Git, filesystem, terminal, Python execution, and search tools are all
implemented under `tools/`; repository indexing/symbol parsing under `repository/`.
* **Objective**: Empower agents with basic local tools and web access.
* **Key Milestones**:
  * Integrating DuckDuckGo search libraries.
  * Multi-format parser engine for plain text, CSV, PDF, Docx, etc.
  * Repository indexing and AST parsing (using Tree-sitter and Grep-ast).
  * Git workspace wrapper using GitPython.

## Phase 3: Graphiti Knowledge Graphs
**Status: Partial.** `memory/graphiti.py` exists and is wired into
`core/prompt_builder.py` (relevance-ranked facts/decisions injected per turn) and the
web UI's Memory panel, but it's a simpler fact/decision store, not the full
relationship-graph/semantic-extraction system originally scoped here.
* **Objective**: Introduce deep relationship-mapped memory.
* **Key Milestones**:
  * Core Graphiti API implementation.
  * Connecting local graph databases (e.g., NetworkX or local Neo4j).
  * Semantic fact-extraction from conversations and code changes.
  * Automatic relationship generation (linking decisions to files and commits).

## Phase 4: Collaborative Routing & Consensus
**Status: Not started**, beyond the Jev fast-routing layer (`jev/decision.py`), which
covers "which agent should handle this" but not parallel agent execution or
consensus/voting between agents.
* **Objective**: Support agent groups working together in parallel.
* **Key Milestones**:
  * Advanced LLM routing algorithms (calculating dynamic tasks based on context windows and latency).
  * Parallel agent runs (multiple tasks executing concurrently).
  * Consensus and voting protocols (Reviewer agent checks Coder work; Coder corrects based on reviews).

## Phase 5: WhatsApp-Like Terminal UI
**Status: Done, different implementation.** `app/repl.py` is a real interactive REPL
with live event-driven streaming and inline permission prompts; `ui/terminal.py` is a
Textual TUI with the same. Neither literally mimics a WhatsApp layout, but the
functional goals (collapsible detail, live tool/reasoning visibility, inline user
prompts) are met.
* **Objective**: Build a clean, transparent, and responsive console layout.
* **Key Milestones**:
  * Interactive CLI console using `Textual` or `Rich`.
  * WhatsApp-like layout where messages represent agent conversations.
  * Collapsible sections detail: thinking steps, tool invocations, files modified, and memory modifications.
  * User audit and input prompts directly injected into the flow.

## Phase 6: Desktop App, Web UI, Plugins
**Status: Partial.** Web UI exists (`app/main.py` + `ui/`) and is the most actively
developed surface. No Electron desktop shell, no plugin SDK, no MCP support.
* **Objective**: Standardize production deployment and extensibility.
* **Key Milestones**:
  * Desktop shell using Electron.
  * Dynamic web interface using React, Tailwind CSS, and FastAPI backend.
  * Plugin SDK for custom agent classes, memory databases, and third-party API keys.
  * Model Context Protocol (MCP) tool support.
