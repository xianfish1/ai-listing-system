"""Base-product expected category (cross-store merged) + quick category templates.

Covers:
- merged cross-store category query (leaf-only, de-duplicated by path, q filter)
- product_category storage on create and update (set / clear / old-data-safe)
- category quick template CRUD (category + attribute values, name-unique upsert, delete)
- copy-to-store records expected_category_path when base category differs and
  never overrides the store template category.
"""
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


def _seed_catalog() -> None:
    """Two stores share a same-path leaf; one extra leaf and a non-leaf exist."""
    storage.save_store(
        {
            "marketplace_categories": [
                {
                    "id": "c1", "category_id": "100244", "parent_id": "0",
                    "name": "T-Shirts", "name_zh": "T恤",
                    "path": "Men Clothes > Tops > T-Shirts",
                    "path_zh": "男装 > 上装 > T恤", "path_en": "Men Clothes > Tops > T-Shirts",
                    "level": 3, "is_leaf": True, "active": True,
                    "store_id": "storeA", "platform": "shopee", "country": "PH",
                },
                {
                    "id": "c2", "category_id": "100244", "parent_id": "0",
                    "name": "T-Shirts", "name_zh": "T恤",
                    "path": "Men Clothes > Tops > T-Shirts",
                    "path_zh": "男装 > 上装 > T恤", "path_en": "Men Clothes > Tops > T-Shirts",
                    "level": 3, "is_leaf": True, "active": True,
                    "store_id": "storeB", "platform": "shopee", "country": "MY",
                },
                {
                    "id": "c3", "category_id": "555001", "parent_id": "0",
                    "name": "Hoodies", "name_zh": "卫衣",
                    "path": "Men Clothes > Tops > Hoodies",
                    "path_zh": "男装 > 上装 > 卫衣", "path_en": "Men Clothes > Tops > Hoodies",
                    "level": 3, "is_leaf": True, "active": True,
                    "store_id": "storeA", "platform": "shopee", "country": "PH",
                },
                {
                    "id": "c4", "category_id": "999", "parent_id": "0",
                    "name": "Men", "path": "Men Clothes", "path_zh": "男装",
                    "level": 1, "is_leaf": False, "active": True,
                    "store_id": "storeA", "platform": "shopee", "country": "PH",
                },
            ]
        }
    )


def _make_base(tmp_path: Path, name: str = "AL0001 测试商品") -> dict:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "主图.jpg").write_bytes(b"image")
    (folder / "白色.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder)})
    assert base is not None
    return base


def _make_site_template(tmp_path: Path, template_name: str = "PH 模板") -> dict:
    # No mabang_category_id → category binding validator is a no-op, so the
    # template carries an arbitrary marketplace path that we can compare to.
    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "template_name": template_name,
            "mabang_store_id": "2022625115",
            "platform": "shopee",
            "country": "PH",
            "marketplace_category_path": "Women > Dresses",
        }
    )
    return template


# --- merged cross-store category query ---


def test_merged_categories_dedup_by_path_and_leaf_only(tmp_path: Path) -> None:
    _seed_catalog()
    merged = storage.list_merged_marketplace_categories()
    # Two leaf paths across stores → 2; the non-leaf "Men Clothes" is excluded.
    assert len(merged) == 2
    paths = {item["path"] for item in merged}
    assert "Men Clothes > Tops > T-Shirts" in paths
    assert "Men Clothes > Tops > Hoodies" in paths


def test_merged_categories_query_filter(tmp_path: Path) -> None:
    _seed_catalog()
    filtered = storage.list_merged_marketplace_categories(query="卫衣")
    assert len(filtered) == 1
    assert filtered[0]["category_id"] == "555001"


def test_merged_categories_empty_when_no_data(tmp_path: Path) -> None:
    assert storage.list_merged_marketplace_categories() == []


def test_merged_categories_skips_inactive(tmp_path: Path) -> None:
    storage.save_store(
        {
            "marketplace_categories": [
                {
                    "id": "x", "category_id": "1", "parent_id": "0", "name": "A",
                    "path": "A > B", "is_leaf": True, "active": False,
                    "store_id": "s", "platform": "shopee", "country": "PH",
                }
            ]
        }
    )
    assert storage.list_merged_marketplace_categories() == []


# --- product_category storage ---


def test_create_base_product_with_product_category(tmp_path: Path) -> None:
    base = _make_base(tmp_path, "AL0101 带类目")
    assert base.get("product_category") == {}


def test_update_base_product_sets_and_clears_product_category(tmp_path: Path) -> None:
    base = _make_base(tmp_path, "AL0102 改类目")
    updated = storage.update_base_product(
        base["id"],
        {"product_category": {"category_id": "100244", "path": "男装 > 上装 > T恤", "store_id": "storeA"}},
    )
    assert updated is not None
    assert updated["product_category"]["category_id"] == "100244"
    assert updated["product_category"]["path"] == "男装 > 上装 > T恤"
    assert updated["product_category"]["store_id"] == "storeA"

    cleared = storage.update_base_product(base["id"], {"product_category": ""})
    assert cleared is not None
    assert cleared["product_category"] == {}


def test_update_base_product_ignores_category_in_whitelist_only(tmp_path: Path) -> None:
    # product_category must survive a normal maintenance save that sends many fields.
    base = _make_base(tmp_path, "AL0103 维护保存")
    storage.update_base_product(
        base["id"],
        {"product_category": {"category_id": "100244", "path": "男装 > 上装 > T恤"}},
    )
    updated = storage.update_base_product(base["id"], {"listing_title": "新标题"})
    assert updated is not None
    assert updated["product_category"]["path"] == "男装 > 上装 > T恤"
    assert updated["listing_title"] == "新标题"


def test_update_base_product_legacy_record_without_category(tmp_path: Path) -> None:
    # Records created before the field existed must not crash when patched.
    base = _make_base(tmp_path, "AL0104 旧记录")
    with storage.locked_store() as (data, save):
        row = next(item for item in data["base_products"] if item["id"] == base["id"])
        row.pop("product_category", None)
        save(data)
    updated = storage.update_base_product(base["id"], {"status": "completed"})
    assert updated is not None
    assert updated["status"] == "completed"
    assert updated.get("product_category") in (None, {})


# --- quick category templates (category + attribute values, never prices) ---


def test_quick_template_save_list_and_upsert(tmp_path: Path) -> None:
    created = storage.save_category_quick_template(
        {
            "name": "T恤",
            "category": {"category_id": "100244", "path": "男装 > 上装 > T恤", "store_id": "storeA"},
            "attribute_values": {"100037": "35", "100134": ["1149", "1150"], "100413": "new"},
        }
    )
    assert created["category"]["path"] == "男装 > 上装 > T恤"
    assert created["attribute_values"] == {"100037": "35", "100134": ["1149", "1150"], "100413": "new"}
    assert len(storage.list_category_quick_templates()) == 1

    # Same name upserts (keeps id, replaces category and attribute values).
    upserted = storage.save_category_quick_template(
        {
            "name": "T恤",
            "category": {"category_id": "555001", "path": "男装 > 上装 > 卫衣"},
            "attribute_values": {"100037": "36"},
        }
    )
    assert upserted["id"] == created["id"]
    assert upserted["category"]["category_id"] == "555001"
    assert upserted["attribute_values"] == {"100037": "36"}
    assert len(storage.list_category_quick_templates()) == 1


def test_quick_template_attribute_values_are_sanitized(tmp_path: Path) -> None:
    created = storage.save_category_quick_template(
        {
            "name": "带脏值",
            "category": {"path": "男装 > 上装 > T恤"},
            "attribute_values": {
                "": "no-key",
                "empty": "   ",
                "none": None,
                "number": 12,
                "flag": False,
                "mixed": [" ", "A", 3],
                "nested": {"nope": "x"},
            },
        }
    )
    values = created["attribute_values"]
    assert "" not in values
    assert "empty" not in values
    assert "none" not in values
    assert "nested" not in values
    assert values["number"] == "12"
    assert values["flag"] is False
    assert values["mixed"] == ["A", "3"]


def test_quick_template_legacy_record_lists_empty_attribute_values(tmp_path: Path) -> None:
    storage.save_category_quick_template({"name": "老模板", "category": {"path": "男装 > 上装 > T恤"}})
    with storage.locked_store() as (data, save):
        row = next(item for item in data["category_quick_templates"] if item["name"] == "老模板")
        row.pop("attribute_values", None)
        save(data)
    listed = storage.list_category_quick_templates()
    assert listed[0]["attribute_values"] == {}


def test_quick_template_requires_name_and_category(tmp_path: Path) -> None:
    with pytest.raises(storage.CategoryQuickTemplateError):
        storage.save_category_quick_template({"category": {"path": "男装 > 上装 > T恤"}})
    with pytest.raises(storage.CategoryQuickTemplateError):
        storage.save_category_quick_template({"name": "缺类目"})


def test_quick_template_delete(tmp_path: Path) -> None:
    created = storage.save_category_quick_template({"name": "卫衣", "category": {"path": "男装 > 上装 > 卫衣"}})
    removed = storage.delete_category_quick_template(created["id"])
    assert removed is not None and removed["id"] == created["id"]
    assert storage.list_category_quick_templates() == []
    assert storage.delete_category_quick_template("missing") is None


# --- copy category rule: base product wins when set, template is the default ---


def test_copy_uses_base_category_when_set_even_if_template_differs(tmp_path: Path) -> None:
    base = _make_base(tmp_path, "AL0201 填了类目")
    storage.update_base_product(
        base["id"],
        {"product_category": {"category_id": "100244", "path": "Men Clothes > Tops > T-Shirts"}},
    )
    template = _make_site_template(tmp_path)  # template carries no category id/path
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert result["created_count"] == 1
    copy = result["created"][0]
    # Base product's category is used verbatim; template category is ignored.
    assert copy["category_source"] == "base_product"
    assert copy["mabang_category_id"] == "100244"
    assert copy["marketplace_category_path"] == "Men Clothes > Tops > T-Shirts"
    assert copy.get("expected_category_path") == ""


def test_copy_uses_template_category_when_base_has_none(tmp_path: Path) -> None:
    base = _make_base(tmp_path, "AL0202 没填类目")
    template = _make_site_template(tmp_path)
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert result["created_count"] == 1
    copy = result["created"][0]
    # No product category → store template category is the default.
    assert copy["category_source"] == "store_template"
    assert copy["marketplace_category_path"] == "Women > Dresses"
    assert copy.get("expected_category_path") == ""


def test_copy_uses_base_category_even_when_template_has_own_category(tmp_path: Path) -> None:
    base = _make_base(tmp_path, "AL0203 商品覆盖模板")
    storage.update_base_product(
        base["id"],
        {"product_category": {"category_id": "100244", "path": "Men Clothes > Tops > T-Shirts"}},
    )
    template = _make_site_template(tmp_path)
    # Template itself configured with a different category.
    with storage.locked_store() as (data, save):
        row = next(t for t in data["store_templates"] if t["id"] == template["id"])
        row["mabang_category_id"] = "555001"
        row["marketplace_category_path"] = "Women > Dresses"
        save(data)
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert result["created_count"] == 1
    copy = result["created"][0]
    # Filled product category wins regardless of what the template has.
    assert copy["category_source"] == "base_product"
    assert copy["mabang_category_id"] == "100244"
    assert copy["marketplace_category_path"] == "Men Clothes > Tops > T-Shirts"


# --- cross-store cascading children (three-column picker backend) ---


def _seed_catalog_tree() -> None:
    """Two stores sharing a subtree plus a store-specific branch."""
    storage.save_store(
        {
            "marketplace_categories": [
                # storeA: Men > Tops (has children) ; leaf T-Shirts / Hoodies
                {"id": "m1", "category_id": "100011", "parent_id": "0", "name": "Men Clothes", "name_zh": "男装",
                 "path": "Men Clothes", "path_zh": "男装", "is_leaf": False, "active": True,
                 "store_id": "storeA", "platform": "shopee", "country": "PH"},
                {"id": "m2", "category_id": "100100", "parent_id": "100011", "name": "Tops", "name_zh": "上衣",
                 "path": "Men Clothes > Tops", "path_zh": "男装 > 上衣", "is_leaf": False, "active": True,
                 "store_id": "storeA", "platform": "shopee", "country": "PH"},
                {"id": "m3", "category_id": "100244", "parent_id": "100100", "name": "T-Shirts", "name_zh": "T恤",
                 "path": "Men Clothes > Tops > T-Shirts", "path_zh": "男装 > 上衣 > T恤", "is_leaf": True, "active": True,
                 "store_id": "storeA", "platform": "shopee", "country": "PH"},
                # storeB duplicates Men > Tops > T-Shirts (same ids) and adds Hoodies
                {"id": "m4", "category_id": "100011", "parent_id": "0", "name": "Men Clothes", "name_zh": "男装",
                 "path": "Men Clothes", "path_zh": "男装", "is_leaf": False, "active": True,
                 "store_id": "storeB", "platform": "shopee", "country": "MY"},
                {"id": "m5", "category_id": "100100", "parent_id": "100011", "name": "Tops", "name_zh": "上衣",
                 "path": "Men Clothes > Tops", "path_zh": "男装 > 上衣", "is_leaf": False, "active": True,
                 "store_id": "storeB", "platform": "shopee", "country": "MY"},
                {"id": "m6", "category_id": "100244", "parent_id": "100100", "name": "T-Shirts", "name_zh": "T恤",
                 "path": "Men Clothes > Tops > T-Shirts", "path_zh": "男装 > 上衣 > T恤", "is_leaf": True, "active": True,
                 "store_id": "storeB", "platform": "shopee", "country": "MY"},
                {"id": "m7", "category_id": "100226", "parent_id": "100100", "name": "Hoodies", "name_zh": "卫衣",
                 "path": "Men Clothes > Tops > Hoodies", "path_zh": "男装 > 上衣 > 卫衣", "is_leaf": True, "active": True,
                 "store_id": "storeB", "platform": "shopee", "country": "MY"},
            ]
        }
    )


def test_merged_children_roots_aggregate_across_stores(tmp_path: Path) -> None:
    _seed_catalog_tree()
    roots = storage.list_merged_category_children("")
    # Both stores contribute the same root 100011 → one row.
    assert len(roots) == 1
    assert roots[0]["category_id"] == "100011"
    assert roots[0]["has_children"] is True
    assert roots[0]["is_leaf"] is False


def test_merged_children_dedup_and_leaf_union(tmp_path: Path) -> None:
    _seed_catalog_tree()
    kids = storage.list_merged_category_children("100100")
    # Tops children: T-Shirts (both stores → 1) + Hoodies (storeB only) → 2 rows.
    assert len(kids) == 2
    by_id = {k["category_id"]: k for k in kids}
    assert set(by_id) == {"100244", "100226"}
    assert by_id["100244"]["is_leaf"] is True
    assert by_id["100226"]["is_leaf"] is True


def test_merged_children_search_query(tmp_path: Path) -> None:
    _seed_catalog_tree()
    matches = storage.list_merged_category_children("100100", query="卫衣")
    assert len(matches) == 1
    assert matches[0]["category_id"] == "100226"
    # Root-level search finds Men Clothes by Chinese name.
    root_matches = storage.list_merged_category_children("", query="男装")
    assert any(item["category_id"] == "100011" for item in root_matches)


# --- category rule on copy: product category wins when set, template is default ---


def _template_with_store(tmp_path: Path, category_id: str = "", path: str = "Women > Dresses") -> dict:
    return storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "template_name": "PH 模板",
            "mabang_store_id": "storeS",
            "platform": "shopee",
            "country": "PH",
            "mabang_category_id": category_id,
            "marketplace_category_path": path,
        }
    )


def test_copy_applies_base_category_regardless_of_store_tree(tmp_path: Path) -> None:
    # Whether the category exists in the collected store tree is irrelevant:
    # a filled product_category is used verbatim.
    base = _make_base(tmp_path, "AL0301 类目驱动")
    storage.update_base_product(
        base["id"],
        {"product_category": {"category_id": "100244", "path": "Men Clothes > Tops > T-Shirts", "path_zh": "男装 > 上衣 > T恤", "store_id": "storeS"}},
    )
    template = _template_with_store(tmp_path, category_id="555001", path="Women > Dresses")
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert result["created_count"] == 1
    copy = result["created"][0]
    assert copy["category_source"] == "base_product"
    assert copy["mabang_category_id"] == "100244"
    assert copy["marketplace_category_path"] == "Men Clothes > Tops > T-Shirts"
    assert copy.get("expected_category_path") == ""


def test_copy_defaults_to_template_category_when_product_has_none(tmp_path: Path) -> None:
    base = _make_base(tmp_path, "AL0302 无商品类目")
    template = _template_with_store(tmp_path, category_id="555001", path="Women > Dresses")
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert result["created_count"] == 1
    copy = result["created"][0]
    # Nothing filled on the product → the store template category is the default.
    assert copy["category_source"] == "store_template"
    assert copy["mabang_category_id"] == "555001"
    assert copy["marketplace_category_path"] == "Women > Dresses"
    assert copy.get("expected_category_path") == ""
    assert result["category_mismatch_count"] == 0


def test_copy_uses_base_category_when_template_has_different_one(tmp_path: Path) -> None:
    # The product filled a category; even though the template has its own
    # category, the product's category is used and the template's ignored.
    base = _make_base(tmp_path, "AL0303 商品覆盖模板")
    storage.update_base_product(
        base["id"],
        {"product_category": {"category_id": "100244", "path": "Men Clothes > Tops > T-Shirts"}},
    )
    template = _template_with_store(tmp_path, category_id="555001", path="Women > Dresses")
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert result["created_count"] == 1
    copy = result["created"][0]
    assert copy["mabang_category_id"] == "100244"
    assert copy["category_source"] == "base_product"
