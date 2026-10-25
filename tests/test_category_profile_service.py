from __future__ import annotations

import pytest

from services.api.category_profile_service import (
    CategoryProfileConflict,
    classify_profile_diff,
    complete_collection_run,
    create_collection_run,
    create_candidate_from_capture,
)


def _field(field_id: str, *, required: bool = False, field_type: str = "text") -> dict:
    return {
        "field_id": field_id,
        "label": field_id,
        "type": field_type,
        "required": required,
        "options": [],
        "automation": {"page_field_id": field_id, "strategy": "label_or_field_id"},
    }


def _capture(*fields: dict, category_id: str = "100244") -> dict:
    return {
        "platform": "shopee",
        "country": "PH",
        "marketplace_category_id": category_id,
        "observed_category_id": category_id,
        "store_id": "store-1",
        "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
        "display_name": "Shopee PH Mens T-Shirts",
        "page_url": "https://www.mabangerp.com/category/100244?token=secret#state",
        "fields": list(fields),
        "variation": {"required": True, "axes": ["color", "size"]},
    }


def _store() -> dict:
    return {
        "category_profiles": [],
        "category_profile_versions": [],
        "category_collection_runs": [],
    }


def test_valid_capture_creates_active_version_without_review_fields() -> None:
    data = _store()

    result = create_candidate_from_capture(
        data,
        run_id="run-1",
        raw_capture=_capture(_field("brand", required=True)),
        now="2026-07-12T00:00:00Z",
    )

    assert result["profile"]["id"] == "shopee-ph-100244"
    assert result["version"]["version"] == 1
    assert result["version"]["status"] == "active"
    assert result["profile"]["active_version_id"] == result["version"]["id"]
    assert "review" not in result["version"]
    assert result["version"]["source"]["page_url"] == "https://www.mabangerp.com/category/100244"


def test_profile_diff_classifies_required_and_non_required_changes() -> None:
    previous = {"fields": [_field("brand", required=True)]}
    candidate = {
        "fields": [
            _field("brand", required=True),
            _field("pattern", required=False),
            _field("material", required=True),
        ]
    }

    diff = classify_profile_diff(previous, candidate)

    assert {item["field_id"]: item["severity"] for item in diff["items"]} == {
        "material": "requires_review",
        "pattern": "non_breaking",
    }


def test_new_active_version_supersedes_previous_but_keeps_history() -> None:
    data = _store()
    first = create_candidate_from_capture(
        data,
        run_id="run-1",
        raw_capture=_capture(_field("brand", required=True)),
        now="2026-07-12T00:00:00Z",
    )["version"]

    second = create_candidate_from_capture(
        data,
        run_id="run-2",
        raw_capture=_capture(_field("brand", required=True), _field("pattern")),
        now="2026-07-12T00:02:00Z",
    )["version"]

    assert first["status"] == "superseded"
    assert second["status"] == "active"
    assert len(data["category_profile_versions"]) == 2
    assert data["category_profiles"][0]["active_version_id"] == second["id"]


def test_blocking_capture_is_rejected_before_a_version_is_stored() -> None:
    data = _store()

    with pytest.raises(CategoryProfileConflict, match="blocking"):
        create_candidate_from_capture(
            data,
            run_id="run-1",
            raw_capture=_capture(
                {
                    "field_id": "material",
                    "label": "Material",
                    "type": "text",
                    "required": True,
                    "identity_confidence": "low",
                    "automation": {},
                }
            ),
            now="2026-07-12T00:00:00Z",
        )

    assert data["category_profile_versions"] == []


def test_failed_capture_does_not_create_version() -> None:
    data = _store()

    with pytest.raises(CategoryProfileConflict, match="capture"):
        create_candidate_from_capture(
            data,
            run_id="run-failed",
            raw_capture={"error": "category was not selected"},
            now="2026-07-12T00:00:00Z",
        )

    assert data["category_profile_versions"] == []


def test_complete_collection_run_activates_version() -> None:
    data = _store()
    run = create_collection_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1", "category_id": "100244"},
        now="2026-07-12T00:00:00Z",
    )

    result = complete_collection_run(
        data,
        run["id"],
        _capture(_field("brand", required=True)),
        now="2026-07-12T00:01:00Z",
    )

    assert result["run"]["status"] == "completed"
    assert result["version"]["status"] == "active"
    assert result["run"]["active_version_id"] == result["version"]["id"]


def test_collection_run_rejects_capture_for_a_different_page_category_or_store() -> None:
    data = _store()
    run = create_collection_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1", "category_id": "100244"},
        now="2026-07-12T00:00:00Z",
    )
    wrong_category = _capture(_field("brand"), category_id="100245")

    with pytest.raises(CategoryProfileConflict, match="does not match"):
        complete_collection_run(data, run["id"], wrong_category, now="2026-07-12T00:01:00Z")

    assert data["category_profiles"] == []
    assert data["category_profile_versions"] == []

    second = create_collection_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1", "category_id": "100244"},
        now="2026-07-12T00:02:00Z",
    )
    wrong_store = _capture(_field("brand"))
    wrong_store["store_id"] = "store-2"
    with pytest.raises(CategoryProfileConflict, match="does not match"):
        complete_collection_run(data, second["id"], wrong_store, now="2026-07-12T00:03:00Z")
