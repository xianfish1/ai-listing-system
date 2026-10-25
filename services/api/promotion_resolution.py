from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


_EXPIRED_STATUSES = {"expired", "ended", "overdue"}
_AVAILABLE_STATUSES = {"available", "active", "enabled", "1", "true"}


def promotion_state(activity_id: str, promotion: dict[str, Any] | None, *, now: str) -> str:
    normalized_id = str(activity_id or "").strip().lower()
    if normalized_id in {"", "0", "none", "null", "undefined"}:
        return "unconfigured"
    if promotion is None:
        return "unresolved"
    if _is_explicitly_expired(promotion, now=now):
        return "expired"
    status = str(promotion.get("status") or "").strip().lower()
    return "available" if status in _AVAILABLE_STATUSES else "unresolved"


def resolve_execution_promotion(
    snapshot: dict[str, Any],
    catalog: list[dict[str, Any]],
    *,
    now: str,
) -> dict[str, str]:
    wanted_id = str(snapshot.get("activity_id") or snapshot.get("id") or "").strip()
    by_id = [item for item in catalog if _activity_id(item) == wanted_id]
    if len(by_id) == 1 and promotion_state(wanted_id, by_id[0], now=now) == "available":
        return {"status": "resolved", "activity_id": wanted_id, "matched_by": "activity_id"}

    wanted_identity = promotion_identity(snapshot)
    if not all(wanted_identity):
        return {"status": "needs_confirmation", "activity_id": "", "matched_by": ""}
    by_identity = [item for item in catalog if promotion_identity(item) == wanted_identity]
    if len(by_identity) == 1:
        resolved_id = _activity_id(by_identity[0])
        if promotion_state(resolved_id, by_identity[0], now=now) == "available":
            return {
                "status": "resolved",
                "activity_id": resolved_id,
                "matched_by": "verified_identity",
            }
    return {"status": "needs_confirmation", "activity_id": "", "matched_by": ""}


def promotion_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        " ".join(str(item.get("name") or item.get("activity_name") or "").split()).casefold(),
        _decimal_text(item.get("sale_ratio", item.get("discount_rate"))),
        str(item.get("end_at") or item.get("end_time") or "").strip()[:10],
    )


def _activity_id(item: dict[str, Any]) -> str:
    return str(item.get("activity_id") or item.get("id") or "").strip()


def _is_explicitly_expired(item: dict[str, Any], *, now: str) -> bool:
    status = str(item.get("status") or "").strip().lower()
    if status in _EXPIRED_STATUSES:
        return True
    end_at = str(item.get("end_at") or item.get("end_time") or "").strip()
    if not end_at:
        return False
    try:
        return _parse_time(end_at) < _parse_time(now)
    except ValueError:
        return False


def _parse_time(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if len(text) == 10:
        text += "T23:59:59+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decimal_text(value: Any) -> str:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return ""
    if number > 1:
        number /= Decimal(100)
    return format(number.normalize(), "f")
