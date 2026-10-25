from __future__ import annotations

import pytest

from scripts.browser_use_runner import build_category_collection_audit_script
from scripts.collect_mabang_category_profile import normalize_capture


def test_normalize_capture_keeps_unique_fields_and_rejects_incomplete_data():
    capture = normalize_capture(
        {
            "platform": "shopee",
            "country": "PH",
            "marketplace_category_id": "900001",
            "observed_category_id": "900001",
            "store_id": "store-1",
            "fields": [
                {"field_id": "brand", "label": "Brand"},
                {"field_id": "brand", "label": "Duplicate"},
                {"field_id": "material", "label": "Material"},
            ],
        }
    )

    assert [field["field_id"] for field in capture["fields"]] == ["brand", "material"]

    with pytest.raises(ValueError, match="incomplete"):
        normalize_capture({"platform": "shopee", "country": "PH", "fields": []})


def test_category_collection_browser_script_is_read_only():
    script = build_category_collection_audit_script(
        {"platform": "shopee", "country": "PH", "category_id": "900001"}
    )

    assert "CATEGORY_COLLECTION_RESULT" in script
    assert "document.querySelectorAll" in script
    assert "__AI_LISTING_CATEGORY_COLLECTION_GUARD__" in script
    assert "FORBIDDEN" in script
    assert "preventDefault" in script
    assert "productDetailShopee" in script
    assert "main.goPublish" in script
    assert "observed_category_id" in script
