# LLM Providers & Routing - sAI

This document outlines LLM provider integrations, primary target models, and router responsibilities.

## LLM Providers

### Phase 1
* **NVIDIA API**: Chosen for initial cloud-hosted, open-source model execution. Highly optimized, low-latency, and cost-effective endpoint hosting.

### Future Integrations
* **Proprietary APIs**: OpenAI, Anthropic, Gemini.
* **Cloud Aggregators**: Groq, Together, OpenRouter.
* **Local Hosts**: Ollama, LM Studio, vLLM.

---

## Primary Target Models (NVIDIA API)
* **Qwen3 Coder (480B/72B)**: Recommended for dense code editing, structure refactoring, and logical syntax generation.
* **Nemotron Ultra (70B/340B)**: Optimized for broad system reasoning, instruction-following, and structuring plans.
* **DeepSeek Coder (V2 / V3)**: High-speed, high-context code completions.
* **Llama 3 / 3.1 (70B / 405B)**: General purpose agents, summaries, conversational interfaces.

---

## Router Architecture & Responsibilities
sAI uses a dynamic **Model Router** middleware layer to direct queries to models based on task nature, current cost, context constraints, and latency requirements.

```
                   +-----------------------+
                   |   Agent Query / task  |
                   +-----------+-----------+
                               |
                               v
                   +-----------------------+
                   |     Model Router      |
                   +-----------+-----------+
                               |
       +-----------------------+-----------------------+
       |                       |                       |
[Cost Optimize?]       [Complex Logic?]        [Failover/Retry?]
       v                       v                       v
(Llama 3 8B)             (Qwen3 480B)             (Backup Host)
```

1. **Model Selection**: Automatically matches the agent's target capability to the model (e.g., Planner tasks to a reasoning model, Coder tasks to a coder-optimized model).
2. **Context Window Management**: Scans file size and prompt length. Swaps to high-context models or trims prompts if approaching context limits.
3. **Fallback & Failover**: If the NVIDIA API rate-limits or returns a 500 error, the router automatically fails over to alternative endpoints (e.g., Together API or local Ollama).
4. **Retry Policies**: Standardizes exponential backoff handling using `tenacity` library.
5. **Cost & Latency Optimization**: Paths low-priority summarization jobs to faster, cheaper endpoints, saving heavy computing power for code generation.
