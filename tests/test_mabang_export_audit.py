from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from services.api import storage
from services.api.mabang_export import export_mabang_products


def _sample_bundle() -> dict:
    product = {
        "id": "prod-audit-1",
        "category": "POD 男装T恤",
        "platform": "shopee",
        "store": "demo-store",
        "group_id": "DL0001",
        "brand": "",
        "base_name": "瓒呭ぉ閰?鎴愪汉绾鐢疯T鎭?",
        "product_name_cn": "瓒呭ぉ閰?鎴愪汉绾鐢疯T鎭?",
        "product_name_en": "adult cotton t-shirt",
        "listing_title": "瓒呭ぉ閰?鎴愪汉绾鐢疯T鎭? DL0001",
        "description": "POD 娴嬭瘯鍟嗗搧銆?",
        "template_id": "pod-mens-tee-v1",
        "weight_kg": 0.25,
        "length_cm": 28,
        "width_cm": 22,
        "height_cm": 2,
        "asset_root": r"\\NAS\products\DL0001",
        "main_image": "涓诲浘1.jpg",
        "status": "draft",
    }
    assets = [
        {
            "asset_type": "main",
            "file_path": r"\\NAS\products\DL0001\main\涓诲浘1.jpg",
            "file_name": "涓诲浘1.jpg",
            "group_id": "DL0001",
            "sku_option": "",
            "status": "ready",
        },
        {
            "asset_type": "variant",
            "file_path": r"\\NAS\products\DL0001\variant\鐧借壊.jpg",
            "file_name": "鐧借壊.jpg",
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
            "color_cn": "鐧借壊",
            "size": "S",
            "variant_name": "鐧借壊/S",
            "stock": 10,
            "weight_kg": 0.25,
            "length_cm": 28,
            "width_cm": 22,
            "height_cm": 2,
            "variant_image": "鐧借壊.jpg",
            "status": "active",
        },
        {
            "sku_code": "DL0001-WHITE-M",
            "group_id": "DL0001",
            "color_code": "white",
            "color_cn": "鐧借壊",
            "size": "M",
            "variant_name": "鐧借壊/M",
            "stock": 12,
            "weight_kg": 0.25,
            "length_cm": 28,
            "width_cm": 22,
            "height_cm": 2,
            "variant_image": "鐧借壊.jpg",
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


def test_export_writes_resolved_optional_fields_and_template_fallbacks() -> None:
    bundle = _sample_bundle()
    bundle["product"] = {
        **bundle["product"],
        "product_name_en": "",
        "description": "",
        "bullet_point": "",
        "search_terms": "",
        "declaration_name_cn": "",
        "declaration_name_en": "",
    }

    result = export_mabang_products([bundle], save_copy=False)

    assert result["ok"] is True
    wb = load_workbook(BytesIO(result["xlsx_bytes"]))
    list_sheet = wb[wb.sheetnames[0]]
    assert list_sheet.cell(2, 3).value == bundle["product"]["listing_title"]
    assert list_sheet.cell(2, 9).value == 28
    assert list_sheet.cell(2, 11).value == 250
    assert list_sheet.cell(2, 17).value == 28
    assert list_sheet.cell(2, 18).value == 22
    assert list_sheet.cell(2, 19).value == 2
    assert list_sheet.cell(2, 27).value
    assert list_sheet.cell(2, 28).value
    assert list_sheet.cell(2, 29).value == bundle["product"]["product_name_cn"]
    assert list_sheet.cell(2, 30).value == bundle["product"]["listing_title"]
    assert list_sheet.cell(2, 52).value == 28
    assert list_sheet.cell(2, 53).value == 250
    assert list_sheet.cell(2, 55).value == 28
    assert list_sheet.cell(2, 56).value == 22
    assert list_sheet.cell(2, 57).value == 2


def test_export_creates_mabang_audit_output() -> None:
    bundle = _sample_bundle()
    product_id = bundle["product"]["id"]
    before_ids = {
        item["id"]
        for item in storage.list_ai_outputs_for_product(product_id, "mabang_export_audit")
    }

    result = export_mabang_products([bundle], save_copy=False)

    assert result["ok"] is True
    outputs = storage.list_ai_outputs_for_product(product_id, "mabang_export_audit")
    new_outputs = [item for item in outputs if item["id"] not in before_ids]
    assert len(new_outputs) == 1
    audit = new_outputs[0]
    payload = audit["payload_json"]
    assert audit["status"] == "suggested"
    assert payload["suggestion_only"] is True
    assert payload["auto_apply"] is False
    assert payload["group_id"] == "DL0001"
    assert payload["bundle_count"] == 1
    assert payload["row_count"] == 2
    assert isinstance(payload["filled_fields"], list)
    assert isinstance(payload["missing_fields"], list)
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["blockers"], list)
    assert isinstance(payload["resolved_sources"], dict)
    assert "product_name_en" in payload["resolved_sources"]
    assert "main_price" in payload["resolved_sources"]
