from __future__ import annotations

from services.api.store_promotions import (
    normalize_promotion_catalog,
    promotion_label,
    replace_promotion_catalog,
)


def test_promotions_are_store_scoped_and_use_readable_labels() -> None:
    items = normalize_promotion_catalog(
        store_id="store-1",
        raw_items=[
            {
                "activityId": "928232107737088",
                "activityName": "暑期六折",
                "discountRate": "0.60",
                "startTime": "2026-07-01T00:00:00Z",
                "endTime": "2026-07-31T23:59:59Z",
                "status": "enabled",
            }
        ],
        now="2026-07-15T00:00:00Z",
    )

    assert items[0]["store_id"] == "store-1"
    assert items[0]["status"] == "available"
    assert promotion_label(items[0]) == "暑期六折｜六折（优惠 40%）｜2026-07-01 至 2026-07-31"


def test_expired_promotion_is_never_reported_available() -> None:
    item = normalize_promotion_catalog(
        store_id="store-1",
        raw_items=[
            {
                "activityId": "old-1",
                "activityName": "旧活动",
                "discountRate": 0.8,
                "startTime": "2026-06-01T00:00:00Z",
                "endTime": "2026-06-30T23:59:59Z",
                "status": "enabled",
            }
        ],
        now="2026-07-15T00:00:00Z",
    )[0]

    assert item["status"] == "expired"
    assert item["discount_percent"] == 20


def test_running_mabang_activity_without_discount_rate_remains_available() -> None:
    item = normalize_promotion_catalog(
        store_id="2022625115",
        raw_items=[
            {
                "activity_id": "928232107737088",
                "name": "40%",
                "start_at": "2026-05-28 17:30:00",
                "end_at": "2026-11-23 23:59:00",
                "status": "进行中",
            }
        ],
        now="2026-07-22T00:00:00Z",
    )[0]

    assert item["status"] == "available"
    assert item["sale_ratio"] == ""
    assert item["discount_percent"] is None
    assert item["label"] == "40%｜2026-05-28 至 2026-11-23"


def test_refresh_preserves_other_stores_and_marks_missing_activity_unresolved() -> None:
    data = {
        "store_promotion_activities": [
            {"id": "old-1", "activity_id": "old-1", "store_id": "store-1", "status": "available"},
            {"id": "other-1", "activity_id": "other-1", "store_id": "store-2", "status": "available"},
        ]
    }

    replace_promotion_catalog(data, store_id="store-1", raw_items=[], now="2026-07-15T00:00:00Z")

    by_id = {item["activity_id"]: item for item in data["store_promotion_activities"]}
    assert by_id["old-1"]["status"] == "unresolved"
    assert by_id["other-1"]["status"] == "available"


def test_activity_without_status_is_unresolved_not_disabled() -> None:
    """状态缺失＝这次没读到，不能判成"已停用"（否则正常活动显示为不可用）。"""

    item = normalize_promotion_catalog(
        store_id="2022625115",
        raw_items=[{"discount_id": "477160318320861", "discount_name": "40%12月29到期"}],
        now="2026-09-15T00:00:00Z",
    )[0]

    assert item["status"] == "unresolved"
    assert item["label"] == "40%12月29到期｜未设置有效期"


def test_mabang_row_fields_map_to_an_available_activity() -> None:
    """马帮折扣弹窗行的字段（discount_id/start_time/end_time/discount_status）。"""

    item = normalize_promotion_catalog(
        store_id="2022625115",
        raw_items=[
            {
                "discount_id": 928232107737088,
                "discount_name": "40%",
                "start_time": "2026-05-28 17:30:00",
                "end_time": "2026-11-23 23:59:00",
                "discount_status": "ongoing",
                "flag": 2,
                "shopId": 2022625115,
            }
        ],
        now="2026-09-15T00:00:00Z",
    )[0]

    assert item["activity_id"] == "928232107737088"
    assert item["status"] == "available"
    assert item["label"] == "40%｜2026-05-28 至 2026-11-23"


def test_chinese_status_labels_are_classified_like_their_english_equivalents() -> None:
    items = normalize_promotion_catalog(
        store_id="store-1",
        raw_items=[
            {"activity_id": "a", "name": "未开始", "status": "未开始"},
            {"activity_id": "b", "name": "已结束", "status": "已结束"},
            {"activity_id": "c", "name": "进行中", "status": "进行中"},
        ],
        now="2026-09-15T00:00:00Z",
    )
    by_id = {item["activity_id"]: item["status"] for item in items}

    assert by_id == {"a": "not_started", "b": "expired", "c": "available"}


def test_refresh_keeps_known_validity_when_the_new_capture_omits_it() -> None:
    """同一条活动再采到"只有 id+名称"时，不能把已采到的有效期/状态抹掉。"""

    data = {
        "store_promotion_activities": [
            {
                "id": "928232107737088",
                "activity_id": "928232107737088",
                "store_id": "2022625115",
                "name": "40%",
                "start_at": "2026-05-28 17:30:00",
                "end_at": "2026-11-23 23:59:00",
                "sale_ratio": "",
                "discount_percent": None,
                "status": "available",
            }
        ]
    }

    replace_promotion_catalog(
        data,
        store_id="2022625115",
        raw_items=[{"activity_id": "928232107737088", "name": "40%"}],
        now="2026-09-15T00:00:00Z",
    )

    item = data["store_promotion_activities"][0]
    assert item["status"] == "available"
    assert item["start_at"] == "2026-05-28 17:30:00"
    assert item["end_at"] == "2026-11-23 23:59:00"
    assert item["label"] == "40%｜2026-05-28 至 2026-11-23"


def test_partial_capture_does_not_downgrade_activities_it_could_not_see() -> None:
    """catalog_loaded=False（弹窗没读到列表）时，别把其它活动打成 unresolved。"""

    data = {
        "store_promotion_activities": [
            {
                "id": "928232107737088",
                "activity_id": "928232107737088",
                "store_id": "2022625115",
                "name": "40%",
                "start_at": "2026-05-28 17:30:00",
                "end_at": "2026-11-23 23:59:00",
                "sale_ratio": "",
                "discount_percent": None,
                "status": "available",
            }
        ]
    }

    replace_promotion_catalog(
        data,
        store_id="2022625115",
        raw_items=[{"activity_id": "477160318320861", "name": "40%12月29到期"}],
        now="2026-09-15T00:00:00Z",
        catalog_loaded=False,
    )
    by_id = {item["activity_id"]: item for item in data["store_promotion_activities"]}

    # 已采到的可用活动保持可用，有效期不被抹掉。
    assert by_id["928232107737088"]["status"] == "available"
    assert by_id["928232107737088"]["end_at"] == "2026-11-23 23:59:00"
    # 本次只回显的那条按"未解析"记录，不冒充可用。
    assert by_id["477160318320861"]["status"] == "unresolved"


def test_full_catalog_capture_still_retires_missing_activities() -> None:
    data = {
        "store_promotion_activities": [
            {"id": "gone", "activity_id": "gone", "store_id": "store-1", "status": "available"},
        ]
    }

    replace_promotion_catalog(
        data, store_id="store-1", raw_items=[], now="2026-09-15T00:00:00Z", catalog_loaded=True
    )

    assert data["store_promotion_activities"][0]["status"] == "unresolved"
