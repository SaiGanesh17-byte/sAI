from openai import OpenAI
from sai.config import settings

def get_nvidia_client() -> OpenAI:
    """Returns an OpenAI-compatible client configured for the NVIDIA API.
    
    Raises a ValueError if the NVIDIA API Key is not set or has the placeholder value.
    """
    if not settings.has_nvidia_key:
        raise ValueError(
            "NVIDIA_API_KEY is not configured.\n\n"
            "To fix this:\n"
            "1. Copy '.env.template' to '.env' in the project root:\n"
            "   cp .env.template .env\n"
            "2. Open '.env' and replace 'your_nvidia_api_key_here' with your real NVIDIA API Key.\n"
            "3. Get an API key from NVIDIA NIM: https://build.nvidia.com/"
        )
    
    return OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_api_base_url
    )

class PrimaryModels:
    """Standard model identifiers hosted on NVIDIA NIM."""
    QWEN3_CODER_480B = "qwen/qwen-2.5-coder-32b-instruct" # Standard available models
    NEMOTRON_340B_INSTRUCT = "nvidia/nemotron-4-340b-instruct"
    DEEPSEEK_CODER_V2_5 = "deepseek-ai/deepseek-coder-236b-instruct"
    LLAMA_3_1_405B_INSTRUCT = "meta/llama-3.1-405b-instruct"
