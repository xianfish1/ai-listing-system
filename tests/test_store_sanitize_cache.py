"""URL 清洗按文件缓存 + 写出的解析结果直接进读缓存。

现场「执行期很卡」的两笔额外开销：
1. 每次整库重建都要把全部数据深遍历一遍做 URL 清洗（实测 165ms），但同一份字节
   遍历一次就够了。
2. 进度/心跳刚写完 17.6MB 的任务文件，界面读列表时又要把它重新 json.loads 一遍
   （实测 177ms）再清洗一遍（78ms）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.api import settings, storage

DIRTY_URL = "https://www.mabangerp.com/index.php?mod=x&a=1"
CLEAN_URL = "https://www.mabangerp.com/index.php"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")
    storage._SPLIT_LAST_BYTES.clear()
    storage._MAIN_LAST_BYTES.clear()
    storage._FILE_PARSE_CACHE.clear()
    storage._SANITIZED_FILE_STATES.clear()
    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    yield
    storage._SANITIZED_FILE_STATES.clear()


def _job(job_id: str = "job-1") -> dict:
    return {
        "id": job_id,
        "listing_copy_id": "copy-1",
        "status": "running",
        "worker_id": "worker-1",
        "lease_token": "tok",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
        "attempt_count": 1,
        "run_log": [],
        "created_at": "2026-10-08T00:00:00+00:00",
        "updated_at": "2026-10-08T00:00:00+00:00",
    }


def _seed_dirty() -> None:
    storage.save_store(
        {
            "store_templates": [{"id": "t1", "template_name": "T1", "page_url": DIRTY_URL}],
            "automation_jobs": [_job()],
        }
    )


def _split_path(name: str) -> Path:
    return settings.STORE_FILE.parent / f"{name}.json"


def _url_keys(value, found=None):
    if found is None:
        found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"url", "page_url", "source_url"} and isinstance(item, str):
                found.append(item)
            else:
                _url_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _url_keys(item, found)
    return found


def test_dirty_urls_are_cleaned_once_and_the_traversal_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_dirty()

    first = storage.load_store()
    assert first["store_templates"][0]["page_url"] == CLEAN_URL
    assert CLEAN_URL in settings.STORE_FILE.read_text(encoding="utf-8"), "清洗结果要落盘"

    calls = {"count": 0}
    real = storage.sanitize_mabang_urls_in_store

    def counting(view):
        calls["count"] += 1
        return real(view)

    monkeypatch.setattr(storage, "sanitize_mabang_urls_in_store", counting)

    # 强制重建（清合并缓存）→ 字节没变，不该再遍历
    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    assert storage.load_store()["store_templates"][0]["page_url"] == CLEAN_URL
    assert calls["count"] == 0, f"同一份字节不该重复清洗，实际遍历 {calls['count']} 次"


def test_changed_bytes_are_traversed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_dirty()
    storage.load_store()

    path = settings.STORE_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["store_templates"][0]["page_url"] = "https://www.mabangerp.com/index.php?mod=y"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    calls = {"count": 0}
    real = storage.sanitize_mabang_urls_in_store

    def counting(view):
        calls["count"] += 1
        return real(view)

    monkeypatch.setattr(storage, "sanitize_mabang_urls_in_store", counting)
    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    data = storage.load_store()

    assert calls["count"] >= 1, "换了字节必须重新遍历"
    assert data["store_templates"][0]["page_url"] == CLEAN_URL


def test_jobs_write_hands_the_parse_and_sanitize_mark_to_readers() -> None:
    _seed_dirty()
    storage.load_store()  # 先建立「这份字节已清洗」的标记

    with storage.locked_automation_jobs() as (jobs, save):
        jobs[0]["current_phase"] = "fill_basic_info"
        save(jobs)
        saved_list = jobs

    data = storage.load_store()
    assert data["automation_jobs"] is saved_list, "刚写出的任务文件不该被重新解析"
    jobs_path = _split_path("automation_jobs")
    assert storage._SANITIZED_FILE_STATES.get(str(jobs_path)) == storage._json_file_stat_key(jobs_path), (
        "阶段/状态这类改动没有引入 URL 键，清洗标记应当顺延"
    )
    assert data["automation_jobs"][0]["current_phase"] == "fill_basic_info"


def test_heartbeat_and_progress_do_not_introduce_url_keys() -> None:
    """清洗标记顺延的前提：这几条写入路径不新增 url/page_url/source_url。"""

    _seed_dirty()
    storage.load_store()

    storage.heartbeat_automation_job(
        "job-1", "worker-1", "tok", phase="load_payload", now="2026-10-08T00:00:10+00:00"
    )
    storage.record_automation_job_progress(
        "job-1",
        "worker-1",
        "tok",
        event={"event": "stage", "stage": "fill_basic_info", "status": "ok"},
        now="2026-10-08T00:00:12+00:00",
    )

    stored = json.loads(_split_path("automation_jobs").read_text(encoding="utf-8"))
    assert _url_keys(stored) == [], f"任务写入引入了 URL 键：{_url_keys(stored)[:3]}"


def test_unsanitized_write_through_locked_store_is_still_cleaned() -> None:
    """走整库写路径的新内容照常清洗（不能因为缓存漏掉新写入的脏 URL）。"""

    _seed_dirty()
    storage.load_store()

    with storage.locked_store(split_keys=()) as (data, save):
        data["store_templates"].append(
            {"id": "t2", "template_name": "T2", "source_url": "https://www.mabangerp.com/other.php?z=9"}
        )
        save(data)

    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    fresh = storage.load_store()
    urls = {item.get("id"): item.get("source_url") for item in fresh["store_templates"]}
    assert urls["t2"] == "https://www.mabangerp.com/other.php", f"新写入的脏 URL 必须被清洗：{urls}"
