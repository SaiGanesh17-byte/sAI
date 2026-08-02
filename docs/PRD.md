# Product Requirements Document (PRD) - sAI v1.0

## Vision
Build an open-source AI Operating System that provides a Perplexity Labs-level experience for software engineering, research, automation, and project management while remaining modular, privacy-first, and extensible.

Unlike existing coding agents, sAI is not a single LLM. It is an orchestration platform where specialized agents collaborate, use tools, maintain long-term memory, and continuously improve project understanding.

Rather than trying to clone Perplexity exactly, sAI aims for **"Perplexity Labs + Cursor + Claude Code"**:
* **Perplexity Labs** for multi-agent planning and transparent execution.
* **Cursor** for deep repository understanding and code editing.
* **Claude Code** for autonomous tool use, safe execution, and iterative reasoning.

---

## Objectives
The system must:
* Work from the terminal.
* Support multiple projects.
* Support multiple conversations per project.
* Coordinate multiple AI models.
* Use cloud-hosted open-source models (NVIDIA API initially).
* Minimize local RAM usage.
* Keep long-term memory.
* Work completely asynchronously.
* Support plugins.
* Be provider-independent.

---

## Core Principles
* **Modular**: Components are loosely coupled and swap-ready.
* **Provider Agnostic**: Workloads can run across different LLM backends.
* **Event Driven**: Orchestrator, agents, and tools publish/subscribe to a shared event stream.
* **Multi-Agent**: Complex tasks are solved through agent division of labor.
* **Memory First**: Dynamic persistence of working context and global knowledge.
* **Tool First**: Agents must be native tool consumers.
* **Streaming First**: Outputs, logs, and tool execution are streamed to the interface in real-time.
* **Extensible**: Rich plugin SDK for additions.
* **Local First**: Keep tooling, files, and parses local (even when using cloud models).

---

## Observability
Everything in sAI is treated as an event. The system logs and displays:
* Agent Started / Finished
* Tool Started / Finished
* Memory Updated
* LLM Called
* Token Usage
* Latency
* Cost
* Errors and Exceptions

---

## Security
* **Secrets**: Encrypted or loaded securely via environments.
* **Sandbox Execution**: Commands run in isolated/controlled contexts.
* **Permission System**: Fine-grained user approval/policy controls.
* **Confirmation**: Required before performing destructive actions (e.g., git resets, system overrides).
* **Read-only Mode**: Optional safe-exploration mode.
* **Audit Logs**: Full trace logs of all activity.

---

## Extensibility
The platform supports plugins that can dynamically register:
* New Agents
* Custom Tools
* Additional Providers
* Custom UI Panels
* Special Memory Modules
* Extra CLI Commands
