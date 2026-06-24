import importlib.util
from pathlib import Path

from app.config import settings


def test_voice_settings_present():
    assert settings.fusionpbx_db_url
    assert settings.esl_port == 8021
    assert hasattr(settings, "voice_ingress_api_keys")
    assert hasattr(settings, "token_signing_key")


def _load_real_config():
    """Load the real app/config.py (conftest swaps app.config for a mock module)."""
    path = Path(__file__).resolve().parent.parent / "app" / "config.py"
    spec = importlib.util.spec_from_file_location("_real_app_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_flags_default_esl_password(monkeypatch):
    """ESL_PASSWORD left at the FreeSWITCH default must be a critical config error in prod."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    cfg = _load_real_config()
    warnings = cfg.validate_settings(cfg.Settings(esl_password="ClueCon"))
    assert any(w.critical and "ESL_PASSWORD" in w.message for w in warnings)


def test_production_accepts_overridden_esl_password(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    cfg = _load_real_config()
    warnings = cfg.validate_settings(cfg.Settings(esl_password="a-strong-unique-secret"))
    assert not any("ESL_PASSWORD" in w.message for w in warnings)


def test_dev_allows_default_esl_password(monkeypatch):
    """Dev keeps the default so local stacks still come up."""
    monkeypatch.setenv("ENVIRONMENT", "dev")
    cfg = _load_real_config()
    warnings = cfg.validate_settings(cfg.Settings(esl_password="ClueCon"))
    assert not any("ESL_PASSWORD" in w.message for w in warnings)
