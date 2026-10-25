from __future__ import annotations

import pytest

from scripts.browser_use_runner import build_category_catalog_collection_audit_script
from scripts.collect_mabang_category_catalog import normalize_capture


def test_normalize_catalog_capture_accepts_nested_tree_and_rejects_empty_data():
    capture = normalize_capture(
        {
            "platform": "shopee",
            "country": "PH",
            "store_id": "store-1",
            "discovery_source": "mabang_shopee_getCategory_api",
            "category_count": 2,
            "categories": [
                {
                    "categoryId": 100,
                    "categoryName": "Men Clothes",
                    "children": [{"categoryId": 111, "categoryName": "T-Shirts"}],
                }
            ],
        }
    )

    assert capture["categories"][0]["categoryId"] == 100

    with pytest.raises(ValueError, match="no categories"):
        normalize_capture({"platform": "shopee", "country": "PH", "store_id": "store-1", "categories": []})


def test_category_catalog_browser_script_is_read_only_and_collects_vue_tree_state():
    script = build_category_catalog_collection_audit_script(
        {"platform": "shopee", "country": "PH", "store_id": "store-1"}
    )

    assert "CATEGORY_CATALOG_RESULT" in script
    assert "__AI_LISTING_CATEGORY_CATALOG_GUARD__" in script
    assert "FORBIDDEN" in script
    assert "preventDefault" in script
    assert "__vue__" in script
    assert "performance.getEntriesByType" in script
    assert "/kandeng/api/v2/shopee/getCategory" in script
    assert "parent_category_id" in script
    assert 'platformId: "17"' in script
    assert 'site: ""' in script
    assert "20000" in script
