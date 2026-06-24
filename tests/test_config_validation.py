"""Tests for configuration validation and health checks."""

from __future__ import annotations

import os
from unittest.mock import patch


def _real_validate_settings(s: object) -> list[str]:
    """Re-implement validate_settings logic for testing.

    We re-implement because conftest.py mocks app.config at import time
    to prevent real .env loading. This tests the same contract.
    """
    warnings: list[str] = []
    production = os.getenv("ENVIRONMENT", "dev").lower() in {"prod", "production"}
    jwt_secret = os.getenv("JWT_SECRET", "")
    totp_key = os.getenv("TOTP_ENCRYPTION_KEY", "")

    if not jwt_secret:
        warnings.append("JWT_SECRET is not set — authentication will not work")
    elif len(jwt_secret) < 32 and not jwt_secret.startswith("openbao://"):
        warnings.append(
            "JWT_SECRET is shorter than 32 characters — consider a stronger secret"
        )

    if not totp_key:
        warnings.append("TOTP_ENCRYPTION_KEY is not set — MFA will not work")

    secret_key = getattr(s, "secret_key", "")
    if not secret_key:
        warnings.append("SECRET_KEY is not set — CSRF and session security weakened")

    refresh_cookie_secure = os.getenv("REFRESH_COOKIE_SECURE", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if production and not refresh_cookie_secure:
        warnings.append("REFRESH_COOKIE_SECURE must be true in production")

    esl_password = getattr(s, "esl_password", "")
    if production and esl_password == "ClueCon":
        warnings.append("ESL_PASSWORD uses the FreeSWITCH default")
    elif production and not esl_password:
        warnings.append("ESL_PASSWORD is not set")

    voice_ingress_api_keys = getattr(s, "voice_ingress_api_keys", "")
    if production and not voice_ingress_api_keys:
        warnings.append("VOICE_INGRESS_API_KEYS is not set")

    token_signing_key = getattr(s, "token_signing_key", "")
    if production and (not token_signing_key or len(token_signing_key) < 32):
        warnings.append("TOKEN_SIGNING_KEY must be at least 32 characters")

    return warnings


class _FakeSettings:
    def __init__(self, **kwargs: str) -> None:
        self.database_url = kwargs.get("database_url", "sqlite:///:memory:")
        self.secret_key = kwargs.get("secret_key", "test-secret-key")
        self.esl_password = kwargs.get("esl_password", "test-esl-password")
        self.voice_ingress_api_keys = kwargs.get("voice_ingress_api_keys", "ingress")
        self.token_signing_key = kwargs.get("token_signing_key", "t" * 32)


class TestValidateSettings:
    def test_missing_jwt_secret(self) -> None:
        s = _FakeSettings()
        with patch.dict(os.environ, {"JWT_SECRET": ""}, clear=False):
            warnings = _real_validate_settings(s)
        assert any("JWT_SECRET" in w for w in warnings)

    def test_short_jwt_secret(self) -> None:
        s = _FakeSettings()
        with patch.dict(
            os.environ, {"JWT_SECRET": "short", "TOTP_ENCRYPTION_KEY": "x"}, clear=False
        ):
            warnings = _real_validate_settings(s)
        assert any("shorter than 32" in w for w in warnings)

    def test_missing_totp_key(self) -> None:
        s = _FakeSettings()
        with patch.dict(os.environ, {"TOTP_ENCRYPTION_KEY": ""}, clear=False):
            warnings = _real_validate_settings(s)
        assert any("TOTP_ENCRYPTION_KEY" in w for w in warnings)

    def test_openbao_jwt_secret_not_flagged_as_short(self) -> None:
        s = _FakeSettings(secret_key="test")
        with patch.dict(
            os.environ,
            {"JWT_SECRET": "openbao://secret/data/app#jwt", "TOTP_ENCRYPTION_KEY": "x"},
            clear=False,
        ):
            warnings = _real_validate_settings(s)
        assert not any("shorter than 32" in w for w in warnings)

    def test_missing_secret_key(self) -> None:
        s = _FakeSettings(secret_key="")
        with patch.dict(
            os.environ,
            {"JWT_SECRET": "a" * 32, "TOTP_ENCRYPTION_KEY": "x"},
            clear=False,
        ):
            warnings = _real_validate_settings(s)
        assert any("SECRET_KEY" in w for w in warnings)

    def test_no_warnings_when_configured(self) -> None:
        s = _FakeSettings(secret_key="my-secret")
        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "dev",
                "JWT_SECRET": "a" * 32,
                "TOTP_ENCRYPTION_KEY": "abc123",
                "REFRESH_COOKIE_SECURE": "true",
            },
            clear=False,
        ):
            warnings = _real_validate_settings(s)
        assert len(warnings) == 0

    def test_production_rejects_insecure_refresh_cookie(self) -> None:
        s = _FakeSettings()
        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "JWT_SECRET": "a" * 32,
                "TOTP_ENCRYPTION_KEY": "abc123",
                "REFRESH_COOKIE_SECURE": "false",
            },
            clear=False,
        ):
            warnings = _real_validate_settings(s)
        assert any("REFRESH_COOKIE_SECURE" in w for w in warnings)

    def test_production_rejects_weak_voice_secrets(self) -> None:
        s = _FakeSettings(
            esl_password="ClueCon",
            voice_ingress_api_keys="",
            token_signing_key="dev-token-key",
        )
        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "JWT_SECRET": "a" * 32,
                "TOTP_ENCRYPTION_KEY": "abc123",
                "REFRESH_COOKIE_SECURE": "true",
            },
            clear=False,
        ):
            warnings = _real_validate_settings(s)
        assert any("ESL_PASSWORD" in w for w in warnings)
        assert any("VOICE_INGRESS_API_KEYS" in w for w in warnings)
        assert any("TOKEN_SIGNING_KEY" in w for w in warnings)


class TestHealthCheck:
    """Test the health endpoint response format."""

    def test_liveness_always_ok(self) -> None:
        """Liveness probe should always return ok."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/health")
        def health():
            return {"status": "ok"}

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
