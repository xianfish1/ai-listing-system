from __future__ import annotations

from copy import deepcopy

from scripts.migrate_store_template_workflow import migrate_store
from services.api.db_store import COLLECTIONS


NEW_COLLECTIONS = {
    "ai_model_connections",
    "description_generation_profiles",
    "store_template_versions",
    "store_promotion_activities",
    "store_watermark_templates",
    "store_size_chart_templates",
    "store_promotion_catalog_runs",
    "mabang_page_import_runs",
    "listing_copy_revisions",
}


def _legacy_store() -> dict:
    return {
        "category_profiles": [{"id": "cp-1", "published_version_id": "cpv-1"}],
        "category_profile_versions": [
            {"id": "cpv-1", "profile_id": "cp-1", "status": "published"}
        ],
        "store_templates": [{"id": "t-1", "mabang_category_id": "100244"}],
        "store_listing_copies": [
            {"id": "c-1", "template_id": "t-1", "description": ""}
        ],
        "automation_jobs": [
            {"id": "j-1", "listing_copy_id": "c-1", "status": "ready"}
        ],
    }


def test_new_workflow_collections_are_persisted() -> None:
    assert NEW_COLLECTIONS.issubset(set(COLLECTIONS))


def test_migration_marks_unverified_templates_and_legacy_jobs_without_inventing_defaults() -> None:
    data = _legacy_store()

    result = migrate_store(data, now="2026-07-15T00:00:00Z")

    template = data["store_templates"][0]
    listing_copy = data["store_listing_copies"][0]
    job = data["automation_jobs"][0]
    assert result["templates_needing_import"] == 1
    assert result["jobs_needing_regeneration"] == 1
    assert template["readiness_status"] == "needs_mabang_import"
    assert "stock" not in template
    assert "weight_kg" not in template
    assert listing_copy["description_status"] == "pending"
    assert job["status"] == "needs_regeneration"
    assert data["category_profiles"][0]["active_version_id"] == "cpv-1"
    assert data["category_profile_versions"][0]["status"] == "active"


def test_migration_is_idempotent() -> None:
    data = _legacy_store()
    migrate_store(data, now="2026-07-15T00:00:00Z")
    first = deepcopy(data)

    second = migrate_store(data, now="2026-07-15T00:01:00Z")

    assert data == first
    assert second == {
        "templates_needing_import": 0,
        "jobs_needing_regeneration": 0,
    }
