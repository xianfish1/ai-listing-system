from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import load_workbook

from services.api import mabang_export, storage
from services.api.mabang_export import export_mabang_products


def _sample_bundle() -> dict:
    product = {
        "id": "prod-resilient-1",
        "category": "POD 男装T恤",
        "platform": "shopee",
        "store": "demo-store",
        "group_id": "DL9999",
        "brand": "",
        "base_name": "韧性测试商品",
        "product_name_cn": "韧性测试商品",
        "product_name_en": "resilient test product",
        "listing_title": "韧性测试商品 DL9999",
        "description": "POD resilience test.",
        "template_id": "pod-mens-tee-v1",
        "weight_kg": 0.25,
        "length_cm": 28,
        "width_cm": 22,
        "height_cm": 2,
        "asset_root": r"\\NAS\products\DL9999",
        "main_image": "主图1.jpg",
        "status": "draft",
    }
    assets = [
        {
            "asset_type": "main",
            "file_path": r"\\NAS\products\DL9999\main\主图1.jpg",
            "file_name": "主图1.jpg",
            "group_id": "DL9999",
            "sku_option": "",
            "status": "ready",
        },
        {
            "asset_type": "variant",
            "file_path": r"\\NAS\products\DL9999\variant\白色.jpg",
            "file_name": "白色.jpg",
            "group_id": "DL9999",
            "sku_option": "white",
            "status": "ready",
        },
    ]
    skus = [
        {
            "sku_code": "DL9999-WHITE-S",
            "group_id": "DL9999",
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
    ]
    prices = [
        {
            "sku_code": "DL9999-WHITE-S",
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


def test_export_keeps_xlsx_bytes_when_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _sample_bundle()

    def broken_audit(*args, **kwargs):
        raise RuntimeError("simulated audit store write failure")

    monkeypatch.setattr(mabang_export, "_record_mabang_export_audits", broken_audit)

    result = export_mabang_products([bundle], save_copy=False)

    assert result["ok"] is True
    assert result["xlsx_bytes"], "xlsx_bytes should still be populated when audit fails"
    assert "audit_error" in result
    assert "simulated audit store write failure" in result["audit_error"]
    assert result.get("audit_outputs") == []
    wb = load_workbook(BytesIO(result["xlsx_bytes"]))
    assert "产品导入列表" in wb.sheetnames
