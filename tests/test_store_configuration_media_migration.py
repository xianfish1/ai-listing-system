from __future__ import annotations

from copy import deepcopy

from scripts.migrate_store_configuration_media import migrate_store


NOW = "2026-07-19T12:00:00+00:00"


def legacy_store() -> dict:
    return {
        "store_templates": [{"id": "template-1", "size_chart_image_path": "chart.png"}],
        "base_products": [{"id": "base-1"}],
        "store_listing_copies": [{
            "id": "copy-1", "size_chart_image_path": "chart.png",
            "promotion_activity_id": "promotion-1", "promotion_activity_expired": True,
        }],
        "automation_jobs": [{"id": "job-1", "status": "saved", "result": {"publish_clicked": False}}],
    }


def legacy_store_with_unknown_fields() -> dict:
    data = legacy_store()
    data["future_field"] = {"keep": True}
    data["store_templates"][0]["future_field"] = "keep"
    return data


def test_migration_preserves_unknown_fields_and_does_not_mutate_on_dry_run() -> None:
    before = legacy_store_with_unknown_fields()
    original = deepcopy(before)

    result = migrate_store(before, now=NOW, dry_run=True)

    assert before == original
    assert result["preview"]["templates_updated"] == 1
    assert result["data"]["future_field"] == {"keep": True}
    assert result["data"]["store_templates"][0]["future_field"] == "keep"


def test_migration_is_idempotent() -> None:
    once = migrate_store(legacy_store(), now=NOW, dry_run=False)["data"]
    twice = migrate_store(deepcopy(once), now=NOW, dry_run=False)["data"]

    assert twice == once


def test_migration_adds_media_defaults_without_changing_saved_history() -> None:
    result = migrate_store(legacy_store(), now=NOW, dry_run=False)["data"]

    template = result["store_templates"][0]
    listing_copy = result["store_listing_copies"][0]
    assert template["video_enabled"] is True
    assert template["main_image_watermark"] == {}
    assert template["product_image_watermark"] == {}
    assert template["size_chart_source"] == "upload"
    assert listing_copy["video"]["validation_status"] == "missing"
    assert listing_copy["promotion_resolution_status"] == "unresolved"
    assert result["automation_jobs"][0]["status"] == "saved"
