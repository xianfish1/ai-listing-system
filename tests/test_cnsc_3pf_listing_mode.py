"""跨境本土（3PF）刊登模式。

覆盖点：
- 三种模式的取值校验，未知模式必须报错（不能静默回落 site）；
- 3PF = 跨境页面能力 + 本土上款能力：站点价格表 / 目标站点 / 三仓库存照旧，
  默认 SKU(白T恤) / 尺码表末位 / 去括号也生效；
- 默认 SKU 行自带全球价与每站价格（页面「全球商品价格」列必填）；
- 默认 SKU 颜色不参与 CNSC 价格档覆盖校验、不要求基础商品成本价；
- 浏览器输入里默认 SKU 颜色/行在末尾（执行器按行号填写），尺码表在产品图末位；
- 现有 site / cnsc_global 行为不变。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage
from services.api.listing_modes import (
    LISTING_MODE_CNSC_3PF,
    category_profile_country,
    is_cnsc_page,
    is_local_like,
    is_pure_cnsc,
    normalize_listing_mode,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


SIZES = ["130cm(21-26kg)", "140cm(24-30kg)"]


def _make_base(tmp_path: Path, colors: tuple[str, ...] = ("Black", "Blue")) -> dict:
    folder = tmp_path / "AL0353 童装T恤"
    folder.mkdir()
    (folder / "main.jpg").write_bytes(b"image")
    for color in colors:
        (folder / f"{color}.jpg").write_bytes(b"image")
    return storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD Kids T-Shirt"}
    )


def _default_sku_config(tmp_path: Path, **overrides) -> dict:
    image = tmp_path / "white-tee.png"
    image.write_bytes(b"image")
    config = {
        "enabled": True,
        "color_name": "PureWhite",
        "sku": "AL9999-PureWhite",
        "image_path": str(image),
        "normal_price": 120,
        "sale_price": 79,
    }
    config.update(overrides)
    return config


def _size_chart_file(tmp_path: Path) -> str:
    path = tmp_path / "size-chart.png"
    path.write_bytes(b"image")
    return str(path)


def _cnsc_common(tmp_path: Path) -> dict:
    return {
        "sizes": list(SIZES),
        "sku_template": "{main_sku}-{color}-{size}",
        "default_site_discount_percent": 40,
        "target_site_stores": [
            {
                "site_code": "PH",
                "store_id": "ph-store",
                "store_name": "海宁PH",
                "mabang_discount_id": "activity-ph",
                "discount_percent": 40,
            }
        ],
        "price_tiers": [
            {
                "id": "uniform",
                "sizes": [],
                "global_price_cny": 19.2,
                "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
            }
        ],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
    }


def _three_pf_template(tmp_path: Path, **overrides) -> dict:
    payload = {
        "store_name": "3PF 童装店",
        "template_name": "3PF Kids Tee",
        "listing_mode": LISTING_MODE_CNSC_3PF,
        "cnsc_store": {"id": "cnsc-store", "name": "3PF 全球商品店"},
        "normal_price": 100,
        "sale_price": 60,
        "stock": 500,
        "global_price_cny": 19.2,
        "default_size_chart_path": _size_chart_file(tmp_path),
        "default_sku": _default_sku_config(tmp_path),
        **_cnsc_common(tmp_path),
    }
    payload.update(overrides)
    return storage.create_store_template(payload)


def _cnsc_template(tmp_path: Path, **overrides) -> dict:
    payload = {
        "store_name": "广州小苗服饰有限公司",
        "template_name": "CNSC 童装",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "default_sku": _default_sku_config(tmp_path),
        **_cnsc_common(tmp_path),
    }
    payload.update(overrides)
    return storage.create_store_template(payload)


def _ready_copy(copy: dict) -> dict:
    """Readiness 只差业务字段：给副本补上标题/描述等公共必填。"""

    return {
        **copy,
        "listing_title": "Kids Tee",
        "description": "描述已就绪",
        "description_status": "ready",
        "weight_kg": 0.5,
        "package_length_cm": 20,
        "package_width_cm": 15,
        "package_height_cm": 5,
    }


def test_listing_mode_predicates_split_page_and_rules() -> None:
    assert is_cnsc_page("cnsc_3pf") is True
    assert is_local_like("cnsc_3pf") is True
    assert is_pure_cnsc("cnsc_3pf") is False

    assert is_cnsc_page("cnsc_global") is True
    assert is_local_like("cnsc_global") is False
    assert is_pure_cnsc("cnsc_global") is True

    assert is_cnsc_page("site") is False
    assert is_local_like("site") is True
    assert normalize_listing_mode(" CNSC_3PF ") == "cnsc_3pf"


def test_unknown_listing_mode_is_rejected(tmp_path: Path) -> None:
    payload = {
        "store_name": "X",
        "template_name": "bad mode",
        "listing_mode": "cnsc_4pf",
        **_cnsc_common(tmp_path),
    }
    with pytest.raises(ValueError, match="刊登模式无效"):
        storage.create_store_template(payload)


def test_three_pf_copy_keeps_cnsc_config_and_local_extras(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _three_pf_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    assert copy["listing_mode"] == "cnsc_3pf"
    # 跨境页面能力照旧
    assert copy["target_site_stores"][0]["site_code"] == "PH"
    # 3PF 不再有价格档：价格由模板价推导，全球价是模板单设值。
    assert copy["price_tiers"] == []
    assert copy["global_price_cny"] == 19.2
    assert copy["warehouse_stock_defaults"] == {"TH": 300, "CN_GZ": 0, "PH": 0}
    # 3PF 没有价格档，也就没有绑定模式。
    assert copy["cnsc_binding_mode"] == ""
    # 本土上款能力
    assert copy["default_sku"]["enabled"] is True
    assert copy["size_chart_as_last_product_image"] is True
    assert copy["sku_strip_parentheses"] is True


def test_three_pf_default_sku_rows_carry_global_and_site_prices(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _three_pf_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    skus = storage.list_listing_skus(copy["id"])
    default_rows = [sku for sku in skus if sku.get("sku_source") == "default_sku"]

    assert [sku["sku_code"] for sku in default_rows] == [
        "AL9999-PureWhite-130cm",
        "AL9999-PureWhite-140cm",
    ]
    for row in default_rows:
        # 全球价用模板单设值；站点价仍用默认 SKU 自己的价。
        assert row["global_price_cny"] == 19.2
        assert row["site_price_matrix"]["PH"] == {"original_price": 120.0, "sale_price": 79.0}
        assert row["warehouse_stocks"] == {"TH": 300, "CN_GZ": 0, "PH": 0}

    prices = {price["sku_code"]: price for price in storage.list_listing_prices(copy["id"])}
    default_price = prices["AL9999-PureWhite-130cm"]
    assert default_price["normal_price"] == 120.0
    assert default_price["sale_price"] == 79.0
    assert default_price["global_price_cny"] == 19.2
    assert default_price["site_price_matrix"]["PH"]["sale_price"] == 79.0
    # 普通颜色按模板价（原价/折扣价），站点价由模板价推导。
    other_price = prices["AL0353-Black-130cm"]
    assert other_price["normal_price"] == 100.0
    assert other_price["sale_price"] == 60.0
    assert other_price["global_price_cny"] == 19.2
    assert other_price["site_price_matrix"]["PH"] == {"original_price": 100.0, "sale_price": 60.0}


def test_three_pf_readiness_requires_cnsc_config_plus_size_chart(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _three_pf_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    skus = storage.list_listing_skus(copy["id"])
    prices = storage.list_listing_prices(copy["id"])
    default_ids = {sku["id"] for sku in skus if sku.get("sku_source") == "default_sku"}
    ready_copy = {
        **_ready_copy(copy),
        # 模板本身没绑店铺/类目时副本也空着：就绪检查要求这两组身份字段。
        "mabang_store_id": "3pf-store",
        "marketplace_category_path": "Men Clothes > Tops",
        "mabang_category_id": "category-1",
    }

    # 3PF 按本土口径用模板价：不要求基础商品成本价，默认 SKU 行也不影响。
    result = storage._check_automation_readiness(ready_copy, skus=skus, prices=prices)
    assert result["ready"] is True, result["missing_fields"]
    priced = prices

    # 去掉尺码图：3PF 按本土口径，必须拦。
    without_chart = {
        key: value
        for key, value in ready_copy.items()
        if key not in {"size_chart_image", "size_chart_image_path", "size_chart_asset_id", "mabang_size_chart_template_id"}
    }
    result = storage._check_automation_readiness(without_chart, skus=skus, prices=priced)
    assert "size_chart_image" in result["missing_fields"]


def test_three_pf_default_sku_is_exempt_from_tier_coverage(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _three_pf_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    variants = [
        (sku.get("color_code") or "", sku.get("size") or "")
        for sku in storage.list_listing_skus(copy["id"])
    ]
    problems = storage._cnsc_configuration_problems(copy, selected_variants=variants)

    assert "cnsc_price_tier_size_coverage" not in problems


def test_three_pf_browser_inputs_put_default_sku_last_and_size_chart_integrates(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _three_pf_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    skus = storage.list_listing_skus(copy["id"])
    prices = storage.list_listing_prices(copy["id"])

    inputs = storage._build_browser_inputs(copy, skus, prices, storage.assets_for_group(copy.get("group_id") or ""))

    assert inputs["listing_mode"] == "cnsc_3pf"
    # 默认 SKU 颜色必须在颜色轴末尾（马帮按颜色轴生成网格行，执行器按行号填写）。
    assert inputs["variation"]["colors"][-1] == "PureWhite"
    assert [sku["sku_code"] for sku in inputs["skus"][-2:]] == [
        "AL9999-PureWhite-130cm",
        "AL9999-PureWhite-140cm",
    ]
    # 尺码表放产品图末位（本土口径）。
    assert inputs["size_chart_last_product_image"] is True
    assert inputs["product_image_form_paths"][-1] == str(tmp_path / "size-chart.png")


def test_existing_modes_are_unchanged(tmp_path: Path) -> None:
    base = _make_base(tmp_path)

    cnsc_template = _cnsc_template(tmp_path)
    cnsc_copy = storage.copy_base_product_to_store_templates(base["id"], [cnsc_template["id"]])["created"][0]
    cnsc_skus = storage.list_listing_skus(cnsc_copy["id"])
    assert cnsc_copy["size_chart_as_last_product_image"] is False
    assert [sku for sku in cnsc_skus if sku.get("sku_source") == "default_sku"] == []

    site_template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "template_name": "Kids Tee",
            "sizes": list(SIZES),
            "normal_price": 100,
            "sale_price": 60,
            "default_sku": _default_sku_config(tmp_path),
        }
    )
    site_copy = storage.copy_base_product_to_store_templates(base["id"], [site_template["id"]])["created"][0]
    site_skus = storage.list_listing_skus(site_copy["id"])
    assert [sku for sku in site_skus if sku.get("sku_source") == "default_sku"]
    site_default = next(sku for sku in site_skus if sku.get("sku_source") == "default_sku")
    # 本土默认 SKU 不带跨境字段（行为与改动前一致）。
    assert site_default["global_price_cny"] == ""
    assert site_default["site_price_matrix"] == {}
    assert site_copy["listing_mode"] == "site"


def test_three_pf_template_price_change_syncs_to_existing_copy(tmp_path: Path) -> None:
    """模板改原价/全球价后，已生成的 3PF 副本要跟着更新价格行与 SKU 矩阵。"""
    base = _make_base(tmp_path)
    template = _three_pf_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    storage.update_store_template(
        template["id"],
        {"normal_price": 200, "sale_price": 120, "global_price_cny": 25},
    )

    prices = {price["sku_code"]: price for price in storage.list_listing_prices(copy["id"])}
    skus = {sku["sku_code"]: sku for sku in storage.list_listing_skus(copy["id"])}
    assert prices["AL0353-Black-130cm"]["normal_price"] == 200.0
    assert prices["AL0353-Black-130cm"]["sale_price"] == 120.0
    assert skus["AL0353-Black-130cm"]["global_price_cny"] == 25.0
    assert skus["AL0353-Black-130cm"]["site_price_matrix"]["PH"] == {
        "original_price": 200.0,
        "sale_price": 120.0,
    }
    # 默认 SKU 的价格仍用自己配置，但全球价跟模板走。
    assert prices["AL9999-PureWhite-130cm"]["normal_price"] == 120.0
    assert skus["AL9999-PureWhite-130cm"]["global_price_cny"] == 25.0


def test_category_profile_scope_splits_local_site_and_cross_border_pages() -> None:
    """类目方案作用域：本土站点=店铺国家；CNSC 页（纯跨境/3PF）=GLOBAL。"""
    assert category_profile_country("site", "PH") == "PH"
    assert category_profile_country("cnsc_global", "PH") == "GLOBAL"
    assert category_profile_country("cnsc_3pf", "PH") == "GLOBAL"
    assert category_profile_country(None, " th ") == "TH"
