from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any


def normalize_watermark_catalog(
    *,
    store_id: str,
    raw_items: list[dict[str, Any]],
    now: str,
) -> list[dict[str, Any]]:
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        raise ValueError("水印模板目录缺少店铺")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        template_id = _text(raw, "template_id", "templateId", "watermark_id", "watermarkId", "id")
        name = _text(raw, "name", "template_name", "templateName", "watermark_name")
        if not template_id or not name or template_id in seen:
            continue
        seen.add(template_id)
        normalized.append(
            {
                "id": str(uuid.uuid4()),
                "store_id": wanted_store,
                "template_id": template_id,
                "name": name,
                "preview_url": _text(raw, "preview_url", "previewUrl", "image_url", "imageUrl", "url"),
                "status": "available",
                "collected_at": now,
                "updated_at": now,
            }
        )
    return sorted(normalized, key=lambda item: (item["name"].casefold(), item["template_id"]))


def replace_watermark_catalog(
    data: dict[str, Any],
    *,
    store_id: str,
    raw_items: list[dict[str, Any]],
    now: str,
    catalog_loaded: bool,
) -> list[dict[str, Any]]:
    if not catalog_loaded:
        raise ValueError("水印模板目录未加载")
    wanted_store = str(store_id or "").strip()
    normalized = normalize_watermark_catalog(store_id=wanted_store, raw_items=raw_items, now=now)
    existing = list(data.get("store_watermark_templates") or [])
    existing_by_id = {
        str(item.get("template_id") or ""): item
        for item in existing
        if str(item.get("store_id") or "") == wanted_store
    }
    for item in normalized:
        previous = existing_by_id.get(item["template_id"])
        if previous:
            item["id"] = previous.get("id") or item["id"]
            item["created_at"] = previous.get("created_at") or previous.get("collected_at") or now
        else:
            item["created_at"] = now
    data["store_watermark_templates"] = [
        item for item in existing if str(item.get("store_id") or "") != wanted_store
    ] + normalized
    return deepcopy(normalized)


def list_watermarks(data: dict[str, Any], store_id: str) -> list[dict[str, Any]]:
    wanted_store = str(store_id or "").strip()
    return sorted(
        [
            deepcopy(item)
            for item in data.get("store_watermark_templates") or []
            if str(item.get("store_id") or "") == wanted_store
        ],
        key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("template_id") or "")),
    )


def resolve_watermark_reference(
    reference: dict[str, Any] | None,
    catalog: list[dict[str, Any]],
) -> dict[str, str]:
    raw = reference if isinstance(reference, dict) else {}
    wanted_id = str(raw.get("template_id") or raw.get("id") or "").strip()
    wanted_name = str(raw.get("name") or "").strip()
    matches = [item for item in catalog if str(item.get("template_id") or "") == wanted_id]
    if len(matches) == 1 and str(matches[0].get("name") or "").strip() == wanted_name:
        return {
            "status": "resolved",
            "template_id": wanted_id,
            "name": wanted_name,
            "matched_by": "template_id",
        }
    return {"status": "unresolved", "template_id": "", "name": wanted_name, "matched_by": ""}


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
    """记录一次「采集水印目录」的结果（每店一条）。

    只写采集状态，**不碰目录本身**：采集失败必须能在界面上看到原因，同时旧目录原样保留
    （目录替换只走 replace_watermark_catalog）。``collected_at`` 只记最后一次成功时间，
    ``attempted_at`` 记最后一次尝试时间，界面据此区分「从来没采到」和「上次采失败了」。
    """
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        raise ValueError("水印模板目录缺少店铺")
    previous = next(
        (
            item
            for item in data.get("store_watermark_catalog_runs") or []
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
    data["store_watermark_catalog_runs"] = [
        item
        for item in data.get("store_watermark_catalog_runs") or []
        if str(item.get("store_id") or "") != wanted_store
    ] + [run]
    return deepcopy(run)


def catalog_status(data: dict[str, Any], store_id: str) -> dict[str, Any] | None:
    """某店铺的水印目录采集状态（没有记录时返回 None）。"""
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        return None
    item = next(
        (
            row
            for row in data.get("store_watermark_catalog_runs") or []
            if str(row.get("store_id") or "") == wanted_store
        ),
        None,
    )
    if not item:
        return None
    status = deepcopy(item)
    status["count"] = int(status.get("count") or 0)
    return status


def _text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
