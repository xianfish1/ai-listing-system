"""存储读缓存的性能护栏。

现场「用起来很卡」的两个来源（2026-10-08 第二轮）：

1. 空轮询也要进写锁、整库解析。Worker 每 3 秒问一次「有任务吗」，即使一个可领的
   任务都没有，claim_next_automation_job 也会 locked_store() 把主库(13MB)+类目
   (13MB)+任务(18MB) 全部 json.loads 一遍（约 300~430ms，期间占着 GIL）。
2. 写完之后第一个读请求要整库重解析。save_store 原来会把整个读缓存清空，于是
   一次任务心跳之后，主库/类目这些根本没变的文件也要重新解析。
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
    _clear_caches()
    yield
    _clear_caches()


def _clear_caches() -> None:
    storage._SPLIT_LAST_BYTES.clear()
    storage._MAIN_LAST_BYTES.clear()
    storage._FILE_PARSE_CACHE.clear()
    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None


def _job(job_id: str = "job-1", *, status: str = "ready", attempt_count: int = 0) -> dict:
    return {
        "id": job_id,
        "listing_copy_id": f"copy-{job_id}",
        "status": status,
        "attempt_count": attempt_count,
        "store_name": "HN_POD",
        "run_log": [],
        "created_at": "2026-10-08T00:00:00+00:00",
        "updated_at": "2026-10-08T00:00:00+00:00",
    }


def test_idle_claim_does_not_enter_the_write_lock_or_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有可领任务时，claim 必须直接返回：不整库解析、不动盘。"""

    storage.save_store({"automation_jobs": [_job("done", status="saved")], "store_templates": [{"id": "t1"}]})
    storage.load_store()  # 让只读缓存热起来（生产里一直在热）

    parses = {"count": 0}
    real_read = storage._read_store_from_disk

    def counting_read(*args, **kwargs):
        parses["count"] += 1
        return real_read(*args, **kwargs)

    monkeypatch.setattr(storage, "_read_store_from_disk", counting_read)
    jobs_path = settings.STORE_FILE.parent / "automation_jobs.json"
    before_bytes = jobs_path.read_bytes()
    before_mtime = jobs_path.stat().st_mtime_ns

    result = storage.claim_next_automation_job("worker-1", now="2026-10-08T00:05:00+00:00")

    assert result == {"job": None, "lease_token": ""}
    assert parses["count"] == 0, "空轮询不该重新解析整库"
    assert jobs_path.read_bytes() == before_bytes
    assert jobs_path.stat().st_mtime_ns == before_mtime, "空轮询不该写盘"


def test_claim_fast_path_still_claims_and_persists() -> None:
    storage.save_store({"automation_jobs": [_job("job-1")]})
    storage.load_store()

    result = storage.claim_next_automation_job("worker-1", now="2026-10-08T00:05:00+00:00")

    assert result["job"]["id"] == "job-1"
    assert result["job"]["status"] == "claimed"
    assert result["lease_token"]
    stored = storage.get_item("automation_jobs", "job-1")
    assert stored and stored["status"] == "claimed"
    assert stored["worker_id"] == "worker-1"


def test_claim_only_parses_the_jobs_file_after_the_first_read() -> None:
    """有任务可领时也只解析任务文件，主库不重解析。"""

    storage.save_store({"automation_jobs": [_job("job-1")], "store_templates": [{"id": "t1"}]})
    cached = storage.load_store()
    templates_before = cached["store_templates"]

    result = storage.claim_next_automation_job("worker-1", now="2026-10-08T00:05:00+00:00")

    assert result["job"]["id"] == "job-1"
    fresh = storage.load_store()
    assert fresh["automation_jobs"][0]["status"] == "claimed"
    # 主库没变：合并视图里的模板还是上次解析出来的同一个对象（没有重新 json.loads）
    assert fresh["store_templates"] is templates_before


def test_jobs_only_write_does_not_rewrite_or_reparse_the_main_store() -> None:
    storage.save_store({"automation_jobs": [_job("job-1")], "store_templates": [{"id": "t1"}]})
    cached = storage.load_store()
    templates_before = cached["store_templates"]
    main_path = settings.STORE_FILE
    main_before = main_path.read_bytes()
    main_mtime = main_path.stat().st_mtime_ns

    with storage.locked_automation_jobs() as (jobs, save):
        jobs[0]["current_phase"] = "playwright_save_draft"
        save(jobs)

    assert main_path.read_bytes() == main_before
    assert main_path.stat().st_mtime_ns == main_mtime, "只动任务不该重写主库"
    fresh = storage.load_store()
    assert fresh["automation_jobs"][0]["current_phase"] == "playwright_save_draft"
    assert fresh["store_templates"] is templates_before, "主库没变就不该重新解析"


def test_heartbeat_and_progress_touch_only_the_jobs_file() -> None:
    storage.save_store({"automation_jobs": [_job("job-1")], "store_templates": [{"id": "t1"}]})
    claimed = storage.claim_next_automation_job("worker-1", now="2026-10-08T00:00:00+00:00")
    token = claimed["lease_token"]
    main_path = settings.STORE_FILE
    main_before = main_path.read_bytes()
    main_mtime = main_path.stat().st_mtime_ns

    storage.heartbeat_automation_job(
        "job-1", "worker-1", token, phase="load_payload", now="2026-10-08T00:00:10+00:00"
    )
    storage.record_automation_job_progress(
        "job-1",
        "worker-1",
        token,
        event={"event": "stage", "stage": "fill_basic_info", "ok": True},
        now="2026-10-08T00:00:12+00:00",
    )

    assert main_path.read_bytes() == main_before
    assert main_path.stat().st_mtime_ns == main_mtime
    stored = storage.get_item("automation_jobs", "job-1")
    assert stored and stored["status"] == "running"
    assert stored["current_phase"] == "fill_basic_info"
    assert stored["result"]["live_steps"], "进度事件要落进执行结果"


def test_legacy_jobs_still_in_the_main_store_are_migrated_on_claim() -> None:
    settings.STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings.STORE_FILE.write_text(
        json.dumps({"automation_jobs": [_job("legacy-job")]}, ensure_ascii=False), encoding="utf-8"
    )

    result = storage.claim_next_automation_job("worker-1", now="2026-10-08T00:05:00+00:00")

    assert result["job"]["id"] == "legacy-job"
    jobs_path = settings.STORE_FILE.parent / "automation_jobs.json"
    assert jobs_path.is_file(), "旧布局的任务要迁到独立文件"
    assert json.loads(jobs_path.read_text(encoding="utf-8"))[0]["status"] == "claimed"


def test_read_cache_reuses_unchanged_split_files() -> None:
    """只改类目集合时，任务文件的解析结果应该继续复用。"""

    storage.save_store(
        {
            "automation_jobs": [_job("job-1")],
            "marketplace_categories": [{"id": "cat-1"}],
            "store_templates": [{"id": "t1"}],
        }
    )
    # 先落一次完整的库形状（_empty_store 的默认键），后面的对比才只反映真正的改动。
    storage.save_store(storage.load_store())
    cached = storage.load_store()
    jobs_before = cached["automation_jobs"]
    categories_before = cached["marketplace_categories"]

    storage.save_store({**storage.load_store(), "marketplace_categories": [{"id": "cat-2"}]})

    fresh = storage.load_store()
    assert [row["id"] for row in fresh["marketplace_categories"]] == ["cat-2"]
    assert fresh["automation_jobs"] is jobs_before, "任务集合没变，解析结果应复用"
    assert fresh["store_templates"] is cached["store_templates"]
    assert categories_before is not fresh["marketplace_categories"]
