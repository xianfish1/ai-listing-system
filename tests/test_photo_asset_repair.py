from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.api import photo_asset_repair, settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")


def _affected_product(folder: Path, *, main_image: str = "", product_images=None, colors=None, sku: str = "JB0357") -> dict:
    """造一个「修复前导入」的商品：商品照被当成 print，图片字段为空。"""
    folder.mkdir(parents=True, exist_ok=True)
    for name in [
        "T恤印花改图任务.png",
        "T恤印花改图任务 (1).png",
        "T恤印花改图任务 (2).png",
        "前胸透明图.png",
    ]:
        (folder / name).write_bytes(b"image")

    # 用当前扫描器拿到稳定 asset id / 路径，再模拟旧分类。
    from workers.asset_scanner.scanner import scan_asset_root

    scanned = scan_asset_root(folder, mode="recursive")["assets"]
    for asset in scanned:
        if "印花改图任务" in asset["file_name"]:
            asset["asset_type"] = "print"
            asset["sku_option"] = ""
    product = {
        "id": "product-1",
        "listing_kind": "base_product",
        "category": "POD Mens T-Shirt",
        "group_id": sku,
        "main_sku": sku,
        "base_name": sku,
        "asset_root": str(folder),
        "main_image": main_image,
        "product_images": list(product_images or []),
        "size_chart_image": "",
        "video": {},
        "recognized_colors": list(colors or []),
        "variant_image_map": {"White": "T恤印花改图任务.png"} if colors else {},
        "variant_count": 0,
        "status": "needs_check",
        "created_at": "2026-11-05T00:00:00+00:00",
        "updated_at": "2026-11-05T00:00:00+00:00",
    }
    with storage.locked_store() as (data, save):
        data.setdefault("assets", [])
        data.setdefault("base_products", [])
        data["assets"] = [asset for asset in data["assets"] if asset.get("id") not in {item["id"] for item in scanned}]
        data["assets"].extend(scanned)
        data["base_products"] = [item for item in data["base_products"] if item.get("id") != product["id"]]
        data["base_products"].append(product)
        save(data)
    return product


def _get_product(product_id: str = "product-1") -> dict:
    return storage.get_item("base_products", product_id)


def test_plan_finds_affected_product_and_previews_restore(tmp_path: Path) -> None:
    _affected_product(tmp_path / "JB0357")

    plan = photo_asset_repair.build_repair_plan(["JB0357"])

    assert len(plan["items"]) == 1
    item = plan["items"][0]
    assert item["before"]["main_image"] == ""
    assert item["before"]["product_images"] == []
    assert item["before"]["asset_counts"] == {"print": 4}
    assert item["after"]["asset_counts"].get("variant") == 3
    assert item["after"]["main_image"] == "T恤印花改图任务.png"
    assert set(item["after"]["product_images"]) == {
        "T恤印花改图任务.png",
        "T恤印花改图任务 (1).png",
        "T恤印花改图任务 (2).png",
    }
    assert "main_image" in item["changes"]
    assert "product_images" in item["changes"]
    assert item["color_stage"] == "needed"
    assert len(item["color_targets"]) == 3
    # 预览不写数据。
    assert _get_product()["main_image"] == ""


def test_apply_restores_images_and_asset_types_and_is_idempotent(tmp_path: Path) -> None:
    _affected_product(tmp_path / "JB0357")

    plan = photo_asset_repair.build_repair_plan(["JB0357"])
    result = photo_asset_repair.apply_repair_plan(plan)

    assert result["backup"]
    assert result["applied"][0]["updated_fields"] == ["main_image", "product_images"]
    product = _get_product()
    assert product["main_image"] == "T恤印花改图任务.png"
    assert set(product["product_images"]) == {
        "T恤印花改图任务.png",
        "T恤印花改图任务 (1).png",
        "T恤印花改图任务 (2).png",
    }
    assets = storage.assets_for_base_product(product)
    types = {asset["file_name"]: asset["asset_type"] for asset in assets}
    assert types["T恤印花改图任务.png"] == "variant"
    assert types["前胸透明图.png"] == "print"
    # 其他字段保持原值。
    assert product["status"] == "needs_check"
    assert product["recognized_colors"] == []
    assert product["created_at"] == "2026-11-05T00:00:00+00:00"

    # 重复执行：不新建素材、不再写数据、结果一致。
    asset_count = len([a for a in storage.list_items("assets") if a.get("group_id") == "JB0357"])
    again = photo_asset_repair.build_repair_plan(["JB0357"])
    assert again["items"][0]["changes"] == []
    second = photo_asset_repair.apply_repair_plan(again)
    assert second.get("unchanged") is True
    assert second["backup"] == ""
    assert len([a for a in storage.list_items("assets") if a.get("group_id") == "JB0357"]) == asset_count
    assert _get_product()["product_images"] == product["product_images"]


def test_manual_selection_and_colors_are_preserved(tmp_path: Path) -> None:
    _affected_product(
        tmp_path / "JB0357",
        main_image="手工主图.jpg",
        product_images=["手工主图.jpg", "T恤印花改图任务.png"],
        colors=["White"],
    )

    plan = photo_asset_repair.build_repair_plan(["JB0357"])
    item = plan["items"][0]
    assert item["after"]["main_image"] == "手工主图.jpg"
    assert item["after"]["product_images"] == ["手工主图.jpg", "T恤印花改图任务.png"]
    assert "main_image" not in item["changes"]
    assert "product_images" not in item["changes"]
    # 已有颜色 → 不重跑 AI，避免把手工颜色改掉。
    assert item["color_stage"] == "skipped_existing_colors"

    photo_asset_repair.apply_repair_plan(plan)
    product = _get_product()
    assert product["main_image"] == "手工主图.jpg"
    assert product["product_images"] == ["手工主图.jpg", "T恤印花改图任务.png"]
    assert product["recognized_colors"] == ["White"]


def test_only_current_asset_root_is_rescanned(tmp_path: Path) -> None:
    product = _affected_product(tmp_path / "current" / "JB0357")
    # 同一 SKU 在旧目录还有一批素材：修复不能把它们混进来或改分类。
    old_dir = tmp_path / "old" / "JB0357"
    old_dir.mkdir(parents=True)
    old_file = old_dir / "T恤印花改图任务.png"
    old_file.write_bytes(b"old")
    old_asset = {
        "id": "old-asset",
        "group_id": "JB0357",
        "category": "POD Mens T-Shirt",
        "asset_type": "print",
        "sku_option": "",
        "file_path": str(old_file),
        "file_name": old_file.name,
        "status": "ready",
    }
    with storage.locked_store() as (data, save):
        data["assets"].append(old_asset)
        save(data)

    plan = photo_asset_repair.build_repair_plan(["JB0357"])
    photo_asset_repair.apply_repair_plan(plan)

    product = _get_product()
    assert product["main_image"] == "T恤印花改图任务.png"
    assets = storage.assets_for_base_product(product)
    assert all(str(asset.get("file_path", "")).startswith(str(tmp_path / "current")) for asset in assets)
    old = next(asset for asset in storage.list_items("assets") if asset["id"] == "old-asset")
    assert old["asset_type"] == "print"


def test_missing_sku_is_reported(tmp_path: Path) -> None:
    plan = photo_asset_repair.build_repair_plan(["JB0366", "NO_SUCH_SKU"])
    assert plan["items"] == []
    assert plan["missing"] == ["JB0366", "NO_SUCH_SKU"]


def test_regenerate_colors_uses_existing_forced_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=0):
        calls.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _Response(
            {
                "ai_stages": {"colors": "done"},
                "ai_generation_error": "",
                "recognized_colors": ["Black", "White"],
                "variant_count": 2,
            }
        )

    monkeypatch.setattr(photo_asset_repair.urllib.request, "urlopen", fake_urlopen)

    result = photo_asset_repair.regenerate_colors("product-1", "http://127.0.0.1:8788/")

    assert result["ok"] is True
    assert result["colors"] == ["Black", "White"]
    assert calls == [
        ("http://127.0.0.1:8788/api/base-products/product-1/regenerate-listing", {"aspect": "colors"})
    ]


def test_regenerate_colors_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "ai_stages": {"colors": "failed"},
                    "ai_generation_error": "颜色识别失败: timeout",
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(photo_asset_repair.urllib.request, "urlopen", lambda *a, **k: _Response())

    result = photo_asset_repair.regenerate_colors("product-1", "http://127.0.0.1:8788")

    assert result["ok"] is False
    assert result["stage"] == "failed"
    assert "timeout" in result["error"]


def test_format_plan_lists_missing_skus(tmp_path: Path) -> None:
    text = photo_asset_repair.format_plan({"items": [], "missing": ["JB0366"]})
    assert "JB0366" in text
    assert "找不到" in text


def test_ai_color_translations_map_to_print_named_product_photos() -> None:
    """模拟 AI 返回：颜色名要按文件名对回「T恤印花改图任务」这批商品照。"""
    from services.api.main import _forced_color_translation_patch

    target_assets = [
        {"file_name": "T恤印花改图任务.png", "asset_type": "variant"},
        {"file_name": "T恤印花改图任务 (1).png", "asset_type": "variant"},
        {"file_name": "T恤印花改图任务 (2).png", "asset_type": "variant"},
    ]
    translations = {
        "T恤印花改图任务.png": "Black",
        "T恤印花改图任务 (1).png": "White",
        "T恤印花改图任务 (2).png": "Red",
    }

    patch = _forced_color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        translations,
        target_assets,
    )

    assert patch["variant_image_map"] == {
        "Black": "T恤印花改图任务.png",
        "White": "T恤印花改图任务 (1).png",
        "Red": "T恤印花改图任务 (2).png",
    }
    assert patch["recognized_colors"] == ["Black", "White", "Red"]
