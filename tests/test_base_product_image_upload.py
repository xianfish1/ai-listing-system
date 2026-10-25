"""商品维护「可选产品图」自上传：落盘、登记素材、范围过滤和 API 契约。"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import settings, storage
from services.api.size_chart_uploads import IMAGE_KINDS
from tests.test_api_boundaries import _start_api

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63f80fff00000101010045fdf6d40000000049454e"
    "44ae426082"
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _call(method: str, url: str, payload: dict | None = None):
    raw = None if payload is None else json.dumps(payload).encode()
    req = request.Request(url, data=raw, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _upload_payload(file_name: str = "IMG_1234.png", content: bytes = TINY_PNG, content_type: str = "image/png") -> dict:
    return {
        "file_name": file_name,
        "content_type": content_type,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _base_product(tmp_path: Path, folder_name: str = "AL0472"):
    folder = tmp_path / folder_name
    folder.mkdir()
    (folder / "1 (1).png").write_bytes(TINY_PNG)
    return storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})


def test_uploaded_product_image_is_registered_and_added_to_the_gallery(tmp_path: Path) -> None:
    base = _base_product(tmp_path)
    before_main = base["main_image"]

    result = storage.add_base_product_product_image(base["id"], _upload_payload())

    asset = result["asset"]
    product = result["product"]
    assert asset["origin"] == "manual_upload"
    assert asset["file_name"] == "IMG_1234.png"
    assert asset["group_id"] == "AL0472"
    assert asset["asset_type"] == "main"
    assert Path(asset["file_path"]).is_file()
    assert product["product_images"][-1] == "IMG_1234.png"
    # 新图追加在末尾，主图仍是导入时选的那张。
    assert product["main_image"] == before_main


def test_uploaded_product_image_survives_the_import_scope_filter(tmp_path: Path) -> None:
    """上传文件在素材目录之外，靠 origin=manual_upload 留在本商品的素材范围里。"""
    base = _base_product(tmp_path)
    storage.add_base_product_product_image(base["id"], _upload_payload())

    names = [asset["file_name"] for asset in storage.assets_for_base_product(base)]
    assert "IMG_1234.png" in names
    # 上传目录不在 asset_root 下，光靠目录过滤会被排除。
    assert not str(Path(base["asset_root"])).startswith(str(settings.STORAGE_DIR))


def test_duplicate_upload_names_are_suffixed(tmp_path: Path) -> None:
    base = _base_product(tmp_path)
    first = storage.add_base_product_product_image(base["id"], _upload_payload())
    second = storage.add_base_product_product_image(base["id"], _upload_payload())

    assert first["asset"]["file_name"] == "IMG_1234.png"
    assert second["asset"]["file_name"] == "IMG_1234-2.png"


def test_upload_rejects_unsupported_type_and_mismatched_content(tmp_path: Path) -> None:
    from services.api.size_chart_uploads import ImageUploadError

    base = _base_product(tmp_path)
    with pytest.raises(ImageUploadError) as unsupported:
        storage.add_base_product_product_image(base["id"], _upload_payload("x.gif", b"GIF89a", "image/gif"))
    assert unsupported.value.code == "unsupported_image_type"

    with pytest.raises(ImageUploadError) as mismatch:
        storage.add_base_product_product_image(base["id"], _upload_payload("x.png", b"\xff\xd8\xff\xe0notapng", "image/png"))
    assert mismatch.value.code == "invalid_image_signature"


def test_upload_rejects_oversized_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.size_chart_uploads import ImageUploadError

    monkeypatch.setitem(IMAGE_KINDS["product_image"], "max_bytes", len(TINY_PNG) - 1)
    base = _base_product(tmp_path)
    with pytest.raises(ImageUploadError) as excinfo:
        storage.add_base_product_product_image(base["id"], _upload_payload())
    assert excinfo.value.code == "image_size_invalid"


def test_multi_megabyte_upload_is_accepted(tmp_path: Path) -> None:
    """真实商品图 2-5MB（base64 后更大）；默认 1MiB 的 JSON 体上限会让它们全部失败
    （浏览器只报 Failed to fetch），图片上传接口必须单独放宽。"""
    base = _base_product(tmp_path)
    big = TINY_PNG + b"\x00" * (1_500_000 - len(TINY_PNG))
    assert len(big) > 1_000_000

    server, base_url = _start_api()
    try:
        status, payload = _call(
            "POST",
            f"{base_url}/api/base-products/{base['id']}/product-images",
            _upload_payload("AL0474-source.png", big),
        )
        assert status == 201
        assert payload["asset"]["file_name"] == "AL0474-source.png"
        assert payload["asset"]["size_bytes"] == len(big)
    finally:
        server.shutdown()


def test_size_chart_upload_also_accepts_multi_megabyte_images(tmp_path: Path) -> None:
    """尺码图上传走同一个 JSON 体限制，之前超过约 1MB 的尺码图也会失败。"""
    big = TINY_PNG + b"\x00" * (1_200_000 - len(TINY_PNG))
    server, base_url = _start_api()
    try:
        status, payload = _call("POST", f"{base_url}/api/uploads/size-chart", _upload_payload("chart.png", big))
        assert status == 201
        assert payload["id"]
    finally:
        server.shutdown()


def test_oversized_request_reports_a_readable_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超限时客户端要收到 413 正文，而不是网络层错误。"""
    from services.api import main as api_main

    monkeypatch.setattr(api_main, "MAX_IMAGE_UPLOAD_BODY_BYTES", 2048)
    base = _base_product(tmp_path)
    server, base_url = _start_api()
    try:
        oversized = TINY_PNG + b"\x00" * 4096
        status, payload = _call(
            "POST",
            f"{base_url}/api/base-products/{base['id']}/product-images",
            _upload_payload("too-big.png", oversized),
        )
        assert status == 413
        assert "too large" in payload["error"].lower()
    finally:
        server.shutdown()


def test_upload_endpoint_returns_asset_and_updated_product(tmp_path: Path) -> None:
    base = _base_product(tmp_path)
    server, base_url = _start_api()
    try:
        status, payload = _call("POST", f"{base_url}/api/base-products/{base['id']}/product-images", _upload_payload())
        assert status == 201
        assert payload["asset"]["file_name"] == "IMG_1234.png"
        assert payload["asset"]["id"]
        assert payload["product"]["id"] == base["id"]
        assert "IMG_1234.png" in payload["product"]["product_images"]

        missing_status, _ = _call("POST", f"{base_url}/api/base-products/nope/product-images", _upload_payload())
        assert missing_status == 404

        bad_status, bad_payload = _call("POST", f"{base_url}/api/base-products/{base['id']}/product-images", _upload_payload("x.gif", b"GIF89a", "image/gif"))
        assert bad_status == 422
        assert bad_payload["code"] == "unsupported_image_type"
    finally:
        server.shutdown()
