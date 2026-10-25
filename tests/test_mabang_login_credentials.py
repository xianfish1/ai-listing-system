from __future__ import annotations

import json

from services.api import settings, storage


def _configure_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def test_login_password_is_stored_encrypted_and_never_echoed(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)

    saved = storage.save_runtime_configuration(
        {"mabang": {"login_account": " ops@example.com ", "login_password": "sup3r-secret"}}
    )

    assert saved["mabang"]["login_account"] == "ops@example.com"
    assert "login_password" not in saved["mabang"]
    assert saved["mabang"]["login_configured"] is True

    stored = json.loads(settings.STORE_FILE.read_text(encoding="utf-8"))
    token = stored["runtime_configuration"]["mabang"]["login_password"]
    assert token and token != "sup3r-secret"
    assert "sup3r-secret" not in json.dumps(stored)

    public = storage.get_runtime_configuration()
    assert "login_password" not in public["mabang"]
    assert public["mabang"]["login_configured"] is True

    assert storage.get_mabang_login_credentials() == ("ops@example.com", "sup3r-secret")


def test_blank_login_password_keeps_the_existing_binding(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    storage.save_runtime_configuration({"mabang": {"login_account": "ops", "login_password": "first-pass"}})

    storage.save_runtime_configuration({"mabang": {"login_account": "ops2", "login_password": "  "}})

    assert storage.get_mabang_login_credentials() == ("ops2", "first-pass")

    audit = storage.list_runtime_configuration_audit()
    # 审计必须脱敏：不出现明文密码，只保留 [redacted] 占位。
    assert "first-pass" not in json.dumps(audit)
    assert "[redacted]" in json.dumps(audit)


def test_unbound_credentials_return_none(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)

    assert storage.get_mabang_login_credentials() == (None, None)

    storage.save_runtime_configuration({"mabang": {"login_account": "only-account"}})
    assert storage.get_mabang_login_credentials() == (None, None)
    assert storage.get_runtime_configuration()["mabang"]["login_configured"] is False
