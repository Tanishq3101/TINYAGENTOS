# infrastructure/config.py

"""
Centralized configuration system for TinyAgentOS.

- Uses Pydantic BaseSettings for environment-based config
- Automatically reads from `.env`
- Cached using lru_cache for performance
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Global configuration object.

    All environment variables and system configs should be defined here.
    """

    # -------------------------
    # Application Settings
    # --------------------a-----
    APP_NAME: str = "TinyAgentOS"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False  # default OFF — opt in per-environment via .env, never ship True

    # -------------------------
    # LLM Configuration
    # -------------------------
    MODEL_PATH: str = "models/phi-3-mini.gguf"
    MAX_TOKENS: int = 512
    TEMPERATURE: float = 0.6

    # -------------------------
    # Agent Behavior (DAY 8)
    # -------------------------
    MAX_RETRIES: int = 2
    MIN_RESPONSE_TOKENS: int = 50

    # -------------------------
    # Tooling Configuration
    # -------------------------
    ALLOWED_TOOLS: list[str] = ["calculator", "weather"]

    # -------------------------
    # Security Configuration
    # -------------------------
    # No safe default on purpose — startup must fail if this isn't set for real.
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REQUIRE_AUTH: bool = True
    API_KEY_HEADER: str = "X-API-Key"

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
    # Output Control
    # -------------------------
    CLEAN_RESPONSE: bool = True
    FORCE_MIN_SENTENCES: int = 3

    # -------------------------
    # Database / Logging (needed by storage + logging modules)
    # -------------------------
    DATABASE_URL: str = "sqlite:///./tinyagentos.db"
    LOG_LEVEL: str = "INFO"

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        if v.strip().lower() in {"change_this_in_production", "changeme", "secret"}:
            raise ValueError("SECRET_KEY is still a placeholder value — set a real one in .env")
        return v

    @field_validator("PORT")
    @classmethod
    def port_in_range(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("PORT must be between 1 and 65535")
        return v

    class Config:
        """
        Pydantic config:
        - Loads environment variables from `.env`
        """

        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached instance of Settings.

    Why caching?
    - Avoid re-reading env file multiple times
    - Improve performance across system
    """
    return Settings()
