from __future__ import annotations

from services.api import settings, storage


def _configure_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def test_runtime_settings_save_records_redacted_audit(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)

    saved = storage.save_runtime_configuration(
        {
            "worker": {"poll_seconds": 5},
            "mabang": {"workspace_url": "https://mabang.example/workspace?token=secret"},
        }
    )
    audit = storage.list_runtime_configuration_audit()

    assert saved["version"] == 1
    assert saved["worker"]["restart_required"] is True
    assert audit[0]["groups"] == ["mabang", "worker"]
    assert "secret" not in str(audit[0])
    assert "token" not in str(audit[0]).lower()
