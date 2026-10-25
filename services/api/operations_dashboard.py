from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.api.store_collection_scheduler import store_collection_key


def build_dashboard(data: dict[str, Any], *, now: str) -> dict[str, Any]:
    products = list(data.get("base_products") or [])
    # Archived templates are hidden from the list endpoint (see storage.list_store_templates),
    # so the dashboard count must exclude them too — otherwise "店铺模板 = 2" while
    # only 1 template is visible in the templates view.
    templates = [
        item
        for item in (data.get("store_templates") or [])
        if str((item or {}).get("status") or "active").strip().lower() != "archived"
    ]
    copies = list(data.get("store_listing_copies") or [])
    jobs = list(data.get("automation_jobs") or [])
    ready_jobs = [job for job in jobs if job.get("status") == "ready"]
    active_jobs = [job for job in jobs if job.get("status") in {"ready", "claimed", "running", "needs_confirmation"}]
    saved_jobs = [job for job in jobs if job.get("status") in {"saved", "saved_with_warnings"}]
    worker_online = any(
        job.get("status") in {"claimed", "running"} and _not_expired(job.get("lease_expires_at"), now)
        for job in jobs
    )
    metrics = {
        "base_products": len(products),
        "store_templates": len(templates),
        "listing_copies": len(copies),
        "ready_jobs": len(ready_jobs),
        "saved_jobs": len(saved_jobs),
        "failed_jobs": sum(job.get("status") in {"failed", "blocked"} for job in jobs),
    }
    recent_result_jobs = sorted(
        [job for job in jobs if job.get("status") in {"saved", "saved_with_warnings", "failed", "blocked", "needs_confirmation"}],
        key=_timestamp,
        reverse=True,
    )[:8]
    recent_results = [
        {
            "id": str(job.get("id") or ""),
            "status": str(job.get("status") or ""),
            "store_name": str(job.get("store_name") or ""),
            "updated_at": _timestamp(job),
            "last_error": str(job.get("last_error") or "")[:300],
            "warning_count": sum(
                isinstance(stage, dict) and stage.get("status") in {"warning", "skipped"}
                for stage in ((job.get("result") or {}).get("stages") or {}).values()
            ),
        }
        for job in recent_result_jobs
    ]
    recent_events = []
    for job in jobs:
        for event in job.get("run_log") or []:
            recent_events.append({"job_id": job.get("id") or "", **event})
    recent_events.sort(key=_timestamp, reverse=True)
    runtime = data.get("runtime_configuration") or {}
    next_action = _next_action(products, copies, jobs, worker_online)
    return {
        "metrics": metrics,
        "current_batch": {
            "active_jobs": len(active_jobs),
            "ready_jobs": len(ready_jobs),
            "needs_confirmation": sum(job.get("status") == "needs_confirmation" for job in jobs),
        },
        "connections": {
            "worker": "online" if worker_online else "offline",
            "ai_connections": len(data.get("ai_model_connections") or []),
            "nas_root": str((runtime.get("nas") or {}).get("asset_root") or ""),
            "mabang": str((runtime.get("mabang") or {}).get("session_status") or "unknown"),
        },
        "recent_events": recent_events[:10],
        "recent_results": recent_results,
        "stale_store_catalogs": _stale_store_catalogs(data, now),
        "next_action": next_action,
        "production": _production_summary(jobs, copies, products, now=now),
    }


_SAVED_STATUSES = {"saved", "saved_with_warnings"}
_FAILED_STATUSES = {"failed", "blocked"}
_ATTENTION_STATUSES = {"needs_confirmation", "needs_manual", "needs_regeneration"}


def _production_summary(
    jobs: list[dict[str, Any]],
    copies: list[dict[str, Any]],
    products: list[dict[str, Any]],
    *,
    now: str,
) -> dict[str, Any]:
    """Roll job saved/failed activity into today + 7-day buckets so the homepage
    can render production stat tiles and a small sparkline without doing math
    in the frontend. Buckets are UTC calendar days ordered oldest → newest."""
    today = _to_date(now) or datetime.now(timezone.utc).date()
    daily_saved: dict[str, int] = {}
    daily_failed: dict[str, int] = {}
    for offset in range(7):
        key = (today - _days(6 - offset)).isoformat()
        daily_saved[key] = 0
        daily_failed[key] = 0

    for job in jobs:
        status = str(job.get("status") or "").lower()
        stamp = _to_date(_timestamp(job))
        if not stamp:
            continue
        bucket = stamp.isoformat()
        if bucket in daily_saved and status in _SAVED_STATUSES:
            daily_saved[bucket] += 1
        if bucket in daily_failed and status in _FAILED_STATUSES:
            daily_failed[bucket] += 1

    today_key = today.isoformat()
    today_saved = daily_saved.get(today_key, 0)
    today_failed = daily_failed.get(today_key, 0)
    week_saved = sum(daily_saved.values())
    week_failed = sum(daily_failed.values())

    today_new_copies = sum(1 for item in copies if _to_date(_timestamp(item)) == today)
    today_new_products = sum(1 for item in products if _to_date(_timestamp(item)) == today)
    today_pending_attention = sum(
        1 for job in jobs if str(job.get("status") or "").lower() in _ATTENTION_STATUSES
    )
    week_success_rate = (
        round(week_saved / (week_saved + week_failed) * 100)
        if (week_saved + week_failed)
        else None
    )
    return {
        "today": today_key,
        "today_saved": today_saved,
        "today_failed": today_failed,
        "today_new_copies": today_new_copies,
        "today_new_products": today_new_products,
        "today_pending_attention": today_pending_attention,
        "week_saved": week_saved,
        "week_failed": week_failed,
        "week_success_rate": week_success_rate,
        "daily_saved": [{"date": k, "count": v} for k, v in daily_saved.items()],
        "daily_failed": [{"date": k, "count": v} for k, v in daily_failed.items()],
    }


def _days(count: int):
    from datetime import timedelta
    return timedelta(days=count)


def _to_date(value: str):
    """Parse ISO 8601 timestamps down to a UTC date. Returns None on failure."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).date()
    except (ValueError, TypeError):
        return None


def _next_action(products: list[dict[str, Any]], copies: list[dict[str, Any]], jobs: list[dict[str, Any]], worker_online: bool) -> dict[str, str]:
    confirmation = sorted((job for job in jobs if job.get("status") == "needs_confirmation"), key=_timestamp)
    if confirmation:
        return {"type": "open_automation_job", "id": str(confirmation[0].get("id") or ""), "label": "处理需要人工确认的任务"}
    unchecked = sorted((item for item in copies if item.get("status") not in {"checked", "ready", "saved"}), key=_timestamp)
    if unchecked:
        return {"type": "open_listing_copy", "id": str(unchecked[0].get("id") or ""), "label": "检查店铺刊登副本"}
    if any(job.get("status") == "ready" for job in jobs) and not worker_online:
        return {"type": "open_automation", "id": "", "label": "检查自动化 Worker"}
    if products:
        product = sorted(products, key=_timestamp)[0]
        return {"type": "copy_base_product", "id": str(product.get("id") or ""), "label": "复制基础商品到店铺模板"}
    return {"type": "create_base_product", "id": "", "label": "从 NAS 创建基础商品"}


def _stale_store_catalogs(data: dict[str, Any], now: str) -> list[dict[str, str]]:
    collected = ((data.get("runtime_configuration") or {}).get("store_collection") or {}).get("last_collected_at") or {}
    stale: list[dict[str, str]] = []
    for item in data.get("mabang_store_options") or []:
        store_id = str(item.get("mabang_store_id") or "")
        account_id = str(item.get("account_id") or "")
        # 先按账号+店铺找，再回退旧的「只按店铺」键，保证存量记录仍然算数。
        last = (
            collected.get(store_collection_key(account_id, store_id))
            or collected.get(store_id)
            or ""
        )
        if str(last).strip():
            continue
        stale.append(
            {
                "store_id": store_id,
                "store_name": str(item.get("store_name") or ""),
                "account_id": account_id,
                "last_collected_at": "",
            }
        )
    return stale


def _timestamp(item: dict[str, Any]) -> str:
    return str(item.get("finished_at") or item.get("updated_at") or item.get("created_at") or item.get("at") or "")


def _not_expired(value: Any, now: str) -> bool:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) > datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        return False
