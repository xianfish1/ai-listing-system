from __future__ import annotations

from cryptography.fernet import Fernet

from services.api import settings
from services.api.credential_crypto import CredentialCrypto, redact_connection


def test_api_key_is_encrypted_and_never_serialized() -> None:
    crypto = CredentialCrypto(Fernet.generate_key().decode("ascii"))

    encrypted = crypto.encrypt("secret-key")
    public = redact_connection({"id": "conn-1", "encrypted_api_key": encrypted})

    assert encrypted != "secret-key"
    assert crypto.decrypt(encrypted) == "secret-key"
    assert public["api_key_configured"] is True
    assert "encrypted_api_key" not in public
    assert "secret-key" not in str(public)


def test_empty_or_invalid_master_key_is_rejected() -> None:
    for value in ["", "not-a-fernet-key"]:
        try:
            CredentialCrypto(value)
        except ValueError as exc:
            assert "AI_CREDENTIALS_MASTER_KEY" in str(exc)
        else:
            raise AssertionError("Invalid master key was accepted")


def test_unconfigured_master_key_is_generated_once_and_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "AI_CREDENTIALS_MASTER_KEY", "")

    first = settings.get_ai_credentials_master_key()
    second = settings.get_ai_credentials_master_key()

    assert first == second
    assert CredentialCrypto(first).decrypt(CredentialCrypto(first).encrypt("secret")) == "secret"
    assert (tmp_path / "ai_credentials_master.key").read_text(encoding="ascii").strip() == first
