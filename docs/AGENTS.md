# Agent Definitions - sAI

This document defines the roles, parameters, and configuration format for sAI agents.

## Core Agents

All 24 are implemented today as YAML configs under `agents/configs/`, auto-discovered
and registered by `agents/registry.py::AgentRegistry` — adding a new agent is just
adding a new `*.yaml` file in that directory, no code change required. Listed in
priority order (the order `Orchestrator` falls back to when an agent doesn't specify an
explicit `next_agent`):

* **ProjectManager** (priority -1): Status/timeline summarization from conversation and working memory; hands new work off to Planner rather than planning itself.
* **Planner** (priority 0): Deconstructs goals into manageable sub-tasks, assigns workflows to specific agents, and tracks task status.
* **BusinessAnalyst** (priority 0.5): Turns a fuzzy goal into concrete user stories and acceptance criteria before design starts.
* **Architect** (priority 1): Designs system layouts, reviews imports, sets directory structures, and ensures modular code patterns.
* **Database** (priority 2): Schema design, query/index optimization, migrations.
* **DataEngineer** (priority 2.5): ETL/data pipelines, ingestion validation, and data quality — distinct from DataScientist's modeling focus.
* **UIDesigner** (priority 3): Visual/interaction design specs for what Coder will build.
* **DataScientist** (priority 4): Narrow data-analysis/ML-specific work only.
* **Researcher** (priority 5): Investigates the local codebase, its libraries, and installed dependencies — hands off to WebSearch for anything external.
* **WebSearch** (priority 5.5): Internet research for docs/errors/version-specific facts, planner→executor→publisher style (inspired by gpt-researcher), always cites sources.
* **Coder** (priority 6): Writes, modifies, and edits code based on implementation plans and research findings.
* **APIIntegration** (priority 6.5): Wires up third-party APIs/SDKs/webhooks — auth, rate limits, retries, failure handling for systems you don't control.
* **Mathematics** (priority 6.8): Exact algebra/calculus/linear-algebra/stats via the `math_solve` tool instead of LLM-guessed arithmetic; also does algorithmic Big-O analysis.
* **Performance** (priority 7): Finds and fixes real, measured performance bottlenecks.
* **Observability** (priority 7.5): Structured logging, alert thresholds, and incident runbooks — makes production failures diagnosable.
* **Security** (priority 8): Whole-codebase dependency/secret audits (deeper than Reviewer's per-file pass).
* **Testing** (priority 9): Writes automated unit/integration tests for what Coder implemented.
* **QAAnalyst** (priority 9.5): Exploratory/manual testing, bug-report reproduction, and multi-persona (end-user / prod-support / stakeholder) evaluation of what automated tests miss.
* **Debugger** (priority 10): Root-cause isolation from failures/stack traces, minimal targeted fixes.
* **Reviewer** (priority 11): Performs verification, syntax checks, lint checks, runs tests, and validates code output quality.
* **GitOps** (priority 11.5): Commit hygiene, changelogs, release notes, branch/merge strategy — distinct from DevOps's CI/CD focus.
* **Documentation** (priority 12): Keeps README/docstrings/docs accurate to the real implementation.
* **DevOps** (priority 13): CI/CD, Dockerfiles, deployment/environment configuration.
* **LegalFinance** (priority 14): License compliance and LLM cost flags, advisory only.

Role taxonomy is deliberately kept non-overlapping (one narrow purpose per agent, with
an explicit "hand off rather than invent scope" rule in every prompt) rather than
maximizing agent count for its own sake — modeled loosely on the fixed-role SOP pattern
from [MetaGPT](https://github.com/foundationagents/metagpt) and
[ChatDev](https://github.com/OpenBMB/ChatDev)'s role pipelines, and on
[gpt-researcher](https://github.com/assafelovic/gpt-researcher)'s
planner/executor/publisher pattern for the WebSearch agent specifically.

---

## Agent Configuration Format
Every agent is defined and configured using a structured YAML specification. This makes it simple to customize agent personalities, modify prompts, swap models, or configure strict tool execution rules.

**Note**: `agents/registry.py::AgentRegistry.load()` currently only reads
`name, priority, role, model, temperature, system_prompt` from each YAML file — it does
not read or enforce `tool_permissions`/`memory_permissions`. If you add those keys today
they're inert documentation, not an access-control mechanism.

### YAML Definition Example (`agents/configs/example.yaml`)
```yaml
name: Planner
priority: 0
role: Task breakdown and workflow orchestrator
model: openai/gpt-4o-mini
temperature: 0.1
system_prompt: |
  You are the sAI Planner. Your job is to dissect complex requests into
  a clear checklist of sub-tasks. You assign tasks to other specialized agents.
```

A coding-heavy agent (Coder, Database, DataEngineer, APIIntegration, Testing, Debugger,
DevOps, Performance, Observability, GitOps in the current roster) typically points
`model:` at whatever `coder_model` is set to in `.sai/settings.json` (currently
`qwen/qwen3-coder-plus` via OpenRouter); everything else typically points at
`reasoner_model` (currently `openai/gpt-4o-mini`). Note that `agents/runtime.py` always
passes each agent's own configured `model:` explicitly into `LLMRuntime.query()`, which
overrides `ModelRouter`'s task-kind guess — so the YAML `model:` field is authoritative,
not the agent's name.

Also register any new tool the agent needs in `core/orchestrator.py::Orchestrator._init_kernel()`
(see `MathTool` for the pattern) — `AgentRegistry` auto-discovers new agent YAML files,
but tools still need an explicit `tool_reg.register(...)` call.
