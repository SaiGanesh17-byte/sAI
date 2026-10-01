# Models and providers

## Providers

**OpenRouter** is the default (`"provider": "openrouter"`): one key for many model
families. Also implemented: OpenAI direct, Ollama (local), and NVIDIA NIM (its previously
used models are end-of-life). All are OpenAI-compatible and share
`llm/providers/base.py::stream_chat`, which streams responses and records token usage.

The API key is read from `OPENROUTER_API_KEY` on first launch and then stored in the OS
keychain (`settings.json` just says `"keyring_secured"`).

## Which model does what

| Work | Model (default) | Set in |
|---|---|---|
| Coder, Debugger, DevOps | `qwen/qwen3-coder-plus` | agent YAML `model:` |
| Architect, Reviewer, Researcher | `openai/gpt-4o-mini` | agent YAML `model:` |
| Planner, Writer | free model | agent YAML `model:` |
| Jev routing (every message) | free model | `jev_model` |
| `/compact` summaries | free model | `compact_model` |

An agent's YAML `model:` always wins; `llm/router.py` only picks models for Jev,
compaction and callers that pass none.

## Free models and fallback

Model ids ending in `:free` are free on OpenRouter but slow, often rate-limited, and
uneven in quality. `llm/runtime.py::LLMRuntime.query` therefore tries, in order:

1. the configured free model,
2. `free_model_retries` (default 1) other models from `free_model_chain`,
3. the paid `free_fallback_model` (default `openai/gpt-4o-mini`).

It moves on when a model errors, goes silent for `free_model_timeout` seconds (default
30), or -- when JSON was requested -- returns something that isn't JSON. Free models are
never sent `response_format` (several reject it). The REPL shows a dim
`free model busy → ...` line when this happens.

Things to know:
- **Daily cap.** Accounts without purchased credits get 50 free-model requests per day
  (OpenRouter offers 1000/day after a $5 credit purchase). When the cap is hit, sAI stops
  trying free models until the reset time OpenRouter reports.
- **Privacy.** Free endpoints may log prompts, which include your code. Set
  `use_free_models: false` to use only paid models.
- **Don't use `openrouter/free`.** That auto-router picks a random free model per request
  and has returned a content-safety classifier's output for a chat request.
- The chain in `core/settings.py` was benchmarked on 2026-10-01 against sAI's own
  routing and agent-JSON tasks; free-model availability changes, so re-check it now and
  then (`https://openrouter.ai/api/v1/models`, ids ending in `:free`).

## Context management

- **Repo map** in each agent prompt is capped by `repo_map_token_budget` (default 1500):
  full map, then file tree only, then a truncated tree.
- **History**: the last 10 messages stay verbatim, older ones are shortened, each message
  is capped at 8k characters, and the whole history section is trimmed to
  `context_token_budget` (~4 chars/token).
- **Compaction**: once history passes `auto_compact_tokens` (default 60% of
  `context_token_budget`), older messages are replaced by an LLM-written summary; `/compact`
  does it on demand.

## Other settings

- `stream_responses` (default true) -- stream output so the REPL shows text as it arrives.
- `agents_json_mode`, `jev_json_mode` -- ask paid models for `response_format: json_object`.
- `use_tool_calling` (default false) -- experimental native function calling. It still
  sends the whole context as one user message; not yet a real multi-message tool
  conversation.
- `temperature_override` (default off) -- forces every call to one temperature; otherwise
  Jev uses 0.0 and each agent its YAML `temperature`. (Saving the web UI's temperature
  field sets this.)
