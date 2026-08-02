import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Determine project root and load environment variables
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE)
else:
    # Try searching parent directories if running from subdirectories
    load_dotenv()

class Settings(BaseModel):
    env: str = Field(default_factory=lambda: os.getenv("SAI_ENV", "development"), description="Current deployment environment")
    nvidia_api_key: str | None = Field(default_factory=lambda: os.getenv("NVIDIA_API_KEY"), description="NVIDIA API Key for NVIDIA NIM models")
    nvidia_api_base_url: str = Field(
        default_factory=lambda: os.getenv("NVIDIA_API_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        description="NVIDIA NIM base URL"
    )
    port: int = Field(default_factory=lambda: int(os.getenv("SAI_PORT", "8000")), description="Application hosting port")
    host: str = Field(default_factory=lambda: os.getenv("SAI_HOST", "127.0.0.1"), description="Application hosting address")

    @property
    def has_nvidia_key(self) -> bool:
        key = self.nvidia_api_key
        return bool(key and key.strip() and key != "your_nvidia_api_key_here")

# Initialize global settings instance
settings = Settings()
