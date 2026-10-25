from __future__ import annotations

from pathlib import Path

import pytest

from scripts.migrate_category_profiles import migrate_store
from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    data = storage.load_store()
    migrate_store(data, storage.CATEGORY_PROFILES, now="2026-07-12T00:00:00Z")
    storage.save_store(data)


def _base_and_template(tmp_path: Path, *, attributes: dict | None = None):
    folder = tmp_path / "TS1001 Product"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    payload = {
        "store_name": "POD PH",
        "mabang_store_id": "store-1",
        "template_name": "Mens Tee",
        "category_profile_id": "pod-ph-mens-tee",
    }
    if attributes is not None:
        payload["attribute_values"] = attributes
    template = storage.create_store_template(payload)
    return base, template


def test_template_references_published_profile_version(tmp_path: Path):
    _, template = _base_and_template(tmp_path)
    profile = storage.get_category_profile("pod-ph-mens-tee")

    assert template["category_profile_version_id"] == profile["published_version_id"]
    assert template["attribute_values"]["material"] == "Cotton"
    assert profile["published_version"]["fields"]


def test_listing_copy_freezes_profile_snapshot_and_hash(tmp_path: Path):
    base, template = _base_and_template(tmp_path)

    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    listing_copy = result["created"][0]

    assert listing_copy["category_profile_snapshot"]["version_id"] == template["category_profile_version_id"]
    assert listing_copy["profile_snapshot_hash"].startswith("sha256:")
    assert listing_copy["attribute_values"]["brand"] == "NoBrand"


def test_template_update_does_not_change_existing_listing_snapshot(tmp_path: Path):
    base, template = _base_and_template(tmp_path)
    listing_copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    original_hash = listing_copy["profile_snapshot_hash"]

    storage.update_store_template(
        template["id"],
        {"attribute_values": {**template["attribute_values"], "material": "Polyester"}},
    )
    stored_copy = storage.get_item("store_listing_copies", listing_copy["id"])

    assert stored_copy["profile_snapshot_hash"] == original_hash
    assert stored_copy["attribute_values"]["material"] == "Cotton"


def test_category_required_field_is_reported_by_automation_readiness(tmp_path: Path):
    base, template = _base_and_template(
        tmp_path,
        attributes={"brand": "NoBrand", "pattern": "Print", "material": "", "sleeve_length": "Short Sleeve", "plus_size": "Plus"},
    )
    listing_copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    assert "category_attributes.material" in job["missing_fields"]
