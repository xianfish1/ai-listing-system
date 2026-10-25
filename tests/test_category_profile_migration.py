from __future__ import annotations

from copy import deepcopy

from scripts.migrate_category_profiles import migrate_store
from services.api.db_store import COLLECTIONS


STATIC_PROFILES = {
    "pod-ph-mens-tee": {
        "profile_id": "pod-ph-mens-tee",
        "display_name": "POD PH Mens T-Shirt",
        "product_line": "POD Mens T-Shirt",
        "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
        "mabang_category_id": "100244",
        "country": "PH",
        "required_attributes": {"brand": "NoBrand", "material": "Cotton"},
        "variation": {"type": "variant", "axes": ["color", "size"]},
    }
}


def _store() -> dict:
    return {
        "store_templates": [
            {
                "id": "template-1",
                "category_profile_id": "pod-ph-mens-tee",
                "category_attributes": {"brand": "NoBrand", "material": "Cotton"},
            }
        ],
        "store_listing_copies": [
            {
                "id": "copy-1",
                "template_id": "template-1",
                "category_profile_id": "pod-ph-mens-tee",
                "category_attributes": {"brand": "NoBrand", "material": "Cotton"},
                "status": "copy_ready",
            },
            {
                "id": "copy-orphan",
                "template_id": "missing",
                "category_attributes": {"brand": "NoBrand"},
                "status": "copy_ready",
            },
        ],
    }


def test_postgres_compatibility_store_includes_product_line_and_category_collections():
    assert {
        "base_products",
        "store_templates",
        "store_listing_copies",
        "listing_skus",
        "listing_prices",
        "automation_jobs",
        "category_profiles",
        "category_profile_versions",
        "category_collection_runs",
    }.issubset(COLLECTIONS)


def test_migration_creates_published_version_and_updates_template_and_copy():
    data = _store()

    report = migrate_store(data, STATIC_PROFILES, now="2026-07-12T00:00:00Z")

    assert report["profiles_created"] == 1
    assert report["versions_created"] == 1
    profile = data["category_profiles"][0]
    version = data["category_profile_versions"][0]
    assert profile["id"] == "pod-ph-mens-tee"
    assert profile["published_version_id"] == version["id"]
    assert version["status"] == "published"
    assert version["version"] == 1
    assert version["source"]["type"] == "legacy_static_profile"
    assert {field["field_id"] for field in version["fields"]} == {"brand", "material"}
    assert data["store_templates"][0]["category_profile_version_id"] == version["id"]
    assert data["store_templates"][0]["attribute_values"] == {"brand": "NoBrand", "material": "Cotton"}
    assert data["store_listing_copies"][0]["profile_snapshot_hash"].startswith("sha256:")


def test_migration_marks_unmatched_listing_copy_for_manual_review():
    data = _store()

    migrate_store(data, STATIC_PROFILES, now="2026-07-12T00:00:00Z")

    orphan = data["store_listing_copies"][1]
    assert orphan["category_profile_snapshot"]["version_id"] == "legacy-v1"
    assert orphan["status"] == "needs_manual"


def test_migration_is_idempotent():
    data = _store()

    migrate_store(data, STATIC_PROFILES, now="2026-07-12T00:00:00Z")
    after_first = deepcopy(data)
    second = migrate_store(data, STATIC_PROFILES, now="2026-07-12T00:00:00Z")

    assert second["profiles_created"] == 0
    assert second["versions_created"] == 0
    assert data == after_first
