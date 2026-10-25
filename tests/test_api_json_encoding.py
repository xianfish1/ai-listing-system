"""JSON 响应：大响应紧凑 + gzip（首屏一次 7.4MB → 几百 KB），小响应保持可读缩进。"""

from __future__ import annotations

import gzip
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


def _fetch(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    try:
        with request.urlopen(request.Request(url, headers=headers or {})) as response:
            return response.status, dict(response.headers.items()), response.read()
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def _seed_many_assets(count: int = 400) -> None:
    storage.save_store(
        {
            "assets": [
                {
                    "id": f"asset-{index}",
                    "asset_type": "main",
                    "group_id": f"GN{index:04d}",
                    "file_name": f"main-{index}.jpg",
                    "file_path": "C:/warehouse/very/long/asset/root/folder/" + f"main-{index}.jpg",
                    "file_hash": "a" * 40,
                    "status": "ready",
                }
                for index in range(count)
            ]
        }
    )


def test_large_json_is_gzipped_when_the_client_accepts_gzip() -> None:
    _seed_many_assets()
    server, base_url = _start_api()
    try:
        status, headers, body = _fetch(f"{base_url}/api/assets", {"Accept-Encoding": "gzip"})
        assert status == 200
        encoding = str(headers.get("Content-Encoding") or headers.get("content-encoding") or "")
        assert "gzip" in encoding
        assert str(headers.get("Vary") or headers.get("vary") or "").lower().find("accept-encoding") >= 0
        payload = json.loads(gzip.decompress(body))
        assert len(payload["items"]) == 400
    finally:
        server.shutdown()
        server.server_close()


def test_large_json_stays_plain_without_accept_encoding() -> None:
    """urllib / 自动化 Worker 不发 Accept-Encoding：必须还是能直接读到明文 JSON。"""

    _seed_many_assets()
    server, base_url = _start_api()
    try:
        status, headers, body = _fetch(f"{base_url}/api/assets")
        assert status == 200
        assert not (headers.get("Content-Encoding") or headers.get("content-encoding"))
        payload = json.loads(body)
        assert len(payload["items"]) == 400
        assert b"\n  " not in body, "大响应应当是紧凑格式"
    finally:
        server.shutdown()
        server.server_close()


def test_small_json_keeps_readable_indentation() -> None:
    server, base_url = _start_api()
    try:
        status, headers, body = _fetch(f"{base_url}/api/health", {"Accept-Encoding": "gzip"})
        assert status == 200
        assert not (headers.get("Content-Encoding") or headers.get("content-encoding"))
        assert b"\n  " in body
        assert json.loads(body)["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
