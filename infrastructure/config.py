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

    # Separate from SECRET_KEY on purpose: SECRET_KEY signs JWTs,
    # FERNET_KEY encrypts stored data (infrastructure/security.py's
    # SecurityManager). Different purposes should never share a key —
    # rotating one shouldn't force rotating the other. Optional here
    # (None) because not every deployment encrypts data at rest yet;
    # SecurityManager itself still refuses a missing/empty key at
    # construction time if something actually tries to use it.
    FERNET_KEY: str | None = None

    # -------------------------
    # Rate Limiting
    # -------------------------
    # Was declared in default.yaml (rate_limit_per_minute) but never
    # exposed on Settings, so nothing in code could actually read it —
    # app.py's slowapi Limiter and routes.py's @limiter.limit(...)
    # decorators reference this field to enforce it.
    RATE_LIMIT_PER_MINUTE: int = 60

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

    # -------------------------
    # Monitoring / Stall Watchdog (DAY 20)
    # -------------------------
    # Defaults match infrastructure/stall_watchdog.py's own hardcoded
    # DEFAULT_STALL_THRESHOLD_SECONDS / DEFAULT_WATCHDOG_INTERVAL_SECONDS,
    # so leaving these unset changes nothing. Override via .env once
    # you've tuned them against real production latency, same as any
    # other setting here.
    STALL_THRESHOLD_SECONDS: float = 25.0
    STALL_WATCHDOG_INTERVAL_SECONDS: float = 5.0

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
    # MYPY FIX (was: "Invalid 'type: ignore' comment [syntax]"): this
    # explanatory block used to *start* with the text "type: ignore[...]",
    # which mypy parses as an ignore-directive attempt on ANY comment line
    # starting with "# type:", not just ones attached to code -- the
    # trailing prose after the brackets then failed to match the expected
    # directive syntax. Reworded so only the real directive below (attached
    # to the `return Settings()` line) is parsed as an ignore comment.
    #
    # Ignore justification: SECRET_KEY has no default
    # (by design -- startup must fail without a real one), so mypy sees
    # this as a missing required constructor argument. That's a false
    # positive: pydantic-settings' BaseSettings populates required
    # fields from the environment / .env file at runtime, after mypy
    # has already finished its static check -- mypy can't see into
    # os.environ or .env to know the value is actually supplied. This
    # is a well-known, unavoidable friction point between
    # pydantic-settings and mypy, not a real bug. If SECRET_KEY is
    # genuinely missing/invalid, Settings() still raises a real
    # pydantic ValidationError at runtime -- that safety net is
    # untouched by this ignore.
    return Settings()  # type: ignore[call-arg]
