from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from services.api.mabang_import_snapshot import (
    MabangImportSnapshotError,
    collapse_uniform_sku_value,
    normalize_mabang_capture,
    snapshot_template_payload,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mabang_page_capture_pod_ph.json"
WATERMARK_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mabang_page_capture_watermarks.json"
CNSC_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mabang_page_capture_cnsc.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _cnsc_fixture() -> dict:
    return json.loads(CNSC_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_snapshot_keeps_only_selected_attributes_and_reports_missing_required() -> None:
    snapshot = normalize_mabang_capture(_fixture())

    assert {
        field_id: field["value"]
        for field_id, field in snapshot["selected_attributes"].items()
    } == {
        "100134": ["1149"],
        "100162": ["1486"],
        "100168": "1500",
        "101004": "5487",
    }
    assert snapshot["missing_required_attributes"] == [
        {"field_id": "required-empty", "label": "未选择必填项"}
    ]
    assert "尺码图表:上传图片选择模板" not in snapshot["selected_attributes"]
    assert "变种属性:" not in snapshot["selected_attributes"]


def test_snapshot_keeps_free_text_attribute_values_without_turning_them_into_selects() -> None:
    # 尺寸(100-150cm)/条件(Brand New) 是马帮页上的自由文本/可自填属性：
    # 采集端把它们标成 type=text（而不是带空 options 的 single_select），
    # 快照与模板都必须原样保留文本值。
    capture = _fixture()
    capture["category_attributes"] = [
        {
            "field_id": "102530", "label": "尺寸", "type": "text", "required": False,
            "options": [], "selected_value": "100-150cm",
        },
        {
            "field_id": "100413", "label": "条件", "type": "text", "required": False,
            "options": [], "selected_value": "Brand New",
        },
        {
            "field_id": "100134", "label": "材料", "type": "multi_select", "required": True,
            "options": [{"value": "1149", "label": "棉花 (Cotton)"}], "selected_value": ["1149"],
        },
        {
            "field_id": "required-empty", "label": "未选择必填项", "type": "single_select",
            "required": True, "options": [], "selected_value": "",
        },
    ]
    capture["sku_rows"] = [row for row in capture["sku_rows"] if row["color"]]

    snapshot = normalize_mabang_capture(capture)
    payload = snapshot_template_payload(snapshot)

    selected = snapshot["selected_attributes"]
    assert selected["102530"]["type"] == "text"
    assert selected["102530"]["value"] == "100-150cm"
    assert selected["102530"]["value_labels"] == ["100-150cm"]
    assert selected["100413"]["value"] == "Brand New"
    assert payload["attribute_values"]["102530"] == "100-150cm"
    assert payload["attribute_values"]["100413"] == "Brand New"


def test_snapshot_derives_sizes_and_preserves_empty_dimensions() -> None:
    snapshot = normalize_mabang_capture(_fixture())

    assert snapshot["variation"] == {
        "colors": ["black"],
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "sku_count": 5,
        "axes": [
            {"name": "color", "values": ["black"]},
            {"name": "size", "values": ["S", "M", "L", "XL", "2XL"]},
        ],
    }
    assert snapshot["transaction_defaults"] == {
        "stock": {"status": "complete", "value": 999},
        "weight_kg": {"status": "missing", "value": None},
        "package_length_cm": {"status": "missing", "value": None},
        "package_width_cm": {"status": "missing", "value": None},
        "package_height_cm": {"status": "missing", "value": None},
    }


def test_snapshot_never_fabricates_a_promotion() -> None:
    snapshot = normalize_mabang_capture(_fixture())

    assert snapshot["promotion_catalog"] == []
    assert snapshot["selected_promotion_id"] == ""
    assert snapshot["coverage"]["promotions"]["status"] == "missing"


def test_snapshot_marks_inconsistent_sku_values_as_conflicting() -> None:
    capture = _fixture()
    capture["sku_rows"][1]["stock"] = 998

    snapshot = normalize_mabang_capture(capture)

    assert snapshot["transaction_defaults"]["stock"] == {
        "status": "conflict",
        "value": None,
    }
    assert snapshot["coverage"]["transaction"]["status"] == "conflict"


def test_snapshot_template_payload_maps_only_complete_defaults() -> None:
    snapshot = normalize_mabang_capture(_fixture())
    payload = snapshot_template_payload(snapshot)

    assert payload["attribute_values"] == {
        "100134": ["1149"],
        "100162": ["1486"],
        "100168": "1500",
        "101004": "5487",
    }
    assert payload["sizes"] == ["S", "M", "L", "XL", "2XL"]
    assert payload["stock"] == 999
    assert payload["weight_kg"] == ""
    assert payload["promotion_activity_id"] == ""


def test_snapshot_maps_uniform_ph_prices_from_the_live_sku_table_to_template_defaults() -> None:
    capture = _fixture()
    for row in capture["sku_rows"]:
        row["normal_price"] = "89"
        row["sale_price"] = "69"

    snapshot = normalize_mabang_capture(capture)
    payload = snapshot_template_payload(snapshot)

    assert snapshot["price_defaults"] == {
        "normal_price": {"status": "complete", "value": 89.0},
        "sale_price": {"status": "complete", "value": 69.0},
    }
    assert payload["normal_price"] == 89.0
    assert payload["sale_price"] == 69.0


def test_snapshot_uses_majority_price_as_default_when_sku_prices_differ() -> None:
    # A native (site) page may carry one differently-priced variant row (e.g. a
    # plain no-print color) while the rest share a price. Differing per-SKU prices
    # are legitimate there and must not blank the template's uniform default:
    # pick the most common price and expose the differing rows to the preview.
    capture = _fixture()
    for row in capture["sku_rows"]:
        row["normal_price"] = "578"
        row["sale_price"] = "219.64"
    capture["sku_rows"][0]["normal_price"] = "450"
    capture["sku_rows"][0]["sale_price"] = "171"

    snapshot = normalize_mabang_capture(capture)
    payload = snapshot_template_payload(snapshot)

    assert snapshot["price_defaults"]["normal_price"] == {
        "status": "complete",
        "value": 578.0,
        "uniform": False,
        "differing_count": 1,
        "value_counts": {"578": 4, "450": 1},
    }
    assert snapshot["price_defaults"]["sale_price"] == {
        "status": "complete",
        "value": 219.64,
        "uniform": False,
        "differing_count": 1,
        "value_counts": {"219.64": 4, "171": 1},
    }
    assert payload["normal_price"] == 578.0
    assert payload["sale_price"] == 219.64


def test_snapshot_still_reports_missing_when_no_sku_has_price() -> None:
    capture = _fixture()
    for row in capture["sku_rows"]:
        row["normal_price"] = ""
        row["sale_price"] = ""

    snapshot = normalize_mabang_capture(capture)
    payload = snapshot_template_payload(snapshot)

    assert snapshot["price_defaults"] == {
        "normal_price": {"status": "missing", "value": None},
        "sale_price": {"status": "missing", "value": None},
    }
    assert payload["normal_price"] == ""
    assert payload["sale_price"] == ""


def test_snapshot_keeps_mabang_size_chart_for_local_import() -> None:
    capture = _fixture()
    capture["size_chart"] = {
        "url": "https://publish-cos.mabangerp.com/upload/publish/shopee/chart.png",
        "id": "chart-1",
        "width": 808,
        "height": 720,
    }

    snapshot = normalize_mabang_capture(capture)
    payload = snapshot_template_payload(snapshot)

    assert snapshot["size_chart"] == capture["size_chart"]
    assert payload["size_chart_source_url"] == capture["size_chart"]["url"]


def test_snapshot_keeps_size_chart_template_identity_without_a_preview_url() -> None:
    capture = _fixture()
    capture["size_chart_template_catalog_loaded"] = True
    capture["size_chart_templates"] = [{
        "template_id": "106239228",
        "name": "男装T恤",
        "preview_url": "",
    }]

    snapshot = normalize_mabang_capture(capture)

    assert snapshot["size_chart_template_catalog"] == [{
        "template_id": "106239228",
        "name": "男装T恤",
        "preview_url": "",
    }]


def test_snapshot_keeps_the_complete_loaded_watermark_catalog() -> None:
    capture = json.loads(WATERMARK_FIXTURE_PATH.read_text(encoding="utf-8"))

    snapshot = normalize_mabang_capture(capture)

    assert snapshot["watermark_catalog_loaded"] is True
    assert snapshot["watermark_catalog"] == capture["watermark_templates"]


def test_snapshot_does_not_treat_a_missing_watermark_source_as_an_empty_catalog() -> None:
    snapshot = normalize_mabang_capture(_fixture())

    assert snapshot["watermark_catalog_loaded"] is False
    assert snapshot["watermark_catalog"] == []


def test_collapse_uniform_sku_value_does_not_choose_a_first_row() -> None:
    rows = [{"weight_kg": "0.3"}, {"weight_kg": "0.4"}]

    assert collapse_uniform_sku_value(rows, "weight_kg") == {
        "status": "conflict",
        "value": None,
    }


def test_cnsc_snapshot_requires_both_global_parameter_and_all_cnsc_headers() -> None:
    capture = _cnsc_fixture()
    snapshot = normalize_mabang_capture(capture)

    assert snapshot["schema_version"] == 3
    assert snapshot["listing_mode"] == "cnsc_global"
    assert snapshot["listing_mode_evidence"] == capture["listing_mode_evidence"]
    assert snapshot["page"]["url"] == "https://publish.mabangerp.com/publish-ui/"
    assert "cKey" not in json.dumps(snapshot)

    capture["listing_mode_evidence"]["matched_headers"].remove("菲律宾仓库存")
    with pytest.raises(MabangImportSnapshotError, match="页面类型识别冲突"):
        normalize_mabang_capture(capture)


def test_cnsc_snapshot_derives_price_tiers_and_uniform_warehouse_defaults() -> None:
    snapshot = normalize_mabang_capture(_cnsc_fixture())
    payload = snapshot_template_payload(snapshot)

    assert len(snapshot["cnsc_sku_rows"]) == 28
    assert snapshot["price_tiers"] == [
        {"id": "standard", "sizes": ["XS", "S", "M", "L"], "global_price_cny": 19.2, "site_prices": {}},
        {"id": "large", "sizes": ["XL", "XXL", "XXXL"], "global_price_cny": 22.2, "site_prices": {}},
    ]
    assert snapshot["warehouse_stock_defaults"] == {"TH": 300, "CN_GZ": 0, "PH": 0}
    assert snapshot["coverage"]["promotions"]["status"] == "not_applicable"
    assert payload["listing_mode"] == "cnsc_global"
    assert payload["cnsc_store"] == {"id": "cnsc-store-1", "name": "广州小苗服饰有限公司"}
    assert payload["target_site_stores"] == []
    assert payload["missing_configuration"] == ["target_site_stores", "site_price_matrix"]


def test_cnsc_snapshot_preserves_rows_but_does_not_choose_conflicting_defaults() -> None:
    capture = _cnsc_fixture()
    capture["sku_rows"][7]["global_price_cny"] = 20
    capture["sku_rows"][9]["warehouse_stocks"]["TH"] = 299

    snapshot = normalize_mabang_capture(capture)
    payload = snapshot_template_payload(snapshot)

    assert snapshot["price_tiers"][0]["global_price_cny"] == ""
    assert snapshot["warehouse_stock_defaults"] == {}
    assert snapshot["cnsc_sku_rows"][7]["global_price_cny"] == 20.0
    assert payload["missing_configuration"] == [
        "price_tiers.standard.global_price_cny",
        "warehouse_stock_defaults",
        "target_site_stores",
        "site_price_matrix",
    ]


def test_cnsc_v3_snapshot_derives_site_prices_and_store_discount_configuration() -> None:
    capture = _cnsc_fixture()
    capture["target_site_stores_loaded"] = True
    capture["target_site_stores"] = [{
        "site_code": "TW",
        "store_id": "tw-store",
        "store_name": "海宁TW",
        "discount_catalog_loaded": True,
        "discount_catalog": [{
            "mabang_discount_id": "discount-40",
            "name": "秋季活动 40%",
            "discount_percent": 40,
        }],
        "mabang_discount_id": "discount-40",
        "discount_name": "秋季活动 40%",
        "discount_label": "40%",
        "discount_percent": 40,
    }]
    for row in capture["sku_rows"]:
        original = 372 if row["size"] in {"XS", "S", "M", "L"} else 405
        row["site_prices"] = {"TW": {"original_price": original, "sale_price": original * 0.6}}

    snapshot = normalize_mabang_capture(capture)
    payload = snapshot_template_payload(snapshot)

    assert snapshot["schema_version"] == 3
    assert snapshot["default_site_discount_percent"] == 40
    assert snapshot["target_site_stores"][0]["mabang_discount_id"] == "discount-40"
    assert snapshot["target_site_stores"][0]["discount_catalog_loaded"] is True
    assert snapshot["cnsc_sku_rows"][0]["site_prices"]["TW"] == {
        "original_price": 372.0,
        "sale_price": 223.2,
    }
    assert snapshot["price_tiers"][0]["site_prices"]["TW"] == {
        "original_price": 372.0,
        "sale_price": 223.2,
    }
    assert snapshot["missing_configuration"] == []
    assert payload["default_site_discount_percent"] == 40


def test_snapshot_template_payload_accepts_v1_and_v2_compatibility_snapshots() -> None:
    for schema_version in (1, 2):
        snapshot = normalize_mabang_capture(_fixture())
        snapshot["schema_version"] = schema_version
        snapshot.pop("default_site_discount_percent", None)

        payload = snapshot_template_payload(snapshot)

        assert payload["listing_mode"] == "site"


def test_cnsc_snapshot_lists_store_activity_and_discount_as_specific_missing_configuration() -> None:
    capture = _cnsc_fixture()
    capture["target_site_stores_loaded"] = True
    capture["target_site_stores"] = [{
        "site_code": "TW",
        "store_id": "tw-store",
        "store_name": "海宁TW",
        "discount_catalog_loaded": True,
        "discount_catalog": [],
    }]
    for row in capture["sku_rows"]:
        row["site_prices"] = {"TW": {"original_price": 372, "sale_price": 223.2}}

    snapshot = normalize_mabang_capture(capture)

    assert "target_site_stores.TW.discount_activity" in snapshot["missing_configuration"]
    assert "target_site_stores.TW.discount_percent" not in snapshot["missing_configuration"]


def test_cnsc_snapshot_allows_store_discount_to_inherit_the_default_percent() -> None:
    capture = _cnsc_fixture()
    capture["target_site_stores_loaded"] = True
    capture["target_site_stores"] = [{
        "site_code": "TW",
        "store_id": "tw-store",
        "store_name": "海宁TW",
        "mabang_discount_id": "discount-40",
        "discount_name": "秋季活动 40%",
        "discount_percent": "",
        "discount_catalog_loaded": True,
        "discount_catalog": [{"mabang_discount_id": "discount-40", "name": "秋季活动 40%"}],
    }]
    for row in capture["sku_rows"]:
        row["site_prices"] = {"TW": {"original_price": 372, "sale_price": 223.2}}

    snapshot = normalize_mabang_capture(capture)

    assert snapshot["default_site_discount_percent"] == 40
    assert snapshot["target_site_stores"][0]["discount_percent"] == ""
    assert snapshot["missing_configuration"] == []


def test_missing_store_id_reports_an_unselected_store_on_a_blank_new_listing() -> None:
    """A new-listing form carries an empty store block, not a missing one."""

    capture = _fixture()
    capture["store_category_state"]["store"] = {"id": "", "name": ""}
    capture["capture_debug"] = {"detail_vm": True, "store_category_ref": True}

    with pytest.raises(MabangImportSnapshotError, match="编辑页未选择店铺"):
        normalize_mabang_capture(capture)


def test_missing_store_id_stays_bare_when_no_capture_diagnostic_is_available() -> None:
    """With no diagnostics at all the message must not render empty parentheses."""

    capture = _fixture()
    capture["store_category_state"]["store"] = {"id": "", "name": ""}
    capture["capture_debug"] = {"detail_vm": False, "store_category_ref": False}

    with pytest.raises(MabangImportSnapshotError, match=r"^未识别到马帮店铺编号$"):
        normalize_mabang_capture(capture)
