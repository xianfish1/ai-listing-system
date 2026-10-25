"""批量生成自动化任务：整库单次读写 + 单个副本失败不影响同批其他副本。"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage
from tests.test_base_product_lifecycle import _make_base, _make_template, _prepare_copy_for_execution


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _copies(tmp_path: Path, count: int) -> list[dict]:
    template = _make_template("Batch")
    copies = []
    for index in range(count):
        base = _make_base(tmp_path, folder_name=f"AL090{index} Batch {index}")
        copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
        _prepare_copy_for_execution(copy["id"], base)
        copies.append(copy)
    return copies


def test_batch_creation_reads_the_store_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """现场反馈「生成会有卡顿」：原来是每个副本 6 次整库加载 + 1 次保存（34MB 库 ~4s/副本）。"""

    copies = _copies(tmp_path, 3)
    calls = {"load": 0, "save": 0, "split": 0}
    original_load = storage.load_store
    original_save = storage.save_store
    original_split = storage._write_split_key

    def counting_load():
        calls["load"] += 1
        return original_load()

    def counting_save(data, **kwargs):
        # 部分保存会带 skip_split_keys（只动主库 + 任务集合）；测试替身要原样转发。
        calls["save"] += 1
        return original_save(data, **kwargs)

    def counting_split(key, value):
        calls["split"] += 1
        return original_split(key, value)

    monkeypatch.setattr(storage, "load_store", counting_load)
    monkeypatch.setattr(storage, "save_store", counting_save)
    monkeypatch.setattr(storage, "_write_split_key", counting_split)

    result = storage.create_automation_jobs_from_listing_copies([copy["id"] for copy in copies])

    assert result["created_count"] == 3
    assert result["error_count"] == 0
    # 单轮写入：3 个副本一起建也只写「任务 + 快照」两个文件（旧实现约 18 次加载 + 3 次保存）。
    assert calls["load"] <= 3, f"整库加载次数过多: {calls['load']}"
    assert calls["split"] == 2, f"应当只写任务与快照文件，实际写了 {calls['split']} 个"
    assert calls["save"] == 0, f"建任务不该再走整库保存，实际 {calls['save']} 次"


def test_batch_creation_reports_per_copy_errors_without_blocking_others(tmp_path: Path) -> None:
    copies = _copies(tmp_path, 2)
    first, second = copies[0], copies[1]
    storage.create_automation_job_from_listing_copy(first["id"])

    result = storage.create_automation_jobs_from_listing_copies([first["id"], second["id"]])

    assert result["created_count"] == 1, "已有活动任务的副本不应影响同批其他副本"
    assert result["error_count"] == 1
    error = result["errors"][0]
    assert error["listing_copy_id"] == first["id"]
    assert error["reason"] == "active_job_exists"
    assert result["created"][0]["listing_copy_id"] == second["id"]


def test_single_creation_still_raises_for_an_active_job(tmp_path: Path) -> None:
    copy = _copies(tmp_path, 1)[0]
    storage.create_automation_job_from_listing_copy(copy["id"])

    with pytest.raises(storage.ActiveAutomationJobExists):
        storage.create_automation_job_from_listing_copy(copy["id"])


def test_job_carries_the_same_payload_as_before(tmp_path: Path) -> None:
    """单次读写重构不能改变任务内容：SKU/价格/执行快照/就绪结论都要照旧。"""

    copy = _copies(tmp_path, 1)[0]
    job = storage.create_automation_job_from_listing_copy(copy["id"])

    assert job["status"] in {"ready", "needs_manual"}
    snapshot = job["execution_snapshot"]
    assert snapshot["listing_copy"]["id"] == copy["id"]
    assert snapshot["browser_inputs"], "执行快照要带 browser_inputs"
    assert len(snapshot["skus"]) == len(snapshot["browser_inputs"].get("skus") or snapshot["skus"])
    assert job["execution_snapshot_hash"]
    stored_copy = storage.get_item("store_listing_copies", copy["id"])
    assert job["listing_copy_revision"] == int(stored_copy.get("revision") or 1)
