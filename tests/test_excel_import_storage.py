"""Excel 导入创建基础商品：素材登记、模板优先字段、重复跳过。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _png(path: Path, color: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)
    return path


def _payload(root: Path) -> dict:
    _png(root / "主图-1.png", "white")
    _png(root / "主图-2.png", "black")
    _png(root / "颜色-Red.png", "red")
    _png(root / "颜色-PureWhite.png", "white")
    return {
        "main_sku": "JB0034",
        "title": "Cute Casual Barbie T-Shirt",
        "asset_root": str(root),
        "gallery": ["主图-1.png", "主图-2.png"],
        "variant_images": {"Red": "颜色-Red.png", "PureWhite": "颜色-PureWhite.png"},
        "colors": ["Red", "PureWhite"],
        "source_batch_id": "batch-1",
        "source_meta": {"store_name": "9️⃣JH_POD", "product_id": "47769106789"},
    }


def test_creates_base_product_with_excel_assets(tmp_path: Path) -> None:
    root = tmp_path / "JB0034"
    result = storage.create_base_product_from_excel(_payload(root))

    assert result["status"] == "created"
    product = result["product"]
    assert product["main_sku"] == "JB0034"
    assert product["group_id"] == "JB0034"
    assert product["listing_title"] == "Cute Casual Barbie T-Shirt"
    # Template-first: price/description/attributes/sizes are not taken from Excel.
    assert product["recognized_sizes"] == []
    assert product["selected_sizes"] == []
    assert product["price_tiers"] == []
    assert "description" not in product
    assert product["status"] == "base_ready"
    assert product["source"] == "excel_import"
    assert product["source_batch_id"] == "batch-1"
    assert product["source_meta"]["store_name"] == "9️⃣JH_POD"
    assert product["main_image"] == "主图-1.png"
    assert product["product_images"] == ["主图-1.png", "主图-2.png"]
    assert product["recognized_colors"] == ["Red", "PureWhite"]
    assert product["variant_image_map"] == {
        "Red": "颜色-Red.png",
        "PureWhite": "颜色-PureWhite.png",
    }
    assert product["variant_count"] == 2

    assets = storage.assets_for_group("JB0034")
    assert len(assets) == 4
    assert {asset["origin"] for asset in assets} == {"excel_import"}
    variants = {asset["sku_option"]: asset["file_name"] for asset in assets if asset["asset_type"] == "variant"}
    assert variants == {"Red": "颜色-Red.png", "PureWhite": "颜色-PureWhite.png"}
    assert all(asset["width"] == 8 and asset["height"] == 8 for asset in assets)


def test_variant_map_can_reuse_a_gallery_image(tmp_path: Path) -> None:
    root = tmp_path / "JB0034"
    payload = _payload(root)
    # Colour photos that duplicate gallery images point at the gallery file; no
    # second copy and no extra variant asset is registered.
    payload["variant_images"] = {"Red": "主图-1.png", "PureWhite": "主图-2.png"}

    result = storage.create_base_product_from_excel(payload)

    product = result["product"]
    assert result["asset_count"] == 2
    assert product["recognized_colors"] == ["Red", "PureWhite"]
    assert product["variant_image_map"] == {"Red": "主图-1.png", "PureWhite": "主图-2.png"}
    assert product["variant_count"] == 2
    assert len(storage.assets_for_group("JB0034")) == 2


def test_same_main_sku_is_skipped_not_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "JB0034"
    first = storage.create_base_product_from_excel(_payload(root))
    second = storage.create_base_product_from_excel(_payload(root))

    assert first["status"] == "created"
    assert second["status"] == "skipped"
    assert second["reason"] == "exists"
    assert second["product_id"] == first["product"]["id"]
    assert len(storage.list_base_products_with_summary()) == 1
    assert "jb0034" in storage.existing_base_product_identifiers()


def test_reimport_replaces_group_assets_instead_of_duplicating(tmp_path: Path) -> None:
    """删商品不会删素材；同一 SKU 再导一次必须整组换掉旧记录，不能堆两份。"""
    root = tmp_path / "JB0034"
    first = storage.create_base_product_from_excel(_payload(root))
    storage.delete_base_products([first["product"]["id"]])
    assert len(storage.assets_for_group("JB0034")) == 4

    second = storage.create_base_product_from_excel(_payload(root))

    assert second["status"] == "created"
    assert second["asset_count"] == 4
    assert len(storage.assets_for_group("JB0034")) == 4


def test_missing_image_aborts_without_partial_product(tmp_path: Path) -> None:
    root = tmp_path / "JB0034"
    payload = _payload(root)
    payload["gallery"] = ["主图-1.png", "缺图.png"]

    with pytest.raises(ValueError, match="素材文件不存在"):
        storage.create_base_product_from_excel(payload)

    assert storage.list_base_products_with_summary() == []
    assert storage.assets_for_group("JB0034") == []
