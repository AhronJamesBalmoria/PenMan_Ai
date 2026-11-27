"""Guardrails for production configuration validation."""
import pytest

from app.config import Settings


def _production_settings(monkeypatch):
    # validate() skips entirely when APP_ENV is "test", so override the env too.
    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    return settings


def test_production_requires_app_base_url(monkeypatch):
    settings = _production_settings(monkeypatch)
    monkeypatch.setattr(settings, "APP_BASE_URL", "")

    with pytest.raises(RuntimeError, match="APP_BASE_URL must be set in production"):
        settings.validate()


def test_production_rejects_malformed_app_base_url(monkeypatch):
    settings = _production_settings(monkeypatch)
    monkeypatch.setattr(settings, "APP_BASE_URL", "penman-ai.onrender.com")

    with pytest.raises(RuntimeError, match="absolute http"):
        settings.validate()


def test_production_accepts_absolute_app_base_url(monkeypatch):
    settings = _production_settings(monkeypatch)
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://penman-ai.onrender.com")

    settings.validate()
