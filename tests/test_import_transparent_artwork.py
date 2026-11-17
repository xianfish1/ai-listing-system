"""导入商品时跳过透明图（前胸/后背/袖口透明图、透明底…）。

透明印花层只用于生产打样，默认不进素材库/商品图/颜色图；操作员在商品维护里
手动上传的商品图（add_base_product_product_image）不走这条链路，不受影响。
确实要导透明图时显式传 include_transparent_artwork=true。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage
from workers.asset_scanner.scanner import is_transparent_artwork_name, scan_asset_root


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _make_folder(root: Path) -> Path:
    folder = root / "AL0004 透明印花测试"
    folder.mkdir(parents=True)
    for name in (
        "主图.png",
        "A白色.png",
        "前胸透明图.png",
        "袖口透明图_验收失败_第1次.png",
        "前印花.png",
    ):
        (folder / name).write_bytes(b"image")
    return folder


def _asset_names(product: dict) -> set[str]:
    return {asset["file_name"] for asset in storage.assets_for_group(product["group_id"])}


def test_import_skips_transparent_artwork(tmp_path: Path) -> None:
    product = storage.create_base_product_from_folder({"root_path": str(_make_folder(tmp_path))})

    names = _asset_names(product)
    assert "前胸透明图.png" not in names
    assert "袖口透明图_验收失败_第1次.png" not in names
    # 非透明素材照常导入：印花 jpg 保留，只是不进商品图（print 的既有规则）。
    assert {"主图.png", "A白色.png", "前印花.png"} <= names
    assert "前胸透明图.png" not in product["product_images"]
    assert product["variant_image_map"].get("White") == "A白色.png"


def test_include_flag_keeps_transparent_artwork(tmp_path: Path) -> None:
    product = storage.create_base_product_from_folder({
        "root_path": str(_make_folder(tmp_path)),
        "include_transparent_artwork": True,
    })

    assert "前胸透明图.png" in _asset_names(product)


def test_scan_reports_skipped_transparent_count(tmp_path: Path) -> None:
    result = scan_asset_root(_make_folder(tmp_path), mode="recursive", skip_transparent_artwork=True)

    assert result["skipped_transparent_count"] == 2
    assert all("透明" not in asset["file_name"] for asset in result["assets"])


def test_transparent_name_matching_follows_print_markers() -> None:
    assert is_transparent_artwork_name("前胸透明图白色.png")
    assert is_transparent_artwork_name("后背透明图 (2).png")
    assert is_transparent_artwork_name("transparent-print.png")
    assert not is_transparent_artwork_name("前印花.png")
    assert not is_transparent_artwork_name("A白色.png")
