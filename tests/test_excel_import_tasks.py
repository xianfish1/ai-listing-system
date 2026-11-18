"""Excel 导入批次编排：下载图片 → 建基础商品，重复批次直接跳过。"""
from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image

from services.api import excel_import_assets, excel_import_tasks, settings, storage

HEADERS = [
    "Parent SKU", "product id", "店铺名称", "产品名称", "属性值1", "属性值2",
    "变种图片", "图片1（主图）", "图片2",
]


def _png_bytes(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _workbook_bytes(*, red_reuses_gallery: bool = True) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(HEADERS)
    for color, size in (("Red", "90cm"), ("Blue", "90cm")):
        # Red's variant image is the same CDN photo as the main gallery image in
        # most Mabang exports; Blue keeps its own photo.
        variant_url = "https://cdn.test/main-1.png" if color == "Red" and red_reuses_gallery else f"https://cdn.test/{color}.png"
        sheet.append([
            "T100",
            "9001",
            "店A",
            "Test Tee",
            color,
            size,
            variant_url,
            "https://cdn.test/main-1.png",
            "https://cdn.test/main-2.png",
        ])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _wait_for_task(storage_dir: Path, batch_id: str, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    latest: dict = {}
    while time.time() < deadline:
        snapshot = excel_import_tasks.task_snapshot(storage_dir, batch_id)
        if snapshot:
            latest = snapshot
            if snapshot.get("status") != "running":
                return snapshot
        time.sleep(0.02)
    raise AssertionError(f"import task did not finish: {latest}")


def test_batch_downloads_images_and_creates_base_product(tmp_path: Path) -> None:
    storage_dir = tmp_path / "storage"
    content = _workbook_bytes()
    batch = excel_import_tasks.create_batch(storage_dir, content, existing_keys=set())
    assert batch["summary"]["products"] == 1

    calls: list[str] = []

    def fake_fetcher(url: str):
        calls.append(url)
        return _png_bytes("#%06x" % (abs(hash(url)) & 0xFFFFFF)), ".png"

    excel_import_tasks.start_batch(storage_dir, batch["batch_id"], fetcher=fake_fetcher)
    task = _wait_for_task(storage_dir, batch["batch_id"])

    assert task["status"] == "completed"
    assert task["summary"]["succeeded"] == 1
    # 2 gallery images + Blue's own variant image; Red reuses the main gallery photo.
    assert len(calls) == 3

    products = storage.list_base_products_with_summary()
    assert [product["main_sku"] for product in products] == ["T100"]
    product = products[0]
    assert product["recognized_colors"] == ["Red", "Blue"]
    assert product["variant_image_map"] == {
        "Red": "主图-1.png",
        "Blue": "颜色-Blue.png",
    }
    folder = excel_import_tasks.batch_dir(storage_dir, batch["batch_id"]) / "T100"
    names = {path.name for path in folder.iterdir()}
    assert names == {"主图-1.png", "主图-2.png", "颜色-Blue.png"}


def test_identical_variant_bytes_collapse_to_one_color_image(tmp_path: Path) -> None:
    storage_dir = tmp_path / "storage"
    content = _workbook_bytes(red_reuses_gallery=False)
    batch = excel_import_tasks.create_batch(storage_dir, content, existing_keys=set())

    # Different URLs, identical bytes: the second colour image must not be kept.
    def same_bytes_fetcher(url: str):
        return _png_bytes("blue"), ".png"

    excel_import_tasks.start_batch(storage_dir, batch["batch_id"], fetcher=same_bytes_fetcher)
    task = _wait_for_task(storage_dir, batch["batch_id"])

    assert task["summary"]["succeeded"] == 1
    product = storage.list_base_products_with_summary()[0]
    # Both colours stay, but they point at the same photo (the first gallery file).
    assert product["recognized_colors"] == ["Red", "Blue"]
    assert set(product["variant_image_map"].values()) == {"主图-1.png"}
    folder = excel_import_tasks.batch_dir(storage_dir, batch["batch_id"]) / "T100"
    assert {path.name for path in folder.iterdir()} == {"主图-1.png", "主图-2.png"}


def test_reimport_skips_without_downloading(tmp_path: Path) -> None:
    storage_dir = tmp_path / "storage"
    content = _workbook_bytes()
    first = excel_import_tasks.create_batch(storage_dir, content, existing_keys=set())
    excel_import_tasks.start_batch(storage_dir, first["batch_id"], fetcher=lambda url: (_png_bytes(), ".png"))
    _wait_for_task(storage_dir, first["batch_id"])

    second = excel_import_tasks.create_batch(
        storage_dir,
        content,
        existing_keys=storage.existing_base_product_identifiers(),
    )
    assert second["products"][0]["existing"] is True

    calls: list[str] = []

    def fake_fetcher(url: str):
        calls.append(url)
        return _png_bytes("#%06x" % (abs(hash(url)) & 0xFFFFFF)), ".png"

    excel_import_tasks.start_batch(storage_dir, second["batch_id"], fetcher=fake_fetcher)
    task = _wait_for_task(storage_dir, second["batch_id"])

    assert task["summary"]["skipped"] == 1
    assert calls == []
    assert len(storage.list_base_products_with_summary()) == 1


def test_download_image_retries_then_succeeds(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def flaky(url: str):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient")
        return _png_bytes(), ".png"

    path = excel_import_assets.download_image(
        "https://cdn.test/x.png",
        tmp_path,
        "x",
        fetcher=flaky,
        attempts=3,
        sleep=lambda _seconds: None,
    )

    assert path.name == "x.png"
    assert path.stat().st_size > 0
    assert attempts["count"] == 3


def test_download_image_raises_after_exhausting_retries(tmp_path: Path) -> None:
    def broken(url: str):
        raise RuntimeError("cdn down")

    with pytest.raises(excel_import_assets.ImageDownloadError):
        excel_import_assets.download_image(
            "https://cdn.test/x.png",
            tmp_path,
            "x",
            fetcher=broken,
            attempts=2,
            sleep=lambda _seconds: None,
        )
