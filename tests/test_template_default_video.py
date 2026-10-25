from __future__ import annotations

from pathlib import Path

import pytest

from scripts import browser_use_runner
from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _transaction_defaults() -> dict:
    return {
        "stock": 100,
        "weight_kg": 0.3,
        "package_length_cm": 30,
        "package_width_cm": 20,
        "package_height_cm": 5,
    }


def _make_site_copy_with_template(tmp_path: Path, *, default_video_id: str = "") -> tuple[dict, dict]:
    folder = tmp_path / "AL0601 Video Tee"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg", "Black.jpg", "size-chart.png"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD Store A",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": "Video Tee",
            "product_line": "POD Mens T-Shirt",
            "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
            "mabang_store_id": "store-A",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S"],
            "description": "Template default description.",
            "default_video_id": default_video_id,
            **_transaction_defaults(),
        }
    )
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    copy = storage.get_item("store_listing_copies", result["created"][0]["id"])
    storage.update_store_listing_copy(
        copy["id"],
        {
            "description": "Video test description",
            "description_status": "ready",
            "size_chart_image": "size-chart.png",
            "size_chart_image_path": str(folder / "size-chart.png"),
        },
    )
    return storage.get_item("store_templates", template["id"]), storage.get_item("store_listing_copies", copy["id"])


def test_template_default_video_flows_to_copy_and_automation_payload(tmp_path: Path) -> None:
    template, _copy = _make_site_copy_with_template(tmp_path, default_video_id="sg-11110106-test")
    assert template["default_video_id"] == "sg-11110106-test"

    created = storage.create_automation_jobs_from_listing_copies([_copy["id"]])
    assert created["created_count"] == 1, created["errors"]
    job_id = created["created"][0]["id"]
    payload = storage.build_automation_payload(job_id)
    assert payload["ok"] is True
    video = payload["browser_inputs"]["video"]
    assert video["default_video_id"] == "sg-11110106-test"


def test_template_without_default_video_keeps_empty_id(tmp_path: Path) -> None:
    _template, copy = _make_site_copy_with_template(tmp_path)
    assert copy["default_video_id"] == ""
    created = storage.create_automation_jobs_from_listing_copies([copy["id"]])
    payload = storage.build_automation_payload(created["created"][0]["id"])
    assert payload["browser_inputs"]["video"]["default_video_id"] == ""


def test_update_template_syncs_default_video_id_to_existing_copies(tmp_path: Path) -> None:
    template, copy = _make_site_copy_with_template(tmp_path)
    updated = storage.update_store_template(template["id"], {"default_video_id": "sg-sync-1"})
    assert updated["default_video_id"] == "sg-sync-1"
    synced = storage.get_item("store_listing_copies", copy["id"])
    assert synced["default_video_id"] == "sg-sync-1"


def test_prepare_video_upload_by_id_is_ready_without_file(tmp_path: Path) -> None:
    prepared = browser_use_runner.prepare_video_upload(
        {"default_video_id": "sg-1", "validation_status": "missing", "validation_errors": ["video_missing"]},
        tmp_path,
    )
    assert prepared["action_status"] == "ready"
    assert prepared["select_by_id"] is True
    assert prepared["validation_status"] == "valid"
    assert prepared["default_video_id"] == "sg-1"


def test_prepare_video_upload_without_id_or_file_still_skips(tmp_path: Path) -> None:
    prepared = browser_use_runner.prepare_video_upload(
        {"validation_status": "missing", "validation_errors": ["video_missing"]},
        tmp_path,
    )
    assert prepared["action_status"] == "skipped"
    assert "select_by_id" not in prepared


def test_record_default_video_id_site_vs_cnsc_and_existing() -> None:
    # 本土：首次上传回填模板 + 该模板下缺 ID 的副本
    data = {
        "store_templates": [{"id": "t1", "default_video_id": ""}],
        "store_listing_copies": [
            {"id": "c1", "template_id": "t1", "listing_mode": "site", "default_video_id": ""},
            {"id": "c2", "template_id": "t1", "listing_mode": "site", "default_video_id": "sg-keep"},
        ],
    }
    storage._record_default_video_id_from_result(
        data,
        {"listing_copy_id": "c1"},
        {"stages": {"video_upload": {"status": "attached", "video_id": "sg-new"}}},
        now="now",
    )
    assert data["store_templates"][0]["default_video_id"] == "sg-new"
    assert data["store_listing_copies"][0]["default_video_id"] == "sg-new"
    assert data["store_listing_copies"][1]["default_video_id"] == "sg-keep"

    # 跨境：不记录
    data_cnsc = {
        "store_templates": [{"id": "t1", "default_video_id": ""}],
        "store_listing_copies": [{"id": "c1", "template_id": "t1", "listing_mode": "cnsc_global", "default_video_id": ""}],
    }
    storage._record_default_video_id_from_result(
        data_cnsc,
        {"listing_copy_id": "c1"},
        {"stages": {"video_upload": {"status": "attached", "video_id": "sg-new"}}},
        now="now",
    )
    assert data_cnsc["store_templates"][0]["default_video_id"] == ""

    # 模板已有 ID：不覆盖；阶段没回传 video_id：不动
    data_existing = {
        "store_templates": [{"id": "t1", "default_video_id": "sg-old"}],
        "store_listing_copies": [{"id": "c1", "template_id": "t1", "listing_mode": "site", "default_video_id": ""}],
    }
    storage._record_default_video_id_from_result(
        data_existing,
        {"listing_copy_id": "c1"},
        {"stages": {"video_upload": {"status": "attached", "video_id": "sg-new"}}},
        now="now",
    )
    assert data_existing["store_templates"][0]["default_video_id"] == "sg-old"
    storage._record_default_video_id_from_result(
        data_existing,
        {"listing_copy_id": "c1"},
        {"stages": {"video_upload": {"status": "attached"}}},
        now="now",
    )
    assert data_existing["store_templates"][0]["default_video_id"] == "sg-old"


def test_finish_automation_job_records_video_id_via_normalized_stages(tmp_path: Path) -> None:
    template, copy = _make_site_copy_with_template(tmp_path)
    storage.create_automation_jobs_from_listing_copies([copy["id"]])
    claim = storage.claim_next_automation_job("worker-test", lease_seconds=120, max_attempts=3)
    assert claim["job"] and claim["job"]["listing_copy_id"] == copy["id"]
    job = storage.finish_automation_job(
        claim["job"]["id"],
        worker_id="worker-test",
        lease_token=claim["lease_token"],
        status="saved",
        result={
            "ok": True,
            "status": "saved",
            "exit_code": 0,
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
            "stages": {"video_upload": {"status": "attached", "file_name": "x.mp4", "video_id": "sg-via-finish"}},
        },
    )
    # attached 状态与 video_id 都要在归一化后的结果里存活
    assert job["result"]["stages"]["video_upload"]["video_id"] == "sg-via-finish"
    assert storage.get_item("store_templates", template["id"])["default_video_id"] == "sg-via-finish"
    assert storage.get_item("store_listing_copies", copy["id"])["default_video_id"] == "sg-via-finish"
