from openai import OpenAI
from app import config

def get_client() -> OpenAI:
    """
    Returns an OpenAI-compatible client pointing to the NVIDIA API base URL.
    Verifies that the NVIDIA API key is properly configured first.
    """
    config.check_config()
    return OpenAI(
        api_key=config.NVIDIA_API_KEY,
        base_url=config.NVIDIA_API_BASE_URL
    )
