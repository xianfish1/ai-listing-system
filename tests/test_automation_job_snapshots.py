"""执行快照拆到独立集合：任务文件从 17.6MB 降到约 300KB。

执行期领取/心跳/进度每次都读写任务文件；快照（每个约 214KB，占任务的 98%）搬到
~automation_job_snapshots~ 之后，这些高频路径完全不再背着 17MB。
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
    storage._SANITIZED_FILE_STATES.clear()
    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    yield


def _snapshot(job_id: str, description: str = "snap") -> dict:
    return {
        "listing_copy": {"id": f"copy-{job_id}", "description": description},
        "skus": [{"id": f"sku-{job_id}"}],
        "prices": [],
        "browser_inputs": {"product_image_form_paths": ["a.jpg"]},
    }


def _job(job_id: str = "job-1", *, inline_snapshot: bool = True) -> dict:
    snapshot = _snapshot(job_id)
    job = {
        "id": job_id,
        "listing_copy_id": f"copy-{job_id}",
        "status": "running",
        "worker_id": "worker-1",
        "lease_token": "tok",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
        "attempt_count": 1,
        "execution_snapshot_hash": storage._stable_hash(snapshot),
        "run_log": [],
        "created_at": "2026-10-08T00:00:00+00:00",
        "updated_at": "2026-10-08T00:00:00+00:00",
    }
    if inline_snapshot:
        job["execution_snapshot"] = snapshot
    return job


def _path(name: str) -> Path:
    return settings.STORE_FILE.parent / f"{name}.json"


def test_inline_snapshots_move_to_their_own_file() -> None:
    storage.save_store({"automation_jobs": [_job("job-1"), _job("job-2")]})

    data = storage.load_store()

    jobs_raw = _path("automation_jobs").read_text(encoding="utf-8")
    assert '"execution_snapshot":' not in jobs_raw, "任务文件不该再背快照内容"
    snapshots = json.loads(_path("automation_job_snapshots").read_text(encoding="utf-8"))
    assert set(snapshots) == {"job-1", "job-2"}
    assert snapshots["job-1"]["listing_copy"]["id"] == "copy-job-1"
    assert all("execution_snapshot" not in job for job in data["automation_jobs"])
    # 哈希留在任务上（执行器保存前自校验用）
    assert all(job.get("execution_snapshot_hash") for job in data["automation_jobs"])


def test_partial_read_never_drops_inline_snapshots() -> None:
    """领取/心跳的部分加载没有快照集合的落点，绝不能 pop 内联快照。"""

    storage.save_store({"automation_jobs": [_job()]})

    with storage.locked_store(split_keys=("automation_jobs",)) as (data, save):
        data["automation_jobs"][0]["current_phase"] = "fill_basic_info"
        save(data)

    jobs_raw = _path("automation_jobs").read_text(encoding="utf-8")
    assert '"execution_snapshot":' in jobs_raw, "部分加载不能把快照 pop 掉（会直接丢 17MB 数据）"
    assert not _path("automation_job_snapshots").exists()

    # 完整读一次才搬家
    data = storage.load_store()
    assert '"execution_snapshot":' not in _path("automation_jobs").read_text(encoding="utf-8")
    assert (data.get("automation_job_snapshots") or {}).get("job-1")


def test_heartbeat_and_progress_do_not_touch_the_snapshots_file() -> None:
    storage.save_store({"automation_jobs": [_job()]})
    storage.load_store()  # 完成迁移
    snapshots_path = _path("automation_job_snapshots")
    before_bytes = snapshots_path.read_bytes()
    before_mtime = snapshots_path.stat().st_mtime_ns
    jobs_before = _path("automation_jobs").stat().st_size

    storage.heartbeat_automation_job(
        "job-1", "worker-1", "tok", phase="load_payload", now="2026-10-08T00:00:10+00:00"
    )
    storage.record_automation_job_progress(
        "job-1",
        "worker-1",
        "tok",
        event={"event": "stage", "stage": "fill_basic_info"},
        now="2026-10-08T00:00:12+00:00",
    )

    assert snapshots_path.read_bytes() == before_bytes, "心跳/进度不该碰快照文件"
    assert snapshots_path.stat().st_mtime_ns == before_mtime
    assert _path("automation_jobs").stat().st_size >= jobs_before


def test_payload_and_list_hydrate_snapshots_on_demand() -> None:
    storage.save_store({"automation_jobs": [_job("job-1")]})
    storage.load_store()

    payload = storage.build_automation_payload("job-1")
    assert payload["ok"] is True
    assert payload["execution_snapshot"]["listing_copy"]["id"] == "copy-job-1"
    assert payload["listing_copy"]["id"] == "copy-job-1"

    light = storage.list_automation_jobs()
    assert light and "execution_snapshot" not in light[0]
    heavy = storage.list_automation_jobs(include_snapshot=True)
    assert heavy and heavy[0]["execution_snapshot"]["browser_inputs"]["product_image_form_paths"] == ["a.jpg"]


def _legacy_snapshot_rows(job_id: str) -> dict:
    """去重前的旧形状：顶层与 browser_inputs 各存了一份 skus/prices。"""

    skus = [{"id": f"sku-{job_id}", "sku_code": "X", "color_code": "black", "size": "M"}]
    prices = [{"id": f"price-{job_id}", "sku_id": f"sku-{job_id}", "sku_code": "X", "normal_price": 10}]
    return {
        "listing_copy": {"id": f"copy-{job_id}"},
        "skus": skus,
        "prices": prices,
        "browser_inputs": {
            "product_image_form_paths": ["a.jpg"],
            "skus": [dict(item) for item in skus],
            "prices": [dict(item) for item in prices],
        },
    }


def test_legacy_duplicate_rows_are_deduped_and_the_hash_recomputed() -> None:
    snapshot = _legacy_snapshot_rows("job-1")
    job = _job("job-1", inline_snapshot=False)
    job["execution_snapshot_hash"] = storage._stable_hash(snapshot)  # 旧形状的哈希
    storage.save_store({"automation_jobs": [job], "automation_job_snapshots": {"job-1": snapshot}})

    data = storage.load_store()
    stored = (data.get("automation_job_snapshots") or {})["job-1"]
    assert stored["skus"] and stored["prices"]
    assert "skus" not in stored["browser_inputs"] and "prices" not in stored["browser_inputs"], (
        "browser_inputs 里的重复行要去掉"
    )

    stored_job = next(item for item in data["automation_jobs"] if item["id"] == "job-1")
    assert stored_job["execution_snapshot_hash"] == storage._stable_hash(stored), (
        "去重后必须按新形状重算哈希，否则执行前自校验会报 execution_snapshot_changed"
    )

    payload = storage.build_automation_payload("job-1")
    assert payload["ok"] is True
    assert "execution_snapshot_changed" not in payload["readiness"]["missing_fields"]
    assert payload["browser_inputs"]["skus"] == stored["skus"], "执行器要看到的网格顺序不能丢"
    assert payload["browser_inputs"]["prices"] == stored["prices"]
    assert payload["execution_snapshot"]["browser_inputs"] == stored["browser_inputs"], (
        "返回的 execution_snapshot 必须与存的一致（哈希校验用）"
    )


def test_snapshot_row_dedup_is_idempotent() -> None:
    snapshot = _legacy_snapshot_rows("job-1")
    job = _job("job-1", inline_snapshot=False)
    job["execution_snapshot_hash"] = storage._stable_hash(snapshot)
    storage.save_store({"automation_jobs": [job], "automation_job_snapshots": {"job-1": snapshot}})

    storage.load_store()
    snapshots_path = _path("automation_job_snapshots")
    first_bytes = snapshots_path.read_bytes()
    assert b'"skus"' in first_bytes  # 顶层那份还在

    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    storage.load_store()
    assert snapshots_path.read_bytes() == first_bytes, "第二次读不该再改快照"


def test_created_job_snapshot_has_no_duplicate_rows(tmp_path: Path) -> None:
    from tests.test_base_product_lifecycle import _make_base, _make_template, _prepare_copy_for_execution

    template = _make_template("Snap")
    base = _make_base(tmp_path, folder_name="AL0909 Snap")
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    _prepare_copy_for_execution(copy["id"], base)

    job = storage.create_automation_job_from_listing_copy(copy["id"])

    snapshot = (storage.load_store().get("automation_job_snapshots") or {})[job["id"]]
    assert snapshot["skus"], "顶层要有 skus"
    assert "skus" not in snapshot["browser_inputs"] and "prices" not in snapshot["browser_inputs"]
    payload = storage.build_automation_payload(job["id"])
    assert payload["browser_inputs"]["skus"] == snapshot["skus"]
    assert payload["execution_snapshot_hash"] == job["execution_snapshot_hash"]


def test_delete_job_removes_its_snapshot() -> None:
    first = _job("job-1")
    second = _job("job-2")
    first["status"] = second["status"] = "blocked"
    storage.save_store({"automation_jobs": [first, second]})
    storage.load_store()

    storage.delete_automation_job("job-1")

    data = storage.load_store()
    assert [job["id"] for job in data["automation_jobs"]] == ["job-2"]
    snapshots = data.get("automation_job_snapshots") or {}
    assert "job-1" not in snapshots, "删任务要顺手清掉快照，别留孤儿"
    assert "job-2" in snapshots
