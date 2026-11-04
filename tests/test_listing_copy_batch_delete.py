"""批量删除店铺刊登副本（storage 层）。

- 一次锁、一次落库：删多个副本不该每个都重写整份主库
- 正在执行（claimed/running）的副本按条跳过，不阻断同批其它副本
- 不存在的 id 只回报，不报错
- 终态任务留作审计，未完成任务/SKU/价格/快照跟着副本清掉
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _seed():
    with storage.locked_store() as (data, save):
        data.setdefault("store_listing_copies", []).extend([
            {"id": "copy-plain", "base_product_id": "base-1", "store_name": "POD Store A"},
            {"id": "copy-ready", "base_product_id": "base-1", "store_name": "POD Store B"},
            {"id": "copy-running", "base_product_id": "base-1", "store_name": "POD Store C"},
        ])
        data.setdefault("listing_skus", []).extend([
            {"id": "sku-1", "listing_copy_id": "copy-plain"},
            {"id": "sku-2", "listing_copy_id": "copy-running"},
        ])
        data.setdefault("listing_prices", []).extend([
            {"id": "price-1", "listing_copy_id": "copy-plain"},
        ])
        data.setdefault("automation_jobs", []).extend([
            # 排队中（还没被领取）：非终态，跟着副本一起清掉
            {"id": "job-ready", "listing_copy_id": "copy-ready", "status": "ready"},
            # 正在执行：不允许被顺手删掉
            {"id": "job-running", "listing_copy_id": "copy-running", "status": "running"},
            # 终态：保留做审计
            {"id": "job-saved", "listing_copy_id": "copy-plain", "status": "saved"},
        ])
        data.setdefault("automation_job_snapshots", {})["job-ready"] = {"steps": []}
        data["automation_job_snapshots"]["job-saved"] = {"steps": []}
        save(data)


def test_batch_delete_removes_copies_and_skips_running_ones() -> None:
    _seed()

    result = storage.delete_store_listing_copies(["copy-plain", "copy-ready", "copy-running", "missing-copy"])

    assert result["ok"] is True
    assert result["requested_count"] == 4
    assert result["deleted_count"] == 2
    assert sorted(result["deleted_ids"]) == ["copy-plain", "copy-ready"]
    assert result["running_job_ids"] == ["copy-running"]
    assert result["not_found_ids"] == ["missing-copy"]
    assert result["deleted_listing_sku_count"] == 1
    assert result["deleted_listing_price_count"] == 1
    # copy-plain 的终态任务保留，copy-ready 的排队任务跟着副本清掉
    assert result["deleted_automation_job_count"] == 1
    assert result["retained_automation_job_count"] == 1

    data = storage.load_store()
    remaining = {copy["id"] for copy in data.get("store_listing_copies", [])}
    assert remaining == {"copy-running"}
    job_ids = {job["id"] for job in data.get("automation_jobs", [])}
    assert job_ids == {"job-running", "job-saved"}
    assert [sku["listing_copy_id"] for sku in data.get("listing_skus", [])] == ["copy-running"]
    assert data.get("listing_prices") == []
    assert "job-ready" not in data.get("automation_job_snapshots", {})
    assert "job-saved" in data.get("automation_job_snapshots", {})


def test_single_delete_still_refuses_running_copy() -> None:
    _seed()

    with pytest.raises(ValueError):
        storage.delete_store_listing_copy("copy-running")

    assert storage.load_store().get("store_listing_copies")
    assert storage.delete_store_listing_copy("copy-plain")["id"] == "copy-plain"
    assert storage.delete_store_listing_copy("missing-copy") is None
