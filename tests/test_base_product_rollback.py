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


def _transaction_defaults() -> dict:
    return {
        "stock": 100,
        "weight_kg": 0.3,
        "package_length_cm": 30,
        "package_width_cm": 20,
        "package_height_cm": 5,
    }


def _make_base_with_two_copies(tmp_path: Path) -> tuple[dict, list[dict]]:
    folder = tmp_path / "AL0501 Rollback Tee"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg", "Black.jpg", "size-chart.png"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    templates = []
    for suffix in ("A", "B"):
        template = storage.create_store_template(
            {
                "store_name": f"POD Store {suffix}",
                "platform": "shopee",
                "country": "PH",
                "currency": "PHP",
                "template_name": f"Mens Tee {suffix}",
                "product_line": "POD Mens T-Shirt",
                "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
                "mabang_store_id": f"store-{suffix}",
                "mabang_category_id": "cat-456",
                "mabang_discount_id": "discount-789",
                "normal_price": 89,
                "sale_price": 69,
                "sizes": ["S"],
                "sku_template": "{main_sku}_{color}||{size}",
                "description": "Template default description.",
                **_transaction_defaults(),
            }
        )
        templates.append(template)
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(
        base["id"], [template["id"] for template in templates]
    )
    for copy in result["created"]:
        storage.update_store_listing_copy(
            copy["id"],
            {
                "description": "Rollback test description",
                "description_status": "ready",
                "size_chart_image": "size-chart.png",
                "size_chart_image_path": str(folder / "size-chart.png"),
            },
        )
    refreshed = [storage.get_item("store_listing_copies", copy["id"]) for copy in result["created"]]
    return base, refreshed


def _drive_job_to_saved(job_id: str) -> dict:
    claim = storage.claim_next_automation_job("worker-test", lease_seconds=120, max_attempts=3)
    assert claim["job"] and claim["job"]["id"] == job_id, "expected claim to return the target job"
    return storage.finish_automation_job(
        job_id,
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
        },
    )


def test_saving_all_copies_marks_base_product_completed(tmp_path: Path) -> None:
    base, copies = _make_base_with_two_copies(tmp_path)
    first_job = storage.create_automation_job_from_listing_copy(copies[0]["id"])

    _drive_job_to_saved(first_job["id"])

    base_after_first = storage.get_item("base_products", base["id"])
    copy_after_first = storage.get_item("store_listing_copies", copies[0]["id"])
    other_copy_after_first = storage.get_item("store_listing_copies", copies[1]["id"])
    assert copy_after_first["status"] == "completed"
    assert other_copy_after_first["status"] != "completed"
    assert base_after_first["status"] != "completed"

    second_job = storage.create_automation_job_from_listing_copy(copies[1]["id"])
    _drive_job_to_saved(second_job["id"])

    base_after_all = storage.get_item("base_products", base["id"])
    other_copy_final = storage.get_item("store_listing_copies", copies[1]["id"])
    assert other_copy_final["status"] == "completed"
    assert base_after_all["status"] == "completed"
    assert base_after_all["completed_at"]


def test_rollback_only_works_on_completed_base_product(tmp_path: Path) -> None:
    base, _ = _make_base_with_two_copies(tmp_path)

    with pytest.raises(ValueError):
        storage.rollback_base_product(base["id"])


def test_rollback_reopens_copies_and_preserves_job_history(tmp_path: Path) -> None:
    base, copies = _make_base_with_two_copies(tmp_path)
    jobs = [storage.create_automation_job_from_listing_copy(copy["id"]) for copy in copies]
    for job in jobs:
        _drive_job_to_saved(job["id"])

    result = storage.rollback_base_product(base["id"])

    assert result is not None
    assert result["status"] == "base_ready"
    assert set(result["reopened_listing_copy_ids"]) == {copy["id"] for copy in copies}

    base_after = storage.get_item("base_products", base["id"])
    assert base_after["status"] == "base_ready"
    assert base_after["completed_at"] == ""
    assert base_after["rolled_back_at"]

    for copy in copies:
        copy_after = storage.get_item("store_listing_copies", copy["id"])
        assert copy_after["status"] == "copy_ready"
        assert copy_after["completed_at"] == ""
        assert copy_after["rolled_back_at"]

    surviving_jobs = storage.list_automation_jobs()
    assert {job["id"] for job in surviving_jobs} == {job["id"] for job in jobs}
    assert all(job["status"] in {"saved", "saved_with_warnings"} for job in surviving_jobs)


def test_list_base_products_with_summary_exposes_automation_glyph(tmp_path: Path) -> None:
    base, copies = _make_base_with_two_copies(tmp_path)

    idle_summary = next(item for item in storage.list_base_products_with_summary() if item["id"] == base["id"])
    assert idle_summary["automation_glyph"] == "idle"
    assert idle_summary["automation_status_summary"]["total_copies"] == 2
    assert idle_summary["automation_status_summary"]["done"] == 0

    first_job = storage.create_automation_job_from_listing_copy(copies[0]["id"])
    _drive_job_to_saved(first_job["id"])
    watch_summary = next(item for item in storage.list_base_products_with_summary() if item["id"] == base["id"])
    assert watch_summary["automation_status_summary"]["done"] == 1
    # One copy still has no job — glyph must not be "done" yet
    assert watch_summary["automation_glyph"] in {"idle", "watch"}

    second_job = storage.create_automation_job_from_listing_copy(copies[1]["id"])
    _drive_job_to_saved(second_job["id"])
    done_summary = next(item for item in storage.list_base_products_with_summary() if item["id"] == base["id"])
    assert done_summary["automation_status_summary"]["done"] == 2
    assert done_summary["automation_glyph"] == "done"
