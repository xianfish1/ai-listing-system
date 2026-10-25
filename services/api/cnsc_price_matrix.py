"""Parse and validate the paste-friendly CNSC site price matrix."""

from __future__ import annotations

import re
from typing import Any


SITE_CODES = ("TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR")
_SITE_RE = re.compile(r"(?<![A-Z])(" + "|".join(SITE_CODES) + r")(?![A-Z])", re.I)
_SALE_HEADER_RE = re.compile(r"(?:折扣价|折扣價|售价|售價|上新价|上新價|销售价|銷售价|sale|selling)", re.I)
_ORIGINAL_HEADER_RE = re.compile(r"(?:^|\s)(?:价格|價格)(?:\s|$)|(?:原价|原價|划线价|劃線價|original|list)", re.I)


def _number(value: str) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if result > 0 else None


def _site_code(value: str) -> str:
    match = _SITE_RE.search(str(value or "").upper())
    return match.group(1).upper() if match else ""


def _is_price_header(value: str) -> bool:
    text = str(value or "")
    return bool(_SALE_HEADER_RE.search(text) or _ORIGINAL_HEADER_RE.search(text))


def _price_positions(header: list[str]) -> tuple[dict[str, list[int]], list[str]]:
    """Return the two price columns for each site, accepting common 2/3-cell headers.

    CNSC exports are seen in both forms ``PH 售价 / PH 原价`` and
    ``PH 菲律宾 / PH 售价 / PH 原价``.  A three-cell run is only collapsed when
    the first cell is a label and the following two cells are explicitly price
    headers; otherwise it remains a duplicate-site error.
    """
    raw: dict[str, list[int]] = {}
    for index, cell in enumerate(header):
        code = _site_code(cell)
        if code:
            raw.setdefault(code, []).append(index)

    positions: dict[str, list[int]] = {}
    errors: list[str] = []
    for code, indexes in raw.items():
        if len(indexes) == 2:
            sale_indexes = [index for index in indexes if _SALE_HEADER_RE.search(header[index])]
            original_indexes = [index for index in indexes if _ORIGINAL_HEADER_RE.search(header[index]) and not _SALE_HEADER_RE.search(header[index])]
            positions[code] = [sale_indexes[0], original_indexes[0]] if len(sale_indexes) == 1 and len(original_indexes) == 1 else indexes
            continue
        if len(indexes) == 3:
            cells = [header[index] for index in indexes]
            if not _is_price_header(cells[0]) and _is_price_header(cells[1]) and _is_price_header(cells[2]):
                price_indexes = indexes[1:]
                sale_indexes = [index for index in price_indexes if _SALE_HEADER_RE.search(header[index])]
                original_indexes = [index for index in price_indexes if _ORIGINAL_HEADER_RE.search(header[index]) and not _SALE_HEADER_RE.search(header[index])]
                positions[code] = [sale_indexes[0], original_indexes[0]] if len(sale_indexes) == 1 and len(original_indexes) == 1 else price_indexes
                continue
        if len(indexes) > 2:
            errors.append(f"duplicate_site:{code}")
            # Keep the final two candidate columns addressable so preview can
            # report row-level price errors in addition to the blocking
            # duplicate-site error.
            positions[code] = indexes[-2:]
        else:
            errors.append(f"invalid_site_columns:{code}:{len(indexes)}")
            # Keep a one-column site addressable so the row validator can
            # report the missing sale/original value precisely as well.
            positions[code] = [indexes[0], indexes[0] + 1]
    return positions, errors


def _split_row(line: str) -> list[str]:
    cells = line.split("\t")
    if len(cells) == 1:
        # A tab-free line of bare numbers (e.g. a paste that lost its tabs to
        # spaces) still counts as one row of columns; text lines keep their
        # spaces so headers like "TW 售价" stay single cells.
        tokens = line.split()
        if len(tokens) > 1 and all(_number(token) is not None for token in tokens):
            return tokens
    return cells


def parse_price_matrix(
    text: str,
    *,
    tiers: list[dict[str, Any]],
    target_sites: list[str],
) -> dict[str, Any]:
    rows = [_split_row(line) for line in str(text or "").splitlines() if line.strip()]
    result = {"site_order": [], "tiers": [], "warnings": [], "errors": []}
    if not rows:
        result["errors"].append("matrix_empty")
        return result

    header = rows[0]
    positions, header_errors = _price_positions(header)
    normalized_targets = [str(code or "").strip().upper() for code in target_sites if str(code or "").strip()]
    data_rows = rows[1:]
    price_columns = 2 * len(normalized_targets)

    def _numeric_row(cells: list[str]) -> bool:
        return all(_number(cell) is not None for cell in cells if str(cell).strip())

    if not positions and (
        _numeric_row(header)
        or (len(header) == price_columns + 1 and _numeric_row(header[1:]))
    ):
        # Headerless paste of numeric rows: two columns per site in
        # target-site order, sale before original like the 售价/原价 column
        # order of CNSC exports.  A leading tier label column is tolerated.
        positions = {code: [2 * index, 2 * index + 1] for index, code in enumerate(normalized_targets)}
        data_rows = [
            row[1:] if len(row) == price_columns + 1 and _number(row[0]) is None else row
            for row in rows
        ]
    result["site_order"] = list(positions)
    result["errors"].extend(header_errors)
    for code in normalized_targets:
        if code not in positions:
            result["errors"].append(f"missing_site:{code}")
    for code in positions:
        if code not in normalized_targets:
            result["warnings"].append(f"unselected_site:{code}")

    if not data_rows and tiers:
        result["errors"].append(f"tier_row_count:{len(data_rows)}:{len(tiers)}")
    elif len(data_rows) != len(tiers):
        # Short pastes still fill every tier (missing rows reuse the last
        # pasted row, extra rows are ignored); the mismatch is a warning so
        # the parsed prices reach the editor instead of being blocked.
        result["warnings"].append(f"tier_row_count:{len(data_rows)}:{len(tiers)}")
    first_site_index = min((indexes[0] for indexes in positions.values()), default=0)
    prefix_count = first_site_index
    compressed_positions: dict[str, list[int]] = {}
    next_price_index = 0
    for code in result["site_order"]:
        source = positions[code]
        ordered = sorted(source)
        compressed_positions[code] = [next_price_index + ordered.index(source[0]), next_price_index + ordered.index(source[1])]
        next_price_index += 2
    for tier_index, tier in enumerate(tiers):
        # Tiers beyond the pasted rows reuse the last pasted row so a short
        # paste still fills every tier (shortfall reported as a warning); an
        # empty paste skips per-site validation entirely.
        row_missing = not data_rows
        row = data_rows[min(tier_index, len(data_rows) - 1)] if data_rows else []
        # Rows may include a tier label (same width as header) or omit the
        # leading label columns entirely (header width minus its prefix).
        tier_ids = {str(item.get("id") or "").strip().lower() for item in tiers}
        row_label = str(row[0] if row else "").strip().lower()
        compressed_row = len(row) == next_price_index and row_label not in tier_ids and _number(row[0] if row else "") is not None
        offset = prefix_count if prefix_count and not compressed_row and len(row) == len(header) - prefix_count else 0
        site_prices: dict[str, dict[str, float]] = {}
        for code in normalized_targets:
            if row_missing:
                continue
            indexes = compressed_positions.get(code, []) if compressed_row else positions.get(code, [])
            if len(indexes) != 2:
                continue
            values = [row[index - offset] if 0 <= index - offset < len(row) else "" for index in indexes]
            sale = _number(values[0])
            original = _number(values[1])
            if sale is None:
                result["errors"].append(f"invalid_price:{code}:{tier.get('id') or tier_index}:sale_price")
            if original is None:
                result["errors"].append(f"invalid_price:{code}:{tier.get('id') or tier_index}:original_price")
            if sale is not None and original is not None and sale > original:
                result["errors"].append(f"sale_price_greater_than_original:{code}:{tier.get('id') or tier_index}")
            if sale is not None and original is not None:
                site_prices[code] = {"sale_price": sale, "original_price": original}
        result["tiers"].append({
            "id": tier.get("id") or str(tier_index),
            "sizes": list(tier.get("sizes") or []),
            "colors": list(tier.get("colors") or []),
            "site_prices": site_prices,
        })
    return result
