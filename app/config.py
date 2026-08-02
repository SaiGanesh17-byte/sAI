import os
from pathlib import Path
from dotenv import load_dotenv

# Find the project root (.env location)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Load the environment variables from the .env file if it exists
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE)
else:
    load_dotenv()

# NVIDIA API Configuration
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_API_BASE_URL = os.getenv("NVIDIA_API_BASE_URL", "https://integrate.api.nvidia.com/v1")

# Verify NVIDIA API Key presence
def check_config():
    if not NVIDIA_API_KEY or NVIDIA_API_KEY == "your_nvidia_api_key_here":
        raise ValueError(
            "NVIDIA_API_KEY is not configured!\n\n"
            "Please perform the following steps:\n"
            "1. Copy '.env.template' to '.env' in the project root:\n"
            "   cp .env.template .env\n"
            "2. Edit the '.env' file and replace 'your_nvidia_api_key_here' with your real NVIDIA API Key.\n"
            "3. Get an API key from NVIDIA NIM: https://build.nvidia.com/"
        )
