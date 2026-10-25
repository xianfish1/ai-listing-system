from __future__ import annotations

import shutil
from pathlib import Path
import json
from urllib import request

import pytest

from services.api import settings, storage
from services.api.local_folder_uploads import LocalFolderUploadError, LocalFolderUploads
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")


def manifest(*items: tuple[str, int]) -> list[dict[str, object]]:
    return [{"path": path, "size_bytes": size} for path, size in items]


def test_create_rejects_paths_outside_selected_folder() -> None:
    uploads = LocalFolderUploads(settings.STORAGE_DIR)

    with pytest.raises(LocalFolderUploadError, match="相对路径"):
        uploads.create("AL0001 商品", manifest(("../main.jpg", 3)))


def test_create_rejects_duplicate_and_unsupported_files() -> None:
    uploads = LocalFolderUploads(settings.STORAGE_DIR)

    with pytest.raises(LocalFolderUploadError, match="重复"):
        uploads.create("AL0001 商品", manifest(("main.jpg", 3), ("main.jpg", 4)))
    with pytest.raises(LocalFolderUploadError, match="不支持"):
        uploads.create("AL0001 商品", manifest(("notes.txt", 3)))
    with pytest.raises(LocalFolderUploadError, match="不支持"):
        uploads.create("AL0001 商品", manifest(("A白骨.psd", 3)))


def test_complete_requires_every_declared_file() -> None:
    uploads = LocalFolderUploads(settings.STORAGE_DIR)
    session = uploads.create("AL0001 商品", manifest(("main.jpg", 3), ("variants/black.jpg", 3)))
    uploads.save_file(session["upload_id"], "main.jpg", b"jpg")

    with pytest.raises(LocalFolderUploadError, match="尚未上传"):
        uploads.complete(session["upload_id"], storage.create_base_product_from_folder)


def test_complete_creates_base_product_from_persistent_uploaded_folder() -> None:
    uploads = LocalFolderUploads(settings.STORAGE_DIR)
    session = uploads.create("AL0001 商品", manifest(("main.jpg", 3), ("variants/black.jpg", 3)))
    uploads.save_file(session["upload_id"], "main.jpg", b"jpg")
    uploads.save_file(session["upload_id"], "variants/black.jpg", b"jpg")

    product = uploads.complete(session["upload_id"], storage.create_base_product_from_folder)

    root = settings.STORAGE_DIR / "uploads" / "base-products" / session["upload_id"] / "AL0001 商品"
    assert product["main_sku"] == "AL0001"
    assert product["asset_root"] == str(root)
    assert (root / "main.jpg").read_bytes() == b"jpg"
    assert (root / "variants" / "black.jpg").read_bytes() == b"jpg"


def test_complete_returns_product_when_session_cleanup_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    uploads = LocalFolderUploads(settings.STORAGE_DIR)
    session = uploads.create("AL0001 商品", manifest(("main.jpg", 3)))
    uploads.save_file(session["upload_id"], "main.jpg", b"jpg")

    def fail_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == "session.json":
            raise OSError("safe-delete unavailable")
        Path.unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    product = uploads.complete(session["upload_id"], storage.create_base_product_from_folder)

    assert product["main_sku"] == "AL0001"


def test_cancel_is_idempotent_when_session_cleanup_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    uploads = LocalFolderUploads(settings.STORAGE_DIR)
    session = uploads.create("AL0001 商品", manifest(("main.jpg", 3)))

    def fail_rmtree(*args: object, **kwargs: object) -> None:
        raise OSError("safe-delete unavailable")

    monkeypatch.setattr(shutil, "rmtree", fail_rmtree)
    uploads.cancel(session["upload_id"])


def test_upload_rejects_size_mismatch_and_cancel_removes_session() -> None:
    uploads = LocalFolderUploads(settings.STORAGE_DIR)
    session = uploads.create("AL0001 商品", manifest(("main.jpg", 3)))

    with pytest.raises(LocalFolderUploadError, match="大小"):
        uploads.save_file(session["upload_id"], "main.jpg", b"too long")

    uploads.cancel(session["upload_id"])
    assert not (settings.STORAGE_DIR / "uploads" / "base-products" / session["upload_id"]).exists()


def test_http_upload_flow_creates_product_and_cancel_route_cleans_session(tmp_path: Path) -> None:
    server, base_url = _start_api()
    try:
        create = request.Request(
            f"{base_url}/api/local-folder-uploads",
            method="POST",
            data=json.dumps({
                "folder_name": "AL0002 世界",
                "files": [
                    {"path": "main.jpg", "size_bytes": 3},
                    {"path": "variants/black.jpg", "size_bytes": 3},
                ],
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(create, timeout=10) as response:
            session = json.loads(response.read())
        upload_id = session["upload_id"]

        for path, content in (("main.jpg", b"jpg"), ("variants/black.jpg", b"jpg")):
            upload = request.Request(
                f"{base_url}/api/local-folder-uploads/{upload_id}/files?path={path}",
                method="PUT",
                data=content,
                headers={"Content-Type": "application/octet-stream"},
            )
            with request.urlopen(upload, timeout=10) as response:
                assert json.loads(response.read())["ok"] is True

        complete = request.Request(
            f"{base_url}/api/local-folder-uploads/{upload_id}/complete",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(complete, timeout=10) as response:
            product = json.loads(response.read())
        assert product["main_sku"] == "AL0002"
        assert Path(product["asset_root"]).is_dir()

        create_cancel = request.Request(
            f"{base_url}/api/local-folder-uploads",
            method="POST",
            data=json.dumps({"folder_name": "AL0003", "files": [{"path": "main.jpg", "size_bytes": 3}]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(create_cancel, timeout=10) as response:
            cancel_session = json.loads(response.read())
        cancel = request.Request(
            f"{base_url}/api/local-folder-uploads/{cancel_session['upload_id']}",
            method="DELETE",
        )
        with request.urlopen(cancel, timeout=10) as response:
            assert json.loads(response.read())["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
