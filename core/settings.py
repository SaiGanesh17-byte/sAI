import json
from pathlib import Path
import os
import keyring

SETTINGS_FILE = Path(__file__).resolve().parent.parent / ".sai" / "settings.json"

DEFAULT_SETTINGS = {
    "provider": "openrouter",
    "nvidia_key": "",
    "openai_key": "",
    "openrouter_key": "",
    "ollama_url": "http://localhost:11434",
    "coder_model": "qwen/qwen3-coder-plus",
    "reasoner_model": "openai/gpt-4o-mini",
    "jev_model": "inclusionai/ling-3.0-flash-sante:free",  # routing on every turn -- a free model; empty = reuse reasoner_model
    "compact_model": "inclusionai/ling-3.0-flash-sante:free",  # summarizing history for /compact
    "use_free_models": True,  # master switch for ':free' models (agents, Jev, compaction)
    # Tried in order after a free model fails or returns junk (benchmarked 2026-10-01). Avoid the
    # "openrouter/free" auto-router: it has served a safety classifier for a chat request.
    "free_model_chain": ["inclusionai/ling-3.0-flash-sante:free", "nvidia/nemotron-3-super-120b-a12b:free", "cohere/north-mini-code:free"],
    "free_model_retries": 1,  # how many other free models to try before the paid fallback
    "free_model_timeout": 30,  # seconds of silence before giving up on a free model
    "free_fallback_model": "openai/gpt-4o-mini",
    "vision_model": "openai/gpt-4o-mini",  # used for any call that carries @image attachments  # paid model used when free ones are rate-limited or fail
    "jev_json_mode": True,
    "agents_json_mode": True,
    "context_token_budget": 32000,
    "stream_responses": True,  # stream LLM output so the REPL can show text as it arrives
    "require_read_before_edit": True,  # agents must read_file an existing file before changing it
    "edit_approval": "ask",  # "ask": show a diff and confirm each edit; "auto": apply edits without asking
    "allow_commands": [],  # command prefixes that never need approval, e.g. ["pytest", "npm test"]
    "mcp_servers": {},  # name -> {"command", "args", "env", "disabled"}; see core/mcp.py
    "allow_mcp_tools": [],  # MCP tools that run without asking, e.g. ["mcp__github__"] for a whole server
    "web_search_fallback": True,  # when DuckDuckGo fails, search via OpenRouter's web plugin (~$0.007/search)
    "web_fetch_allow_private": False,  # let web_fetch reach localhost/LAN addresses (off: blocks SSRF)
    "daily_budget_usd": 0,  # stop paid model calls once today's spend reaches this (0 = no cap)
    "agent_max_steps": 12,  # tool-use loop: max agent responses per agent turn
    "auto_compact_tokens": 0,  # summarize history past this many tokens (0 = 60% of context_token_budget)
    "context_graph": True,  # give agents the code/work neighborhood of each request
    "context_graph_token_budget": 700,
    "repo_map_token_budget": 1500,  # cap on the repo map included in every agent prompt
    "use_tool_calling": False,
    "temperature": 0.2,  # legacy, unused -- see temperature_override
    "temperature_override": None,  # a number forces every LLM call to this temperature; None = each caller's own
    "aider_mode": True,
    "graphiti_mode": True,
    "docker_sandbox": False
}

def get_secure_key(key_name: str, fallback: str = "") -> str:
    try:
        val = keyring.get_password("sAI_agent", key_name)
        if val is not None and val != "keyring_secured":
            return val
    except Exception:
        pass
    return fallback

def set_secure_key(key_name: str, value: str) -> bool:
    try:
        if value and value != "keyring_secured":
            keyring.set_password("sAI_agent", key_name, value)
            return True
    except Exception:
        pass
    return False

def load_settings() -> dict:
    if not SETTINGS_FILE.parent.exists():
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        # Seed from env if present
        nv_env = os.getenv("NVIDIA_API_KEY")
        if nv_env and nv_env != "your_nvidia_api_key_here":
            DEFAULT_SETTINGS["nvidia_key"] = nv_env
        openai_env = os.getenv("OPENAI_API_KEY")
        if openai_env:
            DEFAULT_SETTINGS["openai_key"] = openai_env
        openrouter_env = os.getenv("OPENROUTER_API_KEY")
        if openrouter_env:
            DEFAULT_SETTINGS["openrouter_key"] = openrouter_env
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        for k, v in DEFAULT_SETTINGS.items():
            if k not in data:
                data[k] = v

        # Load secure keys from Keychain with plain text settings fallbacks
        nvidia_raw = data.get("nvidia_key", "")
        openai_raw = data.get("openai_key", "")
        openrouter_raw = data.get("openrouter_key", "")

        nv_key = get_secure_key("nvidia_key", nvidia_raw if nvidia_raw != "keyring_secured" else "")
        op_key = get_secure_key("openai_key", openai_raw if openai_raw != "keyring_secured" else "")
        or_key = get_secure_key("openrouter_key", openrouter_raw if openrouter_raw != "keyring_secured" else "")

        data["nvidia_key"] = nv_key
        data["openai_key"] = op_key
        data["openrouter_key"] = or_key

        # Inject keys into env
        os.environ["NVIDIA_API_KEY"] = nv_key
        os.environ["OPENAI_API_KEY"] = op_key
        os.environ["OPENROUTER_API_KEY"] = or_key
        try:
            from sai.config import settings as sai_settings
            sai_settings.nvidia_api_key = nv_key
        except Exception:
            pass
        return data
    except Exception:
        return DEFAULT_SETTINGS

def save_settings(settings: dict):
    if not SETTINGS_FILE.parent.exists():
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

    nv_key = settings.get("nvidia_key", "")
    op_key = settings.get("openai_key", "")
    or_key = settings.get("openrouter_key", "")

    # Attempt to write to secure storage
    saved_nv = set_secure_key("nvidia_key", nv_key)
    saved_op = set_secure_key("openai_key", op_key)
    saved_or = set_secure_key("openrouter_key", or_key)

    clean_settings = dict(settings)
    if saved_nv:
        clean_settings["nvidia_key"] = "keyring_secured"
    if saved_op:
        clean_settings["openai_key"] = "keyring_secured"
    if saved_or:
        clean_settings["openrouter_key"] = "keyring_secured"

    SETTINGS_FILE.write_text(json.dumps(clean_settings, indent=4), encoding="utf-8")

    os.environ["NVIDIA_API_KEY"] = nv_key
    os.environ["OPENAI_API_KEY"] = op_key
    os.environ["OPENROUTER_API_KEY"] = or_key
    try:
        from sai.config import settings as sai_settings
        sai_settings.nvidia_api_key = nv_key
    except Exception:
        pass
