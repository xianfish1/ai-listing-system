from __future__ import annotations

import json
import tempfile
import uuid
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from services.api import settings, storage


MABANG_DIRECTORY_MAP = {
    "POD 男装T恤": "clothing",
    "POD 女装T恤": "clothing",
    "POD 男童T恤": "clothing",
    "POD 女童T恤": "clothing",
    "POD 童装T恤": "clothing",
}

LIST_SHEET_NAME = "产品导入列表"
TEMPLATE_SHEET_NAME = "产品导入示例"
FIELDS_SHEET_NAME = "字段说明"
LIST_HEADER_ROW = 1
LIST_DATA_START_ROW = 2
TEMPLATE_MAIN_ROW = 4
TEMPLATE_CHILD_ROW = 5

_LOCAL_TEMPLATE_FALLBACK = settings.PROJECT_ROOT / "storage" / "samples" / "mabang-template.xlsx"

REQUIRED_TEMPLATE_SHEETS = [
    LIST_SHEET_NAME,
    TEMPLATE_SHEET_NAME,
    FIELDS_SHEET_NAME,
]

MABANG_LIST_HEADERS = [
    "*主sku编号",
    "*产品中文名",
    "产品英文名",
    "产品日文名",
    "对标产品链接",
    "产品目录",
    "品牌",
    "子品牌",
    "成本价",
    "起订量",
    "产品重量(克)",
    "产品标签",
    "视频URL",
    "原厂SKU",
    "开发员",
    "采购员",
    "产品长度",
    "产品宽度",
    "产品高度",
    "橱窗图",
    "多属性图",
    "描述图",
    "自定义图片组",
    "复杂描述",
    "纯文本描述",
    "纯文本描述（中文）",
    "Bullet Point",
    "Search Terms",
    "申报品名（中文）",
    "申报品名（英文）",
    "申报价格（USD）",
    "报关编码",
    "特殊标记",
    "竞品标题",
    "竞品链接",
    "供应商名称",
    "供应商商品标题",
    "供货产品链接",
    "采购天数",
    "库存警戒天数",
    "质检标准",
    "美工备注",
    "销售备注",
    "采购备注",
    "商品备注",
    "子sku编号",
    "属性名",
    "属性值",
    "子sku多属性图片",
    "子sku中文标题",
    "子sku英文标题",
    "子sku成本价",
    "子sku重量(克)",
    "子sku数量",
    "子sku长",
    "子sku宽",
    "子sku高",
    "子sku起订量",
    "子sku包装长度",
    "子sku包装宽度",
    "子sku包装高度",
    "子sku原厂SKU",
]
MABANG_LIST_COLUMN_COUNT = len(MABANG_LIST_HEADERS)


def _resolve_template_path() -> Path:
    """Return the path to the Mabang import template.

    Order:
    1. ``settings.MABANG_TEMPLATE_PATH`` when that file is reachable.
    2. ``storage/samples/mabang-template.xlsx`` shipped with the repo.

    The local fallback is what keeps the export working on machines where the
    network share ``\\\\Juns\\...`` referenced by the default settings is not
    mounted, and lets the HTTP endpoint serve a real ``.xlsx`` instead of 404.
    """
    candidate = Path(settings.MABANG_TEMPLATE_PATH)
    if candidate.is_file():
        return candidate
    if _LOCAL_TEMPLATE_FALLBACK.is_file():
        return _LOCAL_TEMPLATE_FALLBACK
    raise FileNotFoundError(
        f"Mabang template not found at {candidate} or local fallback {_LOCAL_TEMPLATE_FALLBACK}"
    )

MABANG_OPTIONAL_FIELD_KEYS = [
    "product_name_en",
    "product_name_jp",
    "brand",
    "sub_brand",
    "video_url",
    "description",
    "bullet_point",
    "search_terms",
    "declaration_name_cn",
    "declaration_name_en",
    "declaration_price_usd",
    "customs_code",
    "competitor_title",
    "competitor_link",
    "supplier_name",
    "supplier_product_title",
    "supply_link",
    "purchase_days",
    "stock_warning_days",
    "qc_standard",
    "design_note",
    "sales_note",
    "purchase_note",
    "product_note",
]


def collect_mabang_bundles(product_ids: list[str]) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_product_id in product_ids:
        product_id = str(raw_product_id or "").strip()
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        bundle = build_mabang_bundle(product_id)
        if bundle:
            bundles.append(bundle)
    return bundles


def build_mabang_bundle(product_id: str) -> dict[str, Any] | None:
    product = storage.get_item("products", product_id)
    if not product:
        return None
    group_id = str(product.get("group_id") or "").strip()
    assets = storage.assets_for_base_product(product)
    skus = _sorted_skus(storage.list_skus(product_id))
    prices = storage.list_prices_for_product(product_id)
    return {
        "product": dict(product),
        "assets": assets,
        "skus": skus,
        "prices": prices,
        "product_id": product_id,
        "group_id": group_id,
    }


def validate_mabang_bundles(bundles: list[dict[str, Any]]) -> dict[str, Any]:
    if not bundles:
        empty_blocker = {
            "code": "no_products_selected",
            "message": "请先选择至少 1 个商品再导出马帮模板。",
            "product_id": "",
            "group_id": "",
        }
        return {
            "valid": False,
            "errors": [empty_blocker],
            "blockers": [empty_blocker],
            "warnings": [],
            "bundle_count": 0,
            "row_count": 0,
            "filled_fields": [],
            "missing_fields": [],
            "items": [],
        }

    items: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    filled_fields: set[str] = set()
    missing_fields: set[str] = set()
    row_count = 0

    for bundle in bundles:
        item = _summarize_mabang_bundle(bundle)
        items.append(item)
        row_count += int(item.get("row_count") or 0)
        blockers.extend(item.get("blockers") or [])
        warnings.extend(item.get("warnings") or [])
        filled_fields.update(item.get("filled_field_keys") or [])
        missing_fields.update(item.get("missing_fields") or [])

    return {
        "valid": not blockers,
        "errors": list(blockers),
        "blockers": list(blockers),
        "warnings": list(warnings),
        "bundle_count": len(bundles),
        "row_count": row_count,
        "filled_fields": sorted(filled_fields),
        "missing_fields": sorted(missing_fields),
        "items": items,
    }


def build_mabang_preview_payload(bundles: list[dict[str, Any]]) -> dict[str, Any]:
    validation = validate_mabang_bundles(bundles)
    return {
        "can_export": validation["valid"],
        "bundle_count": validation["bundle_count"],
        "row_count": validation["row_count"],
        "blockers": validation["blockers"],
        "warnings": validation["warnings"],
        "filled_fields": validation["filled_fields"],
        "missing_fields": validation["missing_fields"],
        "items": validation["items"],
        "validation": validation,
    }


def _summarize_mabang_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    profile = _resolve_mabang_profile(bundle)
    product = profile["product"]
    bundle_prices = list(bundle.get("prices") or [])
    product_id = str(product.get("id") or profile.get("product_id") or "")
    group_id = str(profile.get("group_id") or "").strip()
    main_image_paths = profile["main_images"]
    skus = profile["skus"]
    sku_states = profile["sku_states"]
    unresolved_price_skus = [state["sku_code"] for state in sku_states if not _value_present(state.get("cost_price"))]
    unresolved_weight_skus = [state["sku_code"] for state in sku_states if not _value_present(state.get("weight_kg"))]
    unresolved_dimension_skus = [
        state["sku_code"]
        for state in sku_states
        if not all(_value_present(state.get(key)) for key in ["length_cm", "width_cm", "height_cm"])
    ]
    unresolved_variant_image_skus = [state["sku_code"] for state in sku_states if not _value_present(state.get("variant_image"))]

    field_states = [
        {"key": "group_id", "required": True, "value": group_id, "present": _value_present(group_id)},
        {"key": "product_name_cn", "required": True, "value": profile["product_name_cn"], "present": _value_present(profile["product_name_cn"])},
        {"key": "product_name_en", "required": False, "value": profile["product_name_en"], "present": _value_present(profile["product_name_en"])},
        {"key": "product_name_jp", "required": False, "value": profile["product_name_jp"], "present": _value_present(profile["product_name_jp"])},
        {"key": "directory", "required": True, "value": profile["directory"], "present": _value_present(profile["directory"])},
        {"key": "brand", "required": False, "value": profile["brand"], "present": _value_present(profile["brand"])},
        {"key": "sub_brand", "required": False, "value": profile["sub_brand"], "present": _value_present(profile["sub_brand"])},
        {"key": "video_url", "required": False, "value": profile["video_url"], "present": _value_present(profile["video_url"])},
        {"key": "main_image", "required": True, "value": str(product.get("main_image") or "").strip(), "present": _value_present(str(product.get("main_image") or "").strip())},
        {"key": "description", "required": False, "value": profile["description"], "present": _value_present(profile["description"])},
        {"key": "bullet_point", "required": False, "value": profile["bullet_point"], "present": _value_present(profile["bullet_point"])},
        {"key": "search_terms", "required": False, "value": profile["search_terms"], "present": _value_present(profile["search_terms"])},
        {"key": "declaration_name_cn", "required": False, "value": profile["declaration_name_cn"], "present": _value_present(profile["declaration_name_cn"])},
        {"key": "declaration_name_en", "required": False, "value": profile["declaration_name_en"], "present": _value_present(profile["declaration_name_en"])},
        {"key": "declaration_price_usd", "required": False, "value": profile["declaration_price_usd"], "present": _value_present(profile["declaration_price_usd"])},
        {"key": "customs_code", "required": False, "value": profile["customs_code"], "present": _value_present(profile["customs_code"])},
        {"key": "competitor_title", "required": False, "value": profile["competitor_title"], "present": _value_present(profile["competitor_title"])},
        {"key": "competitor_link", "required": False, "value": profile["competitor_link"], "present": _value_present(profile["competitor_link"])},
        {"key": "supplier_name", "required": False, "value": profile["supplier_name"], "present": _value_present(profile["supplier_name"])},
        {"key": "supplier_product_title", "required": False, "value": profile["supplier_product_title"], "present": _value_present(profile["supplier_product_title"])},
        {"key": "supply_link", "required": False, "value": profile["supply_link"], "present": _value_present(profile["supply_link"])},
        {"key": "purchase_days", "required": False, "value": profile["purchase_days"], "present": _value_present(profile["purchase_days"])},
        {"key": "stock_warning_days", "required": False, "value": profile["stock_warning_days"], "present": _value_present(profile["stock_warning_days"])},
        {"key": "qc_standard", "required": False, "value": profile["qc_standard"], "present": _value_present(profile["qc_standard"])},
        {"key": "design_note", "required": False, "value": profile["design_note"], "present": _value_present(profile["design_note"])},
        {"key": "sales_note", "required": False, "value": profile["sales_note"], "present": _value_present(profile["sales_note"])},
        {"key": "purchase_note", "required": False, "value": profile["purchase_note"], "present": _value_present(profile["purchase_note"])},
        {"key": "product_note", "required": False, "value": profile["product_note"], "present": _value_present(profile["product_note"])},
        {"key": "cost_price", "required": True, "value": [str(p.get("cost_price") or "").strip() for p in bundle_prices], "present": any(_value_present(p.get("cost_price")) for p in bundle_prices)},
        {"key": "weight_kg", "required": True, "value": [str(s.get("weight_kg") or "").strip() for s in skus] + [str(product.get("weight_kg") or "").strip()], "present": any(_value_present(s.get("weight_kg")) for s in skus) or _value_present(product.get("weight_kg"))},
        {"key": "dimensions", "required": True, "value": [[str(s.get(k) or "").strip() for k in ["length_cm", "width_cm", "height_cm"]] for s in skus] + [[str(product.get(k) or "").strip() for k in ["length_cm", "width_cm", "height_cm"]]], "present": any(all(_value_present(s.get(k)) for k in ["length_cm", "width_cm", "height_cm"]) for s in skus) or all(_value_present(product.get(k)) for k in ["length_cm", "width_cm", "height_cm"])},
        {"key": "sku_codes", "required": True, "value": [state["sku_code"] for state in sku_states], "present": bool(sku_states)},
        {"key": "sku_cost_price_rows", "required": True, "value": [state["cost_price"] for state in sku_states], "present": not unresolved_price_skus},
        {"key": "sku_weight_rows", "required": True, "value": [state["weight_kg"] for state in sku_states], "present": not unresolved_weight_skus},
        {"key": "sku_dimensions_rows", "required": True, "value": [[state["length_cm"], state["width_cm"], state["height_cm"]] for state in sku_states], "present": not unresolved_dimension_skus},
        {"key": "variant_images", "required": False, "value": [state["variant_image"] for state in sku_states], "present": not unresolved_variant_image_skus},
    ]

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    blocker_code_map = {
        "directory": "missing_directory_mapping",
        "weight_kg": "missing_weight",
    }

    for state in field_states:
        if state["present"]:
            continue
        key = state["key"]
        if state["required"]:
            blockers.append({
                "code": blocker_code_map.get(key, f"missing_{key}"),
                "field": key,
                "message": _mabang_missing_message(key, product_id, group_id, product),
                "product_id": product_id,
                "group_id": group_id,
            })

    if unresolved_price_skus:
        if profile["template"].get("price"):
            warnings.append({
                "code": "inferred_price_rows",
                "field": "sku_cost_price_rows",
                "message": f"商品 {group_id or product_id or '-'} 有 {len(unresolved_price_skus)} 个 SKU 使用模板默认成本价补齐。",
                "product_id": product_id,
                "group_id": group_id,
                "sku_codes": unresolved_price_skus,
            })
        else:
            blockers.append({
                "code": "missing_sku_cost_price_rows",
                "field": "sku_cost_price_rows",
                "message": f"商品 {group_id or product_id or '-'} 有 {len(unresolved_price_skus)} 个 SKU 缺少成本价且无法从模板补齐。",
                "product_id": product_id,
                "group_id": group_id,
                "sku_codes": unresolved_price_skus,
            })

    if unresolved_weight_skus:
        blockers.append({
            "code": "missing_sku_weight_rows",
            "field": "sku_weight_rows",
            "message": f"商品 {group_id or product_id or '-'} 有 {len(unresolved_weight_skus)} 个 SKU 缺少重量且无法从模板补齐。",
            "product_id": product_id,
            "group_id": group_id,
            "sku_codes": unresolved_weight_skus,
        })

    if unresolved_dimension_skus:
        blockers.append({
            "code": "missing_sku_dimensions_rows",
            "field": "sku_dimensions_rows",
            "message": f"商品 {group_id or product_id or '-'} 有 {len(unresolved_dimension_skus)} 个 SKU 缺少长宽高且无法从模板补齐。",
            "product_id": product_id,
            "group_id": group_id,
            "sku_codes": unresolved_dimension_skus,
        })

    if unresolved_variant_image_skus:
        warnings.append({
            "code": "missing_variant_images",
            "field": "variant_images",
            "message": f"商品 {group_id or product_id or '-'} 有 {len(unresolved_variant_image_skus)} 个 SKU 缺少变体图 NAS 路径。",
            "product_id": product_id,
            "group_id": group_id,
            "sku_codes": unresolved_variant_image_skus,
        })

    missing_optional_fields = [state["key"] for state in field_states if not state["required"] and not state["present"]]
    if missing_optional_fields:
        warnings.append({
            "code": "missing_optional_fields",
            "field": "optional_fields",
            "message": f"商品 {group_id or product_id or '-'} 还有 {len(missing_optional_fields)} 项可补全字段。",
            "product_id": product_id,
            "group_id": group_id,
            "fields": missing_optional_fields,
        })

    filled_field_keys = sorted({state["key"] for state in field_states if state["present"]})
    missing_fields = sorted({state["key"] for state in field_states if not state["present"]})

    return {
        "product_id": product_id,
        "group_id": group_id,
        "product_name_cn": profile["product_name_cn"],
        "row_count": max(1, len(skus)),
        "blockers": blockers,
        "warnings": warnings,
        "filled_fields": len(filled_field_keys),
        "filled_field_keys": filled_field_keys,
        "missing_fields": missing_fields,
        "field_states": field_states,
    }


def _mabang_missing_message(key: str, product_id: str, group_id: str, product: dict[str, Any]) -> str:
    group_or_product = group_id or product_id or "-"
    if key == "group_id":
        return "商品缺少 group_id，无法作为主 sku 编号。"
    if key == "product_name_cn":
        return "商品缺少中文名称，无法导出。"
    if key == "directory":
        return f"商品类目 {product.get('category') or '-'} 未映射到马帮产品目录。"
    if key == "main_image":
        return f"商品 {group_or_product} 未找到主图 NAS 路径。"
    if key == "sku_codes":
        return f"商品 {group_or_product} 未找到子 SKU。"
    if key == "cost_price":
        return f"商品 {group_or_product} 缺少主行成本价。"
    if key == "weight_kg":
        return f"商品 {group_or_product} 缺少重量。"
    if key == "dimensions":
        return f"商品 {group_or_product} 缺少长宽高。"
    return f"商品 {group_or_product} 缺少字段 {key}。"


def _resolve_mabang_profile(bundle: dict[str, Any]) -> dict[str, Any]:
    product = dict(bundle.get("product") or {})
    assets = list(bundle.get("assets") or [])
    skus = _sorted_skus(bundle.get("skus") or [])
    prices = list(bundle.get("prices") or [])
    template = storage.get_product_template(str(product.get("category") or ""))
    price_by_sku = {
        str(price.get("sku_code") or "").strip(): dict(price)
        for price in prices
        if str(price.get("sku_code") or "").strip()
    }

    main_images = resolve_main_image_paths(product, assets)
    variant_images = resolve_variant_image_paths(skus, assets, product)
    detail_images = resolve_detail_image_paths(assets)
    custom_images = _dedupe_strings([*main_images, *variant_images, *detail_images])
    video_url = _join_paths(resolve_video_paths(assets))

    product_name_cn = _first_non_empty(product.get("product_name_cn"), product.get("base_name"))
    product_name_en = _first_non_empty(
        product.get("product_name_en"),
        product.get("generated_title"),
        product.get("listing_title"),
        template.get("title_suffix"),
    )
    product_name_jp = _first_non_empty(product.get("product_name_jp"))
    description = _first_non_empty(product.get("description"), template.get("description"))
    bullet_point = _first_non_empty(product.get("bullet_point"), _build_mabang_bullet_point(product, template))
    search_terms = _first_non_empty(product.get("search_terms"), _build_mabang_search_terms(product, template))
    declaration_name_cn = _first_non_empty(product.get("declaration_name_cn"), product_name_cn)
    declaration_name_en = _first_non_empty(product.get("declaration_name_en"), product_name_en)
    declaration_price_usd = _number_or_empty(product.get("declaration_price_usd"))
    main_weight_kg = _resolve_measure_value(product.get("weight_kg"), template.get("weight_kg"), (skus[0].get("weight_kg") if skus else ""))
    main_length_cm = _resolve_measure_value(product.get("length_cm"), template.get("length_cm"), (skus[0].get("length_cm") if skus else ""))
    main_width_cm = _resolve_measure_value(product.get("width_cm"), template.get("width_cm"), (skus[0].get("width_cm") if skus else ""))
    main_height_cm = _resolve_measure_value(product.get("height_cm"), template.get("height_cm"), (skus[0].get("height_cm") if skus else ""))

    sku_states: list[dict[str, Any]] = []
    explicit_price_skus: list[str] = []
    for sku in skus:
        sku_code = str(sku.get("sku_code") or "").strip()
        explicit_price = price_by_sku.get(sku_code) or {}
        if _value_present(explicit_price.get("cost_price")):
            explicit_price_skus.append(sku_code)
        resolved_cost_price = _resolve_price_value(explicit_price.get("cost_price"), _template_cost_price(template, sku))
        sku_states.append({
            "sku": sku,
            "sku_code": sku_code,
            "cost_price": resolved_cost_price,
            "weight_kg": _resolve_measure_value(sku.get("weight_kg"), product.get("weight_kg"), template.get("weight_kg")),
            "length_cm": _resolve_measure_value(sku.get("length_cm"), product.get("length_cm"), template.get("length_cm")),
            "width_cm": _resolve_measure_value(sku.get("width_cm"), product.get("width_cm"), template.get("width_cm")),
            "height_cm": _resolve_measure_value(sku.get("height_cm"), product.get("height_cm"), template.get("height_cm")),
            "variant_image": resolve_variant_image_for_sku(sku, product, assets),
            "title_cn": _build_child_title(product, sku),
            "title_en": _build_child_title_en(product, sku),
            "has_explicit_price": _value_present(explicit_price.get("cost_price")),
        })

    main_price = sku_states[0]["cost_price"] if sku_states else _resolve_price_value(None, _template_cost_price(template, {}))
    return {
        "product": product,
        "assets": assets,
        "skus": skus,
        "prices": prices,
        "template": template,
        "price_by_sku": price_by_sku,
        "product_id": str(product.get("id") or bundle.get("product_id") or ""),
        "group_id": str(product.get("group_id") or bundle.get("group_id") or "").strip(),
        "main_images": main_images,
        "variant_images": variant_images,
        "detail_images": detail_images,
        "custom_images": custom_images,
        "directory": resolve_mabang_directory(product),
        "video_url": video_url,
        "product_name_cn": product_name_cn,
        "product_name_en": product_name_en,
        "product_name_jp": product_name_jp,
        "description": description,
        "bullet_point": bullet_point,
        "search_terms": search_terms,
        "declaration_name_cn": declaration_name_cn,
        "declaration_name_en": declaration_name_en,
        "declaration_price_usd": declaration_price_usd,
        "customs_code": _first_non_empty(product.get("customs_code")),
        "competitor_title": _first_non_empty(product.get("competitor_title")),
        "competitor_link": _first_non_empty(product.get("competitor_link")),
        "supplier_name": _first_non_empty(product.get("supplier_name")),
        "supplier_product_title": _first_non_empty(product.get("supplier_product_title")),
        "supply_link": _first_non_empty(product.get("supply_link")),
        "purchase_days": _number_or_empty(product.get("purchase_days")),
        "stock_warning_days": _number_or_empty(product.get("stock_warning_days")),
        "qc_standard": _first_non_empty(product.get("qc_standard")),
        "design_note": _first_non_empty(product.get("design_note")),
        "sales_note": _first_non_empty(product.get("sales_note")),
        "purchase_note": _first_non_empty(product.get("purchase_note")),
        "product_note": _first_non_empty(product.get("product_note")),
        "brand": _first_non_empty(product.get("brand"), product.get("attribute_brand"), template.get("attribute_brand")),
        "sub_brand": _first_non_empty(product.get("sub_brand")),
        "main_price": main_price,
        "weight_kg": main_weight_kg,
        "length_cm": main_length_cm,
        "width_cm": main_width_cm,
        "height_cm": main_height_cm,
        "sku_states": sku_states,
        "explicit_price_skus": explicit_price_skus,
    }


def _build_mabang_bullet_point(product: dict[str, Any], template: dict[str, Any]) -> str:
    parts = _dedupe_strings([
        f"主推：{_first_non_empty(product.get('product_name_cn'), product.get('base_name'))}",
        f"材质：{_first_non_empty(template.get('attribute_material'), product.get('material'))}",
        f"人群：{_first_non_empty(template.get('attribute_gender'), template.get('target_audience'))}",
        f"版型：{_first_non_empty(template.get('attribute_sleeve_length'))}",
        f"领型：{_first_non_empty(template.get('attribute_collar'))}",
    ])
    return "；".join(parts[:4])


def _build_mabang_search_terms(product: dict[str, Any], template: dict[str, Any]) -> str:
    return ", ".join(_dedupe_strings([
        _first_non_empty(product.get("product_name_cn"), product.get("base_name")),
        _first_non_empty(product.get("product_name_en"), product.get("generated_title"), product.get("listing_title"), template.get("title_suffix")),
        _first_non_empty(product.get("category")),
        _first_non_empty(product.get("brand"), product.get("attribute_brand"), template.get("attribute_brand")),
        _first_non_empty(template.get("attribute_material")),
        _first_non_empty(template.get("attribute_gender"), template.get("target_audience")),
        _first_non_empty(template.get("attribute_sleeve_length")),
        _first_non_empty(template.get("attribute_collar")),
    ]))


def _materialize_export_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    profile = _resolve_mabang_profile(bundle)
    product = dict(profile.get("product") or {})
    skus = [dict(item) for item in (profile.get("skus") or [])]
    prices = [dict(item) for item in (profile.get("prices") or [])]
    price_by_sku = {
        str(price.get("sku_code") or "").strip(): price
        for price in prices
        if str(price.get("sku_code") or "").strip()
    }

    product["product_name_cn"] = profile.get("product_name_cn") or product.get("product_name_cn") or product.get("base_name") or ""
    product["product_name_en"] = profile.get("product_name_en") or product.get("product_name_en") or ""
    product["product_name_jp"] = profile.get("product_name_jp") or product.get("product_name_jp") or ""
    product["brand"] = profile.get("brand") or product.get("brand") or product.get("attribute_brand") or ""
    product["sub_brand"] = profile.get("sub_brand") or product.get("sub_brand") or ""
    product["description"] = profile.get("description") or product.get("description") or ""
    product["bullet_point"] = profile.get("bullet_point") or product.get("bullet_point") or ""
    product["search_terms"] = profile.get("search_terms") or product.get("search_terms") or ""
    product["declaration_name_cn"] = profile.get("declaration_name_cn") or product.get("declaration_name_cn") or ""
    product["declaration_name_en"] = profile.get("declaration_name_en") or product.get("declaration_name_en") or ""
    product["declaration_price_usd"] = profile.get("declaration_price_usd") or product.get("declaration_price_usd") or ""
    product["customs_code"] = profile.get("customs_code") or product.get("customs_code") or ""
    product["competitor_title"] = profile.get("competitor_title") or product.get("competitor_title") or ""
    product["competitor_link"] = profile.get("competitor_link") or product.get("competitor_link") or ""
    product["supplier_name"] = profile.get("supplier_name") or product.get("supplier_name") or ""
    product["supplier_product_title"] = profile.get("supplier_product_title") or product.get("supplier_product_title") or ""
    product["supply_link"] = profile.get("supply_link") or product.get("supply_link") or ""
    product["purchase_days"] = profile.get("purchase_days") or product.get("purchase_days") or ""
    product["stock_warning_days"] = profile.get("stock_warning_days") or product.get("stock_warning_days") or ""
    product["qc_standard"] = profile.get("qc_standard") or product.get("qc_standard") or ""
    product["design_note"] = profile.get("design_note") or product.get("design_note") or ""
    product["sales_note"] = profile.get("sales_note") or product.get("sales_note") or ""
    product["purchase_note"] = profile.get("purchase_note") or product.get("purchase_note") or ""
    product["product_note"] = profile.get("product_note") or product.get("product_note") or ""
    product["weight_kg"] = profile.get("weight_kg") or product.get("weight_kg") or ""
    product["length_cm"] = profile.get("length_cm") or product.get("length_cm") or ""
    product["width_cm"] = profile.get("width_cm") or product.get("width_cm") or ""
    product["height_cm"] = profile.get("height_cm") or product.get("height_cm") or ""

    for state in profile.get("sku_states") or []:
        sku_code = str(state.get("sku_code") or "").strip()
        if not sku_code:
            continue
        if sku_code in price_by_sku:
            price_by_sku[sku_code]["cost_price"] = state.get("cost_price")
        else:
            prices.append({"sku_code": sku_code, "cost_price": state.get("cost_price")})
        for sku in skus:
            if str(sku.get("sku_code") or "").strip() != sku_code:
                continue
            sku["weight_kg"] = state.get("weight_kg")
            sku["length_cm"] = state.get("length_cm")
            sku["width_cm"] = state.get("width_cm")
            sku["height_cm"] = state.get("height_cm")
            if not sku.get("variant_image") and state.get("variant_image"):
                sku["variant_image"] = state.get("variant_image")
            break

    return {
        "product": product,
        "assets": list(profile.get("assets") or bundle.get("assets") or []),
        "skus": skus,
        "prices": prices,
        "profile": profile,
    }


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        elif value not in (None, ""):
            return value
    return ""


def _value_present(value: Any) -> bool:
    if isinstance(value, list):
        return bool([item for item in value if _value_present(item)])
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, "")


def _resolve_measure_value(*values: Any) -> Any:
    return _number_or_empty(_first_non_empty(*values))


def _resolve_price_value(*values: Any) -> Any:
    return _number_or_empty(_first_non_empty(*values))


def _template_cost_price(template: dict[str, Any], sku: dict[str, Any]) -> Any:
    price = dict(template.get("price") or {})
    size = str(sku.get("size") or "").upper()
    is_large = size in {"XL", "2XL", "3XL", "4XL", "5XL"}
    return price.get("large_cost", 31) if is_large else price.get("cost", 28)


def resolve_mabang_directory(product: dict[str, Any]) -> str:
    category = str(product.get("category") or "").strip()
    if not category:
        return ""
    return MABANG_DIRECTORY_MAP.get(category, "")


def resolve_main_image_paths(product: dict[str, Any], assets: list[dict[str, Any]]) -> list[str]:
    candidates: list[str] = []
    main_image = str(product.get("main_image") or "").strip()
    asset_root = str(product.get("asset_root") or "").strip()

    for asset in assets:
        if (asset.get("asset_type") or "") == "main" and asset.get("file_path"):
            candidates.append(str(asset.get("file_path") or "").strip())

    if main_image:
        asset = _find_asset_by_name(assets, main_image, asset_type="main")
        if asset and asset.get("file_path"):
            candidates.append(str(asset.get("file_path") or "").strip())
        elif _looks_like_path(main_image):
            candidates.append(main_image)
        elif asset_root:
            candidates.append(str(Path(asset_root) / main_image))

    return _dedupe_strings(candidates)


def resolve_variant_image_for_sku(
    sku: dict[str, Any],
    product: dict[str, Any],
    assets: list[dict[str, Any]] | None = None,
) -> str:
    assets = list(assets or storage.assets_for_base_product(product))
    sku_image = str(sku.get("variant_image") or "").strip()
    color_code = str(sku.get("color_code") or "").strip()
    asset_root = str(product.get("asset_root") or "").strip()

    if sku_image:
        asset = _find_asset_by_name(assets, sku_image, asset_type="variant")
        if asset and asset.get("file_path"):
            return str(asset.get("file_path") or "").strip()
        if _looks_like_path(sku_image):
            return sku_image
        if asset_root:
            return str(Path(asset_root) / sku_image)

    if color_code:
        asset = _find_asset_by_color(assets, color_code)
        if asset and asset.get("file_path"):
            return str(asset.get("file_path") or "").strip()
        if asset_root:
            return str(Path(asset_root) / f"{color_code}.jpg")

    return ""


def resolve_variant_image_paths(skus: list[dict[str, Any]], assets: list[dict[str, Any]], product: dict[str, Any]) -> list[str]:
    return _dedupe_strings([resolve_variant_image_for_sku(sku, product, assets) for sku in skus])


def resolve_detail_image_paths(assets: list[dict[str, Any]]) -> list[str]:
    return _dedupe_strings([
        str(asset.get("file_path") or "").strip()
        for asset in assets
        if (asset.get("asset_type") or "") in {"model", "detail", "size_chart"} and asset.get("file_path")
    ])


def resolve_video_paths(assets: list[dict[str, Any]]) -> list[str]:
    return _dedupe_strings([
        str(asset.get("file_path") or "").strip()
        for asset in assets
        if (asset.get("asset_type") or "") == "video" and asset.get("file_path")
    ])


def export_mabang_products(items: list[Any], save_copy: bool = True) -> dict[str, Any]:
    bundles = _normalize_export_items(items)
    validation = validate_mabang_bundles(bundles)
    if not validation["valid"]:
        return {
            "ok": False,
            "validation": validation,
        }

    filename = _build_export_filename(bundles)
    output_dir = settings.EXPORT_DIR if save_copy else Path(tempfile.gettempdir()) / "ai-listing-system" / "mabang"
    output_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = output_dir / filename
    template_path = _resolve_template_path()
    workbook = load_workbook(template_path)
    _validate_template_structure(workbook)
    _populate_workbook(workbook, bundles)
    workbook.save(xlsx_path)
    xlsx_bytes = xlsx_path.read_bytes()
    audit_error = ""
    try:
        audit_outputs = _record_mabang_export_audits(bundles, validation, xlsx_path)
    except Exception as exc:
        audit_outputs = []
        audit_error = str(exc)

    result = {
        "ok": True,
        "filename": filename,
        "xlsx_path": str(xlsx_path),
        "xlsx_bytes": xlsx_bytes,
        "audit_path": str(xlsx_path) if save_copy else "",
        "audit_outputs": audit_outputs,
        "validation": validation,
        "bundle_count": len(bundles),
        "row_count": validation["row_count"],
    }
    if audit_error:
        result["audit_error"] = audit_error
    return result


def export_mabang_bytes_from_product_ids(product_ids: list[str], save_copy: bool = True) -> dict[str, Any]:
    return export_mabang_products(list(product_ids), save_copy=save_copy)


def load_mabang_template_workbook() -> str:
    return str(settings.MABANG_TEMPLATE_PATH)


def _validate_template_structure(workbook) -> None:
    missing_sheets = [sheet for sheet in REQUIRED_TEMPLATE_SHEETS if sheet not in workbook.sheetnames]
    if missing_sheets:
        raise ValueError(f"Mabang template missing required sheet(s): {', '.join(missing_sheets)}")

    list_sheet = workbook[LIST_SHEET_NAME]
    actual_headers = [
        list_sheet.cell(LIST_HEADER_ROW, col).value
        for col in range(1, MABANG_LIST_COLUMN_COUNT + 1)
    ]
    extra_headers = [
        list_sheet.cell(LIST_HEADER_ROW, col).value
        for col in range(MABANG_LIST_COLUMN_COUNT + 1, list_sheet.max_column + 1)
        if str(list_sheet.cell(LIST_HEADER_ROW, col).value or "").strip()
    ]
    if actual_headers != MABANG_LIST_HEADERS or extra_headers:
        raise ValueError(
            f"Mabang template sheet {LIST_SHEET_NAME} headers must match "
            f"{MABANG_LIST_COLUMN_COUNT} expected columns"
        )


def _populate_workbook(workbook, bundles: list[dict[str, Any]]) -> None:
    list_sheet = workbook[LIST_SHEET_NAME]
    template_sheet = workbook[TEMPLATE_SHEET_NAME]
    source_main_row = TEMPLATE_MAIN_ROW
    source_child_row = TEMPLATE_CHILD_ROW
    column_count = MABANG_LIST_COLUMN_COUNT

    # Clear existing list data rows while preserving the header row style.
    for row in range(LIST_DATA_START_ROW, list_sheet.max_row + 1):
        for col in range(1, list_sheet.max_column + 1):
            list_sheet.cell(row, col).value = None

    row_cursor = LIST_DATA_START_ROW
    for bundle in bundles:
        export_bundle = _materialize_export_bundle(bundle)
        product = dict(export_bundle.get("product") or {})
        assets = list(export_bundle.get("assets") or [])
        skus = _sorted_skus(export_bundle.get("skus") or [])
        prices = list(export_bundle.get("prices") or [])
        price_by_sku = {str(price.get("sku_code") or ""): price for price in prices}
        main_images = resolve_main_image_paths(product, assets)
        variant_images = resolve_variant_image_paths(skus, assets, product)
        detail_images = resolve_detail_image_paths(assets)
        custom_images = _dedupe_strings([*main_images, *variant_images, *detail_images])
        main_price = prices[0] if prices else {}
        rows = _build_rows(product, assets, skus, price_by_sku, main_images, variant_images, detail_images, custom_images, main_price)
        for values, template_row in rows:
            _ensure_row_exists(list_sheet, row_cursor, source_child_row)
            _copy_row_style(template_sheet, template_row, list_sheet, row_cursor, column_count=column_count)
            _write_row(list_sheet, row_cursor, values)
            row_cursor += 1


def _build_rows(
    product: dict[str, Any],
    assets: list[dict[str, Any]],
    skus: list[dict[str, Any]],
    price_by_sku: dict[str, dict[str, Any]],
    main_images: list[str],
    variant_images: list[str],
    detail_images: list[str],
    custom_images: list[str],
    main_price: dict[str, Any],
) -> list[tuple[list[Any], int]]:
    rows: list[tuple[list[Any], int]] = []
    if not skus:
        rows.append((_build_main_row(product, assets, main_images, variant_images, detail_images, custom_images, main_price, {}, {}), TEMPLATE_MAIN_ROW))
        return rows

    for index, sku in enumerate(skus):
        price = price_by_sku.get(str(sku.get("sku_code") or ""), {})
        if index == 0:
            rows.append((_build_main_row(product, assets, main_images, variant_images, detail_images, custom_images, main_price, sku, price), TEMPLATE_MAIN_ROW))
        else:
            rows.append((_build_child_row(product, sku, price), TEMPLATE_CHILD_ROW))
    return rows


def _build_main_row(
    product: dict[str, Any],
    assets: list[dict[str, Any]],
    main_images: list[str],
    variant_images: list[str],
    detail_images: list[str],
    custom_images: list[str],
    main_price: dict[str, Any],
    sku: dict[str, Any],
    price: dict[str, Any],
) -> list[Any]:
    row = _blank_row()
    row[0] = str(product.get("group_id") or "").strip()
    row[1] = str(product.get("product_name_cn") or product.get("base_name") or "").strip()
    row[2] = str(product.get("product_name_en") or "").strip()
    row[3] = str(product.get("product_name_jp") or "").strip()
    row[4] = str(product.get("listing_url") or product.get("target_link") or "").strip()
    row[5] = resolve_mabang_directory(product)
    row[6] = str(product.get("brand") or product.get("attribute_brand") or "").strip()
    row[7] = str(product.get("sub_brand") or "").strip()
    row[8] = _number_or_empty(price.get("cost_price") if price else main_price.get("cost_price"))
    row[9] = _number_or_empty(product.get("min_order_qty") or 1)
    row[10] = _weight_to_grams(product.get("weight_kg"))
    row[11] = str(product.get("tags") or "").strip()
    row[12] = _join_paths(resolve_video_paths(assets))
    row[13] = str(product.get("group_id") or "").strip()
    row[14] = str(product.get("developer") or "").strip()
    row[15] = str(product.get("buyer") or "").strip()
    row[16] = _number_or_empty(product.get("length_cm"))
    row[17] = _number_or_empty(product.get("width_cm"))
    row[18] = _number_or_empty(product.get("height_cm"))
    row[19] = _join_paths(main_images)
    row[20] = _join_paths(variant_images)
    row[21] = _join_paths(detail_images)
    row[22] = _join_paths(custom_images)
    row[23] = str(product.get("description") or "").strip()
    row[24] = str(product.get("description") or "").strip()
    row[25] = str(product.get("description") or "").strip()
    row[26] = str(product.get("bullet_point") or "").strip()
    row[27] = str(product.get("search_terms") or "").strip()
    row[28] = str(product.get("declaration_name_cn") or product.get("product_name_cn") or "").strip()
    row[29] = str(product.get("declaration_name_en") or product.get("product_name_en") or "").strip()
    row[30] = _number_or_empty(product.get("declaration_price_usd"))
    row[31] = str(product.get("customs_code") or "").strip()
    row[32] = str(product.get("special_mark") or "").strip()
    row[33] = str(product.get("competitor_title") or "").strip()
    row[34] = str(product.get("competitor_link") or "").strip()
    row[35] = str(product.get("supplier_name") or "").strip()
    row[36] = str(product.get("supplier_product_title") or "").strip()
    row[37] = str(product.get("supply_link") or "").strip()
    row[38] = _number_or_empty(product.get("purchase_days"))
    row[39] = _number_or_empty(product.get("stock_warning_days"))
    row[40] = str(product.get("qc_standard") or "").strip()
    row[41] = str(product.get("design_note") or "").strip()
    row[42] = str(product.get("sales_note") or "").strip()
    row[43] = str(product.get("purchase_note") or "").strip()
    row[44] = str(product.get("product_note") or "").strip()
    row[45] = str(sku.get("sku_code") or "").strip() or str(product.get("group_id") or "").strip()
    row[46] = "颜色分类||尺码"
    row[47] = str(sku.get("color_cn") or sku.get("color_code") or "").strip()
    row[48] = resolve_variant_image_for_sku(sku, product, assets)
    row[49] = _build_child_title(product, sku)
    row[50] = _build_child_title_en(product, sku)
    row[51] = _number_or_empty(price.get("cost_price") or main_price.get("cost_price"))
    row[52] = _weight_to_grams(sku.get("weight_kg") or product.get("weight_kg"))
    row[53] = _number_or_empty(sku.get("stock") or 0)
    row[54] = _number_or_empty(sku.get("length_cm") or product.get("length_cm"))
    row[55] = _number_or_empty(sku.get("width_cm") or product.get("width_cm"))
    row[56] = _number_or_empty(sku.get("height_cm") or product.get("height_cm"))
    row[57] = _number_or_empty(sku.get("min_order_qty") or 1)
    row[58] = _number_or_empty(sku.get("package_length_cm") or sku.get("length_cm") or product.get("length_cm"))
    row[59] = _number_or_empty(sku.get("package_width_cm") or sku.get("width_cm") or product.get("width_cm"))
    row[60] = _number_or_empty(sku.get("package_height_cm") or sku.get("height_cm") or product.get("height_cm"))
    row[61] = str(sku.get("sku_code") or "").strip()
    return row


def _build_child_row(product: dict[str, Any], sku: dict[str, Any], price: dict[str, Any]) -> list[Any]:
    row = _blank_row()
    row[0] = str(product.get("group_id") or "").strip()
    row[1] = str(product.get("product_name_cn") or product.get("base_name") or "").strip()
    row[2] = str(product.get("product_name_en") or "").strip()
    row[3] = str(product.get("product_name_jp") or "").strip()
    row[4] = str(product.get("listing_url") or product.get("target_link") or "").strip()
    row[5] = resolve_mabang_directory(product)
    row[6] = str(product.get("brand") or product.get("attribute_brand") or "").strip()
    row[7] = str(product.get("sub_brand") or "").strip()
    row[8] = ""
    row[9] = ""
    row[10] = ""
    row[11] = str(product.get("tags") or "").strip()
    row[12] = ""
    row[13] = str(product.get("group_id") or "").strip()
    row[14] = str(product.get("developer") or "").strip()
    row[15] = str(product.get("buyer") or "").strip()
    row[16] = _number_or_empty(product.get("length_cm"))
    row[17] = _number_or_empty(product.get("width_cm"))
    row[18] = _number_or_empty(product.get("height_cm"))
    row[19] = ""
    row[20] = ""
    row[21] = ""
    row[22] = ""
    row[23] = str(product.get("description") or "").strip()
    row[24] = str(product.get("description") or "").strip()
    row[25] = str(product.get("description") or "").strip()
    row[26] = str(product.get("bullet_point") or "").strip()
    row[27] = str(product.get("search_terms") or "").strip()
    row[28] = str(product.get("declaration_name_cn") or product.get("product_name_cn") or "").strip()
    row[29] = str(product.get("declaration_name_en") or product.get("product_name_en") or "").strip()
    row[30] = _number_or_empty(product.get("declaration_price_usd"))
    row[31] = str(product.get("customs_code") or "").strip()
    row[32] = str(product.get("special_mark") or "").strip()
    row[33] = str(product.get("competitor_title") or "").strip()
    row[34] = str(product.get("competitor_link") or "").strip()
    row[35] = str(product.get("supplier_name") or "").strip()
    row[36] = str(product.get("supplier_product_title") or "").strip()
    row[37] = str(product.get("supply_link") or "").strip()
    row[38] = _number_or_empty(product.get("purchase_days"))
    row[39] = _number_or_empty(product.get("stock_warning_days"))
    row[40] = str(product.get("qc_standard") or "").strip()
    row[41] = str(product.get("design_note") or "").strip()
    row[42] = str(product.get("sales_note") or "").strip()
    row[43] = str(product.get("purchase_note") or "").strip()
    row[44] = str(product.get("product_note") or "").strip()
    row[45] = str(sku.get("sku_code") or "").strip()
    row[46] = "颜色分类||尺码"
    row[47] = str(sku.get("color_cn") or sku.get("color_code") or "").strip()
    row[48] = resolve_variant_image_for_sku(sku, product)
    row[49] = _build_child_title(product, sku)
    row[50] = _build_child_title_en(product, sku)
    row[51] = _number_or_empty(price.get("cost_price"))
    row[52] = _weight_to_grams(sku.get("weight_kg") or product.get("weight_kg"))
    row[53] = _number_or_empty(sku.get("stock") or 0)
    row[54] = _number_or_empty(sku.get("length_cm") or product.get("length_cm"))
    row[55] = _number_or_empty(sku.get("width_cm") or product.get("width_cm"))
    row[56] = _number_or_empty(sku.get("height_cm") or product.get("height_cm"))
    row[57] = _number_or_empty(sku.get("min_order_qty") or 1)
    row[58] = _number_or_empty(sku.get("package_length_cm") or sku.get("length_cm") or product.get("length_cm"))
    row[59] = _number_or_empty(sku.get("package_width_cm") or sku.get("width_cm") or product.get("width_cm"))
    row[60] = _number_or_empty(sku.get("package_height_cm") or sku.get("height_cm") or product.get("height_cm"))
    row[61] = str(sku.get("sku_code") or "").strip()
    return row


def _build_child_title(product: dict[str, Any], sku: dict[str, Any]) -> str:
    return "||".join([
        str(product.get("product_name_cn") or product.get("base_name") or "").strip(),
        str(sku.get("color_cn") or sku.get("color_code") or "").strip(),
        str(sku.get("size") or "").strip(),
    ]).strip("|")


def _build_child_title_en(product: dict[str, Any], sku: dict[str, Any]) -> str:
    return " ".join(part for part in [
        str(product.get("product_name_en") or "").strip(),
        str(sku.get("color_code") or sku.get("color_cn") or "").strip(),
        str(sku.get("size") or "").strip(),
    ] if part).strip()


def _write_row(sheet, row_index: int, values: list[Any]) -> None:
    for col_index, value in enumerate(values, start=1):
        sheet.cell(row_index, col_index).value = value


def _ensure_row_exists(sheet, row_index: int, template_row: int) -> None:
    if row_index <= sheet.max_row:
        return
    sheet.insert_rows(row_index)
    _copy_row_style(sheet, template_row, sheet, row_index, column_count=MABANG_LIST_COLUMN_COUNT)


def _copy_row_style(source_sheet, source_row: int, target_sheet, target_row: int, column_count: int | None = None) -> None:
    count = int(column_count or source_sheet.max_column or target_sheet.max_column or 0)
    for col in range(1, min(count, source_sheet.max_column, target_sheet.max_column or count) + 1):
        src = source_sheet.cell(source_row, col)
        dst = target_sheet.cell(target_row, col)
        if src.has_style:
            dst._style = copy(src._style)
        if src.number_format:
            dst.number_format = src.number_format
        if src.font:
            dst.font = copy(src.font)
        if src.fill:
            dst.fill = copy(src.fill)
        if src.border:
            dst.border = copy(src.border)
        if src.alignment:
            dst.alignment = copy(src.alignment)
        if src.protection:
            dst.protection = copy(src.protection)
    if source_row in source_sheet.row_dimensions:
        target_sheet.row_dimensions[target_row].height = source_sheet.row_dimensions[source_row].height


def _blank_row() -> list[Any]:
    return ["" for _ in range(MABANG_LIST_COLUMN_COUNT)]


def _join_paths(values: list[str]) -> str:
    return ",".join(_dedupe_strings(values))


def _weight_to_grams(value: Any) -> Any:
    if value in [None, ""]:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ""
    return int(round(num * 1000))


def _number_or_empty(value: Any) -> Any:
    if value in [None, ""]:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ""
    return int(num) if num.is_integer() else num


def _dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _looks_like_path(value: str) -> bool:
    text = str(value or "")
    return text.startswith("\\\\") or text.startswith("http://") or text.startswith("https://") or ":\\" in text[:5]


def _missing_price_skus(skus: list[dict[str, Any]], prices: list[dict[str, Any]]) -> list[str]:
    price_codes = {
        str(price.get("sku_code") or "").strip()
        for price in prices
        if str(price.get("sku_code") or "").strip()
    }
    return [
        str(sku.get("sku_code") or "").strip()
        for sku in skus
        if str(sku.get("sku_code") or "").strip() not in price_codes
    ]


def _missing_variant_image_skus(
    skus: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    product: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    for sku in skus:
        if not resolve_variant_image_for_sku(sku, product, assets):
            missing.append(str(sku.get("sku_code") or "").strip())
    return missing


def _find_asset_by_name(assets: list[dict[str, Any]], name: str, asset_type: str = "") -> dict[str, Any] | None:
    target = str(name or "").strip().lower()
    for asset in assets:
        if asset_type and (asset.get("asset_type") or "") != asset_type:
            continue
        if str(asset.get("file_name") or "").strip().lower() == target:
            return asset
    return None


def _find_asset_by_color(assets: list[dict[str, Any]], color_code: str) -> dict[str, Any] | None:
    target = str(color_code or "").strip().lower()
    for asset in assets:
        if (asset.get("asset_type") or "") != "variant":
            continue
        if str(asset.get("sku_option") or "").strip().lower() == target:
            return asset
        if target and target in str(asset.get("file_name") or "").strip().lower():
            return asset
    return None


def _sorted_skus(skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [dict(sku) for sku in skus],
        key=lambda sku: (
            str(sku.get("color_cn") or sku.get("color_code") or "").strip().lower(),
            _size_rank(str(sku.get("size") or "").strip().upper()),
            str(sku.get("size") or "").strip().upper(),
            str(sku.get("sku_code") or "").strip(),
        ),
    )


def _size_rank(size: str) -> int:
    return SIZE_ORDER.get(size, 999)


def _build_export_filename(bundles: list[dict[str, Any]]) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    group_id = str((bundles[0].get("product") or {}).get("group_id") or bundles[0].get("group_id") or "").strip()
    suffix = f"_{group_id}" if group_id else ""
    return f"mabang-export-{timestamp}{suffix}.xlsx"


def _normalize_export_items(items: list[Any]) -> list[dict[str, Any]]:
    if not items:
        return []
    first = items[0]
    if isinstance(first, dict) and "product" in first and "skus" in first and "prices" in first:
        return [dict(item) for item in items]
    if isinstance(first, dict) and ("id" in first or "group_id" in first):
        maybe_ids = [str(item.get("id") or "").strip() for item in items if isinstance(item, dict)]
        if all(maybe_ids):
            bundles = collect_mabang_bundles(maybe_ids)
            if bundles:
                return bundles
    product_ids = [str(item or "").strip() for item in items if str(item or "").strip()]
    return collect_mabang_bundles(product_ids)


def _record_mabang_export_audits(
    bundles: list[dict[str, Any]],
    validation: dict[str, Any],
    xlsx_path: Path,
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    item_by_product_id = {
        str(item.get("product_id") or ""): item
        for item in validation.get("items") or []
    }
    for bundle in bundles:
        profile = _resolve_mabang_profile(bundle)
        product = dict(profile.get("product") or {})
        product_id = str(product.get("id") or bundle.get("product_id") or "").strip()
        if not product_id:
            continue
        item = item_by_product_id.get(product_id) or _summarize_mabang_bundle(bundle)
        payload = {
            "suggestion_only": True,
            "auto_apply": False,
            "group_id": str(profile.get("group_id") or "").strip(),
            "bundle_count": validation.get("bundle_count") or len(bundles),
            "row_count": item.get("row_count") or 0,
            "filled_fields": item.get("filled_field_keys") or [],
            "missing_fields": item.get("missing_fields") or [],
            "warnings": item.get("warnings") or [],
            "blockers": item.get("blockers") or [],
            "resolved_sources": _mabang_resolved_sources(bundle, profile),
            "export_path": str(xlsx_path),
        }
        outputs.append(storage.create_ai_output({
            "product_id": product_id,
            "output_type": "mabang_export_audit",
            "prompt": "",
            "response": f"mabang export audit for {payload['group_id'] or product_id}",
            "model": "local_rules",
            "status": "suggested",
            "payload_json": payload,
        }))
    return outputs


def _mabang_resolved_sources(bundle: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    product = dict(bundle.get("product") or {})
    skus = list(bundle.get("skus") or [])
    prices = list(bundle.get("prices") or [])
    template = dict(profile.get("template") or {})
    sku_states = list(profile.get("sku_states") or [])
    first_sku = skus[0] if skus else {}
    first_sku_state = sku_states[0] if sku_states else {}
    first_price = prices[0] if prices else {}
    return {
        "product_name_en": _resolve_source(
            profile.get("product_name_en"),
            [
                ("product_name_en", product.get("product_name_en")),
                ("generated_title", product.get("generated_title")),
                ("listing_title", product.get("listing_title")),
                ("template.title_suffix", template.get("title_suffix")),
            ],
        ),
        "description": _resolve_source(
            profile.get("description"),
            [
                ("product.description", product.get("description")),
                ("template.description", template.get("description")),
            ],
        ),
        "bullet_point": _resolve_source(
            profile.get("bullet_point"),
            [
                ("product.bullet_point", product.get("bullet_point")),
                ("local_rules.bullet_point", _build_mabang_bullet_point(product, template)),
            ],
        ),
        "search_terms": _resolve_source(
            profile.get("search_terms"),
            [
                ("product.search_terms", product.get("search_terms")),
                ("local_rules.search_terms", _build_mabang_search_terms(product, template)),
            ],
        ),
        "declaration_name_cn": _resolve_source(
            profile.get("declaration_name_cn"),
            [
                ("product.declaration_name_cn", product.get("declaration_name_cn")),
                ("product_name_cn", profile.get("product_name_cn")),
            ],
        ),
        "declaration_name_en": _resolve_source(
            profile.get("declaration_name_en"),
            [
                ("product.declaration_name_en", product.get("declaration_name_en")),
                ("product_name_en", profile.get("product_name_en")),
            ],
        ),
        "main_price": _resolve_source(
            profile.get("main_price"),
            [
                ("prices[0].cost_price", first_price.get("cost_price")),
                ("template.price", _template_cost_price(template, first_sku)),
            ],
        ),
        "weight_kg": _resolve_source(
            profile.get("weight_kg"),
            [
                ("product.weight_kg", product.get("weight_kg")),
                ("template.weight_kg", template.get("weight_kg")),
                ("skus[0].weight_kg", first_sku.get("weight_kg")),
            ],
        ),
        "length_cm": _resolve_source(
            profile.get("length_cm"),
            [
                ("product.length_cm", product.get("length_cm")),
                ("template.length_cm", template.get("length_cm")),
                ("skus[0].length_cm", first_sku.get("length_cm")),
            ],
        ),
        "width_cm": _resolve_source(
            profile.get("width_cm"),
            [
                ("product.width_cm", product.get("width_cm")),
                ("template.width_cm", template.get("width_cm")),
                ("skus[0].width_cm", first_sku.get("width_cm")),
            ],
        ),
        "height_cm": _resolve_source(
            profile.get("height_cm"),
            [
                ("product.height_cm", product.get("height_cm")),
                ("template.height_cm", template.get("height_cm")),
                ("skus[0].height_cm", first_sku.get("height_cm")),
            ],
        ),
        "sku_main_cost_price": _resolve_source(
            first_sku_state.get("cost_price"),
            [
                ("price_by_sku.cost_price", first_price.get("cost_price")),
                ("template.price", _template_cost_price(template, first_sku)),
            ],
        ),
    }


def _resolve_source(resolved_value: Any, candidates: list[tuple[str, Any]]) -> dict[str, Any]:
    for label, value in candidates:
        if _normalized_compare_value(value) == _normalized_compare_value(resolved_value) and _value_present(value):
            return {"source": label, "value": resolved_value}
    return {"source": "unresolved", "value": resolved_value}


def _normalized_compare_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return _number_or_empty(value)


SIZE_ORDER = {
    "XXS": 1,
    "XS": 2,
    "S": 3,
    "M": 4,
    "L": 5,
    "XL": 6,
    "2XL": 7,
    "3XL": 8,
    "4XL": 9,
    "5XL": 10,
}
