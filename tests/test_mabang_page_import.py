from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from services.api.mabang_page_import import MabangPageImportError, confirm_page_import, stage_page_import


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mabang_page_capture_pod_ph.json"
WATERMARK_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mabang_page_capture_watermarks.json"
CNSC_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mabang_page_capture_cnsc.json"
BUSINESS_COLLECTIONS = (
    "mabang_store_options",
    "category_profiles",
    "category_profile_versions",
    "store_promotion_activities",
    "store_templates",
    "store_template_versions",
)


def _capture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _cnsc_capture() -> dict:
    return json.loads(CNSC_FIXTURE_PATH.read_text(encoding="utf-8"))


def _store() -> dict:
    return {
        "mabang_store_options": [],
        "category_profiles": [],
        "category_profile_versions": [],
        "category_collection_runs": [],
        "store_promotion_activities": [
            {"id": "other-1", "activity_id": "other-1", "store_id": "other-store", "status": "available"}
        ],
        "store_watermark_templates": [],
        "store_templates": [],
        "store_template_versions": [],
        "mabang_page_import_runs": [
            {
                "id": "run-1",
                "status": "pending",
                "platform": "shopee",
                "country": "PH",
                "conflict_action": "update",
                "created_at": "2026-07-17T00:00:00Z",
                "updated_at": "2026-07-17T00:00:00Z",
            }
        ],
    }


def test_stage_page_import_updates_only_the_run() -> None:
    data = _store()
    before = deepcopy(data)

    run = stage_page_import(data, "run-1", _capture(), now="2026-07-17T00:01:00Z")

    assert run["status"] == "collected_with_warnings"
    assert run["snapshot"]["store"]["id"] == "2022625115"
    assert run["snapshot"]["page"]["url"] == "https://www.mabangerp.com/index.php"
    for collection in BUSINESS_COLLECTIONS:
        assert data[collection] == before[collection]


def test_stage_page_import_replaces_an_unconfirmed_snapshot() -> None:
    data = _store()
    first = stage_page_import(data, "run-1", _capture(), now="2026-07-17T00:01:00Z")
    capture = _capture()
    capture["sku_rows"][0]["size"] = "XS"

    second = stage_page_import(data, "run-1", capture, now="2026-07-17T00:02:00Z")

    assert first["snapshot"]["variation"]["sizes"][0] == "S"
    assert second["snapshot"]["variation"]["sizes"][0] == "XS"
    assert second["status"] == "collected_with_warnings"


def test_stage_page_import_rejects_completed_run_without_mutating_data() -> None:
    data = _store()
    data["mabang_page_import_runs"][0]["status"] = "completed"
    before = deepcopy(data)

    with pytest.raises(MabangPageImportError, match="已确认"):
        stage_page_import(data, "run-1", _capture(), now="2026-07-17T00:01:00Z")

    assert data == before


def test_confirm_page_import_writes_store_sizes_selected_values_atomically() -> None:
    data = _store()
    capture = _capture()
    for row in capture["sku_rows"]:
        row["normal_price"] = "89"
        row["sale_price"] = "69"
    stage_page_import(data, "run-1", capture, now="2026-07-17T00:01:00Z")

    result = confirm_page_import(data, "run-1", now="2026-07-17T00:02:00Z")

    template = result["template"]
    assert template["mabang_store_id"] == "2022625115"
    assert template["sizes"] == ["S", "M", "L", "XL", "2XL"]
    assert template["attribute_values"] == {
        "100134": ["1149"],
        "100162": ["1486"],
        "100168": "1500",
        "101004": "5487",
    }
    assert template["weight_kg"] == ""
    assert template["normal_price"] == 89.0
    assert template["sale_price"] == 69.0
    assert template["promotion_activity_id"] == ""
    assert data["mabang_store_options"][0]["mabang_store_id"] == "2022625115"
    assert data["mabang_store_options"][0]["store_name"] == "POD菲律宾"
    assert data["category_profile_versions"][0]["status"] == "active"
    assert data["store_promotion_activities"] == _store()["store_promotion_activities"]
    assert result["run"]["status"] == "completed"


def test_confirm_page_import_keeps_listing_importable_when_remote_size_chart_cannot_download() -> None:
    data = _store()
    capture = _capture()
    capture["size_chart"] = {"url": "https://ae01.alicdn.com/kf/expired-size-chart.jpg"}
    stage_page_import(data, "run-1", capture, now="2026-07-17T00:01:00Z")

    result = confirm_page_import(data, "run-1", now="2026-07-17T00:02:00Z")

    assert result["run"]["status"] == "completed"
    assert result["run"]["import_warnings"]
    assert result["template"]["size_chart_asset_id"] == ""


def test_confirm_native_import_uses_majority_sku_price_for_template_default() -> None:
    # Real native (site) page (2026-09-04): 54 SKUs, 53 share 578/219.64 but the
    # No-printWhite 100cm row is 450/171. Confirm must produce a template with the
    # majority default price instead of silently blanking 原价/售价.
    data = _store()
    capture = _capture()
    for row in capture["sku_rows"]:
        row["normal_price"] = "578"
        row["sale_price"] = "219.64"
    capture["sku_rows"][0]["normal_price"] = "450"
    capture["sku_rows"][0]["sale_price"] = "171"
    stage_page_import(data, "run-1", capture, now="2026-07-17T00:01:00Z")

    result = confirm_page_import(data, "run-1", now="2026-07-17T00:02:00Z")

    template = result["template"]
    assert template["normal_price"] == 578.0
    assert template["sale_price"] == 219.64
    snapshot = result["run"]["snapshot"]
    assert snapshot["price_defaults"]["normal_price"]["uniform"] is False
    assert snapshot["price_defaults"]["normal_price"]["differing_count"] == 1


def test_confirm_page_import_imports_a_captured_size_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _store()
    capture = _capture()
    capture["size_chart"] = {"url": "https://publish-cos.mabangerp.com/chart.png"}
    stage_page_import(data, "run-1", capture, now="2026-07-17T00:01:00Z")
    monkeypatch.setattr(
        "services.api.mabang_page_import.import_mabang_size_chart",
        lambda url, storage_dir: {
            "id": "asset-1",
            "preview_url": "/api/uploads/size-chart/asset-1",
            "file_path": "C:/data/asset-1.png",
        },
    )

    result = confirm_page_import(data, "run-1", now="2026-07-17T00:02:00Z")

    assert result["template"]["size_chart_asset_id"] == "asset-1"
    assert result["template"]["size_chart_preview_url"] == "/api/uploads/size-chart/asset-1"
    assert result["template"]["default_size_chart_path"] == "C:/data/asset-1.png"
    assert result["template"]["size_chart_source"] == "upload"
    assert result["template"]["mabang_size_chart_template_id"] == ""


def test_confirm_page_import_imports_the_captured_size_chart_template_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _store()
    capture = _capture()
    capture["size_chart_template_catalog_loaded"] = True
    capture["size_chart_templates"] = [
        {"template_id": "chart-1", "name": "男装尺码表", "preview_url": "https://publish-cos.mabangerp.com/chart-1.png"},
        {"template_id": "chart-2", "name": "童装尺码表", "preview_url": "https://publish-cos.mabangerp.com/chart-2.png"},
    ]
    stage_page_import(data, "run-1", capture, now="2026-07-22T00:01:00Z")
    monkeypatch.setattr(
        "services.api.mabang_page_import.import_mabang_size_chart",
        lambda url, storage_dir: {
            "id": "asset-" + url.rsplit("-", 1)[-1].split(".")[0],
            "preview_url": "/api/uploads/size-chart/asset",
            "file_path": "C:/data/asset.png",
        },
    )

    confirm_page_import(data, "run-1", now="2026-07-22T00:02:00Z")

    assert [(item["template_id"], item["asset_id"]) for item in data["store_size_chart_templates"]] == [
        ("chart-1", "asset-1"),
        ("chart-2", "asset-2"),
    ]


def test_confirm_page_import_keeps_size_chart_template_without_a_preview_url() -> None:
    data = _store()
    capture = _capture()
    capture["size_chart_template_catalog_loaded"] = True
    capture["size_chart_templates"] = [{
        "template_id": "106239228",
        "name": "男装T恤",
        "preview_url": "",
    }]
    stage_page_import(data, "run-1", capture, now="2026-07-22T00:01:00Z")

    confirm_page_import(data, "run-1", now="2026-07-22T00:02:00Z")

    assert [(item["template_id"], item["name"], item["asset_id"], item["file_path"])
            for item in data["store_size_chart_templates"]] == [
        ("106239228", "男装T恤", "", ""),
    ]


def test_confirm_page_import_replaces_the_loaded_store_watermark_catalog() -> None:
    data = _store()
    capture = json.loads(WATERMARK_FIXTURE_PATH.read_text(encoding="utf-8"))
    stage_page_import(data, "run-1", capture, now="2026-07-17T00:01:00Z")

    confirm_page_import(data, "run-1", now="2026-07-17T00:02:00Z")

    assert [(item["template_id"], item["name"]) for item in data["store_watermark_templates"]] == [
        ("wm-product", "podQ-7"),
        ("wm-main", "主图水印"),
    ]
    assert {item["store_id"] for item in data["store_watermark_templates"]} == {"store-ph"}


def test_confirm_page_import_preserves_watermarks_when_catalog_was_not_loaded() -> None:
    data = _store()
    data["store_watermark_templates"] = [
        {"id": "wm-local", "store_id": "2022625115", "template_id": "wm-old", "name": "已有水印"}
    ]
    stage_page_import(data, "run-1", _capture(), now="2026-07-17T00:01:00Z")

    confirm_page_import(data, "run-1", now="2026-07-17T00:02:00Z")

    assert data["store_watermark_templates"][0]["template_id"] == "wm-old"


def test_confirm_page_import_is_idempotent_for_a_completed_run() -> None:
    data = _store()
    stage_page_import(data, "run-1", _capture(), now="2026-07-17T00:01:00Z")
    first = confirm_page_import(data, "run-1", now="2026-07-17T00:02:00Z")
    counts = {key: len(data[key]) for key in BUSINESS_COLLECTIONS}

    second = confirm_page_import(data, "run-1", now="2026-07-17T00:03:00Z")

    assert second["template"]["id"] == first["template"]["id"]
    assert {key: len(data[key]) for key in BUSINESS_COLLECTIONS} == counts


def test_confirm_page_import_leaves_input_unchanged_when_rule_creation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _store()
    stage_page_import(data, "run-1", _capture(), now="2026-07-17T00:01:00Z")
    before = deepcopy(data)

    def fail_rule_creation(*args, **kwargs):
        raise RuntimeError("rule creation failed")

    monkeypatch.setattr("services.api.mabang_page_import.create_candidate_from_capture", fail_rule_creation)

    with pytest.raises(RuntimeError, match="rule creation failed"):
        confirm_page_import(data, "run-1", now="2026-07-17T00:02:00Z")

    assert data == before


def test_confirm_cnsc_import_creates_a_distinct_needs_configuration_template() -> None:
    data = _store()
    data["store_templates"] = [{
        "id": "site-template",
        "listing_mode": "site",
        "mabang_store_id": "cnsc-store-1",
        "mabang_category_id": "100244",
        "status": "active",
        "version": 1,
    }]
    stage_page_import(data, "run-1", _cnsc_capture(), now="2026-08-20T00:01:00Z")

    result = confirm_page_import(data, "run-1", now="2026-08-20T00:02:00Z")

    template = result["template"]
    assert len(data["store_templates"]) == 2
    assert template["id"] != "site-template"
    assert template["listing_mode"] == "cnsc_global"
    assert template["cnsc_store"] == {"id": "cnsc-store-1", "name": "广州小苗服饰有限公司"}
    assert template["status"] == "needs_configuration"
    assert template["missing_configuration"] == ["target_site_stores", "site_price_matrix"]
    assert template["warehouse_stock_defaults"] == {"TH": 300, "CN_GZ": 0, "PH": 0}
    assert len(template["cnsc_import_sku_rows"]) == 28
    assert result["promotions"] == []


def test_confirm_cnsc_import_registers_all_eight_target_store_options() -> None:
    data = _store()
    capture = _cnsc_capture()
    capture["target_site_stores_loaded"] = True
    capture["target_site_stores"] = [
        {"site_code": code, "store_id": f"{code.lower()}-store", "store_name": f"海宁{code}", "discount_catalog_loaded": False}
        for code in ("TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR")
    ]
    stage_page_import(data, "run-1", capture, now="2026-08-20T00:01:00Z")

    result = confirm_page_import(data, "run-1", now="2026-08-20T00:02:00Z")

    assert result["template"]["target_site_stores"]
    target_codes = {item["country"] for item in data["mabang_store_options"] if item["country"] != "GLOBAL"}
    assert target_codes == {"TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR"}


def test_confirm_cnsc_import_preserves_configuration_that_the_page_did_not_load() -> None:
    data = _store()
    data["store_templates"] = [{
        "id": "cnsc-template",
        "listing_mode": "cnsc_global",
        "mabang_store_id": "cnsc-store-1",
        "mabang_category_id": "100244",
        "default_site_discount_percent": 40,
        "target_site_stores": [{
            "site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH",
            "mabang_discount_id": "discount-ph", "discount_percent": 40,
        }],
        "price_tiers": [
            {"id": "standard", "sizes": ["XS", "S", "M", "L"], "global_price_cny": 18, "site_prices": {"PH": {"sale_price": 746, "original_price": 1243}}},
            {"id": "large", "sizes": ["XL", "XXL", "XXXL"], "global_price_cny": 21, "site_prices": {"PH": {"sale_price": 811, "original_price": 1352}}},
        ],
        "warehouse_stock_defaults": {"TH": 100, "CN_GZ": 1, "PH": 2},
        "status": "active",
        "version": 3,
        "created_at": "2026-08-01T00:00:00Z",
    }]
    stage_page_import(data, "run-1", _cnsc_capture(), now="2026-08-20T00:01:00Z")

    template = confirm_page_import(data, "run-1", now="2026-08-20T00:02:00Z")["template"]

    assert template["id"] == "cnsc-template"
    assert template["version"] == 4
    assert template["target_site_stores"][0]["store_id"] == "ph-store"
    assert template["target_site_stores"][0]["mabang_discount_id"] == "discount-ph"
    assert template["price_tiers"][0] == {
        "id": "standard",
        "sizes": ["XS", "S", "M", "L"],
        "global_price_cny": 19.2,
        "site_prices": {"PH": {"sale_price": 746, "original_price": 1243}},
    }
    assert template["warehouse_stock_defaults"] == {"TH": 300, "CN_GZ": 0, "PH": 0}
    assert template["status"] == "active"
    assert template["missing_configuration"] == []


def test_confirm_cnsc_import_does_not_hide_a_new_warehouse_conflict_with_old_defaults() -> None:
    data = _store()
    data["store_templates"] = [{
        "id": "cnsc-template",
        "listing_mode": "cnsc_global",
        "mabang_store_id": "cnsc-store-1",
        "mabang_category_id": "100244",
        "target_site_stores": [{"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}],
        "price_tiers": [
            {"id": "standard", "sizes": ["XS", "S", "M", "L"], "global_price_cny": 18, "site_prices": {"PH": {"sale_price": 746, "original_price": 1243}}},
            {"id": "large", "sizes": ["XL", "XXL", "XXXL"], "global_price_cny": 21, "site_prices": {"PH": {"sale_price": 811, "original_price": 1352}}},
        ],
        "warehouse_stock_defaults": {"TH": 100, "CN_GZ": 1, "PH": 2},
        "status": "active",
        "version": 1,
    }]
    capture = _cnsc_capture()
    capture["sku_rows"][1]["warehouse_stocks"]["TH"] = 299
    stage_page_import(data, "run-1", capture, now="2026-08-20T00:01:00Z")

    template = confirm_page_import(data, "run-1", now="2026-08-20T00:02:00Z")["template"]

    assert template["warehouse_stock_defaults"] == {}
    assert template["status"] == "needs_configuration"
    assert "warehouse_stock_defaults" in template["missing_configuration"]


def test_confirm_cnsc_import_preserves_store_discount_when_catalog_was_not_loaded() -> None:
    data = _store()
    data["store_templates"] = [{
        "id": "cnsc-template",
        "listing_mode": "cnsc_global",
        "mabang_store_id": "cnsc-store-1",
        "mabang_category_id": "100244",
        "default_site_discount_percent": 40,
        "target_site_stores": [{
            "site_code": "TW",
            "store_id": "tw-store",
            "store_name": "海宁TW",
            "mabang_discount_id": "discount-old",
            "discount_name": "原活动 40%",
            "discount_label": "40%",
            "discount_percent": 40,
            "discount_catalog_loaded": True,
            "discount_catalog": [{"mabang_discount_id": "discount-old", "name": "原活动 40%", "discount_percent": 40}],
        }],
        "price_tiers": [
            {"id": "standard", "sizes": ["XS", "S", "M", "L"], "global_price_cny": 18, "site_prices": {"TW": {"sale_price": 223.2, "original_price": 372}}},
            {"id": "large", "sizes": ["XL", "XXL", "XXXL"], "global_price_cny": 21, "site_prices": {"TW": {"sale_price": 243, "original_price": 405}}},
        ],
        "warehouse_stock_defaults": {"TH": 100, "CN_GZ": 1, "PH": 2},
        "status": "active",
        "version": 1,
    }]
    capture = _cnsc_capture()
    capture["target_site_stores_loaded"] = True
    capture["target_site_stores"] = [{
        "site_code": "TW", "store_id": "tw-store", "store_name": "海宁TW", "discount_catalog_loaded": False,
    }]
    for row in capture["sku_rows"]:
        original = 372 if row["size"] in {"XS", "S", "M", "L"} else 405
        row["site_prices"] = {"TW": {"original_price": original, "sale_price": original * 0.6}}
    stage_page_import(data, "run-1", capture, now="2026-08-20T00:01:00Z")

    template = confirm_page_import(data, "run-1", now="2026-08-20T00:02:00Z")["template"]

    target = template["target_site_stores"][0]
    assert target["mabang_discount_id"] == "discount-old"
    assert target["discount_percent"] == 40
    assert target["discount_catalog"][0]["mabang_discount_id"] == "discount-old"
    assert template["status"] == "active"


def _seed_available_activity(data: dict, activity_id: str = "keep-1") -> None:
    data["store_promotion_activities"].append({
        "id": activity_id,
        "activity_id": activity_id,
        "store_id": "2022625115",
        "name": "40%",
        "start_at": "2026-05-28 17:30:00",
        "end_at": "2026-11-23 23:59:00",
        "sale_ratio": "",
        "discount_percent": None,
        "status": "available",
    })


def test_confirm_without_a_loaded_promotion_catalog_keeps_existing_activities() -> None:
    """采集没读到店铺活动列表时，确认导入不能把已有的可用活动打成 unresolved。"""

    data = _store()
    _seed_available_activity(data)
    capture = _capture()
    capture["promotion_catalog_loaded"] = False
    # 这次只回显了页面上当前选中的一条（没有状态/有效期）。
    capture["promotion_options"] = [
        {"activity_id": "echoed-1", "name": "40%12月29到期", "status": "", "selected": True}
    ]
    capture["selected_promotion_id"] = "echoed-1"
    stage_page_import(data, "run-1", capture, now="2026-09-15T00:00:00Z")

    confirm_page_import(data, "run-1", now="2026-09-15T00:01:00Z")

    by_id = {item["activity_id"]: item for item in data["store_promotion_activities"]}
    assert by_id["keep-1"]["status"] == "available"
    assert by_id["keep-1"]["end_at"] == "2026-11-23 23:59:00"
    assert by_id["echoed-1"]["status"] == "unresolved"


def test_confirm_with_a_loaded_promotion_catalog_retires_missing_activities() -> None:
    data = _store()
    _seed_available_activity(data, "gone-1")
    capture = _capture()
    capture["promotion_catalog_loaded"] = True
    capture["promotion_options"] = [
        {
            "activity_id": "live-1",
            "name": "40%",
            "start_at": "2026-05-28 17:30:00",
            "end_at": "2026-11-23 23:59:00",
            "status": "ongoing",
            "selected": True,
        }
    ]
    capture["selected_promotion_id"] = "live-1"
    stage_page_import(data, "run-1", capture, now="2026-09-15T00:00:00Z")

    confirm_page_import(data, "run-1", now="2026-09-15T00:01:00Z")

    by_id = {item["activity_id"]: item for item in data["store_promotion_activities"]}
    assert by_id["live-1"]["status"] == "available"
    assert by_id["gone-1"]["status"] == "unresolved"
