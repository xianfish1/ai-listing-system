"""Excel 在线商品导出 → 导入计划的解析规则。

这些测试只覆盖解析（不发网络、不写库）：真实 42 列表头、列顺序变化、
必需列缺失、同 SKU 多店后缀、非标颜色原样保留、尺码归一化等。
"""
from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook

from services.api.excel_product_import import (
    ExcelImportError,
    apply_existing_skus,
    parse_export_workbook,
    preview_payload,
    safe_file_stem,
)

REAL_HEADERS = [
    "分类ID", "一级分类", "二级分类", "三级分类", "四级分类", "五级分类",
    "店铺名称", "品牌", "在线链接", "分类属性", "产品名称", "产品描述",
    "Parent SKU", "product id", "属性名称", "属性值1", "属性值2", "子sku",
    "model_id", "原价", "售价", "库存", "重量（kg）", "长（cm）", "宽（cm）",
    "高（cm）", "出货天数", "图片1（主图）", "图片2", "图片3", "图片4",
    "图片5", "图片6", "图片7", "图片8", "图片9", "变种图片", "尺码图",
    "来源URL", "创建时间", "创建人", "全球商品ID",
]


def _workbook_bytes(rows: list[dict], headers: list[str] = REAL_HEADERS) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _row(
    *,
    sku: str,
    product_id: str = "1001",
    color: str,
    size: str = "",
    variant_image: str = "",
    store: str = "店A",
    title: str = "Kids Tee",
    gallery: int = 9,
    **extra,
) -> dict:
    row = {
        "分类ID": "101773",
        "一级分类": "Baby & Kids Fashion(婴儿和儿童时尚)",
        "二级分类": "Girl Clothes(女孩服装)",
        "三级分类": "Tops(上衣)",
        "四级分类": "T-shirts(T恤)",
        "店铺名称": store,
        "在线链接": f"https://example.test/product/{product_id}",
        "产品名称": title,
        "产品描述": "source description",
        "Parent SKU": sku,
        "product id": product_id,
        "属性值1": color,
        "属性值2": size,
        "子sku": f"{sku}-{color}-{size}",
        "原价": 432,
        "售价": 259,
        "库存": 10,
        "重量（kg）": 0.11,
        "长（cm）": 37,
        "宽（cm）": 24,
        "高（cm）": 3,
        "图片1（主图）": f"https://cdn.test/{sku}/main-1.jpg",
        "变种图片": variant_image or f"https://cdn.test/{sku}/{color}.jpg",
    }
    for index in range(2, gallery + 1):
        row[f"图片{index}"] = f"https://cdn.test/{sku}/main-{index}.jpg"
    row.update(extra)
    return row


def test_parses_real_headers_and_merges_pure_color_alias():
    content = _workbook_bytes([
        _row(sku="JB0034", color="PureWhite", size="100cm(2–3 Y)"),
        _row(sku="JB0034", color="White", size="90cm(1–2 Y)"),
        _row(sku="JB0034", color="PureWhite", size="90cm(1–2 Y)"),
    ])

    plan = parse_export_workbook(content)
    assert len(plan["products"]) == 1
    product = plan["products"][0]
    assert product["main_sku"] == "JB0034"
    # PureWhite is an alias of White: one colour entry, using White's own photo.
    assert product["colors"] == ["White"]
    assert set(product["variant_images"]) == {"White"}
    assert product["variant_images"]["White"]["url"].endswith("/White.jpg")
    assert any("PureWhite" in warning and "White" in warning for warning in product["warnings"])
    # Sizes are normalized and numerically ordered.
    assert product["sizes"] == ["90cm", "100cm"]
    assert product["title"] == "Kids Tee"
    assert len(product["gallery"]) == 9
    # Excel description/attributes stay in the plan only, never in the preview body.
    view = preview_payload(plan)
    assert "description" not in view["products"][0]
    assert view["products"][0]["source_meta"]["attribute_count"] == 0


def test_pure_alias_spelling_variants_merge_into_one_color():
    content = _workbook_bytes([
        _row(sku="A1", color="Pure WHite", size="M"),
        _row(sku="A1", color="White", size="L"),
        _row(sku="A1", color="Purewhite", size="S"),
    ])
    product = parse_export_workbook(content)["products"][0]
    assert product["colors"] == ["White"]
    assert list(product["variant_images"]) == ["White"]


def test_column_order_and_title_row_are_tolerated():
    headers = list(reversed(REAL_HEADERS))
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["马帮在线商品导出"])
    sheet.append(headers)
    sheet.append([_row(sku="VZ930", color="Red", size="100cm").get(header) for header in headers])
    buffer = BytesIO()
    workbook.save(buffer)

    plan = parse_export_workbook(buffer.getvalue())
    assert plan["products"][0]["main_sku"] == "VZ930"


def test_missing_required_column_raises():
    headers = [header for header in REAL_HEADERS if header != "变种图片"]
    content = _workbook_bytes(
        [_row(sku="A1", color="Red", variant_image="https://cdn.test/x.jpg")],
        headers=headers,
    )
    with pytest.raises(ExcelImportError, match="变种图片"):
        parse_export_workbook(content)


def test_same_parent_sku_in_two_stores_gets_suffix():
    content = _workbook_bytes([
        _row(sku="C00940-3", product_id="28903768933", color="A Set", store="店A"),
        _row(sku="C00940-3", product_id="29482580072", color="B Set", store="店B"),
    ])
    plan = parse_export_workbook(content)
    assert len(plan["products"]) == 2

    apply_existing_skus(plan["products"], set())
    assert [product["main_sku"] for product in plan["products"]] == ["C00940-3", "C00940-3-2"]
    assert [product["existing"] for product in plan["products"]] == [False, False]

    # A previously imported SKU is flagged instead of being suffixed away.
    apply_existing_skus(plan["products"], {"c00940-3"})
    assert plan["products"][0]["existing"] is True
    assert plan["products"][1]["main_sku"] == "C00940-3-2"


def test_color_without_variant_image_is_dropped_with_warning():
    rows = [
        _row(sku="A1", color="Red", size="M"),
        _row(sku="A1", color="Blue", size="M", variant_image=""),
    ]
    # An empty 变种图片 column means the parser sees no URL for Blue.
    rows[1]["变种图片"] = ""
    plan = parse_export_workbook(_workbook_bytes(rows))
    product = plan["products"][0]
    assert product["colors"] == ["Red"]
    assert any("Blue" in warning for warning in product["warnings"])


def test_blank_color_rows_are_skipped():
    rows = [
        _row(sku="A1", color="Red", size="M"),
        _row(sku="A1", color="", size="L"),
    ]
    plan = parse_export_workbook(_workbook_bytes(rows))
    assert plan["products"][0]["colors"] == ["Red"]
    assert any("没有颜色" in warning for warning in plan["products"][0]["warnings"])


def test_nonstandard_colors_get_safe_unique_file_stems():
    rows = [
        _row(sku="A1", color="A/B"),
        _row(sku="A1", color="A_B"),
    ]
    plan = parse_export_workbook(_workbook_bytes(rows))
    stems = [entry["file_stem"] for entry in plan["products"][0]["variant_images"].values()]
    assert stems[0] == "颜色-A_B"
    assert stems[1] == "颜色-A_B-2"
    assert safe_file_stem("5Pcs(+Toys)", "x") == "5Pcs(+Toys)"


def test_workbook_without_importable_products_raises():
    row = _row(sku="A1", color="Red", gallery=0)
    row["图片1（主图）"] = ""
    content = _workbook_bytes([row])
    with pytest.raises(ExcelImportError, match="没有可导入的商品"):
        parse_export_workbook(content)
