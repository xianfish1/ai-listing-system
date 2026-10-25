from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class MabangImportSnapshotError(ValueError):
    pass


_TRANSACTION_FIELDS = (
    "stock",
    "weight_kg",
    "package_length_cm",
    "package_width_cm",
    "package_height_cm",
)

_PRICE_FIELDS = (
    "normal_price",
    "sale_price",
)

_CNSC_REQUIRED_HEADERS = (
    "全球商品价格",
    "泰国仓库存",
    "广州仓库存",
    "菲律宾仓库存",
)

_CNSC_PRICE_TIERS = (
    ("standard", ("XS", "S", "M", "L")),
    ("large", ("XL", "XXL", "XXXL")),
)

_CNSC_WAREHOUSES = ("TH", "CN_GZ", "PH")


def normalize_mabang_capture(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("source") != "mabang_current_page":
        raise MabangImportSnapshotError("当前页面采集数据来源无效")

    page = raw.get("page") if isinstance(raw.get("page"), dict) else {}
    store_category = raw.get("store_category_state") if isinstance(raw.get("store_category_state"), dict) else {}
    store = store_category.get("store") if isinstance(store_category.get("store"), dict) else {}
    category = store_category.get("category") if isinstance(store_category.get("category"), dict) else {}
    listing_mode, listing_mode_evidence = _normalize_listing_mode(raw)
    platform = _text(page.get("platform") or raw.get("platform") or "shopee").lower()
    country = _text(page.get("country") or raw.get("country")).upper()
    if listing_mode == "cnsc_global" and not country:
        country = "GLOBAL"
    normalized_store = {
        "id": _required_text(store.get("id"), _store_id_error(store, raw.get("capture_debug"))),
        "name": _required_text(store.get("name"), "未识别到马帮店铺名称"),
        "platform": platform,
        "country": country,
    }
    normalized_category = {
        "id": _required_text(category.get("id"), "未识别到马帮类目编号"),
        "path": _required_text(category.get("path"), "未识别到马帮类目路径"),
        "id_path": _string_list(category.get("id_path")),
    }
    if category.get("is_leaf") is not True:
        raise MabangImportSnapshotError("必须选择真实叶子类目")

    category_fields, selected_attributes, missing_required = _normalize_category_attributes(
        raw.get("category_attributes")
    )
    sku_rows = raw.get("sku_rows") if isinstance(raw.get("sku_rows"), list) else []
    normalized_rows = [row for row in sku_rows if isinstance(row, dict)]
    variation = _normalize_variation(normalized_rows)
    cnsc_rows = _normalize_cnsc_sku_rows(normalized_rows) if listing_mode == "cnsc_global" else []
    price_tiers = _derive_cnsc_price_tiers(cnsc_rows) if listing_mode == "cnsc_global" else []
    warehouse_stock_defaults = _derive_cnsc_warehouse_defaults(cnsc_rows) if listing_mode == "cnsc_global" else {}
    target_site_stores_loaded = raw.get("target_site_stores_loaded") is True
    target_site_stores = _normalize_target_site_stores(raw.get("target_site_stores")) if target_site_stores_loaded else []
    transaction_defaults = _normalize_transaction_defaults(
        normalized_rows,
        raw.get("transaction_information"),
    )
    price_defaults = _normalize_price_defaults(normalized_rows)
    size_chart = _normalize_size_chart(raw.get("size_chart"))
    size_chart_template_catalog_loaded = raw.get("size_chart_template_catalog_loaded") is True
    size_chart_template_catalog = _normalize_size_chart_templates(raw.get("size_chart_templates")) if size_chart_template_catalog_loaded else []
    promotion_catalog, selected_promotion_id = _normalize_promotions(raw.get("promotion_options"))
    promotion_catalog_loaded = raw.get("promotion_catalog_loaded") is True
    watermark_catalog_loaded = raw.get("watermark_catalog_loaded") is True
    watermark_catalog = _normalize_watermarks(raw.get("watermark_templates")) if watermark_catalog_loaded else []
    coverage = _coverage(
        listing_mode=listing_mode,
        store=normalized_store,
        category=normalized_category,
        missing_required=missing_required,
        variation=variation,
        transaction_defaults=transaction_defaults,
        selected_promotion_id=selected_promotion_id,
    )

    missing_configuration = cnsc_missing_configuration(
        price_tiers=price_tiers,
        warehouse_stock_defaults=warehouse_stock_defaults,
        target_site_stores=target_site_stores,
        default_site_discount_percent=40,
    ) if listing_mode == "cnsc_global" else []

    return {
        "schema_version": 3,
        "listing_mode": listing_mode,
        "listing_mode_evidence": listing_mode_evidence,
        "page": {
            "url": _sanitize_url(page.get("url") or raw.get("page_url")),
            "platform": platform,
            "country": country,
        },
        "store": normalized_store,
        "category": normalized_category,
        "category_fields": category_fields,
        "selected_attributes": selected_attributes,
        "missing_required_attributes": missing_required,
        "variation": variation,
        "cnsc_store": {"id": normalized_store["id"], "name": normalized_store["name"]} if listing_mode == "cnsc_global" else {},
        "target_site_stores_loaded": target_site_stores_loaded,
        "target_site_stores": target_site_stores,
        "default_site_discount_percent": 40 if listing_mode == "cnsc_global" else None,
        "cnsc_sku_rows": cnsc_rows,
        "price_tiers": price_tiers,
        "warehouse_stock_defaults": warehouse_stock_defaults,
        "missing_configuration": missing_configuration,
        "transaction_defaults": transaction_defaults,
        "price_defaults": price_defaults,
        "size_chart": size_chart,
        "size_chart_template_catalog_loaded": size_chart_template_catalog_loaded,
        "size_chart_template_catalog": size_chart_template_catalog,
        "promotion_catalog": promotion_catalog,
        "promotion_catalog_loaded": promotion_catalog_loaded,
        "selected_promotion_id": selected_promotion_id,
        "watermark_catalog_loaded": watermark_catalog_loaded,
        "watermark_catalog": watermark_catalog,
        "coverage": coverage,
        "provenance": _provenance(raw.get("provenance")),
    }


def _normalize_watermarks(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        template_id = _text(raw.get("template_id") or raw.get("templateId") or raw.get("id"))
        name = _text(raw.get("name") or raw.get("template_name") or raw.get("templateName"))
        if not template_id or not name or template_id in seen:
            continue
        seen.add(template_id)
        result.append({
            "template_id": template_id,
            "name": name,
            "preview_url": _sanitize_url(raw.get("preview_url") or raw.get("previewUrl") or raw.get("url")),
        })
    return result


def _normalize_size_chart_templates(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        template_id = _text(raw.get("template_id") or raw.get("templateId") or raw.get("id"))
        name = _text(raw.get("name") or raw.get("template_name") or raw.get("templateName"))
        preview_url = _sanitize_url(raw.get("preview_url") or raw.get("previewUrl") or raw.get("url"))
        if not template_id or not name or template_id in seen:
            continue
        seen.add(template_id)
        result.append({"template_id": template_id, "name": name, "preview_url": preview_url})
    return result


def collapse_uniform_sku_value(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [_text(row.get(key)) for row in rows if isinstance(row, dict)]
    if not values or not any(values):
        return {"status": "missing", "value": None}
    if not all(values):
        return {"status": "missing", "value": None}
    if len(set(values)) != 1:
        return {"status": "conflict", "value": None}
    return {"status": "complete", "value": values[0]}


def snapshot_template_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") not in {1, 2, 3}:
        raise MabangImportSnapshotError("马帮采集快照版本无效")

    selected_attributes = snapshot.get("selected_attributes") or {}
    attribute_values = {
        str(field_id): deepcopy(field.get("value"))
        for field_id, field in selected_attributes.items()
        if isinstance(field, dict) and _has_value(field.get("value"))
    }
    defaults = snapshot.get("transaction_defaults") or {}
    price_defaults = snapshot.get("price_defaults") or {}
    transaction_info = {
        field: _template_transaction_value(defaults.get(field))
        for field in _TRANSACTION_FIELDS
    }
    selected_promotion_id = _text(snapshot.get("selected_promotion_id"))
    listing_mode = _text(snapshot.get("listing_mode") or "site").lower()
    result = {
        "listing_mode": listing_mode,
        "store": deepcopy(snapshot.get("store") or {}),
        "category": deepcopy(snapshot.get("category") or {}),
        "required_fields": deepcopy(snapshot.get("category_fields") or []),
        "attribute_values": attribute_values,
        "variation": deepcopy(snapshot.get("variation") or {}),
        "sizes": list((snapshot.get("variation") or {}).get("sizes") or []),
        **transaction_info,
        "transaction_info": deepcopy(transaction_info),
        "normal_price": _template_price_value(price_defaults.get("normal_price")),
        "sale_price": _template_price_value(price_defaults.get("sale_price")),
        "size_chart_source_url": _text((snapshot.get("size_chart") or {}).get("url")),
        "promotion_activity_id": selected_promotion_id,
        "mabang_discount_id": selected_promotion_id,
        "missing_required_attributes": deepcopy(snapshot.get("missing_required_attributes") or []),
        "import_coverage": deepcopy(snapshot.get("coverage") or {}),
    }
    result["target_site_stores_loaded"] = snapshot.get("target_site_stores_loaded") is True
    result["target_site_stores"] = deepcopy(snapshot.get("target_site_stores") or [])
    if listing_mode == "cnsc_global":
        result.update({
            "cnsc_store": deepcopy(snapshot.get("cnsc_store") or {}),
            "default_site_discount_percent": snapshot.get("default_site_discount_percent") or 40,
            "price_tiers": deepcopy(snapshot.get("price_tiers") or []),
            "warehouse_stock_defaults": deepcopy(snapshot.get("warehouse_stock_defaults") or {}),
            "cnsc_import_sku_rows": deepcopy(snapshot.get("cnsc_sku_rows") or []),
            "missing_configuration": list(snapshot.get("missing_configuration") or []),
            "promotion_activity_id": "",
            "mabang_discount_id": "",
            "normal_price": "",
            "sale_price": "",
        })
    return result


def _normalize_listing_mode(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    evidence = raw.get("listing_mode_evidence") if isinstance(raw.get("listing_mode_evidence"), dict) else {}
    global_param = evidence.get("global_param") is True
    supplied_headers = _string_list(evidence.get("matched_headers"))
    matched_headers = [header for header in _CNSC_REQUIRED_HEADERS if header in supplied_headers]
    has_cnsc_headers = len(matched_headers) == len(_CNSC_REQUIRED_HEADERS)
    supplied_mode = _text(raw.get("listing_mode")).lower()
    detected_mode = "cnsc_global" if global_param and has_cnsc_headers else "site"
    if global_param != has_cnsc_headers:
        raise MabangImportSnapshotError("页面类型识别冲突：global=4 与 CNSC 表头不一致")
    if supplied_mode and supplied_mode not in {"site", "cnsc_global"}:
        raise MabangImportSnapshotError("马帮页面刊登模式无效")
    if supplied_mode and supplied_mode != detected_mode:
        raise MabangImportSnapshotError("页面类型识别冲突：采集模式与页面证据不一致")
    return detected_mode, {"global_param": global_param, "matched_headers": matched_headers}


def _normalize_cnsc_sku_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        warehouses = row.get("warehouse_stocks") if isinstance(row.get("warehouse_stocks"), dict) else {}
        site_prices = row.get("site_prices") if isinstance(row.get("site_prices"), dict) else {}
        normalized_site_prices: dict[str, dict[str, float]] = {}
        for raw_code, raw_prices in site_prices.items():
            code = _text(raw_code).upper()
            if code == "SIP" or not isinstance(raw_prices, dict):
                continue
            original = _optional_number(raw_prices.get("original_price"), positive=True)
            sale = _optional_number(raw_prices.get("sale_price"), positive=True)
            if original != "" and sale != "" and float(sale) <= float(original):
                normalized_site_prices[code] = {"original_price": float(original), "sale_price": float(sale)}
        result.append({
            "seller_sku": _text(row.get("seller_sku")),
            "color": _text(row.get("color")),
            "size": _text(row.get("size")),
            "global_price_cny": _optional_number(row.get("global_price_cny"), positive=True),
            "warehouse_stocks": {
                code: _optional_stock(warehouses.get(code))
                for code in _CNSC_WAREHOUSES
            },
            "site_prices": normalized_site_prices,
        })
    return result


def _derive_cnsc_price_tiers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    present_sizes = set(_ordered_values(rows, "size"))
    result: list[dict[str, Any]] = []
    for tier_id, configured_sizes in _CNSC_PRICE_TIERS:
        sizes = [size for size in configured_sizes if size in present_sizes]
        tier_rows = [row for row in rows if row.get("size") in sizes]
        values = [row.get("global_price_cny") for row in tier_rows]
        complete = bool(tier_rows and sizes) and all(isinstance(value, (int, float)) and value > 0 for value in values)
        unique = {float(value) for value in values if isinstance(value, (int, float))}
        price: float | str = next(iter(unique)) if complete and len(unique) == 1 else ""
        site_codes = {
            code
            for row in tier_rows
            for code in (row.get("site_prices") or {})
        }
        site_prices: dict[str, dict[str, float]] = {}
        for code in sorted(site_codes):
            prices = [(row.get("site_prices") or {}).get(code) for row in tier_rows]
            if not prices or not all(isinstance(item, dict) for item in prices):
                continue
            pairs = {
                (float(item["original_price"]), float(item["sale_price"]))
                for item in prices
                if item.get("original_price") not in (None, "") and item.get("sale_price") not in (None, "")
            }
            if len(pairs) == 1 and len(prices) == len(tier_rows):
                original, sale = next(iter(pairs))
                site_prices[code] = {"original_price": original, "sale_price": sale}
        result.append({"id": tier_id, "sizes": sizes, "global_price_cny": price, "site_prices": site_prices})
    return result


def _derive_cnsc_warehouse_defaults(rows: list[dict[str, Any]]) -> dict[str, int]:
    if not rows:
        return {}
    result: dict[str, int] = {}
    for code in _CNSC_WAREHOUSES:
        values = [(row.get("warehouse_stocks") or {}).get(code) for row in rows]
        if not all(isinstance(value, int) and value >= 0 for value in values) or len(set(values)) != 1:
            return {}
        result[code] = values[0]
    return result


def _normalize_target_site_stores(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        site_code = _text(item.get("site_code")).upper()
        store_id = _text(item.get("store_id") or item.get("id"))
        store_name = _text(item.get("store_name") or item.get("name"))
        if site_code == "SIP" or (not store_id and not store_name):
            continue
        identity = (site_code, store_id or store_name)
        if identity in seen:
            continue
        seen.add(identity)
        discount_id = _text(item.get("mabang_discount_id") or item.get("discount_id"))
        discount_percent = _optional_number(item.get("discount_percent"), positive=True)
        catalog_loaded = item.get("discount_catalog_loaded") is True
        catalog: list[dict[str, Any]] = []
        if catalog_loaded and isinstance(item.get("discount_catalog"), list):
            for raw_activity in item["discount_catalog"]:
                if not isinstance(raw_activity, dict):
                    continue
                activity_id = _text(raw_activity.get("mabang_discount_id") or raw_activity.get("activity_id") or raw_activity.get("id"))
                name = _text(raw_activity.get("name") or raw_activity.get("discount_name"))
                if activity_id and name:
                    catalog.append({
                        "mabang_discount_id": activity_id,
                        "name": name,
                        "discount_percent": _optional_number(raw_activity.get("discount_percent"), positive=True),
                    })
        result.append({
            "site_code": site_code,
            "store_id": store_id,
            "store_name": store_name,
            "mabang_discount_id": discount_id,
            "discount_name": _text(item.get("discount_name")),
            "discount_label": _text(item.get("discount_label")),
            "discount_percent": discount_percent,
            "discount_catalog_loaded": catalog_loaded,
            "discount_catalog": catalog,
        })
    return result


def cnsc_missing_configuration(
    *,
    price_tiers: list[dict[str, Any]],
    warehouse_stock_defaults: dict[str, int],
    target_site_stores: list[dict[str, str]],
    default_site_discount_percent: float | int = 40,
) -> list[str]:
    missing = [
        f"price_tiers.{tier.get('id')}.global_price_cny"
        for tier in price_tiers
        if not isinstance(tier.get("global_price_cny"), (int, float)) or float(tier["global_price_cny"]) <= 0
    ]
    if set(warehouse_stock_defaults) != set(_CNSC_WAREHOUSES):
        missing.append("warehouse_stock_defaults")
    if not target_site_stores:
        missing.append("target_site_stores")
    for target in target_site_stores:
        code = str(target.get("site_code") or "").upper() or "UNKNOWN"
        if not _text(target.get("mabang_discount_id") or target.get("discount_id")):
            missing.append(f"target_site_stores.{code}.discount_activity")
        override = target.get("discount_percent")
        percent = _optional_number(
            default_site_discount_percent if _text(override) == "" else override,
            positive=True,
        )
        if percent == "" or float(percent) >= 100:
            missing.append(f"target_site_stores.{code}.discount_percent")
    target_codes = {str(item.get("site_code") or "").upper() for item in target_site_stores if item.get("site_code")}
    matrix_complete = bool(target_codes)
    for tier in price_tiers:
        matrix = tier.get("site_prices") if isinstance(tier.get("site_prices"), dict) else {}
        for site_code in target_codes:
            prices = matrix.get(site_code) if isinstance(matrix.get(site_code), dict) else {}
            sale = _optional_number(prices.get("sale_price"), positive=True)
            original = _optional_number(prices.get("original_price"), positive=True)
            if sale == "" or original == "" or float(sale) > float(original):
                matrix_complete = False
                break
        if not matrix_complete:
            break
    if not matrix_complete:
        missing.append("site_price_matrix")
    return missing


def _optional_number(value: Any, *, positive: bool = False) -> float | str:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return ""
    if number < 0 or (positive and number <= 0):
        return ""
    return number


def _optional_stock(value: Any) -> int | str:
    number = _optional_number(value)
    if number == "" or not float(number).is_integer():
        return ""
    return int(number)


def _normalize_category_attributes(value: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(value, list):
        raise MabangImportSnapshotError("未采集到真实类目属性")
    fields: list[dict[str, Any]] = []
    selected: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_field in value:
        if not isinstance(raw_field, dict):
            continue
        field_id = _text(raw_field.get("field_id"))
        if not field_id or field_id in seen:
            continue
        seen.add(field_id)
        label = _text(raw_field.get("label")) or field_id
        field_type = _text(raw_field.get("type")) or "text"
        required = raw_field.get("required") is True
        options = _normalize_options(raw_field.get("options"))
        selected_value = _selected_value(raw_field.get("selected_value"), multi=field_type == "multi_select")
        fields.append(
            {
                "field_id": field_id,
                "label": label,
                "type": field_type,
                "required": required,
                "options": options,
                "selected_value": deepcopy(selected_value),
                "automation": {
                    "page_field_id": field_id,
                    "label": label,
                    "strategy": "mabang_category_attribute",
                },
            }
        )
        if _has_value(selected_value):
            values = selected_value if isinstance(selected_value, list) else [selected_value]
            labels = [next((item["label"] for item in options if item["value"] == value), value) for value in values]
            selected[field_id] = {
                "field_id": field_id,
                "label": label,
                "type": field_type,
                "required": required,
                "value": deepcopy(selected_value),
                "value_labels": labels,
            }
        elif required:
            missing.append({"field_id": field_id, "label": label})
    return fields, selected, missing


def _normalize_options(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_option in value:
        if not isinstance(raw_option, dict):
            continue
        option_value = _text(raw_option.get("value"))
        if not option_value or option_value in seen:
            continue
        seen.add(option_value)
        options.append({"value": option_value, "label": _text(raw_option.get("label")) or option_value})
    return options


def _selected_value(value: Any, *, multi: bool) -> str | list[str]:
    if isinstance(value, list):
        values = _string_list(value)
        return values if multi or values else (values[0] if values else "")
    text = _text(value)
    return [text] if multi and text else text


def _normalize_variation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    colors = _ordered_values(rows, "color")
    sizes = _ordered_values(rows, "size")
    axes = []
    if colors:
        axes.append({"name": "color", "values": colors})
    if sizes:
        axes.append({"name": "size", "values": sizes})
    return {"colors": colors, "sizes": sizes, "sku_count": len(rows), "axes": axes}


def _normalize_transaction_defaults(rows: list[dict[str, Any]], transaction_information: Any) -> dict[str, dict[str, Any]]:
    page_values = transaction_information if isinstance(transaction_information, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for field in _TRANSACTION_FIELDS:
        collapsed = collapse_uniform_sku_value(rows, field)
        if collapsed["status"] == "missing" and not any(_text(row.get(field)) for row in rows):
            page_value = _text(page_values.get(field))
            if page_value:
                collapsed = {"status": "complete", "value": page_value}
        if collapsed["status"] == "complete":
            collapsed["value"] = _number_value(field, collapsed["value"])
        result[field] = collapsed
    return result


def _normalize_price_defaults(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for field in _PRICE_FIELDS:
        result[field] = _collapse_price_defaults(rows, field)
    return result


def _collapse_price_defaults(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Collapse per-SKU price values into a single template default.

    Native (site) Mabang pages carry 原价/售价 per SKU row, and rows legitimately
    differ — a variant (e.g. a plain no-print color) is priced differently from
    the rest. Unlike product-level transaction fields (stock/weight), differing
    prices must not blank the template default: keep the most common price as
    the default and record the non-uniform rows so the import preview can tell
    the operator, who adjusts the exceptions later per SKU in the copy editor.
    """
    parsed: list[tuple[int, float]] = []
    counts: dict[float, int] = {}
    has_non_numeric = False
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        text = _text(row.get(key))
        if not text:
            continue
        try:
            number = float(text)
        except ValueError:
            has_non_numeric = True
            continue
        if number < 0:
            raise MabangImportSnapshotError(f"交易信息不能为负数：{key}")
        parsed.append((index, number))
        counts[number] = counts.get(number, 0) + 1
    if has_non_numeric:
        raise MabangImportSnapshotError(f"交易信息不是有效数值：{key}")
    if not parsed:
        return {"status": "missing", "value": None}
    if len(counts) == 1:
        return {"status": "complete", "value": parsed[0][1]}
    # 多数价作模板默认；并列时取最早出现的价格，绝不静默丢空模板。
    best = parsed[0][1]
    best_count = counts[best]
    for index, value in parsed:
        if counts[value] > best_count:
            best = value
            best_count = counts[value]
    return {
        "status": "complete",
        "value": best,
        "uniform": False,
        "differing_count": len(parsed) - best_count,
        "value_counts": {("%g" % value): count for value, count in counts.items()},
    }


def _normalize_size_chart(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "url": _text(raw.get("url")),
        "id": _text(raw.get("id")),
        "width": raw.get("width") if isinstance(raw.get("width"), (int, float)) else None,
        "height": raw.get("height") if isinstance(raw.get("height"), (int, float)) else None,
    }


def _number_value(field: str, value: Any) -> int | float:
    try:
        number = float(_text(value))
    except ValueError as exc:
        raise MabangImportSnapshotError(f"交易信息不是有效数值：{field}") from exc
    if number < 0:
        raise MabangImportSnapshotError(f"交易信息不能为负数：{field}")
    if field == "stock":
        if not number.is_integer():
            raise MabangImportSnapshotError("库存必须是整数")
        return int(number)
    return number


def _normalize_promotions(value: Any) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(value, list):
        return [], ""
    items: list[dict[str, Any]] = []
    selected_promotion_id = ""
    seen: set[str] = set()
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        activity_id = _text(raw_item.get("activity_id") or raw_item.get("activityId") or raw_item.get("id"))
        name = _text(raw_item.get("name") or raw_item.get("activity_name") or raw_item.get("activityName"))
        if not activity_id or not name or activity_id in seen:
            continue
        seen.add(activity_id)
        item = {
            "activity_id": activity_id,
            "name": name,
            "discount_rate": _text(raw_item.get("discount_rate") or raw_item.get("discountRate")),
            "start_at": _text(raw_item.get("start_at") or raw_item.get("startTime")),
            "end_at": _text(raw_item.get("end_at") or raw_item.get("endTime")),
            "status": _text(raw_item.get("status")),
        }
        items.append(item)
        if raw_item.get("selected") is True:
            selected_promotion_id = activity_id
    return items, selected_promotion_id


def _coverage(
    *,
    listing_mode: str,
    store: dict[str, Any],
    category: dict[str, Any],
    missing_required: list[dict[str, str]],
    variation: dict[str, Any],
    transaction_defaults: dict[str, dict[str, Any]],
    selected_promotion_id: str,
) -> dict[str, Any]:
    store_category = "complete" if store.get("id") and store.get("name") and category.get("id") and category.get("path") else "missing"
    attributes = "missing" if missing_required else "complete"
    variation_status = "complete" if variation.get("colors") and variation.get("sizes") and variation.get("sku_count") else "missing"
    transaction_states = {item.get("status") for item in transaction_defaults.values()}
    transaction = "conflict" if "conflict" in transaction_states else ("complete" if transaction_states == {"complete"} else "missing")
    promotions = "not_applicable" if listing_mode == "cnsc_global" else ("complete" if selected_promotion_id else "missing")
    statuses = [store_category, attributes, variation_status, transaction]
    if listing_mode != "cnsc_global":
        statuses.append(promotions)
    return {
        "complete": all(status == "complete" for status in statuses),
        "store_category": {"status": store_category},
        "attributes": {"status": attributes},
        "variation": {"status": variation_status},
        "transaction": {"status": transaction},
        "promotions": {"status": promotions},
    }


def _template_transaction_value(value: Any) -> int | float | str:
    if isinstance(value, dict) and value.get("status") == "complete":
        return value.get("value")
    return ""


def _template_price_value(value: Any) -> float | str:
    if isinstance(value, dict) and value.get("status") == "complete":
        return float(value.get("value"))
    return ""


def _ordered_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    result: list[str] = []
    for row in rows:
        value = _text(row.get(key))
        if value and value not in result:
            result.append(value)
    return result


def _provenance(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _text(item) for key, item in value.items() if _text(item)}


def _sanitize_url(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    parts = urlsplit(text)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _required_text(value: Any, message: str) -> str:
    text = _text(value)
    if not text:
        raise MabangImportSnapshotError(message)
    return text


def _store_id_error(store: dict[str, Any], capture_debug: Any) -> str:
    """Human-readable reason a store id is missing, from the page-capture diagnostics."""
    detail = capture_debug if isinstance(capture_debug, dict) else {}
    if not any(detail.values()):
        return "未识别到马帮店铺编号"
    flags = []
    if detail.get("detail_vm") is not True:
        flags.append("未找到马帮编辑表单")
    elif detail.get("store_category_ref") is not True:
        flags.append("编辑表单缺少 storeCategory 引用")
    elif not (store.get("id") or store.get("name")):
        # A brand-new listing carries an empty store block rather than no store
        # block at all, so a missing selection must be reported explicitly.
        flags.append("编辑页未选择店铺")
    return "未识别到马帮店铺编号（" + "；".join(flags) + "）"


def _string_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result


def _has_value(value: Any) -> bool:
    return bool(_string_list(value))


def _text(value: Any) -> str:
    return str(value or "").strip()
