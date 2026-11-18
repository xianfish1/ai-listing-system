"""Parse a Mabang "online products" Excel export into base-product import plans.

The workbook is one row per sub-SKU (``Parent SKU`` + ``product id`` identify the
listing). Everything below is read-only parsing: no network, no store writes.
Image URLs are downloaded later by ``excel_import_tasks``.

Product policy (agreed with the operator):

* Excel only supplies the title, gallery/variant images, colors and the
  variant structure. Price, description, category attributes and sizes stay
  template-owned and are never written onto the base product.
* Colors are kept exactly as exported (``PureWhite`` and ``White`` are two
  different source variants) — do not run them through the AI color palette.
* Multiple listings of the same parent SKU get a ``-2``/``-3`` suffix.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Any

MAX_ROWS = 20_000
MAX_PRODUCTS = 500
MAX_GALLERY = 9
MAX_WARNINGS = 300

# Canonical header names. Exports use full-width parentheses; normalization
# below folds those to ASCII so both spellings match.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "parent_sku": ("parent sku", "parent_sku", "parentsku", "父sku", "主sku"),
    "product_id": ("product id", "productid", "商品id", "产品id"),
    "title": ("产品名称", "商品名称", "标题"),
    "description": ("产品描述", "商品描述", "描述"),
    "color": ("属性值1", "变种值1", "颜色"),
    "size": ("属性值2", "变种值2", "尺码"),
    "sub_sku": ("子sku", "子 sku", "sub sku"),
    "variant_image": ("变种图片", "变种图", "颜色图"),
    "size_chart": ("尺码图", "尺码表"),
    "store_name": ("店铺名称", "店铺"),
    "online_url": ("在线链接", "商品链接"),
    "source_url": ("来源url", "来源链接"),
    "category_id": ("分类id",),
    "category_l1": ("一级分类",),
    "category_l2": ("二级分类",),
    "category_l3": ("三级分类",),
    "category_l4": ("四级分类",),
    "category_l5": ("五级分类",),
    "attributes": ("分类属性", "商品属性"),
    "original_price": ("原价",),
    "sale_price": ("售价", "折扣价"),
    "stock": ("库存",),
    "weight": ("重量kg", "重量", "重量（kg）"),
    "length": ("长cm", "长（cm）", "长"),
    "width": ("宽cm", "宽（cm）", "宽"),
    "height": ("高cm", "高（cm）", "高"),
    "ship_days": ("出货天数",),
}

REQUIRED_FIELDS = ("parent_sku", "title", "color", "variant_image", "main_image")

# The first gallery column also carries the main image.
COLUMN_ALIASES["main_image"] = ("图片1（主图）", "图片1(主图)", "图片1")
GALLERY_FIELDS = tuple(f"gallery_{index}" for index in range(2, MAX_GALLERY + 1))
for _index in range(2, MAX_GALLERY + 1):
    COLUMN_ALIASES[f"gallery_{_index}"] = (f"图片{_index}",)

_ILLEGAL_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_LETTER_SIZE_ORDER = {
    "XXS": 0,
    "XS": 1,
    "S": 2,
    "M": 3,
    "L": 4,
    "XL": 5,
    "XXL": 6,
    "2XL": 7,
    "3XL": 8,
    "4XL": 9,
    "5XL": 10,
}
_NUMERIC_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:cm|厘米)?$", re.IGNORECASE)


class ExcelImportError(ValueError):
    """A workbook-level problem: bad file, missing columns, no importable rows."""


def normalize_header(value: Any) -> str:
    text = str(value or "").strip().lstrip("\ufeff").strip()
    if not text:
        return ""
    text = text.replace("（", "(").replace("）", ")").replace("　", " ")
    text = text.replace("_", "").replace(" ", "").replace("\t", "").lower()
    return text


_ALIAS_LOOKUP: dict[str, str] = {}
for _field, _names in COLUMN_ALIASES.items():
    for _name in _names:
        _ALIAS_LOOKUP.setdefault(normalize_header(_name), _field)


def _cell_text(value: Any) -> str:
    """Trimmed text for a cell; integrates floats/ints without a trailing ``.0``."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().strip("\t").strip()


def _cell_number(value: Any) -> float | None:
    text = _cell_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row_identity(row: dict[str, str]) -> str:
    product_id = row.get("product_id") or ""
    if product_id:
        return f"{row.get('parent_sku', '')}|{product_id}"
    online = row.get("online_url") or ""
    if online:
        return f"{row.get('parent_sku', '')}|{online}"
    return row.get("parent_sku", "")


def _locate_header(rows: list[tuple[Any, ...]]) -> tuple[int, dict[int, str]]:
    """Find the header row and map column index -> canonical field.

    Exports normally start with the header on row 1, but a decorated title row is
    common enough that we scan the first few rows for the required labels.
    """
    for index in range(min(len(rows), 10)):
        mapping: dict[int, str] = {}
        for column, raw in enumerate(rows[index]):
            field = _ALIAS_LOOKUP.get(normalize_header(raw))
            if field and field not in mapping.values():
                mapping[column] = field
        if len(mapping) >= 5 and {"parent_sku", "title", "color"} <= set(mapping.values()):
            return index, mapping
    raise ExcelImportError("没有找到表头行：请确认这是马帮「导出在线商品」的 Excel")


def _read_rows(
    content: bytes, sheet_name: str = ""
) -> tuple[list[str], list[dict[str, str]], str]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types
        raise ExcelImportError("无法读取 Excel 文件，请确认文件没有损坏或加密") from exc
    try:
        worksheet = workbook[sheet_name] if sheet_name and sheet_name in workbook.sheetnames else workbook.worksheets[0]
        raw_rows = list(worksheet.iter_rows(values_only=True))
        title = worksheet.title
    finally:
        workbook.close()

    if len(raw_rows) > MAX_ROWS + 10:
        raise ExcelImportError(f"Excel 行数超过上限（{MAX_ROWS} 行），请拆分后再导入")

    header_index, mapping = _locate_header(raw_rows)
    header_names = [_cell_text(value) for value in raw_rows[header_index]]
    rows: list[dict[str, str]] = []
    for raw in raw_rows[header_index + 1 :]:
        if raw is None or all(value in (None, "") for value in raw):
            continue
        row: dict[str, str] = {}
        for column, field in mapping.items():
            row[field] = _cell_text(raw[column]) if column < len(raw) else ""
        rows.append(row)
    return header_names, rows, title


def _missing_columns(mapping: dict[int, str]) -> list[str]:
    available = set(mapping.values())
    labels = {
        "parent_sku": "Parent SKU",
        "title": "产品名称",
        "color": "属性值1",
        "variant_image": "变种图片",
        "main_image": "图片1（主图）",
    }
    return [labels[field] for field in REQUIRED_FIELDS if field not in available]


def safe_file_stem(value: str, fallback: str) -> str:
    text = _ILLEGAL_FILE_CHARS.sub("_", str(value or "")).strip().strip(". ")
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(". ")
    if not text:
        text = fallback
    return text[:40]


def _unique_stem(stem: str, taken: set[str]) -> str:
    candidate = stem
    index = 1
    while candidate.casefold() in taken:
        index += 1
        candidate = f"{stem}-{index}"
    taken.add(candidate.casefold())
    return candidate


def _normalize_color(value: str) -> str:
    # Trim only: ``5Pcs(+Toys)`` must survive verbatim.
    return re.sub(r"\s+", " ", str(value or "").strip())


# Source listings sometimes carry a "Pure" alias next to the normal colour
# (``PureWhite`` and ``White`` are the same variant sold twice). The operator
# asked to drop those aliases instead of creating a second colour entry.
_PURE_COLOR_BASES = frozenset({
    "white", "black", "blue", "red", "pink", "green", "purple", "brown",
    "apricot", "yellow", "orange", "grey", "gray", "beige", "khaki",
})
_PURE_ALIAS = re.compile(r"^pure\s*([A-Za-z]+)$", re.IGNORECASE)


def canonical_color(value: str) -> str:
    """Fold a source colour name into the import colour key.

    Only the ``Pure<colour>`` alias is folded (``PureWhite``/``Pure WHite`` ->
    ``White``); every other non-standard name (``5Pcs(+Toys)``, ``A Set``) is
    kept exactly as exported.
    """
    color = _normalize_color(value)
    match = _PURE_ALIAS.match(color)
    if match and match.group(1).lower() in _PURE_COLOR_BASES:
        base = match.group(1)
        return base[:1].upper() + base[1:].lower()
    return color


def _size_label(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    without_note = re.sub(r"[（(].*?[)）]\s*$", "", text).strip()
    candidate = without_note or text
    numeric = _NUMERIC_SIZE.match(candidate.replace(" ", ""))
    if numeric:
        number = numeric.group(1).rstrip("0").rstrip(".") if "." in numeric.group(1) else numeric.group(1)
        return f"{number}cm"
    letters = candidate.replace(" ", "").upper()
    if re.fullmatch(r"(?:[2-5]XL|XXS|XS|S|M|L|XL|XXL)", letters):
        return letters
    return candidate


def _size_sort_key(label: str) -> tuple[int, float, int, str]:
    numeric = _NUMERIC_SIZE.match(label)
    if numeric:
        return (0, float(numeric.group(1)), 0, "")
    if label in _LETTER_SIZE_ORDER:
        return (1, 0.0, _LETTER_SIZE_ORDER[label], "")
    return (2, 0.0, 0, label.casefold())


def _parse_attributes(value: str) -> list[dict[str, str]]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    result: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = _cell_text(item.get("original_attribute_name") or item.get("attribute_name"))
        values = item.get("attribute_value_list") or []
        names: list[str] = []
        if isinstance(values, list):
            for entry in values:
                if isinstance(entry, dict):
                    label = _cell_text(entry.get("original_value_name") or entry.get("value_name"))
                    if label:
                        names.append(label)
                elif entry not in (None, ""):
                    names.append(_cell_text(entry))
        if name:
            result.append({"name": name, "value": " / ".join(names)})
    return result


def _category_path(row: dict[str, str]) -> str:
    parts = [
        row.get(key, "")
        for key in ("category_l1", "category_l2", "category_l3", "category_l4", "category_l5")
    ]
    return " > ".join(part for part in parts if part)


def _product_from_rows(
    key: str,
    rows: list[dict[str, str]],
    warnings: list[str],
) -> dict[str, Any] | None:
    parent_sku = next((row.get("parent_sku", "") for row in rows if row.get("parent_sku")), "")
    title = next((row.get("title", "") for row in rows if row.get("title")), "")
    if not parent_sku or not title:
        warnings.append(f"{key or '未命名分组'}：缺少 Parent SKU 或产品名称，整组跳过")
        return None

    product_warnings: list[str] = []

    # casefolded colour key -> display name (first appearance wins the position)
    colors: "OrderedDict[str, str]" = OrderedDict()
    variant_urls: dict[str, str] = {}
    variant_exact: dict[str, bool] = {}
    merged_aliases: set[str] = set()
    sizes: list[str] = []
    seen_sizes: set[str] = set()
    raw_sizes: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()
    blank_color_rows = 0
    duplicate_rows = 0
    for row in rows:
        raw_color = _normalize_color(row.get("color", ""))
        if not raw_color:
            blank_color_rows += 1
            continue
        color = canonical_color(raw_color)
        key_color = color.casefold()
        if key_color not in colors:
            colors[key_color] = color
        alias = raw_color.casefold() != key_color
        if alias and raw_color not in merged_aliases:
            merged_aliases.add(raw_color)
            product_warnings.append(f"颜色 {raw_color} 已并入 {color}")
        image_url = row.get("variant_image", "")
        if image_url:
            current = variant_urls.get(key_color, "")
            # The colour's own spelling wins over the ``Pure*`` alias photo.
            if not current or (not alias and not variant_exact.get(key_color)):
                variant_urls[key_color] = image_url
                variant_exact[key_color] = not alias
            elif image_url != current and not alias and not variant_exact.get(key_color):
                product_warnings.append(f"颜色 {color} 有多张变种图，只使用第一张")
        raw_size = row.get("size", "")
        label = _size_label(raw_size)
        if label:
            if label not in seen_sizes:
                seen_sizes.add(label)
                sizes.append(label)
            if raw_size and raw_size not in raw_sizes:
                raw_sizes.append(raw_size)
        # Duplicate rows are counted on the source spelling so merging a Pure*
        # alias into its base colour does not look like duplicated variants.
        pair = (raw_color.casefold(), label.casefold())
        if pair in seen_pairs:
            duplicate_rows += 1
        seen_pairs.add(pair)

    if blank_color_rows:
        product_warnings.append(f"{blank_color_rows} 行没有颜色（属性值1），已跳过")
    if duplicate_rows:
        product_warnings.append(f"{duplicate_rows} 行颜色+尺码重复，已合并")

    # Colors without a variant image cannot be listed (the executor requires a
    # thumbnail per color), so drop them by name instead of failing silently.
    for key_color in list(colors):
        if key_color not in variant_urls:
            product_warnings.append(f"颜色 {colors[key_color]} 没有变种图，已跳过该颜色")
            del colors[key_color]
    if not colors:
        warnings.append(f"{parent_sku}：没有任何带变种图的颜色，整组跳过")
        return None

    gallery_urls: list[str] = []
    for row in rows:
        for field in ("main_image", *GALLERY_FIELDS):
            url = row.get(field, "").strip()
            if url and url not in gallery_urls:
                gallery_urls.append(url)
        if gallery_urls:
            break
    if not gallery_urls:
        warnings.append(f"{parent_sku}：没有商品图（图片1-9），整组跳过")
        return None
    if len(gallery_urls) > MAX_GALLERY:
        gallery_urls = gallery_urls[:MAX_GALLERY]

    gallery: list[dict[str, Any]] = []
    taken_stems: set[str] = set()
    for index, url in enumerate(gallery_urls, start=1):
        gallery.append({
            "index": index,
            "url": url,
            "file_stem": _unique_stem(f"主图-{index}", taken_stems),
        })

    variant_images: dict[str, dict[str, Any]] = {}
    for key_color, color in colors.items():
        url = variant_urls.get(key_color, "")
        stem = _unique_stem(f"颜色-{safe_file_stem(color, '未命名')}", taken_stems)
        variant_images[color] = {"url": url, "file_stem": stem}

    first = rows[0]
    sale_prices = [value for row in rows if (value := _cell_number(row.get("sale_price"))) is not None]
    original_prices = [value for row in rows if (value := _cell_number(row.get("original_price"))) is not None]
    stocks = [value for row in rows if (value := _cell_number(row.get("stock"))) is not None]
    weight = _cell_number(first.get("weight"))
    package = {
        key: _cell_number(first.get(source_field))
        for key, source_field in (("length", "length"), ("width", "width"), ("height", "height"))
    }

    source_meta = {
        "store_name": next((row.get("store_name", "") for row in rows if row.get("store_name")), ""),
        "product_id": next((row.get("product_id", "") for row in rows if row.get("product_id")), ""),
        "online_url": next((row.get("online_url", "") for row in rows if row.get("online_url")), ""),
        "source_url": next((row.get("source_url", "") for row in rows if row.get("source_url")), ""),
        "category_id": next((row.get("category_id", "") for row in rows if row.get("category_id")), ""),
        "category_path": _category_path(first),
        "sale_price_range": [min(sale_prices), max(sale_prices)] if sale_prices else [],
        "original_price_range": [min(original_prices), max(original_prices)] if original_prices else [],
        "stock_total": int(sum(stocks)) if stocks else None,
        "weight_kg": weight,
        "package_cm": package,
        "sub_sku_count": len(rows),
        "attributes": _parse_attributes(first.get("attributes", "")),
        "ship_days": _cell_text(first.get("ship_days")),
    }

    return {
        "key": key,
        "parent_sku": parent_sku,
        "main_sku": parent_sku,
        "existing": False,
        "title": title,
        "description": next((row.get("description", "") for row in rows if row.get("description")), ""),
        "colors": list(colors.values()),
        "sizes": sorted(sizes, key=_size_sort_key),
        "raw_sizes": raw_sizes,
        "gallery": gallery,
        "variant_images": variant_images,
        "size_chart_url": next((row.get("size_chart", "") for row in rows if row.get("size_chart")), ""),
        "source_meta": source_meta,
        "warnings": product_warnings,
    }


def parse_export_workbook(content: bytes, *, sheet_name: str = "") -> dict[str, Any]:
    """Parse the workbook bytes into ``{"products": [...], "warnings": [...]}``.

    Raises :class:`ExcelImportError` for workbook-level problems.
    """
    if not content:
        raise ExcelImportError("Excel 文件为空")
    header_names, rows, sheet_title = _read_rows(content, sheet_name=sheet_name)
    if not rows:
        raise ExcelImportError("Excel 里没有商品数据行")

    header_mapping = {
        index: field
        for index, name in enumerate(header_names)
        if (field := _ALIAS_LOOKUP.get(normalize_header(name)))
    }
    missing = _missing_columns(header_mapping)
    if missing:
        raise ExcelImportError(f"缺少必需列：{'、'.join(missing)}")

    grouped: "OrderedDict[str, list[dict[str, str]]]" = OrderedDict()
    for row in rows:
        key = _row_identity(row)
        grouped.setdefault(key, []).append(row)
    if len(grouped) > MAX_PRODUCTS:
        raise ExcelImportError(f"商品数量超过上限（{MAX_PRODUCTS} 个），请拆分后再导入")

    warnings: list[str] = []
    products: list[dict[str, Any]] = []
    for key, group_rows in grouped.items():
        product = _product_from_rows(key, group_rows, warnings)
        if product:
            products.append(product)
    if not products:
        raise ExcelImportError("没有可导入的商品：所有分组都缺少图片或颜色")

    return {
        "file_name": "",
        "sheet": sheet_title,
        "columns": header_names,
        "products": products,
        "row_count": len(rows),
        "warnings": warnings[:MAX_WARNINGS],
    }


def apply_existing_skus(products: list[dict[str, Any]], existing_keys: set[str]) -> None:
    """Assign main SKUs, suffixing same-SKU listings and flagging duplicates.

    ``existing_keys`` is a casefolded set of main SKUs / group ids already in the
    store. The first listing of each parent SKU keeps the plain SKU (and is
    flagged ``existing``); later listings in the same batch get ``-2``, ``-3``...
    """
    existing = {str(value or "").strip().casefold() for value in existing_keys if str(value or "").strip()}
    assigned: set[str] = set()
    for product in products:
        base = str(product.get("parent_sku") or "").strip() or "UNKNOWN"
        candidate = base
        index = 1
        while candidate.casefold() in assigned:
            index += 1
            candidate = f"{base}-{index}"
        product["main_sku"] = candidate
        product["existing"] = candidate.casefold() in existing
        assigned.add(candidate.casefold())


def preview_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """Strip bulky source-only fields from the preview HTTP response."""
    products = []
    warnings = list(plan.get("warnings") or [])
    for product in plan.get("products") or []:
        item = {
            key: value
            for key, value in product.items()
            if key not in {"description", "raw_sizes"}
        }
        source_meta = dict(item.get("source_meta") or {})
        attributes = source_meta.pop("attributes", [])
        source_meta["attribute_count"] = len(attributes) if isinstance(attributes, list) else 0
        item["source_meta"] = source_meta
        # Count the files the import will actually keep: gallery images plus
        # colour photos that are not already one of them.
        gallery_urls = {str(entry.get("url") or "") for entry in item.get("gallery") or []}
        extra_urls = {
            str(entry.get("url") or "")
            for entry in (item.get("variant_images") or {}).values()
            if str(entry.get("url") or "")
        } - gallery_urls
        item["image_count"] = len(item.get("gallery") or []) + len(extra_urls)
        label = item.get("main_sku") or item.get("parent_sku") or ""
        for warning in item.get("warnings") or []:
            warnings.append(f"{label}：{warning}")
        products.append(item)
    return {
        "file_name": plan.get("file_name", ""),
        "sheet": plan.get("sheet", ""),
        "row_count": plan.get("row_count", 0),
        "products": products,
        "warnings": warnings[:MAX_WARNINGS],
        "summary": {
            "products": len(products),
            "existing": sum(1 for item in products if item.get("existing")),
            "selected": sum(1 for item in products if not item.get("existing")),
        },
    }


def file_stem_for(path: Path) -> str:
    """Convenience for tests/callers that only need a sanitized stem."""
    return safe_file_stem(path.stem, "image")
