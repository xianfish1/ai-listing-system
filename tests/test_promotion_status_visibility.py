from __future__ import annotations

from services.api import storage


NOW = "2026-11-29T00:00:00+00:00"


def _data(*activities: dict) -> dict:
    return {"store_promotion_activities": list(activities)}


def test_expired_activity_is_reported_for_templates_and_copies() -> None:
    """活动过了 end_at 就要在模板卡片和副本行上显示「活动已过期」（2026-11-29 用户要求）。"""
    data = _data(
        {
            "store_id": "2022625115",
            "activity_id": "old",
            "status": "available",
            "end_at": "2026-11-23 23:59:00",
        }
    )
    template = {"listing_mode": "site", "mabang_store_id": "2022625115", "promotion_activity_id": "old"}
    copy = {"listing_mode": "site", "mabang_store_id": "2022625115", "mabang_discount_id": "old"}

    assert storage._promotion_status_for(data, template, now=NOW) == "expired"
    assert storage._promotion_status_for(data, copy, now=NOW) == "expired"


def test_available_unresolved_and_unconfigured_states() -> None:
    data = _data(
        {"store_id": "s1", "activity_id": "live", "status": "available", "end_at": "2027-01-01 00:00:00"}
    )

    assert storage._promotion_status_for(
        data, {"listing_mode": "site", "mabang_store_id": "s1", "promotion_activity_id": "live"}, now=NOW
    ) == "available"
    assert storage._promotion_status_for(
        data, {"listing_mode": "site", "mabang_store_id": "s1", "promotion_activity_id": "missing"}, now=NOW
    ) == "unresolved"
    assert storage._promotion_status_for(
        data, {"listing_mode": "site", "mabang_store_id": "s1"}, now=NOW
    ) == "unconfigured"


def test_activity_of_another_store_does_not_count() -> None:
    """目录是按店铺采的：别的店铺的同 id 活动不能当成这个模板的活动。"""
    data = _data(
        {"store_id": "other", "activity_id": "a1", "status": "available", "end_at": "2027-01-01 00:00:00"}
    )
    assert storage._promotion_status_for(
        data, {"listing_mode": "site", "mabang_store_id": "s1", "promotion_activity_id": "a1"}, now=NOW
    ) == "unresolved"


def test_cnsc_templates_skip_the_single_store_activity_check() -> None:
    """跨境的折扣活动在「目标站点店铺」里配，这里不显示单店活动提醒。"""
    data = _data()
    for mode in ("cnsc_global", "cnsc_3pf"):
        assert storage._promotion_status_for(
            data, {"listing_mode": mode, "mabang_store_id": "s1", "promotion_activity_id": "x"}, now=NOW
        ) == ""
