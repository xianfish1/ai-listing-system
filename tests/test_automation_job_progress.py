"""执行进度实时上报 + 人工标记完成/失败（storage + HTTP 路由）。"""

from __future__ import annotations

import json
from pathlib import Path
from urllib import request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api
from tests.test_base_product_lifecycle import _make_base, _make_template, _prepare_copy_for_execution


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _claimed_job(tmp_path: Path, suffix: str = "P") -> tuple[dict, str, str]:
    base = _make_base(tmp_path, folder_name=f"AL0800 Progress {suffix}")
    template = _make_template(suffix)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    _prepare_copy_for_execution(copy["id"], base)
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    assert job["status"] == "ready", job.get("missing_fields")
    claim = storage.claim_next_automation_job("worker-progress", lease_seconds=300, max_attempts=3)
    assert claim["job"] and claim["job"]["id"] == job["id"]
    return job, "worker-progress", claim["lease_token"]


def test_progress_events_accumulate_into_live_steps(tmp_path: Path) -> None:
    """worker 边跑边回报：面板要按顺序显示"执行到哪一步"，所以事件必须累积且保序。"""

    job, worker_id, lease_token = _claimed_job(tmp_path)

    storage.record_automation_job_progress(
        job["id"], worker_id, lease_token,
        event={"event": "stage", "stage": "select_store", "ok": True, "at": "2026-09-28T01:00:00+00:00"},
    )
    updated = storage.record_automation_job_progress(
        job["id"], worker_id, lease_token,
        event={"event": "stage", "stage": "set_category_attributes", "ok": True, "at": "2026-09-28T01:00:05+00:00"},
    )

    steps = updated["result"]["live_steps"]
    assert [step["stage"] for step in steps] == ["select_store", "set_category_attributes"]
    assert updated["current_phase"] == "set_category_attributes"
    assert updated["status"] == "running"
    events = [entry["event"] for entry in updated["run_log"]]
    assert "stage" in events
    assert steps[0]["ok"] is True


def test_progress_requires_the_active_lease(tmp_path: Path) -> None:
    job, worker_id, lease_token = _claimed_job(tmp_path)

    with pytest.raises(ValueError):
        storage.record_automation_job_progress(job["id"], worker_id, "wrong-token", event={"event": "stage", "stage": "x"})
    with pytest.raises(ValueError):
        storage.record_automation_job_progress(job["id"], "other-worker", lease_token, event={"event": "stage", "stage": "x"})


def test_progress_events_are_bounded(tmp_path: Path) -> None:
    """长时间运行不能把作业行撑爆：只保留最近 LIVE_STEP_LIMIT 条。"""

    job, worker_id, lease_token = _claimed_job(tmp_path)
    total = storage.LIVE_STEP_LIMIT + 20
    for index in range(total):
        storage.record_automation_job_progress(
            job["id"], worker_id, lease_token,
            event={"event": "stage", "stage": f"stage_{index}", "ok": True},
        )

    steps = storage.get_item("automation_jobs", job["id"])["result"]["live_steps"]
    assert len(steps) == storage.LIVE_STEP_LIMIT
    assert steps[-1]["stage"] == f"stage_{total - 1}"
    assert steps[0]["stage"] == f"stage_{total - storage.LIVE_STEP_LIMIT}"


def test_manual_finish_records_confirmation_without_faking_evidence(tmp_path: Path) -> None:
    """人工确认只写审计：save_clicked/publish_clicked 保持原样，不能伪造保存证据。"""

    job, worker_id, lease_token = _claimed_job(tmp_path)
    storage.record_automation_job_progress(
        job["id"], worker_id, lease_token,
        event={"event": "stage", "stage": "validate_before_save", "ok": True},
    )

    updated = storage.manual_finish_automation_job(
        job["id"], status="saved", note="在马帮看到草稿已保存", operator="operator",
    )

    assert updated["status"] == "saved"
    assert updated["current_phase"] == "manual_confirmed_saved"
    confirmation = updated["result"]["manual_confirmation"]
    assert confirmation["status"] == "saved"
    assert confirmation["previous_status"] == "running"
    assert confirmation["note"] == "在马帮看到草稿已保存"
    assert confirmation["at"]
    assert updated["lease_token"] == ""
    assert updated["result"].get("save_clicked") is None, "人工确认不得伪造 save_clicked"
    assert updated["result"].get("publish_clicked") is None, "人工确认不得伪造 publish_clicked"
    assert "manual_confirmation" in [entry["event"] for entry in updated["run_log"]]


def test_manual_finish_marks_failed_with_reason(tmp_path: Path) -> None:
    job, _worker_id, _lease_token = _claimed_job(tmp_path)

    updated = storage.manual_finish_automation_job(job["id"], status="failed", note="马帮报保存被拦")

    assert updated["status"] == "failed"
    assert updated["last_error"] == "马帮报保存被拦"


def test_manual_finish_rejects_unknown_status(tmp_path: Path) -> None:
    job, _worker_id, _lease_token = _claimed_job(tmp_path)

    with pytest.raises(ValueError):
        storage.manual_finish_automation_job(job["id"], status="publishing")


def test_progress_and_manual_finish_http_routes(tmp_path: Path) -> None:
    """路由也要通：/progress 用租约写入，/manual-finish 供操作员确认。"""

    job, worker_id, lease_token = _claimed_job(tmp_path)
    server, base_url = _start_api()
    try:
        progress_request = request.Request(
            f"{base_url}/api/automation/jobs/{job['id']}/progress",
            method="POST",
            data=json.dumps({
                "worker_id": worker_id,
                "lease_token": lease_token,
                "event": {"event": "stage", "stage": "fill_basic_info", "ok": True},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(progress_request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["result"]["live_steps"][-1]["stage"] == "fill_basic_info"

        manual_request = request.Request(
            f"{base_url}/api/automation/jobs/{job['id']}/manual-finish",
            method="POST",
            data=json.dumps({"status": "saved", "note": "人工确认", "operator": "operator"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(manual_request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["status"] == "saved"
        assert payload["result"]["manual_confirmation"]["status"] == "saved"
    finally:
        server.shutdown()
        server.server_close()
