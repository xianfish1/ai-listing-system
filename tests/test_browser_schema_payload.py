from __future__ import annotations

from pathlib import Path

import pytest

from scripts.migrate_category_profiles import migrate_store
from services.api import settings, storage
from services.api.size_chart_uploads import save_size_chart


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    data = storage.load_store()
    migrate_store(data, storage.CATEGORY_PROFILES, now="2026-07-12T00:00:00Z")
    storage.save_store(data)


def _listing_copy(tmp_path: Path):
    folder = tmp_path / "TS1001 Product"
    folder.mkdir()
    (folder / "main.jpg").write_bytes(b"image")
    (folder / "White.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "mabang_store_id": "store-1",
            "template_name": "Mens Tee",
            "category_profile_id": "pod-ph-mens-tee",
        }
    )
    return storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]


def test_browser_inputs_use_snapshot_values_without_fixed_attribute_defaults(tmp_path: Path):
    listing_copy = _listing_copy(tmp_path)
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert payload["browser_inputs"]["category_attributes"] == listing_copy["attribute_values"]
    assert payload["browser_inputs"]["profile_snapshot_hash"] == listing_copy["profile_snapshot_hash"]
    assert payload["browser_inputs"]["category_profile_snapshot"]["version_id"] == listing_copy["category_profile_version_id"]


def test_snapshot_hash_change_blocks_automation_payload(tmp_path: Path):
    listing_copy = _listing_copy(tmp_path)
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    listing_copy["profile_snapshot_hash"] = "sha256:changed"
    storage.save_store({**storage.load_store(), "store_listing_copies": [listing_copy]})

    payload = storage.build_automation_payload(job["id"])

    assert payload["readiness"]["ready"] is False
    assert "profile_snapshot_changed" in payload["readiness"]["missing_fields"]


def test_execution_snapshot_hash_mismatch_blocks_payload(tmp_path: Path):
    listing_copy = _listing_copy(tmp_path)
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    data = storage.load_store()
    data["automation_job_snapshots"][job["id"]]["listing_copy"]["description"] = "tampered"
    storage.save_store(data)
    payload = storage.build_automation_payload(job["id"])
    assert payload["readiness"]["ready"] is False
    assert "execution_snapshot_changed" in payload["readiness"]["missing_fields"]


def test_uploaded_listing_copy_size_chart_resolves_to_browser_file_path(tmp_path: Path):
    listing_copy = _listing_copy(tmp_path)
    uploaded = save_size_chart(
        {
            "file_name": "new-size-chart.png",
            "content_type": "image/png",
            "content_base64": "iVBORw0KGgpjb250ZW50",
        },
        storage_dir=settings.STORAGE_DIR,
    )
    updated = storage.update_store_listing_copy(
        listing_copy["id"],
        {
            "size_chart_asset_id": uploaded["id"],
            "size_chart_preview_url": uploaded["preview_url"],
            "size_chart_image": uploaded["file_name"],
            "size_chart_image_path": "",
        },
    )
    assert updated is not None

    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])
    resolved = Path(payload["browser_inputs"]["size_chart_image_path"])

    assert resolved.is_file()
    assert resolved.parent == settings.STORAGE_DIR / "uploads" / "size-charts"


def test_pending_description_blocks_direct_automation_job_creation(tmp_path: Path):
    listing_copy = _listing_copy(tmp_path)
    storage.update_store_listing_copy(
        listing_copy["id"],
        {"description": "Existing draft text", "description_status": "pending"},
    )

    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    assert job["status"] == "needs_manual"
    assert "description_pending" in job["missing_fields"]


def test_media_configuration_is_frozen_in_browser_inputs(tmp_path: Path):
    listing_copy = _listing_copy(tmp_path)
    data = storage.load_store()
    stored = next(item for item in data["store_listing_copies"] if item["id"] == listing_copy["id"])
    stored.update({
        "video": {"path": "video.mp4", "validation_status": "valid"},
        "main_image_watermark": {"template_id": "wm-main", "name": "Main"},
        "product_image_watermark": {"template_id": "wm-product", "name": "Product"},
    })
    storage.save_store(data)
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    data = storage.load_store()
    next(item for item in data["store_listing_copies"] if item["id"] == listing_copy["id"])["video"]["path"] = "changed.mp4"
    storage.save_store(data)
    payload = storage.build_automation_payload(job["id"])

    assert payload["browser_inputs"]["video"]["path"] == "video.mp4"
    assert payload["browser_inputs"]["main_image_watermark"]["template_id"] == "wm-main"
    assert payload["browser_inputs"]["product_image_watermark"]["template_id"] == "wm-product"
