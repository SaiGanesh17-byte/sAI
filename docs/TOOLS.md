# Tools

Tools live in `tools/`, are registered in `core/orchestrator.py::Orchestrator._init_kernel`,
and run through `execution/engine.py::ExecutionEngine`. Agents only see -- and can only
use -- the tools their YAML `tools` list allows (see `docs/AGENTS.md`).

| Tool | File | What it does |
|---|---|---|
| `read_file` | `filesystem.py` | read a file in the workspace |
| `write_file` | `filesystem.py` | create or overwrite a file |
| `edit_file` | `filesystem.py` | exact string replacement (unique unless `replace_all`) -- preferred for changes |
| `patch_file` | `filesystem.py` | Aider-style SEARCH/REPLACE blocks |
| `list_directory` | `filesystem.py` | list a directory |
| `glob` | `find.py` | find files by pattern (`**/*.py`), newest first |
| `grep` | `find.py` | regex search of contents; `files` / `content` / `count` modes |
| `grep_ast`, `codebase_search` | `search.py` | symbol search; full-text index search |
| `execute_command` | `terminal.py` | shell command; `timeout` (≤600s) or `run_in_background` |
| `bash_output`, `kill_shell` | `terminal.py` | read / stop a background command |
| `run_python_script` | `python.py` | run a script in the workspace |
| `git_operation` | `git.py` | status, diff, commit |
| `web_search` | `search.py` | DuckDuckGo search (`ddgs`) |
| `web_fetch` | `web_fetch.py` | fetch a page as readable text |
| `math_solve` | `math.py` | exact math via sympy (never `eval`) |
| `memory_operation` | `memory.py` | persistent facts and decisions |
| `todo_write` | `todo.py` | the visible task checklist (also shown in every agent prompt) |
| `mcp__<server>__<tool>` | `core/mcp.py` | tools from configured MCP servers |

## Safety checks, in order

For every action (`agents/loop.py::execute_with_approval` → `Orchestrator.execute_action`
→ `ExecutionEngine.execute`):

1. **Agent tool list** -- a tool outside the agent's `tools` is refused.
2. **Edit approval** (interactive only) -- `write_file` / `edit_file` / `patch_file` show
   a unified diff and ask `y/N/a`, unless `edit_approval` is `"auto"` or `a` was chosen.
3. **Workspace boundary** -- paths resolve against the workspace; anything outside asks
   for permission. Shell commands referencing `~`, `$HOME`, `..` or `/` are refused.
4. **Read before edit** -- an existing file must have been read (and not changed on disk
   since) before it can be overwritten or edited; `@file` mentions count as reads.
5. **Risky commands** -- word-boundary patterns (`rm`, `sudo`, `chmod 777`, `git push`,
   pipe-to-shell, ...) ask first; scripts with risky content are approved per exact
   content (an edited script asks again). `allow_commands` / session rules skip the
   prompt but never match chained commands.
6. **PreToolUse hooks** -- exit code 2 blocks the tool (`core/hooks.py`).
7. **Output** -- API keys are masked from all tool output; PostToolUse hooks run.

`web_fetch` refuses hosts that resolve to private, loopback, link-local or reserved
addresses (re-checked on every redirect), unless `web_fetch_allow_private` is on.
`docker_sandbox: true` runs shell commands inside a container -- the only real isolation;
the checks above are guard rails, not a sandbox.

## Adding a tool

Subclass `tools/base.py::BaseTool` (`name`, `description`, `permissions`, `schema`,
`execute(args) -> str`; return a string starting with `Error` on failure), register it in
`_init_kernel`, add it to the `tools` list of the agents that should have it, and give it
a display label in `ui/activity.py::TOOL_LABELS`.
