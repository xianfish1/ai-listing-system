from services.api.promotion_resolution import promotion_state, resolve_execution_promotion


def test_absent_catalog_entry_is_unresolved_not_expired() -> None:
    assert promotion_state("activity-1", None, now="2026-07-19T00:00:00Z") == "unresolved"


def test_zero_promotion_placeholder_is_unconfigured() -> None:
    assert promotion_state("0", None, now="2026-07-19T00:00:00Z") == "unconfigured"


def test_explicit_end_time_is_expired() -> None:
    promotion = {
        "activity_id": "activity-1",
        "status": "available",
        "end_at": "2026-07-18T23:59:59Z",
    }
    assert promotion_state("activity-1", promotion, now="2026-07-19T00:00:00Z") == "expired"


def test_execution_resolution_requires_unique_verified_identity() -> None:
    result = resolve_execution_promotion(
        {
            "activity_id": "missing",
            "name": "六折活动",
            "sale_ratio": "0.6",
            "end_at": "2026-08-01",
        },
        [
            {
                "activity_id": "new-id",
                "name": "六折活动",
                "sale_ratio": "0.6",
                "end_at": "2026-08-01",
                "status": "available",
            }
        ],
        now="2026-07-19T00:00:00Z",
    )
    assert result == {
        "status": "resolved",
        "activity_id": "new-id",
        "matched_by": "verified_identity",
    }


def test_ambiguous_identity_requires_confirmation() -> None:
    catalog = [
        {"activity_id": "new-a", "name": "六折活动", "sale_ratio": "0.6", "end_at": "2026-08-01", "status": "available"},
        {"activity_id": "new-b", "name": "六折活动", "sale_ratio": "0.6", "end_at": "2026-08-01", "status": "available"},
    ]
    result = resolve_execution_promotion(
        {"activity_id": "missing", "name": "六折活动", "sale_ratio": "0.6", "end_at": "2026-08-01"},
        catalog,
        now="2026-07-19T00:00:00Z",
    )
    assert result["status"] == "needs_confirmation"
