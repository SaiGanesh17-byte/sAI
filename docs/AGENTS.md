# Agents

sAI has 8 agents. Each is a YAML file in `agents/configs/`, loaded by
`agents/registry.py` into an `agents/runtime.py::AgentRuntime`. Agents differ in their
instructions, model, and -- enforced in code -- which tools they can use.

| Agent | Priority | Model | Tools |
|---|---|---|---|
| **Planner** | 0 | free | read/search, `memory_operation`, `todo_write` |
| **Architect** | 1 | `openai/gpt-4o-mini` | read/search, web, `write_file`/`edit_file` (design docs) |
| **Coder** | 2 | `qwen/qwen3-coder-plus` | all (`*`) |
| **Debugger** | 3 | `qwen/qwen3-coder-plus` | all (`*`) |
| **Reviewer** | 4 | `qwen/qwen3-coder-plus` | read-only |
| **Researcher** | 5 | `openai/gpt-4o-mini` | read/search, `web_search`, `web_fetch`, `math_solve`, `mcp__*` |
| **DevOps** | 6 | `qwen/qwen3-coder-plus` | all (`*`) |
| **Writer** | 7 | free | read/search, web, `write_file`/`edit_file`/`patch_file` -- no shell |

"read/search" = `read_file`, `list_directory`, `glob`, `grep`, `grep_ast`,
`codebase_search`, `memory_operation`.

These replaced an earlier roster of 24 prompt-only personas (all with every tool), whose
overlapping roles caused misrouting. The old roles were folded in: Testing, QAAnalyst and
Performance into Debugger; Security into Reviewer; WebSearch into Researcher; GitOps and
Observability into DevOps; Database, DataEngineer, APIIntegration and Mathematics into
Coder; UIDesigner and DataScientist into Architect; Documentation, BusinessAnalyst,
ProjectManager and LegalFinance into Writer and Planner.

## How a request reaches an agent

1. **Jev** (`jev/decision.py`) makes one fast LLM call per message and picks a route:
   `direct_answer` (greetings, simple questions it can answer), `single_agent` (one
   agent clearly fits), or `full_orchestrator`. Anything time-sensitive ("latest
   version of X") goes to Researcher, never answered from memory.
2. **Single agent:** the agent runs in the tool-use loop below.
3. **Full team:** `core/orchestrator.py` starts with Planner and moves between agents
   via each agent's `next_agent` (invalid names fall back to priority order), up to 12
   agent turns. Each agent turn is itself a tool-use loop.

## The tool-use loop

`agents/loop.py::run_agent_loop` -- the same loop for the REPL, TUI, headless mode and
the orchestrator:

```
agent responds (JSON) ─► actions? ──no──► done: "response" is the answer
        ▲                   │yes
        │                   ▼
        └── results added ◄─ run each action (approval / permissions / hooks)
            to history
```

It stops when the agent returns no actions, when the user declines something, when the
agent repeats exactly the same actions, or at `agent_max_steps` (default 8).

Every agent answers with a JSON object:

| Key | |
|---|---|
| `summary` | one short line: what it is doing now |
| `response` | the reply to the user in Markdown -- the full answer when `actions` is empty |
| `actions` | tool calls to run now: `[{"tool": "read_file", "args": {"path": "..."}}]` |
| `next_agent` | hand-off target in full-team mode, or null |
| `finished`, `confidence`, `reasoning`, `memory_update` | bookkeeping |
| `findings` | Reviewer only: structured issues with suggested patches |

The prompt also tells every agent today's date (so it doesn't assume its training year)
and includes `SAI.md` project instructions, the current todo list, a size-capped repo
map, working memory and recent history.

## Adding or changing an agent

```yaml
# agents/configs/translator.yaml
name: Translator
priority: 8
role: Translates UI strings and docs           # Jev routes on this line -- make it specific
model: openai/gpt-4o-mini                     # any OpenRouter id; ':free' ids get the fallback chain
temperature: 0.2
tools: [read_file, glob, grep, edit_file]     # fnmatch patterns; "*" = all, "mcp__github__*" = one MCP server
system_prompt: |
  You are the sAI Translator. ...
```

No code change is needed -- the registry discovers new files. Keep roles
non-overlapping: Jev picks between them on the `role` line alone.

Code that refers to agents by name: the orchestrator always starts with `Planner`;
`Reviewer` output is schema-checked for `findings`; Architect/Researcher/Coder/Reviewer
each write their own section of working memory; `/init` uses `Writer`.
