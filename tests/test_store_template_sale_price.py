"""店铺模板的六折售价可手工改写，并驱动本土自动化的价格。

覆盖点：
- 本土价格行的售价取模板六折售价（可自定义），留空才退回 原价 × 0.6；
- 「按尺码设置价格」的原价与六折售价都能单独填：售价留空时按模板同一折扣比例换算，
  填了就用那个价，只填售价的行原价继续继承模板；
- 存量「尺码 → 原价」标量形态照旧读得懂；
- 模板改价同步到已有本土副本，但不动操作员手工改过的行；
- 跨境(CNSC)走价目表，完全不受影响。
- 编辑器把尺码售价渲染成可编辑输入框（UI 契约）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "web" / "app.js"
INDEX = ROOT / "apps" / "web" / "index.html"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


SIZES = ["S", "XL"]


def _make_base(tmp_path: Path, colors: tuple[str, ...] = ("Black", "Blue")) -> dict:
    folder = tmp_path / "AL0353 童装T恤"
    folder.mkdir()
    (folder / "main.jpg").write_bytes(b"image")
    for color in colors:
        (folder / f"{color}.jpg").write_bytes(b"image")
    return storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD Kids T-Shirt"}
    )


def _site_template(**overrides) -> dict:
    payload = {
        "store_name": "POD PH",
        "template_name": "Kids Tee",
        "product_line": "POD Kids T-Shirt",
        "marketplace_category_path": "Baby & Kids Fashion > Tops > T-Shirts",
        "normal_price": 380,
        "sale_price": 218,
        "sizes": list(SIZES),
        "variation_rule": "color_size",
        "sku_template": "{main_sku}-{color}-{size}",
    }
    payload.update(overrides)
    return storage.create_store_template(payload)


def _prices_by_code(copy_id: str) -> dict[str, dict]:
    return {price["sku_code"]: price for price in storage.list_listing_prices(copy_id)}


def test_site_rows_use_the_templates_editable_sale_price(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template()
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    prices = _prices_by_code(copy["id"])

    assert {price["normal_price"] for price in prices.values()} == {380.0}
    assert {price["sale_price"] for price in prices.values()} == {218.0}


def test_blank_sale_price_still_derives_sixty_percent(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template(normal_price=380, sale_price="")
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    prices = _prices_by_code(copy["id"])

    assert {price["sale_price"] for price in prices.values()} == {228.0}


def test_size_price_override_follows_the_template_discount_ratio(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    # 原价 100、自定义售价 80 → 比例 0.8；XL 单独设原价 150 → 售价 120。
    template = _site_template(normal_price=100, sale_price=80, size_price_overrides={"XL": 150})
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    prices = _prices_by_code(copy["id"])

    assert prices["AL0353-Black-S"]["normal_price"] == 100.0
    assert prices["AL0353-Black-S"]["sale_price"] == 80.0
    assert prices["AL0353-Black-XL"]["normal_price"] == 150.0
    assert prices["AL0353-Black-XL"]["sale_price"] == 120.0


def test_size_price_override_keeps_sixty_percent_when_sale_price_is_blank(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template(normal_price=100, sale_price="", size_price_overrides={"XL": 150})
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    prices = _prices_by_code(copy["id"])

    assert prices["AL0353-Black-S"]["sale_price"] == 60.0
    assert prices["AL0353-Black-XL"]["sale_price"] == 90.0


def test_template_sale_price_change_reaches_existing_site_copies(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template()
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    storage.update_store_template(template["id"], {"normal_price": 380, "sale_price": 199})

    prices = _prices_by_code(copy["id"])

    assert {price["normal_price"] for price in prices.values()} == {380.0}
    assert {price["sale_price"] for price in prices.values()} == {199.0}


def test_template_price_sync_keeps_hand_edited_copy_rows(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template()
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    edited = _prices_by_code(copy["id"])["AL0353-Black-S"]
    storage.update_store_listing_copy(copy["id"], {"prices": [{"id": edited["id"], "normal_price": 500, "sale_price": 260}]})

    storage.update_store_template(template["id"], {"normal_price": 380, "sale_price": 199})

    prices = _prices_by_code(copy["id"])

    # 手工改过的行保持不动，其余行跟着模板走。
    assert prices["AL0353-Black-S"]["normal_price"] == 500.0
    assert prices["AL0353-Black-S"]["sale_price"] == 260.0
    assert prices["AL0353-Black-XL"]["sale_price"] == 199.0


def test_base_rows_keep_the_typed_sale_price_when_only_normal_price_changes(tmp_path: Path) -> None:
    """只改原价（例如走 API 的部分更新）时，售价就是操作员填的那个数，不再重算。"""
    base = _make_base(tmp_path)
    template = _site_template(normal_price=89, sale_price=69)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    storage.update_store_template(template["id"], {"normal_price": 200})

    prices = _prices_by_code(copy["id"])

    assert {price["normal_price"] for price in prices.values()} == {200.0}
    assert {price["sale_price"] for price in prices.values()} == {69.0}


def test_size_price_override_change_reaches_existing_site_copies(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template(normal_price=100, sale_price=80)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    storage.update_store_template(template["id"], {"normal_price": 100, "sale_price": 80, "size_price_overrides": {"XL": 150}})

    prices = _prices_by_code(copy["id"])

    assert prices["AL0353-Black-XL"]["normal_price"] == 150.0
    assert prices["AL0353-Black-XL"]["sale_price"] == 120.0
    assert prices["AL0353-Black-S"]["sale_price"] == 80.0


def test_size_sale_price_override_is_used_verbatim(tmp_path: Path) -> None:
    """按尺码填的六折售价直接用，不再按比例换算。"""
    base = _make_base(tmp_path)
    template = _site_template(
        normal_price=100,
        sale_price=80,
        size_price_overrides={"XL": {"normal_price": 150, "sale_price": 99}},
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    prices = _prices_by_code(copy["id"])

    assert prices["AL0353-Black-XL"]["normal_price"] == 150.0
    assert prices["AL0353-Black-XL"]["sale_price"] == 99.0
    assert prices["AL0353-Black-S"]["normal_price"] == 100.0
    assert prices["AL0353-Black-S"]["sale_price"] == 80.0


def test_size_sale_only_override_keeps_the_template_normal_price(tmp_path: Path) -> None:
    """只填尺码售价时：原价继续继承模板，售价用填的那个价。"""
    base = _make_base(tmp_path)
    template = _site_template(
        normal_price=100,
        sale_price=80,
        size_price_overrides={"XL": {"sale_price": 99}},
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    prices = _prices_by_code(copy["id"])

    assert prices["AL0353-Black-XL"]["normal_price"] == 100.0
    assert prices["AL0353-Black-XL"]["sale_price"] == 99.0


def test_size_override_with_blank_sale_price_still_derives_the_ratio(tmp_path: Path) -> None:
    """新形态里售价留空＝自动：和有原价没有售价的老标量行为一致。"""
    base = _make_base(tmp_path)
    template = _site_template(
        normal_price=100,
        sale_price=80,
        size_price_overrides={"XL": {"normal_price": 150, "sale_price": ""}},
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    prices = _prices_by_code(copy["id"])

    assert prices["AL0353-Black-XL"]["sale_price"] == 120.0


def test_size_sale_override_change_reaches_existing_site_copies(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template(normal_price=100, sale_price=80)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    storage.update_store_template(
        template["id"],
        {"normal_price": 100, "sale_price": 80, "size_price_overrides": {"XL": {"normal_price": 150, "sale_price": 99}}},
    )

    prices = _prices_by_code(copy["id"])

    assert prices["AL0353-Black-XL"]["normal_price"] == 150.0
    assert prices["AL0353-Black-XL"]["sale_price"] == 99.0


def test_size_price_override_normalization_keeps_sale_only_and_drops_empty_entries(tmp_path: Path) -> None:
    template = _site_template(
        size_price_overrides={
            "S": {"normal_price": "", "sale_price": 55},
            "XL": {"normal_price": "", "sale_price": ""},
            "M": 90,  # 老标量形态：只覆盖原价
        }
    )

    assert template["size_price_overrides"] == {
        "S": {"normal_price": "", "sale_price": 55.0},
        "M": {"normal_price": 90.0, "sale_price": ""},
    }


def test_size_price_editor_renders_an_editable_sale_price_input() -> None:
    """规格与 SKU 的「按尺码设置价格」：六折售价是可编辑输入框，占位符是自动值。"""
    script = APP.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    assert 'data-size-sale-price="${escapeHtml(size)}" type="number"' in script
    assert "refreshSizePricePlaceholders()" in script
    assert "return normalOverride ? `自动 ${auto}` : `继承 ${auto}`;" in script
    assert "entry.sale_price = sale" in script and "if (normal) entry.normal_price = normal;" in script
    assert "sizeSalePreview" not in script  # 旧只读 output 预览已删除
    assert "<output data-size-sale=" not in script
    assert "留空＝自动换算" in html


def test_cnsc_template_and_copy_prices_stay_empty(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = storage.create_store_template({
        "store_name": "广州小苗服饰有限公司",
        "template_name": "CNSC 童装",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "sizes": ["S"],
        "sku_template": "{main_sku}-{color}-{size}",
        "default_site_discount_percent": 40,
        "target_site_stores": [{"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}],
        "price_tiers": [
            {
                "id": "uniform",
                "sizes": [],
                "global_price_cny": 19.2,
                "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
            }
        ],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        "normal_price": 380,
        "sale_price": 218,
    })
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    storage.update_store_template(template["id"], {"normal_price": 380, "sale_price": 199})

    prices = _prices_by_code(copy["id"])

    assert all(price["normal_price"] == "" for price in prices.values())
    assert all(price["sale_price"] == "" for price in prices.values())
