"""导入目录里的 标题.txt / title.txt：直接当商品标题，AI 标题阶段不再覆盖。"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.api import main, settings, storage
from services.api.local_folder_uploads import LocalFolderUploads
from services.api.product_title_file import is_title_file_name, read_title_file


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _folder(tmp_path: Path, name: str = "AL0100测试款") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    for file_name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / file_name).write_bytes(b"image")
    return folder


def test_title_file_name_matches_only_the_two_agreed_names() -> None:
    assert is_title_file_name("标题.txt")
    assert is_title_file_name("Title.TXT")
    assert is_title_file_name("sub/标题.txt")
    assert not is_title_file_name("notes.txt")
    assert not is_title_file_name("标题.docx")


def test_import_uses_utf8_title_file_and_marks_its_source(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    (folder / "标题.txt").write_text(
        "Custom Women's T-Shirt Soft Cotton Tee\n第二行不该进标题\n", encoding="utf-8"
    )

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})

    assert product["listing_title"] == "Custom Women's T-Shirt Soft Cotton Tee"
    assert product["title_source"] == "title_file"


def test_import_reads_gbk_title_file_from_windows_notepad(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    (folder / "标题.txt").write_bytes("男士纯棉短袖T恤 夏季新款".encode("gb18030"))

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})

    assert product["listing_title"] == "男士纯棉短袖T恤 夏季新款"
    assert product["title_source"] == "title_file"


def test_title_txt_alias_and_other_txt_files(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    (folder / "title.txt").write_text("Title From Alias\n", encoding="utf-8")
    (folder / "notes.txt").write_text("不要当标题\n", encoding="utf-8")

    assert read_title_file(folder) == "Title From Alias"
    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    assert product["listing_title"] == "Title From Alias"


def test_without_title_file_the_title_stays_empty_for_ai(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    (folder / "notes.txt").write_text("备注\n", encoding="utf-8")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})

    assert product["listing_title"] == ""
    assert product["title_source"] == ""


def test_ai_title_stage_does_not_overwrite_a_title_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage.save_runtime_configuration({"ai_listing_generation": {"generate_title_on_import": True}})
    folder = _folder(tmp_path)
    (folder / "标题.txt").write_text("Operator Title\n", encoding="utf-8")
    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})

    def fail_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("有 标题.txt 时不应该再调 AI 写标题")

    monkeypatch.setattr(
        "services.api.vision_listing.generate_listing_from_images", fail_call, raising=False
    )

    result = main._enrich_with_vision_listing(product, {})

    assert result["listing_title"] == "Operator Title"
    assert result["title_source"] == "title_file"
    assert result["ai_stages"]["title"] == "skipped"


def test_editing_the_title_removes_the_title_file_lock(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    (folder / "标题.txt").write_text("Operator Title\n", encoding="utf-8")
    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})

    unchanged = storage.update_base_product(product["id"], {"listing_title": "Operator Title"})
    assert unchanged["title_source"] == "title_file"

    changed = storage.update_base_product(product["id"], {"listing_title": "改过的标题"})
    assert changed["title_source"] == ""

    # 存量回填可以直接带上 title_source（显式写入以自己的值为准，不被自动清掉）。
    restored = storage.update_base_product(
        product["id"], {"listing_title": "再来一次", "title_source": "title_file"}
    )
    assert restored["listing_title"] == "再来一次"
    assert restored["title_source"] == "title_file"


def test_local_upload_accepts_the_title_file_and_import_uses_it(tmp_path: Path) -> None:
    uploads = LocalFolderUploads(settings.STORAGE_DIR)
    title_bytes = "Uploaded Title".encode("utf-8")
    manifest = [
        {"path": "main.jpg", "size_bytes": 3},
        {"path": "标题.txt", "size_bytes": len(title_bytes)},
    ]
    session = uploads.create("AL0200商品", manifest)
    uploads.save_file(session["upload_id"], "main.jpg", b"jpg")
    uploads.save_file(session["upload_id"], "标题.txt", title_bytes)

    product = uploads.complete(session["upload_id"], storage.create_base_product_from_folder)

    assert product["listing_title"] == "Uploaded Title"
    assert product["title_source"] == "title_file"
