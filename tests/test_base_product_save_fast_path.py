"""基础商品保存快路径（load_main=False）与拆分集合写路径护栏。

2026-11-03 主库瘦身第二批（listing_skus / listing_prices / store_listing_copies /
assets / title_generation_history / base_products / title_skills 拆出主库）：

- update_base_product 走 ``locked_store(load_main=False)``：保存一个 0.9MB 的商品
  不应该解析+重写整份 55.7MB 主库。这里的守卫是把 ``_read_main_store_json`` 打成
  抛异常——快路径只要碰了主库就会当场炸。
- save_store 的护栏：部分加载（skip_split_keys）的 data 里出现被跳过的拆分集合
  必须报错，不能静默吞掉调用方的改动。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")
    storage._SPLIT_LAST_BYTES.clear()
    storage._MAIN_LAST_BYTES.clear()
    storage._FILE_PARSE_CACHE.clear()
    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    storage._LAST_GOOD_WRITTEN_AT = 0.0
    storage._SANITIZED_FILE_STATES.clear()
    yield


def _base(product_id: str = "bp-1", main_sku: str = "HN0001") -> dict:
    return {
        "id": product_id,
        "group_id": "GRP1",
        "main_sku": main_sku,
        "base_name": "测试商品",
        "product_name_cn": "测试商品",
        "listing_title": "old title",
        "description": "old desc",
        "product_images": ["a.png"],
        "main_image": "a.png",
        "recognized_colors": [{"color_code": "White", "color_cn": "白色"}],
        "status": "base_ready",
        "created_at": "2026-11-01T00:00:00+00:00",
        "updated_at": "2026-11-01T00:00:00+00:00",
    }


def _seed() -> None:
    storage.save_store(
        {
            "base_products": [_base()],
            "title_skills": [{"id": "rule-1", "name": "默认", "is_default": True}],
            "store_listing_copies": [{"id": "copy-1", "base_product_id": "bp-1"}],
            "listing_skus": [{"listing_copy_id": "copy-1", "sku": "S1"}],
            "listing_prices": [{"listing_copy_id": "copy-1", "price": 100}],
            "automation_jobs": [],
        }
    )
    storage.load_store()


def _main_bytes() -> bytes:
    return settings.STORE_FILE.read_bytes()


def test_update_base_product_does_not_touch_main_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed()
    before = _main_bytes()
    assert (settings.STORE_FILE.parent / "base_products.json").exists()

    calls: list[bool] = []

    def _must_not_read_main(*, use_cache: bool = False):
        calls.append(True)
        raise AssertionError("update_base_product 快路径不应该解析主库 store.json")

    # 只替换这一个函数，并在 finally 里手工还原：monkeypatch.undo() 会把 fixture
    # 对 settings 的路径隔离也一并撤掉，让后续断言读到真实 data/store.json。
    original_read_main = storage._read_main_store_json
    monkeypatch.setattr(storage, "_read_main_store_json", _must_not_read_main)
    try:
        updated = storage.update_base_product(
            "bp-1",
            {"listing_title": "new title", "description": "new desc"},
            mark_edited=True,
        )
    finally:
        storage._read_main_store_json = original_read_main
    assert not calls
    assert updated is not None
    assert updated["listing_title"] == "new title"
    # 主库一个字节没动；改动全部落在 base_products.json。
    assert _main_bytes() == before
    on_disk = json.loads((settings.STORE_FILE.parent / "base_products.json").read_text(encoding="utf-8"))
    assert on_disk[0]["listing_title"] == "new title"
    assert on_disk[0]["last_edited_at"]
    # 读路径（合并视图）也能看到这次保存。
    assert storage.load_store()["base_products"][0]["listing_title"] == "new title"


def test_update_base_product_title_skill_validated_from_split_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed()

    def _must_not_read_main(*, use_cache: bool = False):
        raise AssertionError("title_skills 校验也不应该读主库")

    original_read_main = storage._read_main_store_json
    monkeypatch.setattr(storage, "_read_main_store_json", _must_not_read_main)
    try:
        with pytest.raises(ValueError, match="指定的标题规则不存在"):
            storage.update_base_product("bp-1", {"title_skill_id": "no-such-rule"})
        updated = storage.update_base_product("bp-1", {"title_skill_id": "rule-1"})
    finally:
        storage._read_main_store_json = original_read_main
    assert updated is not None
    assert updated["title_skill_id"] == "rule-1"


def test_update_base_product_triggers_split_migration_for_legacy_layout() -> None:
    """旧布局（base_products 还在主库里、没有拆分文件）：首次保存先迁移再走快路径。"""

    legacy = {"base_products": [_base("legacy-1", "HN0999")], "title_skills": []}
    settings.STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings.STORE_FILE.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not (settings.STORE_FILE.parent / "base_products.json").exists()

    updated = storage.update_base_product("legacy-1", {"listing_title": "migrated"})
    assert updated is not None
    assert updated["listing_title"] == "migrated"
    # 迁移已发生：主库瘦身、base_products.json 落地且带上本次改动。
    main_data = json.loads(_main_bytes().decode("utf-8"))
    assert "base_products" not in main_data
    on_disk = json.loads((settings.STORE_FILE.parent / "base_products.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in on_disk] == ["legacy-1"]
    assert on_disk[0]["listing_title"] == "migrated"


def test_save_store_rejects_mutated_skipped_split_collection() -> None:
    """护栏：部分加载的 data 里动了被跳过的拆分集合 → 当场报错而不是静默丢数据。"""

    _seed()
    with pytest.raises(RuntimeError, match="base_products"):
        with storage.locked_store(split_keys=()) as (_data, save):
            # 模拟调用方「没加载却改了它」——这正是拆分后最容易犯的错。
            save({"base_products": [_base("bp-2")]})


def test_locked_store_load_main_false_requires_split_keys() -> None:
    _seed()
    with pytest.raises(ValueError, match="split_keys"):
        with storage.locked_store(load_main=False) as (_data, _save):
            pass
    with pytest.raises(ValueError, match="已拆分集合"):
        with storage.locked_store(split_keys=("store_templates",), load_main=False) as (_data, _save):
            pass
