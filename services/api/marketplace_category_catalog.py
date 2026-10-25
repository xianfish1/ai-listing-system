from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class MarketplaceCategoryCatalogConflict(ValueError):
    pass


class MarketplaceCategoryCatalogNotFound(ValueError):
    pass


def create_catalog_run(data: dict[str, Any], payload: dict[str, Any], *, now: str) -> dict[str, Any]:
    platform = str(payload.get("platform") or "shopee").strip().lower()
    country = str(payload.get("country") or "").strip().upper()
    store_id = str(payload.get("store_id") or payload.get("mabang_store_id") or "").strip()
    if not country:
        raise MarketplaceCategoryCatalogConflict("country is required")
    if not store_id:
        raise MarketplaceCategoryCatalogConflict("store_id is required")
    run = {
        "id": str(uuid.uuid4()),
        "platform": platform,
        "country": country,
        "store_id": store_id,
        "status": "pending",
        "source": str(payload.get("source") or "mabang_browser").strip(),
        "created_at": now,
        "updated_at": now,
    }
    data.setdefault("marketplace_category_catalog_runs", []).append(run)
    return deepcopy(run)


def complete_catalog_run(
    data: dict[str, Any],
    run_id: str,
    raw_capture: dict[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    run = next(
        (item for item in data.get("marketplace_category_catalog_runs", []) if item.get("id") == run_id),
        None,
    )
    if not run:
        raise MarketplaceCategoryCatalogNotFound(f"Marketplace category catalog run not found: {run_id}")
    if run.get("status") != "pending":
        raise MarketplaceCategoryCatalogConflict(f"Catalog run is already {run.get('status')}")

    newer_completed = next(
        (
            item
            for item in data.get("marketplace_category_catalog_runs", [])
            if item.get("id") != run_id
            and _scope(item) == _scope(run)
            and item.get("status") == "completed"
            and str(item.get("created_at") or "") > str(run.get("created_at") or "")
        ),
        None,
    )
    if newer_completed:
        raise MarketplaceCategoryCatalogConflict("Catalog run is stale because a newer run already completed")

    categories = normalize_catalog_capture(raw_capture, run=run, now=now)
    catalog = data.setdefault("marketplace_categories", [])
    scope = (run["platform"], run["country"], run["store_id"])
    existing_active_count = sum(1 for item in catalog if _scope(item) == scope and item.get("active", True))
    _validate_authoritative_capture(raw_capture, categories, existing_active_count=existing_active_count)
    existing_by_category_id: dict[str, dict[str, Any]] = {}
    previously_active_ids: set[str] = set()
    for item in catalog:
        if _scope(item) != scope:
            continue
        category_id = str(item.get("category_id") or "")
        existing_by_category_id[category_id] = item
        if item.get("active", True):
            previously_active_ids.add(category_id)
            item["active"] = False
            item["updated_at"] = now

    created_count = 0
    updated_count = 0
    captured_ids = {category["category_id"] for category in categories}
    for category in categories:
        existing = existing_by_category_id.get(category["category_id"])
        if existing:
            category["id"] = existing.get("id") or category["id"]
            category["created_at"] = existing.get("created_at") or category["created_at"]
            existing.clear()
            existing.update(category)
            updated_count += 1
        else:
            catalog.append(category)
            created_count += 1

    run.update(
        {
            "status": "completed",
            "saved_count": len(categories),
            "created_count": created_count,
            "updated_count": updated_count,
            "deactivated_count": len(previously_active_ids - captured_ids),
            "source_url": sanitize_source_url(raw_capture.get("source_url") or raw_capture.get("url") or ""),
            "completed_at": now,
            "updated_at": now,
        }
    )
    return deepcopy(run)


def normalize_catalog_capture(raw_capture: dict[str, Any], *, run: dict[str, Any], now: str) -> list[dict[str, Any]]:
    if not isinstance(raw_capture, dict):
        raise MarketplaceCategoryCatalogConflict("Category catalog capture must be an object")
    roots = raw_capture.get("categories")
    if not isinstance(roots, list) or not roots:
        raise MarketplaceCategoryCatalogConflict("Category catalog capture contains no categories")

    flattened: list[dict[str, Any]] = []

    def visit(raw: Any, inherited_parent_id: str = "", inherited_path: tuple[str, ...] = ()) -> None:
        if not isinstance(raw, dict):
            return
        category_id = _first_text(raw, "category_id", "categoryId", "cat_id", "catId", "id", "value")
        name = _first_text(raw, "category_name", "categoryName", "name", "label", "title")
        if not category_id or not name:
            return
        parent_id = _first_text(raw, "parent_id", "parentId", "parent_category_id", "pid") or inherited_parent_id
        explicit_path = _path_parts(raw.get("path") or raw.get("category_path") or raw.get("categoryPath"))
        path_parts = tuple(explicit_path) if explicit_path else (*inherited_path, name)
        name_zh = _first_text(raw, "name_zh", "category_name_zh", "categoryNameZh")
        name_en = _first_text(raw, "name_en", "category_name_en", "categoryNameEn") or name
        children = _children(raw)
        raw_zh_path = str(raw.get("name_zh_path") or raw.get("path_zh") or "").strip()
        raw_en_path = str(raw.get("name_en_path") or raw.get("path_en") or "").strip()
        flattened.append(
            {
                "category_id": category_id,
                "parent_id": parent_id,
                "name": name,
                "name_zh": name_zh,
                "name_en": name_en,
                "path_parts": path_parts,
                # Mabang's localized path fields can be malformed for some shops
                # (empty segments, e.g. a root whose name_zh_path is just ",").
                # An explicit path with any empty segment is unreliable — drop it
                # so resolved_localized_path rebuilds it node-by-node from name_zh.
                "path_zh_parts": tuple(_clean_path_segments(raw_zh_path)),
                "path_en_parts": tuple(_clean_path_segments(raw_en_path)),
                "explicit_is_leaf": _optional_bool(raw.get("is_leaf", raw.get("isLeaf"))),
                "has_nested_children": bool(children),
            }
        )
        for child in children:
            visit(child, category_id, path_parts)

    for root in roots:
        visit(root)
    if not flattened:
        raise MarketplaceCategoryCatalogConflict("Category catalog capture contains no identifiable categories")

    unique: dict[str, dict[str, Any]] = {}
    for item in flattened:
        unique.setdefault(item["category_id"], item)
    parent_ids = {item["parent_id"] for item in unique.values() if item["parent_id"]}

    def resolved_path(category_id: str, stack: tuple[str, ...] = ()) -> tuple[str, ...]:
        item = unique[category_id]
        if len(item["path_parts"]) > 1 or not item["parent_id"]:
            return item["path_parts"]
        parent_id = item["parent_id"]
        if parent_id not in unique or parent_id in stack:
            return item["path_parts"]
        return (*resolved_path(parent_id, (*stack, category_id)), item["name"])

    def resolved_localized_path(
        category_id: str,
        *,
        path_key: str,
        name_key: str,
        stack: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        item = unique[category_id]
        current_name = item[name_key] or item["name"]
        parent_id = item["parent_id"]
        if parent_id and parent_id in unique and parent_id not in stack:
            return (
                *resolved_localized_path(parent_id, path_key=path_key, name_key=name_key, stack=(*stack, category_id)),
                current_name,
            )
        if item[path_key]:
            return item[path_key]
        return (current_name,)

    platform = str(run.get("platform") or "shopee").strip().lower()
    country = str(run.get("country") or "").strip().upper()
    store_id = str(run.get("store_id") or "").strip()
    normalized = []
    for category_id, item in unique.items():
        path_parts = resolved_path(category_id)
        path_zh_parts = resolved_localized_path(category_id, path_key="path_zh_parts", name_key="name_zh")
        path_en_parts = resolved_localized_path(category_id, path_key="path_en_parts", name_key="name_en")
        effective_path_parts = path_en_parts if len(path_en_parts) >= len(path_parts) else path_parts
        explicit_leaf = item["explicit_is_leaf"]
        is_leaf = explicit_leaf if explicit_leaf is not None else not item["has_nested_children"] and category_id not in parent_ids
        stable_key = f"{platform}:{country}:{store_id}:{category_id}"
        normalized.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"marketplace-category:{stable_key}")),
                "platform": platform,
                "country": country,
                "store_id": store_id,
                "category_id": category_id,
                "parent_id": item["parent_id"],
                "name": item["name"],
                "name_zh": item["name_zh"],
                "name_en": item["name_en"],
                "path": " > ".join(effective_path_parts),
                "path_zh": " > ".join(path_zh_parts),
                "path_en": " > ".join(effective_path_parts),
                "level": len(path_parts),
                "is_leaf": bool(is_leaf),
                "active": True,
                "source_run_id": str(run.get("id") or ""),
                "created_at": now,
                "updated_at": now,
            }
        )
    return sorted(normalized, key=lambda item: (item["path"].casefold(), item["category_id"]))


def search_catalog(
    categories: list[dict[str, Any]],
    *,
    platform: str = "",
    country: str = "",
    store_id: str = "",
    query: str = "",
    parent_id: str | None = None,
    leaf_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    wanted_platform = str(platform or "").strip().lower()
    wanted_country = str(country or "").strip().upper()
    wanted_store = str(store_id or "").strip()
    needle = str(query or "").strip().casefold()
    wanted_parent = None if parent_id is None else str(parent_id or "").strip()
    bounded_limit = max(1, min(int(limit or 50), 500))
    scoped = []
    for item in categories:
        if not item.get("active", True):
            continue
        if wanted_platform and str(item.get("platform") or "").strip().lower() != wanted_platform:
            continue
        if wanted_country and str(item.get("country") or "").strip().upper() != wanted_country:
            continue
        if wanted_store and str(item.get("store_id") or "").strip() != wanted_store:
            continue
        scoped.append(item)

    parent_ids = {str(item.get("parent_id") or "").strip() for item in scoped if str(item.get("parent_id") or "").strip()}
    matches = []
    for item in scoped:
        item_parent = str(item.get("parent_id") or "").strip()
        if wanted_parent is not None:
            if wanted_parent in {"", "0"} and item_parent not in {"", "0"}:
                continue
            if wanted_parent not in {"", "0"} and item_parent != wanted_parent:
                continue
        if leaf_only and not item.get("is_leaf"):
            continue
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("category_id", "name", "name_zh", "name_en", "path", "path_zh", "path_en")
        ).casefold()
        if needle and needle not in haystack:
            continue
        match = deepcopy(item)
        match["has_children"] = str(item.get("category_id") or "").strip() in parent_ids
        matches.append(match)
    matches.sort(
        key=lambda item: (
            0 if needle and str(item.get("category_id") or "").casefold() == needle else 1,
            0 if needle and str(item.get("name") or "").casefold().startswith(needle) else 1,
            str(item.get("path") or "").casefold(),
            str(item.get("category_id") or ""),
        )
    )
    return matches[:bounded_limit]


def sanitize_source_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def sanitize_mabang_urls_in_store(data: dict[str, Any]) -> bool:
    changed = False

    def visit(value: Any) -> None:
        nonlocal changed
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key in {"url", "page_url", "source_url"} and isinstance(item, str) and "mabangerp.com" in item:
                    sanitized = sanitize_source_url(item)
                    if sanitized != item:
                        value[key] = sanitized
                        changed = True
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data)
    return changed


def _validate_authoritative_capture(
    raw_capture: dict[str, Any],
    categories: list[dict[str, Any]],
    *,
    existing_active_count: int,
) -> None:
    if str(raw_capture.get("discovery_source") or "").strip() != "mabang_shopee_getCategory_api":
        raise MarketplaceCategoryCatalogConflict("Category catalog capture is not from the authoritative Mabang API")
    raw_ids = _raw_category_ids(raw_capture.get("categories") or [])
    unique_ids = {item["category_id"] for item in categories}
    try:
        declared_count = int(raw_capture.get("category_count"))
    except (TypeError, ValueError) as exc:
        raise MarketplaceCategoryCatalogConflict("Category catalog capture is missing category_count") from exc
    if declared_count != len(raw_ids) or len(raw_ids) != len(unique_ids):
        raise MarketplaceCategoryCatalogConflict("Category catalog capture count or category IDs are inconsistent")
    if any(not category_id.isdigit() for category_id in unique_ids):
        raise MarketplaceCategoryCatalogConflict("Shopee category IDs must be numeric")
    parent_ids = {item["parent_id"] for item in categories if item.get("parent_id")}
    missing_parents = sorted(parent_ids - unique_ids)
    if missing_parents:
        raise MarketplaceCategoryCatalogConflict(f"Category catalog capture has missing parents: {missing_parents[:5]}")
    if not any(not item.get("parent_id") for item in categories):
        raise MarketplaceCategoryCatalogConflict("Category catalog capture has no root categories")
    if existing_active_count and len(categories) < max(1, int(existing_active_count * 0.8)):
        raise MarketplaceCategoryCatalogConflict(
            f"Category catalog shrank from {existing_active_count} to {len(categories)}; refusing partial replacement"
        )


def _raw_category_ids(items: list[Any]) -> list[str]:
    category_ids: list[str] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        category_id = _first_text(raw, "category_id", "categoryId", "cat_id", "catId", "id", "value")
        if category_id:
            category_ids.append(category_id)
        category_ids.extend(_raw_category_ids(_children(raw)))
    return category_ids


def _scope(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("platform") or "").strip().lower(),
        str(item.get("country") or "").strip().upper(),
        str(item.get("store_id") or "").strip(),
    )


def _first_text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _children(raw: dict[str, Any]) -> list[Any]:
    for key in ("children", "subcategories", "subCategories", "category_list", "categoryList"):
        value = raw.get(key)
        if isinstance(value, list):
            return value
    return []


def _path_parts(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"\s*(?:>|/|≫|›)\s*", value) if part.strip()]
    return []


def _clean_path_segments(value: str) -> list[str]:
    """Split a localized path string into non-empty segments when unambiguous.

    Mabang returns name_zh_path / name_en_path with "," between nodes, but a
    category *name* may itself contain a comma (e.g. "Tickets, Vouchers &
    Services") and some shops emit empty segments (a root whose name_zh_path is
    just ","). A single string cannot reliably separate in-name commas from
    node separators, so only accept the explicit path when it is ">"-delimited
    with no empty segments; anything ambiguous returns [] and the caller
    rebuilds the path from parent links (resolved_localized_path) instead.
    """
    raw = str(value or "").strip()
    if not raw or "," in raw:
        return []
    if raw.startswith((">", "/", "≫", "›")) or raw.endswith((">", "/", "≫", "›")):
        return []
    segments = [seg.strip() for seg in re.split(r"\s*(?:>|/|≫|›)\s*", raw) if seg.strip()]
    return segments if segments else []


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "0", "no"}:
        return False
    return None
