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

    # AI / LLM Configuration
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY", None)
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

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

