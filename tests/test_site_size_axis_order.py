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


def _transaction_defaults() -> dict:
    return {
        "stock": 100,
        "weight_kg": 0.3,
        "package_length_cm": 30,
        "package_width_cm": 20,
        "package_height_cm": 5,
    }


def test_order_sizes_by_template_matches_parenthesis_stripped() -> None:
    sizes = ["M", "90cm", "S", "130cm(21-26kg)"]
    template_sizes = ["130cm(21-26kg)", "S(45-55kg)", "90cm(10-12kg)", "M(55-65kg)"]
    ordered = storage._order_sizes_by_template(sizes, template_sizes)
    # 精确匹配 + 去括号匹配都跟模板顺序；模板里没有的尺码排最后
    assert ordered == ["130cm(21-26kg)", "S", "90cm", "M"]


def test_order_sizes_by_template_appends_unknown_and_keeps_empty_behaviour() -> None:
    assert storage._order_sizes_by_template(["XXL", "S"], ["S"]) == ["S", "XXL"]
    assert storage._order_sizes_by_template(["M", "L"], []) == ["M", "L"]


def _make_site_copy(tmp_path: Path, *, template_sizes: list[str], selected_sizes: list[str]) -> tuple[dict, dict, dict]:
    folder = tmp_path / "AL0801 Size Tee"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg", "Black.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    base = storage.update_base_product(base["id"], {"selected_sizes": selected_sizes})
    template = storage.create_store_template(
        {
            "store_name": "POD Store A",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": "Size Tee",
            "product_line": "POD Mens T-Shirt",
            "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
            "mabang_store_id": "store-A",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": template_sizes,
            "description": "Size order description.",
            **_transaction_defaults(),
        }
    )
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    copy = storage.get_item("store_listing_copies", result["created"][0]["id"])
    storage.update_store_listing_copy(copy["id"], {"description": "Size order description", "description_status": "ready"})
    return template, storage.get_item("store_listing_copies", copy["id"]), base


def test_site_copy_sku_sizes_follow_template_size_order(tmp_path: Path) -> None:
    """本土副本生成 SKU 时尺码按模板 chip 顺序（`_listing_sizes`；勾选顺序不再决定
    上架顺序）。list_listing_skus 读取时按 sku_code 重排属正常——执行器按
    「颜色+尺码」匹配行，上架顺序由尺码轴（variation.sizes）决定。"""
    template_sizes = ["130cm(21-26kg)", "S(45-55kg)", "90cm(10-12kg)", "M(55-65kg)"]
    selected_sizes = ["M(55-65kg)", "90cm(10-12kg)", "S(45-55kg)", "130cm(21-26kg)"]
    template, copy, base = _make_site_copy(tmp_path, template_sizes=template_sizes, selected_sizes=selected_sizes)

    assert storage._listing_sizes(base, template) == template_sizes
    white_sizes = {sku["size"] for sku in storage.list_listing_skus(copy["id"]) if sku.get("color_code") == "White"}
    assert white_sizes == set(template_sizes)


def test_site_browser_inputs_size_axis_follows_template_order(tmp_path: Path) -> None:
    """执行器尺码轴（variation.sizes）按模板 chip 顺序；副本没带 sizes 时从模板现值取。"""
    template_sizes = ["130cm(21-26kg)", "S(45-55kg)", "90cm(10-12kg)", "M(55-65kg)"]
    selected_sizes = ["M(55-65kg)", "90cm(10-12kg)", "S(45-55kg)", "130cm(21-26kg)"]
    template, copy, base = _make_site_copy(tmp_path, template_sizes=template_sizes, selected_sizes=selected_sizes)

    skus = storage.list_listing_skus(copy["id"])
    prices = storage.list_listing_prices(copy["id"])
    assets = storage.assets_for_group(copy.get("group_id") or "")
    browser_inputs = storage._build_browser_inputs(copy, skus, prices, assets)
    assert browser_inputs["variation"]["sizes"] == template_sizes

    # 模板 chip 重排后（未改副本），任务重建时按模板现顺序走
    storage.update_store_template(template["id"], {"sizes": ["M(55-65kg)", "130cm(21-26kg)", "S(45-55kg)", "90cm(10-12kg)"]})
    browser_inputs = storage._build_browser_inputs(copy, skus, prices, assets)
    assert browser_inputs["variation"]["sizes"] == ["M(55-65kg)", "130cm(21-26kg)", "S(45-55kg)", "90cm(10-12kg)"]


def test_listing_sku_rows_follow_template_size_order_on_read(tmp_path: Path) -> None:
    """读取侧（SKU 行清单 / 副本编辑器 detail）也按模板 chip 顺序。

    旧排序用 _size_sort_value（只认 S/M/L），cm 类尺码全部落到 999 后按
    sku_code 字母序，90cm 会排到 100~110cm 后面，编辑器里 SKU 行与模板
    顺序对不上（现场 2026-09-27：从商品复制到模板后 SKU 不按模板顺序）。"""
    template_sizes = ["90cm(10-12kg)", "100cm(13-18kg)", "110cm(19-24kg)"]
    selected_sizes = ["110cm(19-24kg)", "90cm(10-12kg)", "100cm(13-18kg)"]
    template, copy, base = _make_site_copy(tmp_path, template_sizes=template_sizes, selected_sizes=selected_sizes)

    def sizes_of(rows: list[dict], color: str) -> list[str]:
        return [row["size"] for row in rows if row.get("color_code") == color]

    listed = storage.list_listing_skus(copy["id"])
    assert sizes_of(listed, "White") == template_sizes
    assert sizes_of(listed, "Black") == template_sizes
    assert sizes_of(storage.get_store_listing_copy_detail(copy["id"])["skus"], "White") == template_sizes

    # 模板 chip 重排后，读取侧跟随模板现顺序（与执行器尺码轴同一来源）
    reordered = ["110cm(19-24kg)", "90cm(10-12kg)", "100cm(13-18kg)"]
    storage.update_store_template(template["id"], {"sizes": reordered})
    assert sizes_of(storage.list_listing_skus(copy["id"]), "White") == reordered
    assert sizes_of(storage.get_store_listing_copy_detail(copy["id"])["skus"], "White") == reordered


def test_size_order_rank_is_site_only_and_appends_unknown() -> None:
    """跨境返回 None（沿用旧排序，跨境零改动）；本土给出模板顺序秩，
    模板里没有的尺码排在已知尺码之后。"""
    assert storage._listing_size_order_rank({"listing_mode": "cnsc_global"}, ["90cm", "100cm"]) is None
    rank = storage._listing_size_order_rank(
        {"listing_mode": "site", "template_id": "", "sizes": ["90cm(10-12kg)", "100cm(13-18kg)"]},
        ["100cm(13-18kg)", "90cm(10-12kg)", "80cm"],
    )
    assert rank == {"90cm(10-12kg)": 0, "100cm(13-18kg)": 1, "80cm": 2}
