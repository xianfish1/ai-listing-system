"""字体自托管护栏：首屏不该再等 fonts.googleapis.com（本机实测约 500ms 的渲染阻塞样式表）。"""

from __future__ import annotations

import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from urllib import error, request

from services.api import settings
from services.api.main import ApiHandler

WEB = Path(__file__).resolve().parents[1] / "apps" / "web"


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


def _fetch(url: str) -> tuple[int, dict[str, str], bytes]:
    try:
        with request.urlopen(request.Request(url), timeout=30) as response:
            return response.status, dict(response.headers.items()), response.read()
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def test_web_app_has_no_external_font_dependency() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")

    # 注释里可以提到来源，但不能再有真正的外链
    assert "https://fonts.googleapis.com" not in html, "首屏不该再请求 Google Fonts 的样式表"
    assert "https://fonts.gstatic.com" not in html
    assert "/fonts.css" in html
    assert 'rel="preload"' in html and "inter-400-latin.woff2" in html, "关键字体要预加载"


def test_local_fonts_css_points_at_local_files_that_exist() -> None:
    css_path = WEB / "fonts.css"
    assert css_path.is_file(), "自托管字体样式表要跟着前端一起发布"
    css = css_path.read_text(encoding="utf-8")

    assert "https://fonts.gstatic.com" not in css and "https://fonts.googleapis.com" not in css
    referenced = re.findall(r"url\(([^)]+)\)", css)
    assert referenced, "fonts.css 里应该有 @font-face 的本地文件引用"
    for item in referenced:
        assert item.startswith("/fonts/"), item
        assert item.endswith(".woff2"), item
        assert (WEB / item.lstrip("/")).is_file(), f"{item} 缺失"


def test_fonts_are_served_locally_with_the_right_content_type() -> None:
    server, base_url = _start_api()
    try:
        status, headers, body = _fetch(f"{base_url}/fonts.css")
        assert status == 200
        assert "text/css" in str(headers.get("Content-Type") or "")
        assert b"@font-face" in body

        status, headers, body = _fetch(f"{base_url}/fonts/inter-400-latin.woff2")
        assert status == 200
        assert "font/woff2" in str(headers.get("Content-Type") or "")
        assert body[:4] == b"wOF2", "发出来的得是真的 woff2"

        status, _, _ = _fetch(f"{base_url}/fonts/does-not-exist.woff2")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
