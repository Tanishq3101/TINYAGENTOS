# infrastructure/config.py

"""
Centralized configuration system for TinyAgentOS.

- Uses Pydantic BaseSettings for environment-based config
- Automatically reads from `.env`
- Cached using lru_cache for performance
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """
    Global configuration object.

    All environment variables and system configs should be defined here.
    """

    # -------------------------
    # Application Settings
    # -------------------------
    APP_NAME: str = "TinyAgentOS"
    DEBUG: bool = True

    # -------------------------
    # LLM Configuration
    # -------------------------
    MODEL_PATH: str = "models/phi-3-mini.gguf"

    # 🔥 UPDATED (better generation quality)
    MAX_TOKENS: int = 512  # was 1024 (no need too high for local model)
    TEMPERATURE: float = 0.6  # slightly controlled for stable JSON

    # -------------------------
    # Agent Behavior (NEW - DAY 8)
    # -------------------------
    MAX_RETRIES: int = 2  # retry for invalid JSON decisions
    MIN_RESPONSE_TOKENS: int = 50  # enforce minimum response length

    # -------------------------
    # Tooling Configuration (NEW)
    # -------------------------
    ALLOWED_TOOLS: list[str] = ["calculator", "weather"]

    # -------------------------
    # Security Configuration
    # -------------------------
    SECRET_KEY: str = "change_this_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # -------------------------
    # API Server Configuration
    # -------------------------
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # -------------------------
    # LLM Runtime Settings
    # -------------------------
    N_THREADS: int = 4
    N_CTX: int = 2048
    N_GPU_LAYERS: int = 0  # 0 = CPU only

    # -------------------------
    # Output Control (NEW)
    # -------------------------
    CLEAN_RESPONSE: bool = True  # trim incomplete sentences
    FORCE_MIN_SENTENCES: int = 3  # improve response quality

    class Config:
        """
        Pydantic config:
        - Loads environment variables from `.env`
        """
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached instance of Settings.

    Why caching?
    - Avoid re-reading env file multiple times
    - Improve performance across system
    """
    return Settings()