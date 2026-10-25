from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any


def replace_size_chart_catalog(
    data: dict[str, Any],
    *,
    store_id: str,
    raw_items: list[dict[str, Any]],
    now: str,
    catalog_loaded: bool,
) -> list[dict[str, Any]]:
    if not catalog_loaded:
        raise ValueError("尺码图模板目录未加载")
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        raise ValueError("尺码图模板目录缺少店铺")
    existing = list(data.get("store_size_chart_templates") or [])
    previous_by_template_id = {
        str(item.get("template_id") or ""): item
        for item in existing
        if str(item.get("store_id") or "") == wanted_store
    }
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        template_id = _text(raw, "template_id", "templateId", "id")
        name = _text(raw, "name", "template_name", "templateName")
        asset_id = _text(raw, "asset_id", "assetId")
        file_path = _text(raw, "file_path", "filePath")
        if not template_id or not name or template_id in seen:
            continue
        seen.add(template_id)
        previous = previous_by_template_id.get(template_id) or {}
        normalized.append(
            {
                "id": previous.get("id") or str(uuid.uuid4()),
                "store_id": wanted_store,
                "template_id": template_id,
                "name": name,
                "asset_id": asset_id,
                "file_path": file_path,
                "preview_url": _text(raw, "preview_url", "previewUrl"),
                "status": "available" if asset_id and file_path else "metadata_only",
                "created_at": previous.get("created_at") or now,
                "collected_at": now,
                "updated_at": now,
            }
        )
    data["store_size_chart_templates"] = [
        item for item in existing if str(item.get("store_id") or "") != wanted_store
    ] + normalized
    return deepcopy(normalized)


def list_size_chart_templates(data: dict[str, Any], store_id: str) -> list[dict[str, Any]]:
    wanted_store = str(store_id or "").strip()
    return sorted(
        [
            deepcopy(item)
            for item in data.get("store_size_chart_templates") or []
            if str(item.get("store_id") or "") == wanted_store
        ],
        key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("template_id") or "")),
    )


def _text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
