from core.kernel import kernel
from core.settings import load_settings

class ModelRouter:
    """
    Model Router dynamically maps request kinds to models and retrieves 
    registered provider instances from the Kernel.
    """
    @staticmethod
    def route(task_kind: str) -> tuple[str, str]:
        """
        Returns a tuple of (provider_name, model_name) based on the task nature.
        Supported providers: 'nvidia', 'openai', 'ollama'.
        """
        settings = load_settings()
        provider = settings.get("provider", "nvidia")
        
        t_kind = task_kind.lower()
        if t_kind == "jev":
            # An unset/empty jev_model reuses reasoner_model, so Jev always tracks
            # whatever model the rest of the app is actually configured to use.
            model = settings.get("jev_model") or settings.get("reasoner_model", "nvidia/llama-3.3-nemotron-super-49b-v1")
            return provider, model
        if "code" in t_kind or "edit" in t_kind or "implement" in t_kind:
            model = settings.get("coder_model", "meta/llama-3.1-70b-instruct")
        else:
            model = settings.get("reasoner_model", "nvidia/llama-3.3-nemotron-super-49b-v1")
            
        return provider, model
