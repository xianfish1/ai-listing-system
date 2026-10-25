"""资源图片/文件响应必须可重新校验（现场：导入商品后图片要刷新页面才更新）。"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from urllib import error, request

from services.api import settings, storage
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


def _make_asset(tmp_path: Path) -> tuple[str, Path]:
    image_path = tmp_path / "storage" / "uploads" / "asset.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"first-version" * 10)
    with storage.locked_store() as (data, save):
        asset = {
            "id": "asset-1",
            "asset_type": "main",
            "group_id": "AL0001",
            "file_name": "asset.png",
            "file_path": str(image_path),
            "status": "ready",
        }
        data.setdefault("assets", []).append(asset)
        save(data)
    return "asset-1", image_path


def _fetch(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    try:
        with request.urlopen(request.Request(url, headers=headers or {})) as response:
            return response.status, dict(response.headers.items()), response.read()
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def test_asset_file_response_revalidates_with_etag(tmp_path: Path) -> None:
    asset_id, image_path = _make_asset(tmp_path)
    server, base_url = _start_api()
    try:
        url = f"{base_url}/api/assets/{asset_id}/file"
        status, headers, body = _fetch(url)
        assert status == 200
        assert body.startswith(b"\x89PNG")
        etag = headers.get("ETag") or headers.get("etag")
        cache_control = str(headers.get("Cache-Control") or headers.get("cache-control") or "")
        assert etag, "必须带 ETag 才能重新校验"
        # no-cache = 可缓存但每次使用前校验；max-age=300 会让新图 5 分钟内看不到
        assert "no-cache" in cache_control
        assert "max-age=300" not in cache_control

        # 未改动 → 304（便宜）
        status, _, _ = _fetch(url, {"If-None-Match": etag})
        assert status == 304

        # 文件换了字节 → 立刻返回新内容（现场就是这一条：导入后不刷新页面看不到新图）
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"second-version" * 10)
        status, headers, body = _fetch(url, {"If-None-Match": etag})
        assert status == 200, "文件变了必须给新内容，而不是 304"
        assert b"second-version" in body
        assert (headers.get("ETag") or headers.get("etag")) != etag
    finally:
        server.shutdown()
        server.server_close()


def test_frontend_asset_urls_carry_a_version_token() -> None:
    """前端图片地址要带版本参数（file_hash），列表重画就能拿到新图。"""

    script = (Path(__file__).resolve().parents[1] / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert "function assetImageUrl(asset, thumbEdge = 0)" in script
    assert "asset?.file_hash" in script
    assert "params.push(`v=${encodeURIComponent(version)}`)" in script
    # 直接拼 /api/assets/<id>/file 的地方只允许 helper 内部那一处（其余一律走 assetImageUrl）
    assert script.count("`/api/assets/${encodeURIComponent(") == 1
    helper = script[script.index("function assetImageUrl(asset, thumbEdge = 0)"):]
    helper = helper[: helper.index("\n}\n")]
    assert "`/api/assets/${encodeURIComponent(" in helper


def test_local_import_refreshes_assets_so_images_appear_immediately() -> None:
    """导入完成一项就要连着素材一起刷新：只刷商品列表会渲染成没图，得等整队跑完。"""

    script = (Path(__file__).resolve().parents[1] / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    entry = script[script.index("async function runLocalImportQueueItem(task)"):]
    entry = entry[: entry.index("\n}\n")]

    assert "await Promise.all([loadBaseProducts(), loadAssets()])" in entry, "每项导入后要同时刷新商品与素材"
