import json
from pathlib import Path
import os
import keyring

SETTINGS_FILE = Path("/Users/saiganeshongolu/sAI/.sai/settings.json")

DEFAULT_SETTINGS = {
    "provider": "nvidia",
    "nvidia_key": "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",
    "openai_key": "",
    "ollama_url": "http://localhost:11434",
    "coder_model": "meta/llama-3.1-70b-instruct",
    "reasoner_model": "nvidia/llama-3.3-nemotron-super-49b-v1",
    "temperature": 0.2,
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
        
        nv_key = get_secure_key("nvidia_key", nvidia_raw if nvidia_raw != "keyring_secured" else "")
        op_key = get_secure_key("openai_key", openai_raw if openai_raw != "keyring_secured" else "")
        
        data["nvidia_key"] = nv_key
        data["openai_key"] = op_key
        
        # Inject keys into env
        os.environ["NVIDIA_API_KEY"] = nv_key
        os.environ["OPENAI_API_KEY"] = op_key
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
    
    # Attempt to write to secure storage
    saved_nv = set_secure_key("nvidia_key", nv_key)
    saved_op = set_secure_key("openai_key", op_key)
    
    clean_settings = dict(settings)
    if saved_nv:
        clean_settings["nvidia_key"] = "keyring_secured"
    if saved_op:
        clean_settings["openai_key"] = "keyring_secured"
        
    SETTINGS_FILE.write_text(json.dumps(clean_settings, indent=4), encoding="utf-8")
    
    os.environ["NVIDIA_API_KEY"] = nv_key
    os.environ["OPENAI_API_KEY"] = op_key
    try:
        from sai.config import settings as sai_settings
        sai_settings.nvidia_api_key = nv_key
    except Exception:
        pass
