from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from services.api.category_profile_service import create_candidate_from_capture
from services.api import settings
from services.api.mabang_size_chart_import import import_mabang_size_chart
from services.api.mabang_import_snapshot import (
    MabangImportSnapshotError,
    cnsc_missing_configuration,
    normalize_mabang_capture,
    snapshot_template_payload,
)
from services.api.store_promotions import record_catalog_run as record_promotion_catalog_run
from services.api.store_promotions import replace_promotion_catalog
from services.api.store_watermarks import replace_watermark_catalog
from services.api.store_size_chart_templates import replace_size_chart_catalog


class MabangPageImportError(RuntimeError):
    pass


def stage_page_import(
    data: dict[str, Any],
    run_id: str,
    raw_capture: dict[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    staged = deepcopy(data)
    run = _find_run(staged, run_id)
    if run.get("status") == "completed":
        raise MabangPageImportError("马帮页面导入已确认，不能重新采集")
    if run.get("status") not in {"pending", "collected", "collected_with_warnings"}:
        raise MabangPageImportError("马帮页面导入当前状态不能采集")
    try:
        snapshot = normalize_mabang_capture(raw_capture)
    except MabangImportSnapshotError as exc:
        raise MabangPageImportError(str(exc)) from exc
    expected_store_id = str(run.get("expected_store_id") or "").strip()
    captured_store_id = str((snapshot.get("store") or {}).get("id") or "").strip()
    if expected_store_id and captured_store_id != expected_store_id:
        raise MabangPageImportError("当前马帮页面店铺与目标店铺不一致")
    run.update(
        {
            "status": "collected" if snapshot["coverage"]["complete"] else "collected_with_warnings",
            "snapshot": snapshot,
            "last_error": "",
            "collected_at": now,
            "updated_at": now,
        }
    )
    data.clear()
    data.update(staged)
    return deepcopy(run)


def record_page_import_error(
    data: dict[str, Any],
    run_id: str,
    message: str,
    *,
    now: str,
) -> dict[str, Any]:
    staged = deepcopy(data)
    run = _find_run(staged, run_id)
    if run.get("status") == "completed":
        raise MabangPageImportError("马帮页面导入已确认，不能记录采集错误")
    run.update({"last_error": str(message or "采集失败"), "updated_at": now})
    data.clear()
    data.update(staged)
    return deepcopy(run)


def confirm_page_import(
    data: dict[str, Any],
    run_id: str,
    *,
    now: str,
    account_id: str = "",
) -> dict[str, Any]:
    staged = deepcopy(data)
    _ensure_confirmation_collections(staged)
    run = _find_run(staged, run_id)
    if run.get("status") == "completed":
        return _confirmed_result(staged, run, already_confirmed=True)
    if run.get("status") not in {"collected", "collected_with_warnings"}:
        raise MabangPageImportError("请先采集当前马帮页面并检查快照")
    snapshot = run.get("snapshot")
    if not isinstance(snapshot, dict):
        raise MabangPageImportError("马帮页面导入缺少可确认的采集快照")

    payload = snapshot_template_payload(snapshot)
    size_chart_url = str(payload.get("size_chart_source_url") or "").strip()
    import_warnings: list[str] = []
    if size_chart_url:
        try:
            size_chart = import_mabang_size_chart(size_chart_url, settings.STORAGE_DIR)
        except (OSError, ValueError) as exc:
            # A remote Mabang/Ali CDN image can expire or be blocked while the
            # listing data itself is still valid. Keep the captured source as a
            # non-local reference so template import can finish; the operator
            # can upload a local chart later before automation.
            import_warnings.append(f"尺码图未能下载：{exc}")
        else:
            payload.update(
                {
                    "size_chart_source": "upload",
                    "size_chart_asset_id": size_chart["id"],
                    "size_chart_preview_url": size_chart["preview_url"],
                    "default_size_chart_path": size_chart["file_path"],
                    "mabang_size_chart_template_id": "",
                    "mabang_size_chart_template_name": "",
                }
            )
    store = payload["store"]
    category = payload["category"]
    _upsert_store_option(staged, store, now=now, account_id=account_id)
    if payload.get("listing_mode") == "cnsc_global":
        for target in payload.get("target_site_stores") or []:
            target_id = str(target.get("store_id") or "").strip()
            target_name = str(target.get("store_name") or "").strip()
            if not target_id and not target_name:
                continue
            _upsert_store_option(
                staged,
                {
                    "id": target_id,
                    "name": target_name or target_id,
                    "platform": store.get("platform") or "shopee",
                    "country": str(target.get("site_code") or "").strip().upper(),
                },
                now=now,
                account_id=account_id,
            )
    category_result = create_candidate_from_capture(
        staged,
        run_id=f"page-import:{run_id}",
        raw_capture={
            "platform": store["platform"],
            "country": store["country"],
            "store_id": store["id"],
            "marketplace_category_id": category["id"],
            "observed_category_id": category["id"],
            "marketplace_category_path": category["path"],
            "display_name": category["path"],
            "page_url": (snapshot.get("page") or {}).get("url") or "",
            "fields": payload["required_fields"],
            "variation": payload["variation"],
        },
        now=now,
    )
    promotions = []
    if payload.get("listing_mode") != "cnsc_global":
        catalog_loaded = snapshot.get("promotion_catalog_loaded") is True
        promotions = replace_promotion_catalog(
            staged,
            store_id=store["id"],
            raw_items=snapshot.get("promotion_catalog") or [],
            now=now,
            catalog_loaded=catalog_loaded,
        )
        # 导入也记一笔活动目录采集状态：模板编辑器要能显示「目录 N 条 · 采集于 …」，
        # 没读到活动列表时也能直接看到原因（有效期缺失就出在这里）。
        record_promotion_catalog_run(
            staged,
            store_id=store["id"],
            status="ok" if catalog_loaded else "failed",
            count=len(promotions),
            error="" if catalog_loaded else "页面导入没读到「选择折扣活动」列表；活动只有名称、没有有效期",
            catalog_loaded=catalog_loaded,
            now=now,
        )
    watermarks = None
    if snapshot.get("watermark_catalog_loaded") is True:
        watermarks = replace_watermark_catalog(
            staged,
            store_id=store["id"],
            raw_items=snapshot.get("watermark_catalog") or [],
            now=now,
            catalog_loaded=True,
        )
    size_chart_templates = None
    if snapshot.get("size_chart_template_catalog_loaded") is True:
        imported_templates = []
        for item in snapshot.get("size_chart_template_catalog") or []:
            preview_url = str(item.get("preview_url") or "").strip()
            imported = {}
            if preview_url:
                try:
                    imported = import_mabang_size_chart(preview_url, settings.STORAGE_DIR)
                except (ValueError, OSError):
                    imported = {}
            imported_templates.append(
                {
                    **item,
                    "asset_id": imported.get("id") or "",
                    "file_path": imported.get("file_path") or "",
                    "preview_url": imported.get("preview_url") or item.get("preview_url") or "",
                }
            )
        size_chart_templates = replace_size_chart_catalog(
            staged,
            store_id=store["id"],
            raw_items=imported_templates,
            now=now,
            catalog_loaded=True,
        )
    selected_promotion_id = str(payload["promotion_activity_id"] or "").strip()
    promotion_ids = {str(item.get("activity_id") or item.get("id") or "") for item in promotions}
    if selected_promotion_id and selected_promotion_id not in promotion_ids:
        raise MabangPageImportError("所选促销活动不属于当前店铺采集目录")
    template = _upsert_template(
        staged,
        store=store,
        category=category,
        category_result=category_result,
        payload=payload,
        conflict_action=str(run.get("conflict_action") or "update"),
        now=now,
        # 模板跟着采集用的马帮账号走：账号之间店铺名称可能重名，只有账号能区分。
        account_id=str(account_id or run.get("account_id") or "").strip(),
    )
    run.update(
        {
            "status": "completed",
            "store_id": store["id"],
            "category_id": category["id"],
            "category_version_id": category_result["version"]["id"],
            "template_id": template["id"],
            "promotion_count": len(promotions),
            "watermark_count": len(watermarks) if watermarks is not None else None,
            "size_chart_template_count": len(size_chart_templates) if size_chart_templates is not None else None,
            "confirmed_at": now,
            "updated_at": now,
            "import_warnings": import_warnings,
        }
    )
    data.clear()
    data.update(staged)
    return _confirmed_result(data, _find_run(data, run_id), already_confirmed=False)


def _ensure_confirmation_collections(data: dict[str, Any]) -> None:
    for collection in (
        "mabang_store_options",
        "category_profiles",
        "category_profile_versions",
        "category_collection_runs",
        "store_promotion_activities",
        "store_watermark_templates",
        "store_templates",
        "store_template_versions",
        "mabang_page_import_runs",
    ):
        data.setdefault(collection, [])


def _upsert_store_option(
    data: dict[str, Any],
    store: dict[str, Any],
    *,
    now: str,
    account_id: str = "",
) -> dict[str, Any]:
    options = data.setdefault("mabang_store_options", [])
    wanted_account = str(account_id or store.get("account_id") or "").strip()
    existing = next(
        (
            item
            for item in options
            if str(item.get("account_id") or "").strip() == wanted_account
            and str(item.get("platform") or "").lower() == str(store["platform"]).lower()
            and str(item.get("mabang_store_id") or "") == str(store["id"])
        ),
        None,
    )
    option = {
        "id": (existing or {}).get("id") or str(uuid.uuid4()),
        "platform": store["platform"],
        "country": store["country"],
        "store_name": store["name"],
        "mabang_store_id": store["id"],
        "account_id": wanted_account,
        "source": "mabang_page_import",
        "status": "active",
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    if existing is None:
        options.append(option)
        return option
    existing.clear()
    existing.update(option)
    return existing


def _upsert_template(
    data: dict[str, Any],
    *,
    store: dict[str, Any],
    category: dict[str, Any],
    category_result: dict[str, Any],
    payload: dict[str, Any],
    conflict_action: str,
    now: str,
    account_id: str = "",
) -> dict[str, Any]:
    if conflict_action not in {"update", "save_as_new"}:
        raise MabangPageImportError("模板冲突操作必须是 update 或 save_as_new")
    templates = data.setdefault("store_templates", [])
    existing = next(
        (
            item
            for item in templates
            if str(item.get("listing_mode") or "site").strip().lower() == str(payload.get("listing_mode") or "site").strip().lower()
            and item.get("mabang_store_id") == store["id"]
            and item.get("mabang_category_id") == category["id"]
        ),
        None,
    )
    if existing is not None and conflict_action == "update":
        template = existing
        version = int(template.get("version") or 1) + 1
    else:
        template = {"id": str(uuid.uuid4()), "created_at": now}
        templates.append(template)
        version = 1
    existing_snapshot = deepcopy(template)
    listing_mode = str(payload.get("listing_mode") or "site").strip().lower()
    target_site_stores = deepcopy(payload.get("target_site_stores") or [])
    if listing_mode == "cnsc_global" and payload.get("target_site_stores_loaded") is not True:
        target_site_stores = deepcopy(existing_snapshot.get("target_site_stores") or [])
    elif listing_mode == "cnsc_global":
        existing_targets = {
            (str(item.get("site_code") or "").upper(), str(item.get("store_id") or item.get("store_name") or "")): item
            for item in existing_snapshot.get("target_site_stores") or []
            if isinstance(item, dict)
        }
        discount_fields = (
            "mabang_discount_id", "discount_name", "discount_label", "discount_percent",
            "discount_catalog_loaded", "discount_catalog",
        )
        for target in target_site_stores:
            identity = (str(target.get("site_code") or "").upper(), str(target.get("store_id") or target.get("store_name") or ""))
            previous = existing_targets.get(identity) or {}
            if target.get("discount_catalog_loaded") is not True:
                for field in discount_fields:
                    if field in previous:
                        target[field] = deepcopy(previous[field])
    price_tiers = deepcopy(payload.get("price_tiers") or [])
    if listing_mode == "cnsc_global":
        existing_tiers = {
            str(item.get("id") or ""): item
            for item in existing_snapshot.get("price_tiers") or []
            if isinstance(item, dict)
        }
        for tier in price_tiers:
            previous = existing_tiers.get(str(tier.get("id") or "")) or {}
            if not tier.get("site_prices"):
                tier["site_prices"] = deepcopy(previous.get("site_prices") or {})
    warehouse_stock_defaults = deepcopy(payload.get("warehouse_stock_defaults") or {})
    missing_configuration = []
    if listing_mode == "cnsc_global":
        missing_configuration = cnsc_missing_configuration(
            price_tiers=price_tiers,
            warehouse_stock_defaults=warehouse_stock_defaults,
            target_site_stores=target_site_stores,
        )
    version_id = str(uuid.uuid4())
    template.update(
        {
            "listing_mode": listing_mode,
            "store_name": store["name"],
            "mabang_store_id": store["id"],
            "account_id": str(account_id or template.get("account_id") or "").strip(),
            "template_name": f"{store['name']} - {category['path']}",
            "platform": store["platform"],
            "country": store["country"],
            "category_profile_id": category_result["profile"]["id"],
            "category_profile_version_id": category_result["version"]["id"],
            "marketplace_category_path": category["path"],
            "mabang_category_id": category["id"],
            "attribute_values": deepcopy(payload["attribute_values"]),
            "category_attributes": deepcopy(payload["attribute_values"]),
            "variation": deepcopy(payload["variation"]),
            "sizes": list(payload["sizes"]),
            "cnsc_store": deepcopy(payload.get("cnsc_store") or {}) if listing_mode == "cnsc_global" else {},
            "target_site_stores": target_site_stores if listing_mode == "cnsc_global" else [],
            "default_site_discount_percent": (
                payload.get("default_site_discount_percent")
                or existing_snapshot.get("default_site_discount_percent")
                or 40
            ) if listing_mode == "cnsc_global" else None,
            "price_tiers": price_tiers if listing_mode == "cnsc_global" else [],
            "warehouse_stock_defaults": warehouse_stock_defaults if listing_mode == "cnsc_global" else {},
            "cnsc_import_sku_rows": deepcopy(payload.get("cnsc_import_sku_rows") or []) if listing_mode == "cnsc_global" else [],
            "missing_configuration": missing_configuration,
            "promotion_activity_id": payload["promotion_activity_id"],
            "mabang_discount_id": payload["mabang_discount_id"],
            "normal_price": payload["normal_price"],
            "sale_price": payload["sale_price"],
            "size_chart_asset_id": payload.get("size_chart_asset_id", ""),
            "size_chart_preview_url": payload.get("size_chart_preview_url", ""),
            "size_chart_source": payload.get("size_chart_source", "upload"),
            "mabang_size_chart_template_id": payload.get("mabang_size_chart_template_id", ""),
            "mabang_size_chart_template_name": payload.get("mabang_size_chart_template_name", ""),
            "default_size_chart_path": payload.get("default_size_chart_path", ""),
            "stock": payload["stock"],
            "weight_kg": payload["weight_kg"],
            "package_length_cm": payload["package_length_cm"],
            "package_width_cm": payload["package_width_cm"],
            "package_height_cm": payload["package_height_cm"],
            "transaction_info": deepcopy(payload["transaction_info"]),
            "missing_required_attributes": deepcopy(payload["missing_required_attributes"]),
            "import_coverage": deepcopy(payload["import_coverage"]),
            "status": "needs_configuration" if missing_configuration else "active",
            "version": version,
            "current_version_id": version_id,
            "updated_at": now,
        }
    )
    data.setdefault("store_template_versions", []).append(
        {
            "id": version_id,
            "template_id": template["id"],
            "version": version,
            "snapshot": deepcopy(template),
            "created_at": now,
        }
    )
    return template


def _confirmed_result(data: dict[str, Any], run: dict[str, Any], *, already_confirmed: bool) -> dict[str, Any]:
    template_id = str(run.get("template_id") or "")
    category_version_id = str(run.get("category_version_id") or "")
    template = next((item for item in data.get("store_templates", []) if item.get("id") == template_id), None)
    category_version = next(
        (item for item in data.get("category_profile_versions", []) if item.get("id") == category_version_id),
        None,
    )
    category_profile = next(
        (item for item in data.get("category_profiles", []) if item.get("id") == (category_version or {}).get("profile_id")),
        None,
    )
    if not isinstance(template, dict) or not isinstance(category_version, dict) or not isinstance(category_profile, dict):
        raise MabangPageImportError("已确认的马帮页面导入缺少业务数据")
    promotions = [
        deepcopy(item)
        for item in data.get("store_promotion_activities", [])
        if str(item.get("store_id") or "") == str(run.get("store_id") or "")
    ]
    return {
        "run": {**deepcopy(run), "already_confirmed": already_confirmed},
        "category_profile": deepcopy(category_profile),
        "category_version": deepcopy(category_version),
        "promotions": promotions,
        "template": deepcopy(template),
    }


def _find_run(data: dict[str, Any], run_id: str) -> dict[str, Any]:
    runs = data.setdefault("mabang_page_import_runs", [])
    run = next((item for item in runs if item.get("id") == run_id), None)
    if not isinstance(run, dict):
        raise MabangPageImportError("马帮页面导入任务不存在")
    return run
