from app.config import settings


def test_voice_settings_present():
    assert settings.fusionpbx_db_url
    assert settings.esl_port == 8021
    assert hasattr(settings, "voice_ingress_api_keys")
    assert hasattr(settings, "token_signing_key")
