from __future__ import annotations

from copy import deepcopy

import pytest

from services.api.store_watermarks import (
    list_watermarks,
    replace_watermark_catalog,
    resolve_watermark_reference,
)


NOW = "2026-07-19T00:00:00Z"


def _existing_catalog() -> dict:
    return {
        "store_watermark_templates": [
            {
                "id": "local-old",
                "store_id": "store-a",
                "template_id": "old",
                "name": "旧模板",
                "preview_url": "https://example.invalid/old.png",
                "status": "available",
                "collected_at": NOW,
            }
        ]
    }


def test_watermark_catalog_is_store_scoped() -> None:
    data = {"store_watermark_templates": []}
    replace_watermark_catalog(
        data,
        store_id="store-a",
        raw_items=[{"template_id": "1", "name": "A"}],
        now=NOW,
        catalog_loaded=True,
    )
    replace_watermark_catalog(
        data,
        store_id="store-b",
        raw_items=[{"template_id": "1", "name": "B"}],
        now=NOW,
        catalog_loaded=True,
    )

    assert [item["name"] for item in list_watermarks(data, "store-a")] == ["A"]
    assert [item["name"] for item in list_watermarks(data, "store-b")] == ["B"]


def test_empty_failed_capture_does_not_replace_last_valid_catalog() -> None:
    data = _existing_catalog()
    before = deepcopy(data)

    with pytest.raises(ValueError, match="水印模板目录未加载"):
        replace_watermark_catalog(
            data,
            store_id="store-a",
            raw_items=[],
            now=NOW,
            catalog_loaded=False,
        )

    assert data == before


def test_loaded_empty_catalog_clears_only_the_target_store() -> None:
    data = _existing_catalog()
    data["store_watermark_templates"].append(
        {"id": "other", "store_id": "store-b", "template_id": "1", "name": "B", "status": "available"}
    )

    items = replace_watermark_catalog(
        data,
        store_id="store-a",
        raw_items=[],
        now=NOW,
        catalog_loaded=True,
    )

    assert items == []
    assert [item["store_id"] for item in data["store_watermark_templates"]] == ["store-b"]


def test_reference_resolution_is_id_first_and_name_verified() -> None:
    catalog = [
        {"template_id": "wm-1", "name": "主图水印", "status": "available"},
        {"template_id": "wm-2", "name": "产品图水印", "status": "available"},
    ]

    assert resolve_watermark_reference({"template_id": "wm-2", "name": "产品图水印"}, catalog) == {
        "status": "resolved",
        "template_id": "wm-2",
        "name": "产品图水印",
        "matched_by": "template_id",
    }
    assert resolve_watermark_reference({"template_id": "wm-2", "name": "错误名称"}, catalog)["status"] == "unresolved"
