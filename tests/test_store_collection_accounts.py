from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import operations_dashboard, settings, storage
from services.api import store_collection_service
from services.api.store_collection_scheduler import (
    AccountCollectionError,
    StoreCollectionScheduler,
)
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


# --- 调度器：按账号隔离 ---------------------------------------------------


def test_account_login_failure_pauses_only_that_account() -> None:
    calls: list[str] = []

    def collect(store_id: str) -> dict:
        calls.append(store_id)
        if store_id == "a-1":
            raise AccountCollectionError("acct-a", "登录已过期")
        return {"categories": [store_id]}

    scheduler = StoreCollectionScheduler(
        stores={
            "a-1": {"enabled": True, "account_id": "acct-a"},
            "a-2": {"enabled": True, "account_id": "acct-a"},
            "b-1": {"enabled": True, "account_id": "acct-b"},
        },
        collector=collect,
    )

    runs = scheduler.collect_now()

    assert calls == ["a-1", "b-1"]
    assert [run.store_id for run in runs] == ["a-1", "b-1"]
    assert runs[0].status == "failed" and runs[0].account_id == "acct-a"
    assert runs[1].status == "succeeded" and runs[1].account_id == "acct-b"
    assert scheduler.store_status("a-2")["paused"] is True
    assert scheduler.account_status("acct-a")["paused"] is True
    assert scheduler.account_status("acct-b")["paused"] is False

    assert sorted(scheduler.resume_account("acct-a")) == ["a-1", "a-2"]
    assert scheduler.account_status("acct-a")["paused"] is False


def test_store_level_failure_still_only_fails_that_store() -> None:
    def collect(store_id: str) -> dict:
        if store_id == "a-1":
            raise RuntimeError("类目解析失败")
        return {"categories": [store_id]}

    scheduler = StoreCollectionScheduler(
        stores={
            "a-1": {"enabled": True, "account_id": "acct-a"},
            "a-2": {"enabled": True, "account_id": "acct-a"},
        },
        collector=collect,
    )

    runs = scheduler.collect_now()

    assert [run.status for run in runs] == ["failed", "succeeded"]
    assert scheduler.store_status("a-2")["paused"] is False
    assert scheduler.account_status("acct-a")["paused"] is False


def test_runs_record_the_account_and_can_be_filtered_by_it() -> None:
    scheduler = StoreCollectionScheduler(
        stores={
            "a-1": {"enabled": True, "account_id": "acct-a"},
            "b-1": {"enabled": True, "account_id": "acct-b"},
        },
        collector=lambda store_id: {"categories": []},
    )

    scheduler.collect_now()

    assert [run.account_id for run in scheduler.list_runs()] == ["acct-b", "acct-a"]
    only_a = scheduler.list_runs(account_id="acct-a")
    assert [run.store_id for run in only_a] == ["a-1"]
    assert only_a[0].to_dict()["account_id"] == "acct-a"


# --- 看板：采集时间按账号归属 ---------------------------------------------


def test_stale_store_catalogs_are_attributed_per_account() -> None:
    data = {
        "mabang_store_options": [
            {"store_name": "A店", "mabang_store_id": "shared-1", "account_id": "acct-a"},
            {"store_name": "B店", "mabang_store_id": "shared-1", "account_id": "acct-b"},
        ],
        "runtime_configuration": {
            "store_collection": {
                "last_collected_at": {"acct-a|shared-1": "2026-10-01T00:00:00+00:00"}
            }
        },
    }

    stale = operations_dashboard._stale_store_catalogs(data, "")

    assert [item["account_id"] for item in stale] == ["acct-b"]
    assert stale[0]["store_id"] == "shared-1"


def test_stale_store_catalogs_still_accept_the_legacy_store_only_key() -> None:
    data = {
        "mabang_store_options": [
            {"store_name": "A店", "mabang_store_id": "s-1", "account_id": "acct-a"},
        ],
        "runtime_configuration": {
            "store_collection": {"last_collected_at": {"s-1": "2026-10-01T00:00:00+00:00"}}
        },
    }

    assert operations_dashboard._stale_store_catalogs(data, "") == []


# --- 采集服务：持久化 + 按账号隔离 ----------------------------------------


def test_run_store_collection_persists_runs_and_collection_time() -> None:
    account = storage.create_mabang_account(
        {"name": "A", "login_account": "acct-a", "cdp_port": 9223}
    )
    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "PH", "store_name": "A店", "mabang_store_id": "s-1"},
        account_id=account["id"],
    )

    result = store_collection_service.run_store_collection(
        collector=lambda store_id, store: {"watermarks": [store_id]}
    )

    assert [run["store_id"] for run in result["runs"]] == ["s-1"]
    assert result["runs"][0]["account_id"] == account["id"]
    assert result["runs"][0]["status"] == "succeeded"
    assert result["errors"] == []

    stored = storage.list_store_collection_runs()
    assert [row["store_id"] for row in stored] == ["s-1"]
    collected = storage.load_store()["runtime_configuration"]["store_collection"]["last_collected_at"]
    assert collected.get(f"{account['id']}|s-1")


def test_run_store_collection_isolates_account_login_failure() -> None:
    a = storage.create_mabang_account({"name": "A", "login_account": "acct-a", "cdp_port": 9223})
    b = storage.create_mabang_account({"name": "B", "login_account": "acct-b", "cdp_port": 9224})
    for store_id, account in (("a-1", a), ("a-2", a), ("b-1", b)):
        storage.upsert_mabang_store_options(
            {
                "platform": "shopee",
                "country": "PH",
                "store_name": store_id,
                "mabang_store_id": store_id,
            },
            account_id=account["id"],
        )

    def collector(store_id: str, store: dict) -> dict:
        if store_id == "a-1":
            raise AccountCollectionError(str(store.get("account_id") or ""), "登录已过期")
        return {"watermarks": [store_id]}

    result = store_collection_service.run_store_collection(collector=collector)

    by_store = {run["store_id"]: run for run in result["runs"]}
    assert by_store["a-1"]["status"] == "failed"
    assert by_store["b-1"]["status"] == "succeeded"
    # A 账号被暂停，它的其余店铺本轮直接跳过
    assert "a-2" not in by_store
    assert result["accounts"][a["id"]]["paused"] is True
    assert result["accounts"][b["id"]]["paused"] is False

    # 失败不覆盖上一次有效快照：只有成功的 b-1 有采集时间
    collected = storage.load_store()["runtime_configuration"]["store_collection"]["last_collected_at"]
    assert collected.get(f"{b['id']}|b-1")
    assert f"{a['id']}|a-1" not in collected


# --- API -----------------------------------------------------------------


def test_store_collection_api_collects_and_lists_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.api.store_collection_service._default_collector",
        lambda store_id, store: {"watermarks": [store_id]},
    )
    server, base_url = _start_api()
    try:
        _status, account = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "A", "login_account": "acct-a", "cdp_port": 9223},
        )
        _request_json(
            "POST",
            f"{base_url}/api/mabang/store-options",
            {
                "account_id": account["id"],
                "platform": "shopee",
                "country": "PH",
                "store_name": "A店",
                "mabang_store_id": "s-1",
            },
        )

        status, result = _request_json("POST", f"{base_url}/api/store-collection/collect", {})
        assert status == 201
        assert [run["store_id"] for run in result["runs"]] == ["s-1"]
        assert result["runs"][0]["account_id"] == account["id"]

        status, listed = _request_json("GET", f"{base_url}/api/store-collection/runs")
        assert status == 200
        assert [row["store_id"] for row in listed["items"]] == ["s-1"]
    finally:
        server.shutdown()
        server.server_close()
