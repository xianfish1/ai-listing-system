from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "storage" / "tmp" / "verify-real-mabang-export-data"
TEMPLATE_PATH = Path(r"\\Juns\产品编辑部\宁\产品导入示例模板 (1).xlsx")
REAL_ROOT = Path(r"\\Juns\产品编辑部\一步履\POD款式\DL  23#成人纯棉\DL0001超天酱\超天酱-1")

os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ["STORAGE_BACKEND"] = "json"
os.environ["DATABASE_URL"] = ""
os.environ["MABANG_TEMPLATE_PATH"] = str(TEMPLATE_PATH)

from services.api import storage
from services.api.mabang_export import (  # noqa: E402
    MABANG_LIST_COLUMN_COUNT,
    export_mabang_products,
)


COLORS = [
    ("black", "黑色", "黑色超天酱1.jpg"),
    ("white", "白色", "白色超天酱1.jpg"),
    ("gray", "灰色", "灰色超天酱1.jpg"),
    ("pink", "粉色", "粉色超天酱1.jpg"),
    ("purple", "紫色", "紫色超天酱1.jpg"),
]
SIZES = ["S", "M", "L", "XL", "2XL"]
IMAGE_COLUMNS = [20, 21, 23, 49]


def _nas_path(name: str) -> str:
    return str(REAL_ROOT / name)


def _assert_file(path: Path) -> None:
    if not path.is_file():
        raise AssertionError(f"required NAS file is not readable: {path}")


def _seed_dl0001_product() -> dict[str, Any]:
    _assert_file(TEMPLATE_PATH)
    _assert_file(REAL_ROOT / "主图1.jpg")
    for _, _, file_name in COLORS:
        _assert_file(REAL_ROOT / file_name)

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    storage.ensure_dirs()
    storage.save_store(storage._empty_store())

    assets = [
        {
            "asset_type": "main",
            "file_path": _nas_path("主图1.jpg"),
            "file_name": "主图1.jpg",
            "group_id": "DL0001",
            "group_root": str(REAL_ROOT),
            "sku_option": "",
            "status": "ready",
        }
    ]
    for color_code, color_cn, file_name in COLORS:
        assets.append(
            {
                "asset_type": "variant",
                "file_path": _nas_path(file_name),
                "file_name": file_name,
                "group_id": "DL0001",
                "group_root": str(REAL_ROOT),
                "sku_option": color_code,
                "color_cn": color_cn,
                "status": "ready",
            }
        )
    storage.replace_assets(assets)

    product = storage.create_product(
        {
            "category": "POD 男装T恤",
            "platform": "shopee",
            "store": "real-nas-verify",
            "group_id": "DL0001",
            "product_name_cn": "DL0001超天酱 成人纯棉T恤",
            "product_name_en": "DL0001 cotton anime graphic t-shirt",
            "listing_title": "DL0001超天酱 成人纯棉T恤",
            "description": "真实 NAS 路径马帮导出验收商品。",
            "asset_root": str(REAL_ROOT),
            "main_image": "主图1.jpg",
            "weight_kg": 0.25,
            "length_cm": 28,
            "width_cm": 22,
            "height_cm": 2,
            "status": "ready",
        }
    )

    sku_rows = []
    for color_code, color_cn, file_name in COLORS:
        for size in SIZES:
            sku_rows.append(
                {
                    "sku_code": f"DL0001-{color_code.upper()}-{size}",
                    "group_id": "DL0001",
                    "color_code": color_code,
                    "color_cn": color_cn,
                    "size": size,
                    "variant_name": f"{color_cn}/{size}",
                    "stock": 999,
                    "weight_kg": 0.25,
                    "length_cm": 28,
                    "width_cm": 22,
                    "height_cm": 2,
                    "variant_image": file_name,
                    "status": "active",
                }
            )
    storage.replace_product_skus(product["id"], sku_rows)

    price_rows = [
        {
            "sku_code": sku["sku_code"],
            "country": "MY",
            "currency": "MYR",
            "cost_price": 31 if sku["size"] == "2XL" else 28,
            "shipping_cost": 12,
            "normal_price": 95 if sku["size"] == "2XL" else 89,
            "sale_price": 75 if sku["size"] == "2XL" else 69,
            "status": "active",
        }
        for sku in sku_rows
    ]
    storage.replace_product_prices(product["id"], price_rows)
    return product


def _split_paths(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _assert_export_matches_template(xlsx_path: Path) -> None:
    template_wb = load_workbook(TEMPLATE_PATH, read_only=True)
    export_wb = load_workbook(xlsx_path)

    if export_wb.sheetnames != template_wb.sheetnames:
        raise AssertionError(f"sheet names differ: {export_wb.sheetnames} != {template_wb.sheetnames}")

    template_sheet = template_wb["产品导入列表"]
    export_sheet = export_wb["产品导入列表"]
    expected_headers = [template_sheet.cell(1, col).value for col in range(1, MABANG_LIST_COLUMN_COUNT + 1)]
    actual_headers = [export_sheet.cell(1, col).value for col in range(1, MABANG_LIST_COLUMN_COUNT + 1)]
    if actual_headers != expected_headers:
        raise AssertionError("产品导入列表 headers do not match the NAS template")

    data_rows = [
        row
        for row in range(2, export_sheet.max_row + 1)
        if str(export_sheet.cell(row, 46).value or "").strip()
    ]
    if len(data_rows) != 25:
        raise AssertionError(f"expected 25 SKU rows, got {len(data_rows)}")

    checked_paths: set[str] = set()
    for row in data_rows:
        for col in IMAGE_COLUMNS:
            for path_text in _split_paths(export_sheet.cell(row, col).value):
                if "sample_assets" in path_text or r"\\NAS\products" in path_text:
                    raise AssertionError(f"placeholder image path leaked into export: {path_text}")
                if not path_text.startswith(r"\\Juns"):
                    raise AssertionError(f"image path is not a real Juns UNC path: {path_text}")
                if not Path(path_text).is_file():
                    raise AssertionError(f"exported image path is not readable: {path_text}")
                checked_paths.add(path_text)

    if len(checked_paths) < 6:
        raise AssertionError(f"expected main plus 5 variant images, checked {len(checked_paths)} path(s)")


def main() -> None:
    product = _seed_dl0001_product()
    result = export_mabang_products([product["id"]], save_copy=True)
    if not result.get("ok"):
        raise AssertionError(result.get("validation") or result)

    xlsx_path = Path(result["xlsx_path"])
    _assert_export_matches_template(xlsx_path)
    print(f"real_mabang_export_ok path={xlsx_path} rows={result['row_count']}")


if __name__ == "__main__":
    main()
