import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.core.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()


def get_engine():
    db_url = settings.DATABASE_URL
    try:
        # Test if connection works (especially if postgres is expected)
        connect_args = {}
        if "sqlite" in db_url:
            connect_args = {"check_same_thread": False}
        engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
        # Try a test connection
        with engine.connect() as conn:
            pass
        return engine
    except Exception as e:
        logger.warning(
            f"Could not connect to primary database at '{db_url}': {e}. Falling back to SQLite '{settings.SQLITE_FALLBACK_URL}'."
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
    Base.metadata.create_all(bind=engine)

    try:
        with engine.begin() as conn:
            # Check SQLite/PostgreSQL schema for owner_id in diagrams
            result = conn.execute(text("PRAGMA table_info(diagrams)")).fetchall()
            col_names = [row[1] for row in result]
            if col_names and "owner_id" not in col_names:
                conn.execute(text("ALTER TABLE diagrams ADD COLUMN owner_id VARCHAR(64)"))
    except Exception as e:
        logger.debug(f"Migration notice: {e}")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

