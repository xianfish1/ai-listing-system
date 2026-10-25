"""资产缩略图：列表/卡片不再加载原图（现场素材库 2000+ 张、3.5GB）。"""

from __future__ import annotations

import io
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from urllib import error, request

from PIL import Image

from services.api import asset_thumbnails, settings, storage
from services.api.main import ApiHandler


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _start_api() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _fetch(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    try:
        with request.urlopen(request.Request(url, headers=headers or {})) as response:
            return response.status, dict(response.headers.items()), response.read()
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def _make_image(tmp_path: Path, name: str = "big.png", size: tuple[int, int] = (1200, 900)) -> Path:
    image_path = tmp_path / "storage" / "uploads" / name
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 30, 40)).save(image_path)
    return image_path


def _register_asset(image_path: Path, asset_id: str = "asset-1", *, group_id: str = "AL0001") -> str:
    with storage.locked_store() as (data, save):
        data.setdefault("assets", []).append(
            {
                "id": asset_id,
                "asset_type": "main",
                "group_id": group_id,
                "file_name": image_path.name,
                "file_path": str(image_path),
                "status": "ready",
            }
        )
        save(data)
    return asset_id


def test_thumbnail_is_generated_cached_and_reused(tmp_path: Path) -> None:
    source = _make_image(tmp_path)
    first = asset_thumbnails.ensure_thumbnail(source, 200, key="asset-1")
    assert first is not None and first.is_file()
    assert first.suffix == ".jpg"
    with Image.open(first) as thumb:
        assert max(thumb.size) <= 200
    assert first.stat().st_size < source.stat().st_size

    stamp = first.stat().st_mtime_ns
    again = asset_thumbnails.ensure_thumbnail(source, 200, key="asset-1")
    assert again == first
    assert again.stat().st_mtime_ns == stamp, "第二次必须直接复用，不重新生成"


def test_thumbnail_regenerates_when_the_source_changes(tmp_path: Path) -> None:
    source = _make_image(tmp_path)
    first = asset_thumbnails.ensure_thumbnail(source, 128, key="asset-1")
    assert first is not None
    _make_image(tmp_path, "big.png", (800, 800))
    second = asset_thumbnails.ensure_thumbnail(source, 128, key="asset-1")
    assert second is not None
    assert second != first, "源图换了新版本，缩略图地址也要换"


def test_unsupported_and_broken_sources_return_none(tmp_path: Path) -> None:
    text_file = tmp_path / "notes.txt"
    text_file.write_text("not an image", encoding="utf-8")
    assert asset_thumbnails.ensure_thumbnail(text_file, 200) is None

    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not really a png")
    assert asset_thumbnails.ensure_thumbnail(broken, 200) is None


def test_thumbnail_edge_is_clamped() -> None:
    assert asset_thumbnails.normalize_edge("") == asset_thumbnails.DEFAULT_THUMB_EDGE
    assert asset_thumbnails.normalize_edge("abc") == asset_thumbnails.DEFAULT_THUMB_EDGE
    assert asset_thumbnails.normalize_edge(1) == asset_thumbnails.MIN_THUMB_EDGE
    assert asset_thumbnails.normalize_edge(100000) == asset_thumbnails.MAX_THUMB_EDGE
    assert asset_thumbnails.normalize_edge(320) == 320


def test_thumb_endpoint_serves_a_small_jpeg_with_etag(tmp_path: Path) -> None:
    source = _make_image(tmp_path)
    asset_id = _register_asset(source)
    server, base_url = _start_api()
    try:
        url = f"{base_url}/api/assets/{asset_id}/thumb?w=200"
        status, headers, body = _fetch(url)
        assert status == 200
        assert "image/jpeg" in str(headers.get("Content-Type") or headers.get("content-type") or "")
        assert len(body) < source.stat().st_size
        with Image.open(io.BytesIO(body)) as image:
            assert max(image.size) <= 200
        etag = headers.get("ETag") or headers.get("etag")
        assert etag
        assert "no-cache" in str(headers.get("Cache-Control") or headers.get("cache-control") or "")
        assert (settings.STORAGE_DIR / "thumbnails").is_dir()

        status, _, _ = _fetch(url, {"If-None-Match": etag})
        assert status == 304
    finally:
        server.shutdown()
        server.server_close()


def test_thumb_endpoint_defaults_and_reports_missing_assets(tmp_path: Path) -> None:
    source = _make_image(tmp_path)
    asset_id = _register_asset(source)
    server, base_url = _start_api()
    try:
        status, _, body = _fetch(f"{base_url}/api/assets/{asset_id}/thumb")
        assert status == 200
        with Image.open(io.BytesIO(body)) as image:
            assert max(image.size) <= asset_thumbnails.DEFAULT_THUMB_EDGE

        status, _, _ = _fetch(f"{base_url}/api/assets/does-not-exist/thumb")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_frontend_list_views_use_thumbnail_urls() -> None:
    """列表/卡片必须走缩略图；只有 helper 内部允许直接拼原图地址。"""

    script = (Path(__file__).resolve().parents[1] / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    assert "function assetImageUrl(asset, thumbEdge = 0)" in script

    assert "listingCopyThumbUrl" in script and "assetImageUrl(asset, 128)" in script
    assert "assetImageUrl(mainAsset, 480)" in script, "商品卡片主图要用缩略图"
    marker = chr(96) + "/api/assets/" + chr(36) + "{encodeURIComponent("
    assert script.count(marker) == 1

def _upload_size_chart(tmp_path: Path, size: tuple[int, int] = (1600, 1200)) -> str:
    import base64

    from services.api import size_chart_uploads

    buffer = io.BytesIO()
    Image.new("RGB", size, (250, 250, 250)).save(buffer, "PNG")
    info = size_chart_uploads.save_image_upload(
        {
            "file_name": "size-chart.png",
            "content_type": "image/png",
            "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
        storage_dir=settings.STORAGE_DIR,
        kind="size_chart",
    )
    return str(info["id"])


def test_size_chart_preview_can_be_requested_small(tmp_path: Path) -> None:
    """尺码图预览：列表里只显示 72px，原图却可能 1MB+。"""

    chart_id = _upload_size_chart(tmp_path)
    server, base_url = _start_api()
    try:
        url = f"{base_url}/api/uploads/size-chart/{chart_id}"
        status, headers, body = _fetch(url)
        assert status == 200
        assert "image/png" in str(headers.get("Content-Type") or "")
        original_bytes = len(body)

        status, headers, small = _fetch(f"{url}?w=192")
        assert status == 200
        assert "image/jpeg" in str(headers.get("Content-Type") or "")
        with Image.open(io.BytesIO(small)) as image:
            assert max(image.size) <= 192
        assert len(small) < original_bytes

        etag = headers.get("ETag") or headers.get("etag")
        assert etag
        status, _, _ = _fetch(f"{url}?w=192", {"If-None-Match": etag})
        assert status == 304
    finally:
        server.shutdown()
        server.server_close()


def test_size_chart_url_stays_raw_in_stored_data() -> None:
    """库里存的仍然是 /api/uploads/size-chart/<id>：小图只在渲染时挂 ?w=。"""

    from services.api import size_chart_uploads

    assert size_chart_uploads.IMAGE_KINDS["size_chart"]["preview_url"] == "/api/uploads/size-chart/{asset_id}"

    script = (Path(__file__).resolve().parents[1] / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    assert "function sizeChartPreviewUrl(url, edge = 480)" in script
    assert "sizeChartPreviewUrl(template.size_chart_preview_url, 192)" in script, "模板表的小图要走缩略图"
    assert "sizeChartPreviewUrl(url, 720)" in script
    assert "sizeChartPreviewUrl(src, 720)" in script


def test_smaller_sizes_derive_from_one_master_decode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一张原图只解码一次：其它尺寸从 900 母版派生（实测 400ms → 15ms）。"""

    source = _make_image(tmp_path, "huge.png", (1600, 1200))
    decodes: list[int] = []
    real_generate = asset_thumbnails._generate_from_source

    def counting(source_path, edge, key):
        decodes.append(edge)
        return real_generate(source_path, edge, key)

    monkeypatch.setattr(asset_thumbnails, "_generate_from_source", counting)

    small = asset_thumbnails.ensure_thumbnail(source, 128, key="asset-1")
    medium = asset_thumbnails.ensure_thumbnail(source, 480, key="asset-1")
    big = asset_thumbnails.ensure_thumbnail(source, asset_thumbnails.MASTER_EDGE, key="asset-1")

    assert small is not None and medium is not None and big is not None
    assert decodes == [asset_thumbnails.MASTER_EDGE], f"原图只该解码一次，实际 {decodes}"
    for path, edge in ((small, 128), (medium, 480)):
        with Image.open(path) as image:
            assert max(image.size) <= edge


def test_warm_masters_generates_in_the_background(tmp_path: Path) -> None:
    source = _make_image(tmp_path, "warm.png", (1200, 900))
    asset = {"id": "asset-warm", "file_path": str(source), "file_hash": "hash-1"}

    before = asset_thumbnails.warm_progress()["queued"]
    assert asset_thumbnails.schedule_warm_masters([asset]) == 1
    assert asset_thumbnails.warm_progress()["queued"] >= before

    master = asset_thumbnails._target_path(source, asset_thumbnails.MASTER_EDGE, "asset-warm")
    deadline = time.time() + 15
    while time.time() < deadline:
        if master is not None and master.is_file() and master.stat().st_size > 0:
            break
        time.sleep(0.05)
    assert master is not None and master.is_file(), "后台预热应该生成母版"


def test_warm_is_deduplicated_per_asset_version(tmp_path: Path) -> None:
    source = _make_image(tmp_path, "warm2.png", (400, 400))
    asset = {"id": "asset-warm-2", "file_path": str(source), "file_hash": "hash-2"}

    baseline = asset_thumbnails.warm_progress()["queued"]
    asset_thumbnails.schedule_warm_masters([asset])
    deadline = time.time() + 10
    while time.time() < deadline and asset_thumbnails.warm_progress()["queued"] <= baseline:
        time.sleep(0.05)
    assert asset_thumbnails.warm_progress()["queued"] > baseline

    after_first = asset_thumbnails.warm_progress()["queued"]
    asset_thumbnails.schedule_warm_masters([asset])
    time.sleep(0.5)
    assert asset_thumbnails.warm_progress()["queued"] == after_first, "同一 asset 版本不重复排队"
    asset_thumbnails.schedule_warm_masters([{**asset, "file_hash": "hash-3"}])


def test_assets_endpoint_schedules_warming_without_blocking(tmp_path: Path) -> None:
    source = _make_image(tmp_path, "list.png", (900, 900))
    _register_asset(source, "asset-list")
    server, base_url = _start_api()
    try:
        status, _, body = _fetch(f"{base_url}/api/assets")
        assert status == 200
        assert len(json.loads(body)["items"]) == 1
        master = asset_thumbnails._target_path(source, asset_thumbnails.MASTER_EDGE, "asset-list")
        deadline = time.time() + 15
        while time.time() < deadline:
            if master is not None and master.is_file() and master.stat().st_size > 0:
                break
            time.sleep(0.05)
        assert master is not None and master.is_file(), "列表接口应顺手把母版排进后台"
    finally:
        server.shutdown()
        server.server_close()

def test_priority_asset_ids_cover_the_list_main_images(tmp_path: Path) -> None:
    """商品/副本列表会显示的主图要排在最前：现场预热按入库顺序只覆盖到 33/91。"""

    referenced = _make_image(tmp_path, "referenced.png", (300, 300))
    other = _make_image(tmp_path, "other.png", (300, 300))
    _register_asset(referenced, "asset-ref")
    _register_asset(other, "asset-other", group_id="AL9999")
    with storage.locked_store() as (data, save):
        data["base_products"] = [
            {
                "id": "bp1",
                "group_id": "AL0001",
                "main_image": "referenced.png",
                "product_images": ["referenced.png"],
            }
        ]
        data["store_listing_copies"] = [
            {"id": "copy1", "base_product_id": "bp1", "group_id": "AL0001", "main_image": "referenced.png"}
        ]
        save(data)

    ids = storage.priority_thumbnail_asset_ids()

    assert ids and ids[0] == "asset-ref"
    assert "asset-other" not in ids, "没被列表引用的素材不该占用优先位"


def test_assets_endpoint_warms_referenced_images_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    referenced = _make_image(tmp_path, "referenced.png", (300, 300))
    other = _make_image(tmp_path, "other.png", (300, 300))
    _register_asset(referenced, "asset-ref")
    _register_asset(other, "asset-other", group_id="AL9999")
    with storage.locked_store() as (data, save):
        data["base_products"] = [
            {"id": "bp1", "group_id": "AL0001", "main_image": "referenced.png", "product_images": ["referenced.png"]}
        ]
        save(data)

    captured: dict[str, list[str]] = {}
    real_schedule = asset_thumbnails.schedule_warm_masters

    def capture(assets, *args, **kwargs):
        captured["order"] = [str(item.get("id") or "") for item in assets]
        return 0

    monkeypatch.setattr(asset_thumbnails, "schedule_warm_masters", capture)
    server, base_url = _start_api()
    try:
        status, _, _ = _fetch(f"{base_url}/api/assets")
        assert status == 200
        assert captured.get("order"), "列表接口应该排预热队列"
        assert captured["order"][0] == "asset-ref", f"被引用的主图要排最前，实际 {captured['order'][:3]}"
    finally:
        server.shutdown()
        server.server_close()
        monkeypatch.setattr(asset_thumbnails, "schedule_warm_masters", real_schedule)

