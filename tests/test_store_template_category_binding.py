from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage
from services.api.category_profile_service import CategoryProfileConflict


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _seed_catalog_and_profile() -> None:
    data = storage._empty_store()
    data["mabang_store_options"] = [
        {
            "id": "store-option-1",
            "platform": "shopee",
            "country": "PH",
            "store_name": "POD菲律宾",
            "mabang_store_id": "store-1",
            "status": "active",
        }
    ]
    data["category_profiles"] = [
        {
            "id": "shopee-ph-100244",
            "platform": "shopee",
            "country": "PH",
            "marketplace_category_id": "100244",
            "published_version_id": "version-1",
        }
    ]
    data["category_profile_versions"] = [
        {
            "id": "version-1",
            "profile_id": "shopee-ph-100244",
            "status": "published",
            "version": 1,
            "fields": [],
        }
    ]
    data["marketplace_categories"] = [
        {
            "id": "category-100244",
            "platform": "shopee",
            "country": "PH",
            "store_id": "store-1",
            "category_id": "100244",
            "path": "Men Clothes > Tops > T-Shirts",
            "is_leaf": True,
            "active": True,
        },
        {
            "id": "category-100245",
            "platform": "shopee",
            "country": "PH",
            "store_id": "store-1",
            "category_id": "100245",
            "path": "Men Clothes > Tops > Polo Shirts",
            "is_leaf": True,
            "active": True,
        },
    ]
    storage.save_store(data)


def test_store_template_requires_matching_published_profile_when_catalog_exists():
    _seed_catalog_and_profile()

    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "platform": "shopee",
            "country": "PH",
            "mabang_store_id": "store-1",
            "template_name": "Mens Tee",
            "category_profile_id": "shopee-ph-100244",
            "category_profile_version_id": "version-1",
            "mabang_category_id": "100244",
            "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
        }
    )
    assert template["mabang_category_id"] == "100244"

    with pytest.raises(CategoryProfileConflict, match="does not match"):
        storage.create_store_template(
            {
                "store_name": "POD菲律宾",
                "platform": "shopee",
                "country": "PH",
                "mabang_store_id": "store-1",
                "template_name": "Wrong Rules",
                "category_profile_id": "shopee-ph-100244",
                "category_profile_version_id": "version-1",
                "mabang_category_id": "100245",
                "marketplace_category_path": "Men Clothes > Tops > Polo Shirts",
            }
        )

    with pytest.raises(CategoryProfileConflict, match="active leaf"):
        storage.create_store_template(
            {
                "store_name": "POD菲律宾",
                "platform": "shopee",
                "country": "PH",
                "mabang_store_id": "store-1",
                "template_name": "Unknown",
                "category_profile_id": "shopee-ph-100244",
                "category_profile_version_id": "version-1",
                "mabang_category_id": "999999",
                "marketplace_category_path": "Unknown",
            }
        )


def test_store_template_rejects_unknown_or_non_leaf_catalog_category():
    _seed_catalog_and_profile()

    with pytest.raises(CategoryProfileConflict, match="published category profile"):
        storage.create_store_template(
            {
                "store_name": "POD菲律宾",
                "platform": "shopee",
                "country": "PH",
                "mabang_store_id": "store-1",
                "template_name": "No Rules",
                "mabang_category_id": "100245",
                "marketplace_category_path": "Men Clothes > Tops > Polo Shirts",
            }
        )


def test_mabang_store_options_preserve_country_for_category_scope():
    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "TH", "mabang_store_id": "store-th", "store_name": "TH Store"}
    )

    option = storage.list_mabang_store_options("shopee")[0]
    assert option["country"] == "TH"


def test_mabang_store_options_normalize_site_fallback_country():
    storage.upsert_mabang_store_options(
        {"platform": "shopee", "site": "th", "mabang_store_id": "store-th", "store_name": "TH Store"}
    )

    option = storage.list_mabang_store_options("shopee")[0]
    assert option["country"] == "TH"


def test_store_template_rejects_profile_from_another_store_country():
    _seed_catalog_and_profile()
    data = storage.load_store()
    data["mabang_store_options"].append(
        {
            "id": "store-option-th",
            "platform": "shopee",
            "country": "TH",
            "store_name": "TH Store",
            "mabang_store_id": "store-th",
            "status": "active",
        }
    )
    storage.save_store(data)

    with pytest.raises(CategoryProfileConflict, match="selected store scope"):
        storage.create_store_template(
            {
                "store_name": "TH Store",
                "platform": "shopee",
                "mabang_store_id": "store-th",
                "template_name": "Wrong Country Rules",
                "category_profile_id": "shopee-ph-100244",
                "category_profile_version_id": "version-1",
                "mabang_category_id": "100244",
                "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
            }
        )


def test_store_template_still_requires_published_profile_when_scope_catalog_is_empty():
    data = storage._empty_store()
    data["mabang_store_options"] = [
        {
            "id": "store-option-th",
            "platform": "shopee",
            "country": "TH",
            "store_name": "TH Store",
            "mabang_store_id": "store-th",
            "status": "active",
        }
    ]
    storage.save_store(data)

    with pytest.raises(CategoryProfileConflict, match="published category profile"):
        storage.create_store_template(
            {
                "store_name": "TH Store",
                "platform": "shopee",
                "country": "TH",
                "mabang_store_id": "store-th",
                "template_name": "Missing Rules",
                "mabang_category_id": "101516",
                "marketplace_category_path": "Tickets, Vouchers & Services > Beauty & Wellness > Dental",
            }
        )


def test_cnsc_template_requires_complete_price_matrix_for_every_target_site():
    payload = {
        "store_name": "广州小苗服饰有限公司",
        "mabang_store_id": "cnsc-store",
        "template_name": "CNSC Hoodie",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "sizes": ["S", "XL"],
        "default_site_discount_percent": 40,
        "target_site_stores": [{
            "site_code": "PH",
            "store_id": "ph-store",
            "store_name": "海宁PH",
            "mabang_discount_id": "discount-ph",
            "discount_percent": 40,
        }],
        "price_tiers": [
            {"id": "standard", "sizes": ["S"], "global_price_cny": 19.2, "site_prices": {"PH": {"sale_price": 746, "original_price": 1243}}},
            {"id": "large", "sizes": ["XL"], "global_price_cny": 22.2, "site_prices": {"PH": {"sale_price": 811, "original_price": 1352}}},
        ],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
    }

    template = storage.create_store_template(payload)
    assert template["price_tiers"][1]["site_prices"]["PH"]["sale_price"] == 811.0

    incomplete = {**payload, "template_name": "Missing PH Matrix"}
    incomplete["price_tiers"] = [
        {"id": "standard", "sizes": ["S"], "global_price_cny": 19.2, "site_prices": {}},
        payload["price_tiers"][1],
    ]
    with pytest.raises(ValueError, match="incomplete for standard/PH"):
        storage.create_store_template(incomplete)


def test_store_template_saves_when_store_has_no_category_catalog_yet():
    """已选店铺(store_options 存在)但从未采集类目树时，允许保存模板——
    否则已存在的模板连其它字段都保存不了（采集入口独立于模板保存）。
    回归：此前 all_categories 非空 + 该店无树 → 硬 422「selected store scope」。"""
    data = storage._empty_store()
    data["mabang_store_options"] = [
        {
            "id": "store-option-kids",
            "platform": "shopee",
            "country": "PH",
            "store_name": "3️⃣JH_POD-Kids",
            "mabang_store_id": "store-kids",
            "status": "active",
        }
    ]
    # 全局已有其它店的类目树（让 old bug 触发条件成立），但 store-kids 无树
    data["marketplace_categories"] = [
        {
            "id": "cat-other",
            "platform": "shopee",
            "country": "PH",
            "store_id": "store-other",
            "category_id": "100244",
            "path": "Men Clothes > Tops > T-Shirts",
            "is_leaf": True,
            "active": True,
        }
    ]
    data["category_profiles"] = [
        {
            "id": "profile-kids",
            "platform": "shopee",
            "country": "PH",
            "marketplace_category_id": "101773",
            "published_version_id": "version-kids",
        }
    ]
    data["category_profile_versions"] = [
        {
            "id": "version-kids",
            "profile_id": "profile-kids",
            "status": "published",
            "version": 1,
            "fields": [],
        }
    ]
    storage.save_store(data)

    template = storage.create_store_template(
        {
            "store_name": "3️⃣JH_POD-Kids",
            "platform": "shopee",
            "country": "PH",
            "mabang_store_id": "store-kids",
            "template_name": "Kids Tee",
            "category_profile_id": "profile-kids",
            "category_profile_version_id": "version-kids",
            "mabang_category_id": "101773",
            "marketplace_category_path": "Baby & Kids Fashion>Girl Clothes>Tops>T-Shirts",
        }
    )
    assert template["mabang_category_id"] == "101773"


def test_store_template_without_catalog_still_requires_profile():
    """店无类目树时允许保存，但 profile（类目规则）仍必填——不能连规则都没有。"""
    data = storage._empty_store()
    data["mabang_store_options"] = [
        {
            "id": "store-option-th",
            "platform": "shopee",
            "country": "TH",
            "store_name": "TH Store",
            "mabang_store_id": "store-th",
            "status": "active",
        }
    ]
    data["marketplace_categories"] = [
        {
            "id": "cat-other",
            "platform": "shopee",
            "country": "PH",
            "store_id": "store-other",
            "category_id": "100244",
            "path": "Men Clothes > Tops > T-Shirts",
            "is_leaf": True,
            "active": True,
        }
    ]
    storage.save_store(data)

    with pytest.raises(CategoryProfileConflict, match="published category profile"):
        storage.create_store_template(
            {
                "store_name": "TH Store",
                "platform": "shopee",
                "country": "TH",
                "mabang_store_id": "store-th",
                "template_name": "No Rules",
                "mabang_category_id": "101516",
                "marketplace_category_path": "Tickets > Beauty",
            }
        )


def test_store_template_accepts_legacy_compact_path_and_self_heals_to_canonical():
    """存量模板存的是旧采集形态 "A>B>C"（无空格）：保存应通过并把路径就地
    归一成目录 canonical 形态，而不是报 "category path does not match"。"""
    _seed_catalog_and_profile()

    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "platform": "shopee",
            "country": "PH",
            "mabang_store_id": "store-1",
            "template_name": "Mens Tee Legacy Path",
            "category_profile_id": "shopee-ph-100244",
            "category_profile_version_id": "version-1",
            "mabang_category_id": "100244",
            "marketplace_category_path": "Men Clothes>Tops>T-Shirts",
        }
    )
    assert template["marketplace_category_path"] == "Men Clothes > Tops > T-Shirts"


def test_store_template_still_rejects_genuinely_different_catalog_path():
    _seed_catalog_and_profile()

    with pytest.raises(CategoryProfileConflict, match="path does not match"):
        storage.create_store_template(
            {
                "store_name": "POD菲律宾",
                "platform": "shopee",
                "country": "PH",
                "mabang_store_id": "store-1",
                "template_name": "Wrong Path",
                "category_profile_id": "shopee-ph-100244",
                "category_profile_version_id": "version-1",
                "mabang_category_id": "100244",
                "marketplace_category_path": "Men Clothes > Tops > Polo Shirts",
            }
        )

def _seed_category_scene(tmp_path: Path, *, with_profile_for_base_category: bool) -> tuple[dict, dict, dict]:
    """基础商品选了另一个类目（Costumes 100059）的场景：模板绑 100244 方案。"""
    _seed_catalog_and_profile()
    data = storage.load_store()
    if with_profile_for_base_category:
        data["category_profiles"].append({
            "id": "shopee-ph-100059",
            "platform": "shopee",
            "country": "PH",
            "marketplace_category_id": "100059",
            "active_version_id": "version-59",
        })
        data["category_profile_versions"].append({
            "id": "version-59",
            "profile_id": "shopee-ph-100059",
            "status": "published",
            "version": 1,
            "fields": [{"field_id": "100777", "label": "图案类型", "input_kind": 2, "default_value": "5487"}],
        })
        storage.save_store(data)
    folder = tmp_path / "AL0701 Costume Cape"
    folder.mkdir()
    (folder / "main.jpg").write_bytes(b"image")
    (folder / "White.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Cape"})
    base = storage.update_base_product(
        base["id"],
        {"product_category": {"category_id": "100059", "path": "Men Clothes > Costumes"}},
    )
    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "platform": "shopee",
            "country": "PH",
            "mabang_store_id": "store-1",
            "template_name": "Cape Tee",
            "category_profile_id": "shopee-ph-100244",
            "category_profile_version_id": "version-1",
            "mabang_category_id": "100244",
            "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
            "mabang_discount_id": "discount-789",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S"],
            "description": "Scene description.",
            "attribute_values": {"100134": ["1257"]},
        }
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    return base, template, storage.get_item("store_listing_copies", copy["id"])


def test_copy_with_base_category_without_profile_clears_binding(tmp_path: Path) -> None:
    """生效类目来自基础商品且没有已发布方案：清空快照与绑定，绝不带模板方案的
    字段（现场 2026-09-26：Costumes 页面找不到 Hoodies 方案的「材料」→ 整单 blocked）。"""
    _base, _template, copy = _seed_category_scene(tmp_path, with_profile_for_base_category=False)

    assert copy["mabang_category_id"] == "100059"
    assert copy["category_source"] == "base_product"
    assert copy.get("category_profile_id") == ""
    assert copy.get("category_profile_version_id") == ""
    assert not (copy.get("category_profile_snapshot") or {})
    assert copy.get("attribute_values") == {}
    assert copy.get("category_attributes") == {}

    detail = storage.get_store_listing_copy_detail(copy["id"])
    missing = detail["readiness"]["missing_fields"]
    assert not [field for field in missing if field.startswith("category_attributes.")]
    assert "category_profile_version_expired" not in missing


def test_copy_with_base_category_rebinds_published_profile(tmp_path: Path) -> None:
    _base, _template, copy = _seed_category_scene(tmp_path, with_profile_for_base_category=True)

    assert copy["category_profile_id"] == "shopee-ph-100059"
    assert copy["category_profile_version_id"] == "version-59"
    snapshot = copy.get("category_profile_snapshot") or {}
    assert [field["field_id"] for field in snapshot.get("fields") or []] == ["100777"]
    assert copy["attribute_values"] == {"100777": "5487"}


def test_update_listing_copy_can_repair_profile_binding(tmp_path: Path) -> None:
    _base, _template, copy = _seed_category_scene(tmp_path, with_profile_for_base_category=False)
    assert not (copy.get("category_profile_snapshot") or {})

    updated = storage.update_store_listing_copy(
        copy["id"],
        {
            "category_profile_id": "shopee-ph-100059",
            "category_profile_version_id": "version-59",
            "category_profile_snapshot": {
                "profile_id": "shopee-ph-100059",
                "marketplace_category_id": "100059",
                "fields": [{"field_id": "100777", "label": "图案类型"}],
            },
            "attribute_values": {"100777": "5487"},
        },
    )
    assert updated["category_profile_id"] == "shopee-ph-100059"
    assert updated["profile_snapshot_hash"].startswith("sha256:")

    cleared = storage.update_store_listing_copy(
        copy["id"],
        {
            "category_profile_id": "",
            "category_profile_version_id": "",
            "category_profile_snapshot": {},
            "attribute_values": {},
        },
    )
    assert cleared["category_profile_id"] == ""
    assert cleared.get("profile_snapshot_hash", "") == ""
