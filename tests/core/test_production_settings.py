"""Deployment hardening: insecure defaults must not boot in production."""

import logging

import pytest

from app.core import database
from app.core.config import DEFAULT_JWT_SECRET, Settings, validate_production_settings

GOOD_SECRET = "a" * 48
PG_URL = "postgresql+psycopg2://app:s3cretpw@db:5432/case2code_db"


def _settings(**overrides) -> Settings:
    base = {"ENVIRONMENT": "production", "JWT_SECRET": GOOD_SECRET, "DATABASE_URL": PG_URL}
    base.update(overrides)
    return Settings(**base)


def test_development_accepts_anything():
    validate_production_settings(_settings(ENVIRONMENT="development", JWT_SECRET=DEFAULT_JWT_SECRET))


def test_production_accepts_a_strong_secret_and_postgres():
    validate_production_settings(_settings())


@pytest.mark.parametrize("secret", [DEFAULT_JWT_SECRET, "short", ""])
def test_production_rejects_default_missing_or_short_secret(secret):
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        validate_production_settings(_settings(JWT_SECRET=secret))


def test_production_rejects_sqlite_database():
    with pytest.raises(RuntimeError, match="SQLite"):
        validate_production_settings(_settings(DATABASE_URL="sqlite:///./case2code.db"))


def test_cors_is_closed_by_default_in_production_and_open_in_development():
    assert _settings().CORS_ORIGINS == []
    assert "*" in _settings(ENVIRONMENT="development").CORS_ORIGINS


def test_cors_origins_come_from_the_environment_variable():
    s = _settings(CORS_ALLOWED_ORIGINS=" https://a.example.com , https://b.example.com ")
    assert s.CORS_ORIGINS == ["https://a.example.com", "https://b.example.com"]


def test_production_does_not_fall_back_to_sqlite_when_postgres_is_down(monkeypatch, caplog):
    monkeypatch.setattr(database.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        database.settings, "DATABASE_URL", "postgresql+psycopg2://app:s3cretpw@127.0.0.1:1/nodb"
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(Exception):
            database.get_engine()
    assert "s3cretpw" not in caplog.text  # password masked in logs


def test_development_still_falls_back_to_sqlite(monkeypatch):
    monkeypatch.setattr(database.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(
        database.settings, "DATABASE_URL", "postgresql+psycopg2://app:s3cretpw@127.0.0.1:1/nodb"
    )
    engine = database.get_engine()
    assert engine.url.get_backend_name() == "sqlite"
