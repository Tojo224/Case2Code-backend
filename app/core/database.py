import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.core.config import settings, validate_production_settings

logger = logging.getLogger(__name__)

Base = declarative_base()


def _mask_url(url: str) -> str:
    """Hides the password of a database URL so it never lands in the logs."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable database url>"


def get_engine():
    # The engine is built at import time, before the app lifespan runs: check the insecure
    # production defaults here too so the error names the real problem (not a DB timeout).
    validate_production_settings(settings)
    db_url = settings.DATABASE_URL
    try:
        # Test if connection works (especially if postgres is expected)
        connect_args = {}
        if "sqlite" in db_url:
            connect_args = {"check_same_thread": False}
        else:
            connect_args = {"connect_timeout": 3}
        engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
        # Try a test connection
        with engine.connect() as conn:
            pass
        return engine
    except Exception as e:
        if settings.is_production:
            # A silent SQLite inside the container would "work" and lose every diagram on redeploy.
            logger.error("Could not connect to the database at '%s': %s", _mask_url(db_url), e)
            raise
        logger.warning(
            f"Could not connect to primary database at '{_mask_url(db_url)}': {e}. Falling back to SQLite '{settings.SQLITE_FALLBACK_URL}'."
        )
        return create_engine(
            settings.SQLITE_FALLBACK_URL,
            connect_args={"check_same_thread": False},
        )


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create all tables in the database and ensure columns exist."""
    import app.infrastructure.persistence.models  # noqa: F401
    import app.infrastructure.persistence.version_models  # noqa: F401
    import app.infrastructure.persistence.room_memory_models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    try:
        with engine.begin() as conn:
            is_postgres = "postgresql" in str(engine.url)
            if is_postgres:
                conn.execute(text("ALTER TABLE diagrams ADD COLUMN IF NOT EXISTS owner_id VARCHAR(64)"))
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token VARCHAR(255)"))
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires_at TIMESTAMP WITH TIME ZONE"))
                conn.execute(text("ALTER TABLE diagram_chat_messages ADD COLUMN IF NOT EXISTS ai_engine VARCHAR(128)"))
                conn.execute(text("ALTER TABLE diagram_chat_messages ADD COLUMN IF NOT EXISTS suggested_commands JSONB"))
                conn.execute(text("ALTER TABLE diagram_chat_messages ADD COLUMN IF NOT EXISTS suggested_operations JSONB"))
                conn.execute(text("ALTER TABLE diagram_chat_messages ADD COLUMN IF NOT EXISTS resolutions JSONB"))
            else:
                # Check SQLite schema for owner_id in diagrams
                result = conn.execute(text("PRAGMA table_info(diagrams)")).fetchall()
                col_names = [row[1] for row in result]
                if col_names and "owner_id" not in col_names:
                    conn.execute(text("ALTER TABLE diagrams ADD COLUMN owner_id VARCHAR(64)"))

                # Check SQLite schema for reset_token and reset_token_expires_at in users
                user_result = conn.execute(text("PRAGMA table_info(users)")).fetchall()
                user_cols = [row[1] for row in user_result]
                if user_cols and "reset_token" not in user_cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN reset_token VARCHAR(255)"))
                if user_cols and "reset_token_expires_at" not in user_cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN reset_token_expires_at DATETIME"))

                # Check SQLite schema for ai_engine in diagram_chat_messages
                chat_result = conn.execute(text("PRAGMA table_info(diagram_chat_messages)")).fetchall()
                chat_cols = [row[1] for row in chat_result]
                if chat_cols and "ai_engine" not in chat_cols:
                    conn.execute(text("ALTER TABLE diagram_chat_messages ADD COLUMN ai_engine VARCHAR(128)"))
                if chat_cols and "suggested_commands" not in chat_cols:
                    conn.execute(text("ALTER TABLE diagram_chat_messages ADD COLUMN suggested_commands JSON"))
                if chat_cols and "suggested_operations" not in chat_cols:
                    conn.execute(text("ALTER TABLE diagram_chat_messages ADD COLUMN suggested_operations JSON"))
                if chat_cols and "resolutions" not in chat_cols:
                    conn.execute(text("ALTER TABLE diagram_chat_messages ADD COLUMN resolutions JSON"))
    except Exception as e:
        logger.warning(f"Database migration notice: {e}")

    # Existing databases predate the composite index (create_all skips existing tables).
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_diagram_chat_messages_diagram_created "
                    "ON diagram_chat_messages (diagram_id, created_at)"
                )
            )
    except Exception as e:
        logger.warning(f"Chat index migration notice: {e}")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

