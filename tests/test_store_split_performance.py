"""生成自动化任务/任务列表的性能护栏。

现场「用起来很卡」的两个来源：
1. `/api/automation/jobs` 把每个任务的 `execution_snapshot` 也吐出来 —— 81 个任务 27.8MB，
   浏览器解析完就卡住（快照本身占任务的 17.7MB/18.5MB）。
2. 只改任务（心跳/进度/建任务）也要重写整份 59MB 的 store.json —— 任务集合现在拆成
   独立文件，主库没变就跳过重写。
"""

from __future__ import annotations

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
    yield
    storage._SPLIT_LAST_BYTES.clear()
    storage._MAIN_LAST_BYTES.clear()


def _job(job_id: str = "job-1", *, snapshot_kb: int = 8) -> dict:
    return {
        "id": job_id,
        "listing_copy_id": f"copy-{job_id}",
        "status": "ready",
        "store_name": "HN_POD",
        "execution_snapshot": {"listing_copy": {"id": f"copy-{job_id}"}, "payload": "x" * (snapshot_kb * 1024)},
        "execution_snapshot_hash": "sha256:abc",
        "run_log": [{"event": "claimed", "at": "2026-10-08T00:00:00+00:00"}],
        "created_at": "2026-10-08T00:00:00+00:00",
        "updated_at": "2026-10-08T00:00:00+00:00",
    }


def test_automation_jobs_live_in_their_own_file_now() -> None:
    """任务集合必须拆出主库：否则每次心跳/进度都要重写整份主库。"""

    assert "automation_jobs" in storage._SPLIT_STORE_KEYS
    assert "marketplace_categories" in storage._SPLIT_STORE_KEYS

    storage.save_store({"automation_jobs": [_job()], "store_templates": [{"id": "t1"}]})

    main_raw = settings.STORE_FILE.read_text(encoding="utf-8")
    assert "execution_snapshot" not in main_raw, "主库不该再带任务快照"
    jobs_path = settings.STORE_FILE.parent / "automation_jobs.json"
    assert jobs_path.is_file()
    # 读一次库：内联快照会被搬到独立集合，任务文件从此只有约 300KB。
    assert [job["id"] for job in storage.load_store()["automation_jobs"]] == ["job-1"]
    assert storage.load_store()["store_templates"] == [{"id": "t1"}]
    jobs_raw = jobs_path.read_text(encoding="utf-8")
    assert '"execution_snapshot":' not in jobs_raw, "任务文件不该再背快照内容"
    assert '"execution_snapshot_hash":' in jobs_raw, "哈希要留在任务上（执行前校验用）"
    snapshots_path = settings.STORE_FILE.parent / "automation_job_snapshots.json"
    assert snapshots_path.is_file(), "快照要落到独立文件"
    assert "job-1" in snapshots_path.read_text(encoding="utf-8")


def test_job_only_change_does_not_rewrite_the_main_store() -> None:
    """只改任务时主库一个字节都不该重写（心跳/进度/建任务都在这条路上）。"""

    storage.save_store(
        {
            "automation_jobs": [_job("job-1")],
            "store_templates": [{"id": "t1"}],
            "listing_skus": [{"id": "sku-1"}],
        }
    )
    # 第一次读盘会把缺的集合补成空集合（_empty_store 的形状）——先让它稳定下来，
    # 之后再断言「只动任务 → 主库不重写」。
    storage.save_store(storage.load_store())
    storage._MAIN_LAST_BYTES.clear()  # 新进程/新一轮：下一次写盘照常
    storage.save_store(storage.load_store())

    main_path = settings.STORE_FILE
    before_bytes = main_path.read_bytes()
    before_mtime = main_path.stat().st_mtime_ns

    # 模拟一次任务心跳：只动 automation_jobs
    with storage.locked_store() as (data, save):
        data["automation_jobs"][0]["current_phase"] = "playwright_save_draft"
        data["automation_jobs"][0]["updated_at"] = "2026-10-08T00:05:00+00:00"
        save(data)

    assert main_path.read_bytes() == before_bytes
    assert main_path.stat().st_mtime_ns == before_mtime, "主库不该被重新写一遍"
    jobs_path = settings.STORE_FILE.parent / "automation_jobs.json"
    assert "playwright_save_draft" in jobs_path.read_text(encoding="utf-8")


def test_main_store_is_rewritten_when_main_data_changes() -> None:
    """反过来：真改了主库里的东西（模板）就必须落盘。"""

    storage.save_store({"automation_jobs": [_job()], "store_templates": [{"id": "t1"}]})
    assert "t1" in settings.STORE_FILE.read_text(encoding="utf-8")

    storage.save_store({"automation_jobs": [_job()], "store_templates": [{"id": "t2"}]})

    main_raw = settings.STORE_FILE.read_text(encoding="utf-8")
    assert "t2" in main_raw and "t1" not in main_raw


def test_jobs_list_hides_the_execution_snapshot_by_default() -> None:
    """列表默认不带快照：它就是 27.8MB 的来源，而界面上一个字段都用不到。"""

    storage.save_store({"automation_jobs": [_job("job-1"), _job("job-2")]})

    rows = storage.list_automation_jobs()

    assert [row["id"] for row in rows] == ["job-1", "job-2"]
    assert all("execution_snapshot" not in row for row in rows)
    # 列表要用的字段都得留着（前端读 店铺/状态/日志）
    assert rows[0]["store_name"] == "HN_POD"
    assert rows[0]["status"] == "ready"
    assert rows[0]["run_log"]

    heavy = storage.list_automation_jobs(include_snapshot=True)
    assert all("execution_snapshot" in row for row in heavy)


def test_list_projection_never_mutates_the_stored_job() -> None:
    """列表返回的是浅拷贝，剥掉快照绝不能把库里的任务改瘦。"""

    storage.save_store({"automation_jobs": [_job("job-1")]})

    storage.list_automation_jobs()

    stored = storage.get_item("automation_jobs", "job-1")
    assert stored and "execution_snapshot" not in stored, "任务本身不该再带快照"
    snapshots = storage.load_store().get("automation_job_snapshots") or {}
    assert snapshots.get("job-1"), "快照要完好地留在独立集合里"
