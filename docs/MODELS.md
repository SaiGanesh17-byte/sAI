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
| Coder, Debugger, DevOps, Reviewer | `qwen/qwen3-coder-plus` | agent YAML `model:` |
| Architect, Researcher | `openai/gpt-4o-mini` | agent YAML `model:` |
| Planner, Writer | free model | agent YAML `model:` |
| Jev routing (every message) | free model | `jev_model` |
| `/compact` summaries | free model | `compact_model` |

An agent's YAML `model:` always wins; `llm/router.py` only picks models for Jev,
compaction and callers that pass none.

## Free models and fallback

### Free-tier providers

Besides OpenRouter, sAI talks to three providers with their own free tiers. Write their
models as `provider:model` anywhere a model id goes (`free_model_chain`, `council_models`,
`jev_model`, an agent's YAML `model:`):

| Prefix | Provider | Key (keychain entry / env var) | Notes |
|---|---|---|---|
| `groq:` | Groq | `groq_key` / `GROQ_API_KEY` | fastest (~1s); 1000 requests/day but 8k tokens/minute, so Jev and council only |
| `gemini:` | Google AI Studio | `gemini_key` / `GEMINI_API_KEY` | `gemini-3.5-flash`, `gemini-3.1-flash-lite` pass the tool-call check |
| `nvidia:` | NVIDIA NIM | `nvidia_key` / `NVIDIA_API_KEY` | nemotron-3-super/ultra without OpenRouter's daily cap |

Their requests don't count toward OpenRouter's 50 free a day, and when that cap is hit
they keep working. A model whose provider has no key is skipped. Use one account per
provider -- rotating accounts to get around a provider's limits breaks their terms.

Model ids ending in `:free` are free on OpenRouter but slow, often rate-limited, and
uneven in quality. `llm/runtime.py::LLMRuntime.query` therefore tries, in order:

1. the configured free model,
2. `free_model_retries` (default 2) other models from `free_model_chain`,
3. the paid `free_fallback_model` (default `openai/gpt-4o-mini`).

It moves on when a model errors, goes silent for `free_model_timeout` seconds (default
30), or -- when JSON was requested -- returns something that isn't JSON. Free models are
never sent `response_format` (several reject it). The REPL shows a dim
`free model busy → ...` line when this happens.

A free model that fails is skipped for `free_model_cooldown` seconds (default 120), so
the later steps of a turn go straight to one that works. After every response the REPL
prints which models answered, e.g. `models: Jev → ling-3.0-flash-sante (free) · Coder →
nemotron-3-super-120b-a12b (free) ×3` (`sai -p` prints it to stderr; `--output-format
json` has a `models` list).

### /council -- several models, one answer

`/council <question>` sends the question (plus the last few messages for context) to every
model in `council_models` in parallel, then `council_judge` compares the answers -- shown
to it anonymously as Answer A, B, C -- and writes one answer, ending with an `Agreement:`
line. Where the models agree the answer is more likely right; where they differ the judge
has to settle it. Members have no tools, so use it for questions, design choices and
reviews of pasted code, not for work on files. Each run costs one request per member plus
one for the judge (4 with the defaults) out of the 50 free requests a day. If only one
member answers, its answer is used as-is.

Things to know:
- **Daily cap.** Accounts without purchased credits get 50 free-model requests per day
  (OpenRouter offers 1000/day after a $5 credit purchase). When the cap is hit, sAI stops
  trying free models until the reset time OpenRouter reports.
- **Privacy.** Free endpoints may log prompts, which include your code. Set
  `use_free_models: false` to use only paid models.
- **Don't use `openrouter/free`.** That auto-router picks a random free model per request
  and has returned a content-safety classifier's output for a chat request.
- The chain in `core/settings.py` was re-benchmarked on 2026-10-03 with a native
  tool-call task (nemotron-3-super, north-mini-code, qwen3.8-27b and nemotron-3-ultra made
  a correct `edit_file` call; ling-3.0-flash dropped the arguments; gemma-4 and laguna
  errored; inkling is limited to approved harnesses); free-model availability changes, so re-check it now and
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
