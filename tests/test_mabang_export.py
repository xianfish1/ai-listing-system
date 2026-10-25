from __future__ import annotations

from io import BytesIO
from pathlib import Path
import shutil

from openpyxl import load_workbook
import pytest

from services.api import settings
from services.api import storage
from services.api.main import ApiHandler
from services.api.mabang_export import export_mabang_products, validate_mabang_bundles


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_TEMPLATE = PROJECT_ROOT / "storage" / "samples" / "mabang-template.xlsx"


def _template_list_headers() -> list[str]:
    template_wb = load_workbook(LOCAL_TEMPLATE, read_only=True)
    sheet = template_wb["产品导入列表"]
    return [sheet.cell(1, col).value for col in range(1, 63)]


def _sample_bundle() -> dict:
    product = {
        "id": "prod-1",
        "category": "POD 男装T恤",
        "platform": "shopee",
        "store": "demo-store",
        "group_id": "DL0001",
        "brand": "",
        "base_name": "超天酱 成人纯棉男装T恤",
        "product_name_cn": "超天酱 成人纯棉男装T恤",
        "product_name_en": "adult cotton t-shirt",
        "listing_title": "超天酱 成人纯棉男装T恤 DL0001",
        "description": "POD 测试商品。",
        "template_id": "pod-mens-tee-v1",
        "weight_kg": 0.25,
        "length_cm": 28,
        "width_cm": 22,
        "height_cm": 2,
        "asset_root": r"\\NAS\products\DL0001",
        "main_image": "主图1.jpg",
        "status": "draft",
    }
    assets = [
        {
            "asset_type": "main",
            "file_path": r"\\NAS\products\DL0001\main\主图1.jpg",
            "file_name": "主图1.jpg",
            "group_id": "DL0001",
            "sku_option": "",
            "status": "ready",
        },
        {
            "asset_type": "variant",
            "file_path": r"\\NAS\products\DL0001\variant\白色.jpg",
            "file_name": "白色.jpg",
            "group_id": "DL0001",
            "sku_option": "white",
            "status": "ready",
        },
    ]
    skus = [
        {
            "sku_code": "DL0001-WHITE-S",
            "group_id": "DL0001",
            "color_code": "white",
            "color_cn": "白色",
            "size": "S",
            "variant_name": "白色/S",
            "stock": 10,
            "weight_kg": 0.25,
            "length_cm": 28,
            "width_cm": 22,
            "height_cm": 2,
            "variant_image": "白色.jpg",
            "status": "active",
        },
        {
            "sku_code": "DL0001-WHITE-M",
            "group_id": "DL0001",
            "color_code": "white",
            "color_cn": "白色",
            "size": "M",
            "variant_name": "白色/M",
            "stock": 12,
            "weight_kg": 0.25,
            "length_cm": 28,
            "width_cm": 22,
            "height_cm": 2,
            "variant_image": "白色.jpg",
            "status": "active",
        },
    ]
    prices = [
        {
            "sku_code": "DL0001-WHITE-S",
            "platform": "shopee",
            "store": "demo-store",
            "country": "MY",
            "currency": "MYR",
            "cost_price": 28,
            "shipping_cost": 12,
            "fee_rate": 0.08,
            "margin_rate": 0.35,
            "normal_price": 89,
            "sale_price": 69,
            "promo_price": "",
            "status": "active",
        },
        {
            "sku_code": "DL0001-WHITE-M",
            "platform": "shopee",
            "store": "demo-store",
            "country": "MY",
            "currency": "MYR",
            "cost_price": 28,
            "shipping_cost": 12,
            "fee_rate": 0.08,
            "margin_rate": 0.35,
            "normal_price": 89,
            "sale_price": 69,
            "promo_price": "",
            "status": "active",
        },
    ]
    return {"product": product, "assets": assets, "skus": skus, "prices": prices}


def test_validate_mabang_export_blocks_missing_main_image_and_directory_mapping() -> None:
    bundle = _sample_bundle()
    bundle["product"] = {**bundle["product"], "category": "未知类目", "main_image": ""}

    validation = validate_mabang_bundles([bundle])

    assert validation["valid"] is False
    assert {item["code"] for item in validation["errors"]} >= {
        "missing_directory_mapping",
        "missing_main_image",
    }
    assert {item["code"] for item in validation["blockers"]} >= {
        "missing_directory_mapping",
        "missing_main_image",
    }


def test_validate_mabang_export_blocks_missing_core_price_weight_and_dimensions() -> None:
    bundle = _sample_bundle()
    bundle["product"] = {
        **bundle["product"],
        "category": "未知类目",
        "weight_kg": "",
        "length_cm": "",
        "width_cm": "",
        "height_cm": "",
    }
    bundle["skus"] = [
        {**sku, "weight_kg": "", "length_cm": "", "width_cm": "", "height_cm": ""}
        for sku in bundle["skus"]
    ]
    bundle["prices"] = [{**price, "cost_price": ""} for price in bundle["prices"]]

    validation = validate_mabang_bundles([bundle])

    assert validation["valid"] is False
    assert {item["code"] for item in validation["blockers"]} >= {
        "missing_cost_price",
        "missing_weight",
        "missing_dimensions",
    }


def test_validate_mabang_export_reports_optional_missing_fields_without_blocking() -> None:
    bundle = _sample_bundle()
    bundle["product"] = {
        **bundle["product"],
        "product_name_jp": "",
        "competitor_title": "",
        "supplier_name": "",
    }

    validation = validate_mabang_bundles([bundle])

    assert validation["valid"] is True
    assert validation["row_count"] == 2
    assert "product_name_jp" in validation["missing_fields"]
    assert "competitor_title" in validation["missing_fields"]
    assert "group_id" in validation["filled_fields"]
    assert "main_image" in validation["filled_fields"]


def test_mabang_preview_payload_exposes_readiness_details() -> None:
    bundle = _sample_bundle()
    payload = ApiHandler._mabang_preview_payload(None, [bundle])

    assert payload["can_export"] is True
    assert payload["row_count"] == 2
    assert payload["blockers"] == []
    assert isinstance(payload["warnings"], list)
    assert payload["items"][0]["filled_fields"] >= 1
    assert isinstance(payload["items"][0]["missing_fields"], list)


def test_export_preserves_template_and_writes_nas_paths() -> None:
    bundle = _sample_bundle()

    result = export_mabang_products([bundle], save_copy=False)

    assert result["ok"] is True
    assert result["filename"].endswith(".xlsx")
    wb = load_workbook(BytesIO(result["xlsx_bytes"]))
    template_wb = load_workbook(LOCAL_TEMPLATE)
    assert wb.sheetnames == template_wb.sheetnames

    list_sheet = wb["产品导入列表"]
    headers = [list_sheet.cell(1, c).value for c in range(1, 63)]
    assert headers == _template_list_headers()
    assert list_sheet.cell(2, 1).value == "DL0001"
    assert list_sheet.cell(2, 2).value == "超天酱 成人纯棉男装T恤"
    assert list_sheet.cell(2, 6).value == "clothing"
    assert str(list_sheet.cell(2, 20).value).startswith(r"\\NAS\products\DL0001")
    assert str(list_sheet.cell(2, 21).value).startswith(r"\\NAS\products\DL0001")
    assert list_sheet.cell(2, 46).value == "DL0001-WHITE-S"
    assert list_sheet.cell(3, 46).value == "DL0001-WHITE-M"
    assert str(list_sheet.cell(2, 49).value).startswith(r"\\NAS\products\DL0001")

    assert list(wb["产品导入示例"].merged_cells.ranges) == list(template_wb["产品导入示例"].merged_cells.ranges)
    assert list(wb["字段说明"].merged_cells.ranges) == list(template_wb["字段说明"].merged_cells.ranges)


def test_export_rejects_template_with_mismatched_list_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_template = tmp_path / "broken-mabang-template.xlsx"
    shutil.copy2(LOCAL_TEMPLATE, broken_template)
    workbook = load_workbook(broken_template)
    workbook["产品导入列表"].cell(1, 1).value = "主SKU"
    workbook.save(broken_template)
    monkeypatch.setattr(settings, "MABANG_TEMPLATE_PATH", broken_template)

    with pytest.raises(ValueError, match="产品导入列表"):
        export_mabang_products([_sample_bundle()], save_copy=False)


def test_export_writes_real_unc_image_paths_without_placeholders() -> None:
    real_root = r"\\Juns\产品编辑部\一步履\POD款式\DL  23#成人纯棉\DL0001超天酱\超天酱-1"
    bundle = _sample_bundle()
    bundle["product"] = {
        **bundle["product"],
        "asset_root": real_root,
        "main_image": "主图1.jpg",
    }
    bundle["assets"] = [
        {
            **asset,
            "file_path": str(Path(real_root) / ("主图1.jpg" if asset["asset_type"] == "main" else "白色超天酱1.jpg")),
            "file_name": "主图1.jpg" if asset["asset_type"] == "main" else "白色超天酱1.jpg",
            "sku_option": "white" if asset["asset_type"] == "variant" else "",
        }
        for asset in bundle["assets"]
    ]
    bundle["skus"] = [
        {**sku, "variant_image": "白色超天酱1.jpg"}
        for sku in bundle["skus"]
    ]

    result = export_mabang_products([bundle], save_copy=False)

    wb = load_workbook(BytesIO(result["xlsx_bytes"]))
    sheet = wb["产品导入列表"]
    path_cells = [sheet.cell(2, col).value for col in [20, 21, 23, 49]]
    for value in path_cells:
        text = str(value or "")
        assert text.startswith(r"\\Juns\\") or text.startswith(r"\\Juns")
        assert "sample_assets" not in text
        assert r"\\NAS\products" not in text
