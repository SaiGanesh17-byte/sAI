# LLM Providers & Routing - sAI

This document outlines LLM provider integrations, primary target models, and router responsibilities.

## LLM Providers

### Active
* **OpenRouter**: The active default provider (`"provider": "openrouter"` in
  `.sai/settings.json`). OpenAI-chat-completions-compatible, so it needs no
  provider-specific logic beyond a base URL — see `llm/providers/openrouter.py`. Chosen
  over a single vendor's direct API for access to many model families (OpenAI,
  Anthropic, Qwen, etc.) through one key and one billing relationship.

### Also implemented, not the default
* **NVIDIA NIM** (`llm/providers/nvidia.py`): the original provider. Left in place, but
  every model previously configured against it (Llama 3.1, Nemotron) has since reached
  end-of-life on NVIDIA's API and returns `410 Gone` — do not re-enable without first
  confirming live model availability at build.nvidia.com.
* **OpenAI direct** (`llm/providers/openai.py`), **Ollama** (`llm/providers/ollama.py`,
  local-only, `ollama_url` setting).

### Not yet implemented
* Anthropic direct, Gemini, Groq, Together, LM Studio, vLLM — straightforward to add
  following the `BaseProvider` interface (`llm/providers/base.py`) any of the existing
  providers implement, but none exist today.

---

## Current Models (via OpenRouter)

Set in `core/settings.py::DEFAULT_SETTINGS` / `.sai/settings.json`, live-verified against
OpenRouter's `/api/v1/models` catalog:

* **`coder_model`**: `qwen/qwen3-coder-plus` — used by agents whose work is
  code-writing-heavy (Coder, Database, Testing, Debugger, DevOps, Performance).
* **`reasoner_model`**: `openai/gpt-4o-mini` — used by every other agent, and by Jev's
  routing decisions (`jev_model` is empty by default, meaning "reuse `reasoner_model`").

Per-agent `model:` fields in `agents/configs/*.yaml` are passed through explicitly (see
`agents/runtime.py::AgentRuntime.execute_turn` → `llm/runtime.py::LLMRuntime.query`'s
`model` parameter) rather than being re-derived from the agent's name/task_kind.

---

## Router Architecture & Responsibilities

`llm/router.py::ModelRouter.route(task_kind)` is intentionally simple, not the dynamic
cost/latency-aware system this document originally described as aspirational:
1. **Model Selection**: `task_kind == "jev"` → `jev_model` (or `reasoner_model` if
   unset); `"code"`/`"edit"`/`"implement"` substring in `task_kind` → `coder_model`;
   otherwise → `reasoner_model`. In practice this exists mainly as a *fallback* now that
   agents pass their own `model` explicitly (above) — it's still the only source of
   truth for Jev and for any caller that doesn't pass an explicit model.
2. **Context Window Management**: `llm/runtime.py::ContextCompressor.compress()`
   estimates prompt size (~4 chars/token) against `context_token_budget`
   (`DEFAULT_SETTINGS`, default 32000) and, if over budget, trims the
   `[CONVERSATION HISTORY LOGS]` section of the prompt from the oldest entries forward
   until it fits, leaving a `"...[older history trimmed to fit context budget]..."`
   marker. `core/prompt_builder.py` separately compacts older conversation messages
   (full text → one-line summaries) as it assembles that section in the first place.
3. **Fallback & Failover**: Provider selection is static per request (from
   `provider`/`coder_model`/`reasoner_model` settings) — there is no automatic failover
   to a different provider on error today.
4. **Retry Policies**: `LLMRuntime.query`/`query_with_tools` are wrapped in `tenacity`
   (3 attempts, exponential backoff) against the *same* provider/model — this is retry,
   not failover.
5. **Structured tool-calling**: `AgentRuntime.execute_turn` can optionally use real
   OpenAI-style function-calling (`DEFAULT_SETTINGS["use_tool_calling"]`, off by
   default) via `LLMRuntime.query_with_tools`, built from each tool's existing
   `BaseTool.schema`. Off by default because support varies by model family on
   OpenRouter — verify before flipping it on for a given `coder_model`/`reasoner_model`.
