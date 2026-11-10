import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Case2Code CASE Platform"
    API_V1_STR: str = "/api"
    
    # Database config: default to PostgreSQL, easily overridden via DATABASE_URL env var
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgrespassword@localhost:5432/case2code_db",
    )
    
    # Fallback to SQLite if PostgreSQL connection fails in local testing/dev
    SQLITE_FALLBACK_URL: str = "sqlite:///./case2code.db"

    # AI / LLM Configuration (Local-First Hybrid + Cloud Fallback)
    AI_PROVIDER_MODE: str = os.getenv("AI_PROVIDER_MODE", "hybrid")  # "local" | "hybrid" | "gemini"
    LOCAL_LLM_BASE_URL: str = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434")
    LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "qwen2.5-coder:3b")
    LOCAL_LLM_API_KEY: Optional[str] = os.getenv("LOCAL_LLM_API_KEY", None)
    # Hosts a user may point the runtime LLM URL to (comma separated). The host of
    # the currently configured LOCAL_LLM_BASE_URL is always allowed too.
    AI_ALLOWED_LLM_HOSTS: str = os.getenv(
        "AI_ALLOWED_LLM_HOSTS", "localhost,127.0.0.1,host.docker.internal,ollama"
    )
    LOCAL_LLM_TIMEOUT: float = float(os.getenv("LOCAL_LLM_TIMEOUT", "15.0"))
    # Hard deadline for ONE provider call and for the whole provider chain of a turn.
    # They bound how long a hung LLM can hold the room's AI lock.
    AI_PROVIDER_DEADLINE_S: float = float(os.getenv("AI_PROVIDER_DEADLINE_S", "25.0"))
    AI_CHAIN_DEADLINE_S: float = float(os.getenv("AI_CHAIN_DEADLINE_S", "45.0"))
    # Circuit breaker per provider: open after N consecutive failures, retry after the cooldown.
    AI_BREAKER_FAILURES: int = int(os.getenv("AI_BREAKER_FAILURES", "3"))
    AI_BREAKER_COOLDOWN_S: float = float(os.getenv("AI_BREAKER_COOLDOWN_S", "60.0"))

    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY", None)
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

    # Auth & JWT Configuration
    JWT_SECRET: str = os.getenv("JWT_SECRET", "case2code-super-secret-key-change-in-prod-123456789")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS configuration
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",  # Vite default
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        "*",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

