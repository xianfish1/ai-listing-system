from __future__ import annotations

from email.message import Message
from pathlib import Path

import pytest

from services.api import mabang_size_chart_import


class _ImageResponse:
    def __init__(self, content: bytes) -> None:
        self.headers = Message()
        self.headers["Content-Type"] = "image/png"
        self._content = content

    def read(self, _limit: int = -1) -> bytes:
        return self._content

    def __enter__(self) -> "_ImageResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_import_accepts_a_public_https_image_from_a_non_mabang_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"chart"
    monkeypatch.setattr(
        mabang_size_chart_import,
        "urlopen",
        lambda request, timeout=0: _ImageResponse(png),
    )

    imported = mabang_size_chart_import.import_mabang_size_chart(
        "https://ae01.alicdn.com/kf/size-chart.png", tmp_path
    )

    assert imported["content_type"] == "image/png"
    assert Path(imported["file_path"]).is_file()


def test_import_rejects_non_https_or_private_size_chart_addresses(tmp_path: Path) -> None:
    for url in ("http://cdn.example.com/chart.png", "https://127.0.0.1/chart.png"):
        with pytest.raises(ValueError, match="来源无效"):
            mabang_size_chart_import.import_mabang_size_chart(url, tmp_path)
