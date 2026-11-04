from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _request_json(method: str, url: str, payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _collect_ok(store_id: str = "2022749877") -> dict:
    return {
        "ok": True,
        "status": "completed",
        "store": {"id": store_id, "name": "Oct.3H"},
        "items": [
            {
                "activity_id": "516401018581044",
                "name": "新店40%",
                "start_at": "2026-10-19 23:57:00",
                "end_at": "2027-04-17 01:00:00",
                "status": "ongoing",
                "selected": True,
            }
        ],
        "catalog_loaded": True,
        "diagnostics": {"opened": True, "method": "activity_header_link", "rows": 1, "closed": "close_button"},
    }


def test_collect_store_promotions_writes_catalog_with_validity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.api.main.run_store_promotion_collect", lambda store_id, **kwargs: _collect_ok(store_id))
    server, base_url = _start_api()
    try:
        status, payload = _request_json(
            "POST",
            f"{base_url}/api/store-promotions/collect",
            {"store_id": "2022749877"},
        )
        assert status == 200, payload
        assert payload["ok"] is True
        assert payload["count"] == 1
        item = payload["items"][0]
        assert item["activity_id"] == "516401018581044"
        assert item["start_at"] == "2026-10-19 23:57:00"
        assert item["end_at"] == "2027-04-17 01:00:00"
        # 状态由采集的 ongoing + 有效期判定，目录里这条活动应可用。
        assert item["status"] == "available"
        assert payload["status"]["status"] == "ok"
        assert payload["status"]["count"] == 1

        status, listing = _request_json("GET", f"{base_url}/api/store-promotions?store_id=2022749877")
        assert status == 200
        assert listing["count"] == 1
        assert listing["status"]["status"] == "ok"
        assert "2026-10-19" in listing["items"][0]["label"]
    finally:
        server.shutdown()
        server.server_close()


def test_collect_store_promotions_failure_keeps_catalog_and_records_status(monkeypatch: pytest.MonkeyPatch) -> None:
    storage.replace_store_promotion_catalog(
        "2022749877",
        [{"activity_id": "1", "name": "旧活动", "start_at": "2026-01-01 00:00:00", "end_at": "2026-12-31 00:00:00", "status": "ongoing"}],
    )
    monkeypatch.setattr(
        "services.api.main.run_store_promotion_collect",
        lambda store_id, **kwargs: {
            "ok": False,
            "status": "promotion_dialog_not_opened",
            "error": "没能打开马帮的「选择折扣活动」弹窗",
        },
    )
    server, base_url = _start_api()
    try:
        status, payload = _request_json(
            "POST",
            f"{base_url}/api/store-promotions/collect",
            {"store_id": "2022749877"},
        )
        assert status == 422
        assert "选择折扣活动" in payload["error"]
        # 目录原样保留，只记失败状态。
        items = storage.list_store_promotions("2022749877")
        assert [item["activity_id"] for item in items] == ["1"]
        catalog_status = storage.get_store_promotion_catalog_status("2022749877")
        assert catalog_status["status"] == "failed"
        assert "选择折扣活动" in catalog_status["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_collect_store_promotions_rejects_a_different_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.api.main.run_store_promotion_collect",
        lambda store_id, **kwargs: {
            **_collect_ok("different-store"),
            "diagnostics": {"opened": True},
        },
    )
    server, base_url = _start_api()
    try:
        status, payload = _request_json(
            "POST",
            f"{base_url}/api/store-promotions/collect",
            {"store_id": "2022749877"},
        )
        assert status == 422
        assert "不是要采集的店铺" in payload["error"]
        assert storage.list_store_promotions("2022749877") == []
    finally:
        server.shutdown()
        server.server_close()


def test_store_promotion_catalog_status_keeps_last_success_time_on_failure() -> None:
    items = [{"activity_id": "1", "name": "A", "start_at": "", "end_at": "", "status": "ongoing"}]
    storage.replace_store_promotion_catalog("s1", items, now="2026-10-01T00:00:00+00:00")
    ok_status = storage.get_store_promotion_catalog_status("s1")
    assert ok_status["status"] == "ok"
    assert ok_status["collected_at"] == "2026-10-01T00:00:00+00:00"

    storage.record_store_promotion_collect_failure("s1", "弹窗没打开")
    failed_status = storage.get_store_promotion_catalog_status("s1")
    assert failed_status["status"] == "failed"
    assert failed_status["error"] == "弹窗没打开"
    # 最后一次成功采集时间要保留，界面才能显示"上次采到是什么时候"。
    assert failed_status["collected_at"] == "2026-10-01T00:00:00+00:00"
    assert failed_status["count"] == 1
