# Development Roadmap - sAI

This document outlines the six developmental phases of the sAI project.

```
+-----------+     +-----------+     +-----------+     +-----------+     +-----------+     +-----------+
|  Phase 1  | --> |  Phase 2  | --> |  Phase 3  | --> |  Phase 4  | --> |  Phase 5  | --> |  Phase 6  |
| Core Engine|    |Integrations|    |Memory/Graph|    | Routing/AI|    |Terminal UI|    |Desktop/Web|
+-----------+     +-----------+     +-----------+     +-----------+     +-----------+     +-----------+
```

---

## Phase 1: Core Engine & Nvidia API (Current Phase)
* **Objective**: Build the asynchronous multi-agent coordination loop.
* **Key Milestones**:
  * Multi-agent execution loop with Planner and Coder.
  * Integration with NVIDIA LLM API (Qwen3 Coder, Nemotron, Llama).
  * Short-term working memory data structures.
  * Local file-based conversation history logs.
  * Scaffolding project config (`pyproject.toml`, `.env`, `requirements.txt`).

## Phase 2: Local Integrations & Searching
* **Objective**: Empower agents with basic local tools and web access.
* **Key Milestones**:
  * Integrating DuckDuckGo search libraries.
  * Multi-format parser engine for plain text, CSV, PDF, Docx, etc.
  * Repository indexing and AST parsing (using Tree-sitter and Grep-ast).
  * Git workspace wrapper using GitPython.

## Phase 3: Graphiti Knowledge Graphs
* **Objective**: Introduce deep relationship-mapped memory.
* **Key Milestones**:
  * Core Graphiti API implementation.
  * Connecting local graph databases (e.g., NetworkX or local Neo4j).
  * Semantic fact-extraction from conversations and code changes.
  * Automatic relationship generation (linking decisions to files and commits).

## Phase 4: Collaborative Routing & Consensus
* **Objective**: Support agent groups working together in parallel.
* **Key Milestones**:
  * Advanced LLM routing algorithms (calculating dynamic tasks based on context windows and latency).
  * Parallel agent runs (multiple tasks executing concurrently).
  * Consensus and voting protocols (Reviewer agent checks Coder work; Coder corrects based on reviews).

## Phase 5: WhatsApp-Like Terminal UI
* **Objective**: Build a clean, transparent, and responsive console layout.
* **Key Milestones**:
  * Interactive CLI console using `Textual` or `Rich`.
  * WhatsApp-like layout where messages represent agent conversations.
  * Collapsible sections detail: thinking steps, tool invocations, files modified, and memory modifications.
  * User audit and input prompts directly injected into the flow.

## Phase 6: Desktop App, Web UI, Plugins
* **Objective**: Standardize production deployment and extensibility.
* **Key Milestones**:
  * Desktop shell using Electron.
  * Dynamic web interface using React, Tailwind CSS, and FastAPI backend.
  * Plugin SDK for custom agent classes, memory databases, and third-party API keys.
  * Model Context Protocol (MCP) tool support.
