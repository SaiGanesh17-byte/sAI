# sAI

A multi-agent AI coding assistant for the terminal, modeled on Claude Code. You talk to it
in a REPL; a fast router (Jev) answers simple things directly or hands the request to one
of 8 specialist agents, which work in a tool-use loop -- reading code, editing files (with
your approval), running commands and searching the web -- until the job is done.

```
❯ fix the failing test in tools/math.py
  ⎿  jev → Debugger
⏺ Debugger
  Running the test suite to reproduce the failure.
⏺ Bash(pytest -q tests/test_math_tool.py)
  ⎿  F.... 1 failed, 4 passed
⏺ Read(tools/math.py)
  ⎿  Read 135 lines
⏺ Update(tools/math.py)
  ⎿  +1 -1 lines
     -        return round(value)
     +        return value
     Allow? [y/N/a] y
⏺ Debugger
  The test failed because `evaluate` rounded exact results. Removed the rounding; all 5 tests pass.
```

## Setup

Requires Python 3.10+ and an [OpenRouter](https://openrouter.ai/keys) API key.

```bash
git clone https://github.com/SaiGanesh17-byte/sAI && cd sAI
python3 -m venv venv && venv/bin/pip install -e .
export OPENROUTER_API_KEY=sk-or-...      # read on first launch, then kept in your OS keychain
```

Optional shell shortcut (zsh), so `hey sAI` / `sai` work from any folder:

```bash
alias sai="$PWD/venv/bin/sai"
hey() { [ "${1:l}" = "sai" ] && { shift; sai "$@"; } }
```

## Using it

`cd` into the project you want to work on and run `sai`. The first time in a folder it asks
whether you trust it -- agents can read everything there and, with your approval, change
files and run commands. That folder is the workspace; agents can't reach outside it.

| Input | What it does |
|---|---|
| plain text | a request -- Jev routes it (direct answer, one agent, or the full team) |
| `@path/to/file` | attaches that file (or a directory listing) to the request |
| `!command` | runs a shell command yourself |
| `\` at line end, or Option+Enter | new line |
| ↑ / ↓, Tab | history; completion for `/commands` and `@files` |
| Esc or Ctrl+C | stop the current turn (not sAI) |

### Commands

| Command | |
|---|---|
| `/init` | study this project and write `SAI.md` -- instructions every agent follows |
| `/undo` | roll back the file edits from the last turn (repeat to go further back) |
| `/compact [focus]` | summarize the conversation to free context (also happens automatically) |
| `/resume` | continue an earlier session in this folder (`sai -c` / `sai -r` at launch) |
| `/clear` | start a fresh session (the old one stays resumable) |
| `/cost`, `/budget <usd>` | session cost in dollars and today's spend; set a daily cap (paid models stop, free ones keep working) |
| `/permissions` | approval rules: `/permissions edits auto\|ask`, `/permissions allow <cmd prefix>` |
| `/mcp` | connected MCP servers and their tools |
| `/agents`, `/help` | roster, all commands |

Your own commands: put Markdown files in `.sai/commands/` (project) or `~/.sai/commands/`
(personal). `review.md` becomes `/review`; `$ARGUMENTS` is replaced by what follows it.

### Permissions

- **Edits** show a colored diff and ask `y/N/a` (`a` = accept edits for the session).
  Agents must read a file before changing it.
- **Risky commands** (`rm`, `sudo`, `git push`, pipe-to-shell, ...) ask first; `a` allows
  that command prefix for the session. Allow rules never match chained commands
  (`;`, `&&`, `|`, ...).
- **MCP tools** ask before each use unless allowed.

### Headless

```bash
sai -p "summarize what this repo does"
cat error.log | sai -p "why is this failing?"
sai -p "add docstrings to utils.py" --accept-edits --output-format json
```

Answer on stdout, progress on stderr. Nobody can answer prompts, so permission requests
are declined (and reported) unless allowed up front.

## Agents

| Agent | For | Tools |
|---|---|---|
| Planner | breaking down multi-step work | read/search, todo list |
| Architect | design: APIs, data models, UI, approach | read/search, web, write design docs |
| Coder | implementing features, APIs, DB, data code | all |
| Debugger | failures, reproduction, tests, performance | all |
| Reviewer | code review and security audit | read-only |
| Researcher | facts from the codebase and the live web | read/search, web, math, MCP |
| DevOps | CI/CD, Docker, deploys, monitoring, git history | all |
| Writer | docs, READMEs, specs, requirements | read/search, web, edit files (no shell) |

Each is a YAML file in `agents/configs/` (prompt, model, and an enforced `tools` list).
See [docs/AGENTS.md](docs/AGENTS.md).

## Configuration

Settings live in `.sai/settings.json` (created on first run). The useful ones:

| Setting | Default | |
|---|---|---|
| `coder_model` / `reasoner_model` | `qwen/qwen3-coder-plus` / `openai/gpt-4o-mini` | paid models |
| `jev_model`, `compact_model` | a free model | routing and summarizing |
| `use_free_models` | `true` | master switch for free OpenRouter models |
| `free_fallback_model` | `openai/gpt-4o-mini` | used when a free model fails or is rate-limited |
| `edit_approval` | `"ask"` | `"auto"` applies edits without asking |
| `allow_commands` | `[]` | command prefixes that never ask, e.g. `["pytest", "npm test"]` |
| `mcp_servers` | `{}` | MCP servers to start -- see `core/mcp.py` |
| `hooks` | -- | shell commands on PreToolUse / PostToolUse / UserPromptSubmit / Stop -- see `core/hooks.py` |
| `daily_budget_usd` | `0` (off) | daily spending cap for paid models |
| `agent_max_steps` | `12` | tool-use steps per agent turn |
| `docker_sandbox` | `false` | run shell commands inside Docker |

Free models are slower, sometimes unavailable, and limited to 50 requests/day on
OpenRouter accounts without credits; sAI falls back to the paid model automatically.
Requests to free endpoints may be logged by their providers -- set `use_free_models` to
`false` if that matters for your code. See [docs/MODELS.md](docs/MODELS.md).

## Development

```bash
venv/bin/python -m pytest -q          # unit tests (fake models, free, ~10s)
venv/bin/python evals/run_evals.py    # real-model evals: 19 prompts across personas (~180k tokens)
```

Unit tests can't tell a good answer from a bad one; run the evals after changing agent
prompts, models or routing. `--only <ids>` / `--persona <name>` run a subset.

Layout: `app/` (REPL, headless, web server), `agents/` (runtime, tool-use loop, configs),
`core/` (orchestrator, security, sessions, hooks, MCP), `llm/` (providers, routing,
fallback), `tools/`, `ui/`, `jev/`. Also available: `sai web` (browser UI) and `sai tui`
(Textual UI; older, without the REPL's newer features).
