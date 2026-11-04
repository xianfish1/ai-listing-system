from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


def normalize_promotion_catalog(
    *,
    store_id: str,
    raw_items: list[dict[str, Any]],
    now: str,
) -> list[dict[str, Any]]:
    current = _parse_time(now)
    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        # 也接受马帮折扣弹窗表格行的原始字段名（discount_id/discount_name/
        # start_time/end_time/discount_status）：采集侧虽然会转成规范名，但目录
        # 层直接认这些别名，任何一层拿到的行都不会被静默丢掉。
        activity_id = _text(raw, "activity_id", "activityId", "discount_id", "discountId", "id")
        name = _text(raw, "activity_name", "activityName", "discount_name", "discountName", "name")
        if not activity_id or not name:
            continue
        raw_discount_rate = raw.get("discount_rate", raw.get("discountRate"))
        has_discount_rate = raw_discount_rate is not None and str(raw_discount_rate).strip() != ""
        sale_ratio = _discount_ratio(raw_discount_rate) if has_discount_rate else None
        start_at = _text(raw, "start_at", "startTime", "start_time")
        end_at = _text(raw, "end_at", "endTime", "end_time")
        raw_status = _text(raw, "status", "discount_status", "discountStatus").lower()
        status = _status(raw_status, start_at=start_at, end_at=end_at, now=current)
        discount_percent = (
            int(((Decimal(1) - sale_ratio) * Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            if sale_ratio is not None
            else None
        )
        item = {
            "id": activity_id,
            "activity_id": activity_id,
            "store_id": str(store_id or "").strip(),
            "name": name,
            "sale_ratio": str(sale_ratio) if sale_ratio is not None else "",
            "discount_percent": discount_percent,
            "start_at": start_at,
            "end_at": end_at,
            "status": status,
        }
        item["label"] = promotion_label(item)
        normalized.append(item)
    return sorted(normalized, key=lambda item: (item["status"] != "available", item["name"], item["activity_id"]))


def promotion_label(item: dict[str, Any]) -> str:
    start = str(item.get("start_at") or "")[:10]
    end = str(item.get("end_at") or "")[:10]
    validity = f"{start} 至 {end}" if start or end else "未设置有效期"
    name = item.get("name") or "未命名活动"
    raw_ratio = str(item.get("sale_ratio") or "").strip()
    if not raw_ratio:
        return f"{name}｜{validity}"
    sale_ratio = _discount_ratio(raw_ratio)
    chinese_discount = _chinese_discount(sale_ratio)
    discount_percent = int(item.get("discount_percent") or 0)
    return f"{name}｜{chinese_discount}（优惠 {discount_percent}%）｜{validity}"


def replace_promotion_catalog(
    data: dict[str, Any],
    *,
    store_id: str,
    raw_items: list[dict[str, Any]],
    now: str,
    catalog_loaded: bool = True,
) -> list[dict[str, Any]]:
    """写入店铺活动目录。

    ``catalog_loaded=False`` 表示这次采集没能读到店铺活动列表（弹窗里没有行，只回
    显了当前选中项）。此时**不能**把这次没出现的活动判成失效——那会把之前采到的
    可用活动反复打成 unresolved（与水印/尺码图目录的 catalog_loaded 语义一致）。
    """
    wanted_store = str(store_id or "").strip()
    normalized = normalize_promotion_catalog(store_id=wanted_store, raw_items=raw_items, now=now)
    existing = list(data.setdefault("store_promotion_activities", []))
    existing_by_id = {
        str(item.get("activity_id") or item.get("id") or ""): item
        for item in existing
        if str(item.get("store_id") or "") == wanted_store
    }
    current_ids = {item["activity_id"] for item in normalized}
    merged: list[dict[str, Any]] = [
        item for item in existing if str(item.get("store_id") or "") != wanted_store
    ]
    for item in normalized:
        previous = existing_by_id.get(item["activity_id"])
        item["created_at"] = (previous or {}).get("created_at") or now
        item["updated_at"] = now
        merged.append(_keep_previous_details(item, previous))
    for activity_id, previous in existing_by_id.items():
        if activity_id in current_ids:
            continue
        historical = dict(previous)
        if catalog_loaded and historical.get("status") != "expired":
            historical["status"] = "unresolved"
        historical["updated_at"] = now
        merged.append(historical)
    data["store_promotion_activities"] = merged
    return [dict(item) for item in merged if str(item.get("store_id") or "") == wanted_store]


def _keep_previous_details(item: dict[str, Any], previous: Any) -> dict[str, Any]:
    """本次采集没给出的字段沿用上次已知值（有效期/折扣率），不写空。

    采集只能读到当次页面上可见的信息：活动列表没加载完时，同一条活动会以"只有
    id+名称"的形态回来。若直接覆盖，之前采到的有效期就被抹掉，活动随之从"可用"
    变成"未解析"（桌面上的症状是模板/副本里活动显示"未设置有效期（不可用）"）。
    status 由本次的原始状态重新判定，但状态本身缺失时同样保留上次结论。
    """
    if not isinstance(previous, dict):
        return item
    for key in ("start_at", "end_at", "discount_rate"):
        if not str(item.get(key) or "").strip() and str(previous.get(key) or "").strip():
            item[key] = previous[key]
    if item.get("status") == "unresolved" and str(previous.get("status") or "") in {"available", "not_started", "expired"}:
        item["status"] = previous["status"]
    if not str(item.get("sale_ratio") or "").strip() and str(previous.get("sale_ratio") or "").strip():
        item["sale_ratio"] = previous["sale_ratio"]
        item["discount_percent"] = previous.get("discount_percent")
    item["label"] = promotion_label(item)
    return item


def record_catalog_run(
    data: dict[str, Any],
    *,
    store_id: str,
    status: str,
    count: int = 0,
    error: str = "",
    catalog_loaded: bool = False,
    now: str,
) -> dict[str, Any]:
    """记录一次「采集店铺活动目录」的结果（每店一条）。

    与水印目录同一套语义：只写采集状态，不碰目录本身；``collected_at`` 只记最后一次
    成功时间，``attempted_at`` 记最后一次尝试时间，界面据此区分「从没采到」和「上次失败」。
    """
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        raise ValueError("店铺活动目录缺少店铺")
    previous = next(
        (
            item
            for item in data.get("store_promotion_catalog_runs") or []
            if str(item.get("store_id") or "") == wanted_store
        ),
        None,
    ) or {}
    succeeded = str(status or "").strip().lower() == "ok"
    run = {
        "id": str(previous.get("id") or uuid.uuid4()),
        "store_id": wanted_store,
        "status": "ok" if succeeded else "failed",
        "count": int(count or 0),
        "catalog_loaded": bool(catalog_loaded),
        "error": "" if succeeded else str(error or "").strip()[:500],
        "collected_at": now if succeeded else str(previous.get("collected_at") or ""),
        "attempted_at": now,
        "created_at": str(previous.get("created_at") or now),
        "updated_at": now,
    }
    data["store_promotion_catalog_runs"] = [
        item
        for item in data.get("store_promotion_catalog_runs") or []
        if str(item.get("store_id") or "") != wanted_store
    ] + [run]
    return run


def catalog_status(data: dict[str, Any], store_id: str) -> dict[str, Any] | None:
    """某店铺的活动目录采集状态（没有记录时返回 None）。"""
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        return None
    item = next(
        (
            row
            for row in data.get("store_promotion_catalog_runs") or []
            if str(row.get("store_id") or "") == wanted_store
        ),
        None,
    )
    if not item:
        return None
    status = dict(item)
    status["count"] = int(status.get("count") or 0)
    return status


def _discount_ratio(value: Any) -> Decimal:
    try:
        ratio = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return Decimal(1)
    if ratio > 1:
        ratio = ratio / Decimal(100)
    return min(Decimal(1), max(Decimal(0), ratio))


def _chinese_discount(ratio: Decimal) -> str:
    value = (ratio * Decimal(10)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    text = format(value, "f").rstrip("0").rstrip(".")
    if value == value.to_integral_value() and int(value) in range(1, 10):
        text = "一二三四五六七八九"[int(value) - 1]
    return f"{text}折"


def _status(raw_status: str, *, start_at: str, end_at: str, now: datetime) -> str:
    """活动状态：可用 / 未开始 / 已过期 / 已停用 / 未解析。

    采集侧的状态词来自马帮（`discount_status` 如 ongoing、`flag` 1/2/3 映射的中文），
    所以中文标签必须与英文同义：只有**明确**表示停用的状态才算 disabled；状态缺失
    说明这次没读到（例如活动列表还没加载完），记 unresolved —— 判成"已停用"会让
    操作员看到一个其实正常的活动被标成不可用。
    """
    status = str(raw_status or "").strip().lower()
    if not status:
        return "unresolved"
    if status in {"not_started", "notstarted", "pending", "upcoming", "未开始"}:
        return "not_started"
    if status in {"expired", "ended", "finished", "overdue", "已结束", "已过期"}:
        return "expired"
    if status not in {
        "enabled", "active", "available", "running", "in_progress", "ongoing", "进行中", "1", "true",
    }:
        return "disabled"
    start = _parse_time(start_at) if start_at else None
    end = _parse_time(end_at) if end_at else None
    if start and now < start:
        return "not_started"
    if end and now > end:
        return "expired"
    return "available"


def _parse_time(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
