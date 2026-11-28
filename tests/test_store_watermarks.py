from __future__ import annotations

from copy import deepcopy

import pytest

from services.api.store_watermarks import (
    catalog_status,
    list_watermarks,
    record_catalog_run,
    replace_watermark_catalog,
    resolve_watermark_reference,
)


NOW = "2026-07-19T00:00:00Z"
LATER = "2026-07-19T01:00:00Z"


def _existing_catalog() -> dict:
    """升级前的老数据：水印行没有 account_id（当年按店铺存）。"""
    return {
        "store_watermark_templates": [
            {
                "id": "local-old",
                "store_id": "store-a",
                "template_id": "old",
                "name": "旧模板",
                "preview_url": "https://example.invalid/old.png",
                "status": "available",
                "collected_at": NOW,
            }
        ]
    }


def test_watermark_catalog_is_company_wide_across_stores() -> None:
    # 马帮水印模板是公司级的（真实数据 companyId 相同、shopId 为空）：同一账号下
    # 从任意一个店铺采集，所有店铺（本土/跨境/3PF）都看到同一份目录。
    data = {"store_watermark_templates": []}
    replace_watermark_catalog(
        data,
        store_id="store-a",
        raw_items=[{"template_id": "1", "name": "A"}],
        now=NOW,
        catalog_loaded=True,
        account_id="acct-1",
    )

    assert [item["name"] for item in list_watermarks(data, "store-a", account_id="acct-1")] == ["A"]
    assert [item["name"] for item in list_watermarks(data, "store-b", account_id="acct-1")] == ["A"]
    # 从另一个店铺再采一次同一份目录：替换而不是叠加。
    replace_watermark_catalog(
        data,
        store_id="store-b",
        raw_items=[{"template_id": "2", "name": "B"}],
        now=LATER,
        catalog_loaded=True,
        account_id="acct-1",
    )
    assert [item["template_id"] for item in list_watermarks(data, "store-a", account_id="acct-1")] == ["2"]


def test_watermark_catalog_is_not_shared_across_accounts() -> None:
    data = {"store_watermark_templates": []}
    replace_watermark_catalog(
        data,
        store_id="store-a",
        raw_items=[{"template_id": "1", "name": "A"}],
        now=NOW,
        catalog_loaded=True,
        account_id="acct-1",
    )
    replace_watermark_catalog(
        data,
        store_id="store-c",
        raw_items=[{"template_id": "1", "name": "C"}],
        now=LATER,
        catalog_loaded=True,
        account_id="acct-2",
    )

    assert [item["name"] for item in list_watermarks(data, "store-b", account_id="acct-1")] == ["A"]
    assert [item["name"] for item in list_watermarks(data, "store-c", account_id="acct-2")] == ["C"]


def test_legacy_rows_stay_visible_and_get_adopted_by_the_next_collect() -> None:
    data = _existing_catalog()

    # 老数据没有 account_id：升级后界面不能突然显示「未采集」。
    assert [item["name"] for item in list_watermarks(data, "store-b", account_id="acct-1")] == ["旧模板"]

    replace_watermark_catalog(
        data,
        store_id="store-a",
        raw_items=[{"template_id": "1", "name": "新模板"}],
        now=LATER,
        catalog_loaded=True,
        account_id="acct-1",
    )

    assert [item["template_id"] for item in data["store_watermark_templates"]] == ["1"]
    assert [item["name"] for item in list_watermarks(data, "store-b", account_id="acct-1")] == ["新模板"]


def test_same_template_from_two_stores_is_deduped_by_latest_collect() -> None:
    data = {
        "store_watermark_templates": [
            {"store_id": "store-a", "account_id": "acct-1", "template_id": "1", "name": "旧名", "collected_at": NOW},
            {"store_id": "store-b", "account_id": "acct-1", "template_id": "1", "name": "新名", "collected_at": LATER},
        ]
    }

    assert [item["name"] for item in list_watermarks(data, "store-c", account_id="acct-1")] == ["新名"]


def test_empty_failed_capture_does_not_replace_last_valid_catalog() -> None:
    data = _existing_catalog()
    before = deepcopy(data)

    with pytest.raises(ValueError, match="水印模板目录未加载"):
        replace_watermark_catalog(
            data,
            store_id="store-a",
            raw_items=[],
            now=NOW,
            catalog_loaded=False,
            account_id="acct-1",
        )

    assert data == before


def test_loaded_empty_catalog_clears_only_the_target_account() -> None:
    data = _existing_catalog()
    data["store_watermark_templates"].append(
        {
            "id": "other",
            "store_id": "store-c",
            "account_id": "acct-2",
            "template_id": "1",
            "name": "B",
            "status": "available",
        }
    )

    items = replace_watermark_catalog(
        data,
        store_id="store-a",
        raw_items=[],
        now=LATER,
        catalog_loaded=True,
        account_id="acct-1",
    )

    assert items == []
    assert [item["account_id"] for item in data["store_watermark_templates"]] == ["acct-2"]


def test_catalog_status_falls_back_to_the_account_and_then_globally() -> None:
    data: dict = {}
    record_catalog_run(
        data,
        store_id="store-a",
        status="failed",
        count=0,
        error="页面没打开",
        now=NOW,
        account_id="acct-1",
    )
    record_catalog_run(
        data,
        store_id="store-c",
        status="ok",
        count=35,
        now=LATER,
        catalog_loaded=True,
        account_id="acct-2",
    )

    # 本店铺的记录优先（失败原因要看得见）。
    assert catalog_status(data, "store-a", account_id="acct-1")["status"] == "failed"
    # 同账号别的店铺：回退到账号最近一次。
    account_status = catalog_status(data, "store-b", account_id="acct-1")
    assert account_status["store_id"] == "store-a"
    # 账号对不上：回退到全库最近一次。
    assert catalog_status(data, "store-x", account_id="acct-9")["store_id"] == "store-c"


def test_reference_resolution_is_id_first_and_name_verified() -> None:
    catalog = [
        {"template_id": "wm-1", "name": "主图水印", "status": "available"},
        {"template_id": "wm-2", "name": "产品图水印", "status": "available"},
    ]

    assert resolve_watermark_reference({"template_id": "wm-2", "name": "产品图水印"}, catalog) == {
        "status": "resolved",
        "template_id": "wm-2",
        "name": "产品图水印",
        "matched_by": "template_id",
    }
    assert resolve_watermark_reference({"template_id": "wm-2", "name": "错误名称"}, catalog)["status"] == "unresolved"
