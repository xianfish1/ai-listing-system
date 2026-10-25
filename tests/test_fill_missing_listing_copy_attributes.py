"""从模板补全副本缺失属性：只补缺、不覆盖、只碰副本快照里存在的字段。"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage
from tests.test_base_product_lifecycle import _make_base, _make_template, _prepare_copy_for_execution

FIELD_MODEL = "101639"
FIELD_CONDITION = "100413"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _copy_with_template_values(tmp_path: Path) -> dict:
    base = _make_base(tmp_path, folder_name="AL0777 Fill Missing")
    template = _make_template("Fill")
    template["category_attributes"] = {
        FIELD_MODEL: ["1790002081766"],
        FIELD_CONDITION: "new",
        "100999": "not-in-snapshot",  # 副本生效类目没有这个字段，不允许带过来
    }
    template["attribute_values"] = dict(template["category_attributes"])
    with storage.locked_store() as (data, save):
        target = next(item for item in data["store_templates"] if item.get("id") == template["id"])
        target["category_attributes"] = dict(template["category_attributes"])
        target["attribute_values"] = dict(template["category_attributes"])
        save(data)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    _prepare_copy_for_execution(copy["id"], base)
    # 复现真实现场：副本快照里有这两个字段（101639=型号 / 100413=条件），
    # 但副本自己的 attribute_values 里没有值（早于模板配置创建 → 模板同步按设计不改已有副本）。
    with storage.locked_store() as (data, save):
        target = next(item for item in data["store_listing_copies"] if item.get("id") == copy["id"])
        snapshot = dict(target.get("category_profile_snapshot") or {})
        snapshot["fields"] = [
            {"field_id": FIELD_MODEL, "label": "型号", "type": "text", "required": False},
            {"field_id": FIELD_CONDITION, "label": "条件", "type": "text", "required": False},
        ]
        target["category_profile_snapshot"] = snapshot
        values = dict(target.get("attribute_values") or {})
        values.pop(FIELD_MODEL, None)
        values.pop(FIELD_CONDITION, None)
        values.pop("100999", None)  # 快照外字段：补全时也不许带回来
        target["attribute_values"] = values
        target["category_attributes"] = dict(values)
        save(data)
    return storage.get_item("store_listing_copies", copy["id"])


def test_fill_only_adds_missing_values_from_the_template(tmp_path: Path) -> None:
    copy = _copy_with_template_values(tmp_path)

    result = storage.fill_listing_copy_missing_attributes([copy["id"]])

    assert result["filled_count"] == 1
    filled = result["filled"][0]
    assert FIELD_MODEL in filled["filled_fields"]
    updated = storage.get_item("store_listing_copies", copy["id"])
    assert updated["attribute_values"][FIELD_MODEL] == ["1790002081766"]
    # 生效类目没有的字段绝不带过来（拿错类目的属性去填会上不了页面）
    assert "100999" not in updated["attribute_values"]


def test_fill_never_overwrites_existing_values(tmp_path: Path) -> None:
    copy = _copy_with_template_values(tmp_path)
    with storage.locked_store() as (data, save):
        target = next(item for item in data["store_listing_copies"] if item.get("id") == copy["id"])
        values = dict(target.get("attribute_values") or {})
        values[FIELD_MODEL] = ["t-shirt"]
        target["attribute_values"] = values
        target["category_attributes"] = dict(values)
        save(data)

    result = storage.fill_listing_copy_missing_attributes([copy["id"]])

    updated = storage.get_item("store_listing_copies", copy["id"])
    assert updated["attribute_values"][FIELD_MODEL] == ["t-shirt"], "已有值不能被模板覆盖"
    assert FIELD_MODEL not in (result["filled"][0]["filled_fields"] if result["filled"] else [])


def test_fill_scans_every_copy_when_no_ids_given(tmp_path: Path) -> None:
    first = _copy_with_template_values(tmp_path)

    result = storage.fill_listing_copy_missing_attributes([])

    assert result["filled_count"] >= 1
    assert any(item["id"] == first["id"] for item in result["filled"])
    # 再跑一次应该没有可补的（幂等）
    again = storage.fill_listing_copy_missing_attributes([])
    assert again["filled_count"] == 0


def test_job_warns_about_template_values_the_copy_never_got(tmp_path: Path) -> None:
    copy = _copy_with_template_values(tmp_path)

    job = storage.create_automation_job_from_listing_copy(copy["id"])

    assert f"template_attribute_not_copied.{FIELD_MODEL}" in (job.get("warnings") or [])
    assert job["status"] in {"ready", "needs_manual"}, "只是提醒，不阻塞生成任务"
