# Agent Definitions - sAI

This document defines the roles, parameters, and configuration format for sAI agents.

## Core Agents (Phase 1)
* **Planner**: Deconstructs goals into manageable sub-tasks, assigns workflows to specific agents, and tracks task status.
* **Architect**: Designs system layouts, reviews imports, sets directory structures, and ensures modular code patterns.
* **Researcher**: Explores existing code libraries, reads external documentations, and searches the web to find answers.
* **Coder**: Writes, modifies, and edits code based on the implementation plans and research findings.
* **Reviewer**: Performs verification, syntax checks, checks lint errors, runs tests, and validates code output quality.

---

## Future Agents
* **Security**: Audits libraries and code changes for vulnerabilities or credential leaks.
* **Database**: Optimizes queries, structures migrations, and manages schema updates.
* **DevOps**: Configures CI/CD, manages Dockerfiles, and handles cloud deployment pipelines.
* **Performance**: profiles execution time, measures RAM/CPU usage, and refactors slow functions.
* **UI Designer**: Formats terminal visual frames, designs layouts, and mocks component interactions.
* **Documentation**: Maintains READMEs, keeps docstrings current, and builds static docs.
* **Testing**: Writes unit and integration tests automatically.
* **Debugger**: Investigates run failures, parses stack traces, and isolates root-cause bugs.
* **Data Scientist / ML Engineer**: Analyzes local data schemas, coordinates local dataset steps.
* **Legal / Finance**: Audits open-source license compliance and monitors API token costs.
* **Project Manager**: Manages delivery timelines and updates status dashboards.

---

## Agent Configuration Format
Every agent is defined and configured using a structured YAML specification. This makes it simple to customize agent personalities, modify prompts, swap models, or configure strict tool execution rules.

### YAML Definition Example (`agents.yaml`)
```yaml
agents:
  - name: "Planner"
    role: "Task breakdown and workflow orchestrator"
    model: "qwen3-coder-480b"
    temperature: 0.1
    priority: 1
    system_prompt: |
      You are the sAI Planner. Your job is to dissect complex requests into
      a clear checklist of sub-tasks. You assign tasks to other specialized agents.
    tool_permissions:
      - "read_file"
      - "search"
    memory_permissions:
      - "read_short_term"
      - "write_short_term"
      - "read_long_term"

  - name: "Coder"
    role: "Code writing and editor"
    model: "deepseek-coder"
    temperature: 0.2
    priority: 3
    system_prompt: |
      You are the sAI Coder. You specialize in generating clean, correct, and modular code.
    tool_permissions:
      - "read_file"
      - "write_file"
      - "edit_file"
    memory_permissions:
      - "read_short_term"
```
