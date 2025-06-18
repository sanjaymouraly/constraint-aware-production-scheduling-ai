"""Configuration validation tests, including the production secret guard."""

from __future__ import annotations

import pytest
from app.config import Settings
from pydantic import ValidationError


def test_development_settings_keep_the_local_defaults() -> None:
    settings = Settings(
        environment="test", secret_key="test-key", bootstrap_admin_password="test-pass"
    )
    assert not settings.is_production
    assert settings.cors_origin_list


def test_production_rejects_development_secrets() -> None:
    with pytest.raises(ValidationError, match="Production SECRET_KEY"):
        Settings(
            environment="production",
            secret_key="dev-insecure-change-me-in-production",
            bootstrap_admin_password="admin",
        )


def test_production_requires_strong_bootstrap_password() -> None:
    with pytest.raises(ValidationError, match="Production BOOTSTRAP_ADMIN_PASSWORD"):
        Settings(
            environment="production",
            secret_key="unique-random-production-secret-key-12345",
            bootstrap_admin_password="short",
        )


def test_production_accepts_explicit_strong_secrets() -> None:
    settings = Settings(
        environment="production",
        secret_key="unique-random-production-secret-key-12345",
        bootstrap_admin_password="long-unique-admin-password-2026",
    )
    assert settings.is_production
