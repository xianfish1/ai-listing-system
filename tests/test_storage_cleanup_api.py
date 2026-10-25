from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest
from cryptography.fernet import Fernet

from services.api import disk_cleanup, settings, storage
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "storage" / "exports")
    monkeypatch.setattr(settings, "SCREENSHOT_DIR", tmp_path / "storage" / "screenshots")
    monkeypatch.setattr(settings, "LOG_DIR", tmp_path / "storage" / "logs")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "AI_CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode("ascii"))


def _call(method: str, url: str, payload: dict | None = None):
    raw = None if payload is None else json.dumps(payload).encode()
    req = request.Request(url, data=raw, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_storage_usage_and_cleanup_api_require_confirmation() -> None:
    stale = settings.STORAGE_DIR / "tmp" / "stale.bin"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"123")
    server, base = _start_api()
    try:
        status, usage = _call("GET", base + "/api/settings/storage/usage")
        assert status == 200
        assert "temporary_files" in usage["categories"]

        status, rejected = _call("POST", base + "/api/settings/storage/cleanup", {"categories": ["temporary_files"]})
        assert status == 422 and "确认" in rejected["error"]
        status, result = _call("POST", base + "/api/settings/storage/cleanup", {
            "categories": ["temporary_files"], "confirm": True,
        })
        assert status == 200 and result["deleted_files"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_storage_cleanup_api_rejects_unknown_category_and_persists_settings() -> None:
    server, base = _start_api()
    try:
        status, rejected = _call("POST", base + "/api/settings/storage/cleanup", {
            "categories": ["data"], "confirm": True,
        })
        assert status == 422 and "未知清理类别" in rejected["error"]
        status, config = _call("PUT", base + "/api/settings", {
            "storage_cleanup": {"enabled": False, "temp_retention_days": 14},
        })
        assert status == 200
        assert config["storage_cleanup"]["enabled"] is False
        assert config["storage_cleanup"]["temp_retention_days"] == 14
    finally:
        server.shutdown()
        server.server_close()


def test_storage_usage_lists_asset_categories_and_settings_persist() -> None:
    server, base = _start_api()
    try:
        status, usage = _call("GET", base + "/api/settings/storage/usage")
        assert status == 200
        # 商品素材进自动集合；「已删商品遗留素材」只能手动。
        assert usage["categories"]["product_assets"]["automatic"] is True
        assert usage["categories"]["product_assets_legacy"]["automatic"] is False
        assert usage["categories"]["product_assets"]["label"] == "商品素材"

        status, config = _call("PUT", base + "/api/settings", {
            "storage_cleanup": {
                "assets_retention_days": 45,
                "compact_enabled": False,
                "compact_delay_days": 5,
                "compact_max_edge": 900,
                "compact_jpeg_quality": 82,
            },
        })
        assert status == 200
        saved = config["storage_cleanup"]
        assert saved["assets_retention_days"] == 45
        assert saved["compact_enabled"] is False
        assert saved["compact_delay_days"] == 5
        assert saved["compact_max_edge"] == 900
        assert saved["compact_jpeg_quality"] == 82

        status, rejected = _call("PUT", base + "/api/settings", {
            "storage_cleanup": {"compact_max_edge": 10},
        })
        assert status == 422 and "长边" in rejected["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_storage_compact_api_requires_confirmation_and_reports_result() -> None:
    server, base = _start_api()
    try:
        status, rejected = _call("POST", base + "/api/settings/storage/compact", {})
        assert status == 422 and "确认" in rejected["error"]
        status, report = _call("POST", base + "/api/settings/storage/compact", {"confirm": True})
        assert status == 200 and report["ok"] is True and report["files"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_storage_cleanup_api_returns_conflict_when_cdp_is_online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk_cleanup, "cdp_is_available", lambda _url: True)
    server, base = _start_api()
    try:
        status, result = _call("POST", base + "/api/settings/storage/cleanup", {
            "categories": ["browser_cache"], "confirm": True,
        })
        assert status == 409
        assert "关闭" in result["error"]
    finally:
        server.shutdown()
        server.server_close()
