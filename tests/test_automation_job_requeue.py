"""自动化列表的「再次尝试」= 在原任务上重排，不再新建一条任务行。

覆盖 ``storage.requeue_automation_job`` 与 ``POST /api/automation/jobs/{id}/self-heal``：

- 重排后任务 id 不变，同一副本仍然只有一条任务（旧流程会多出一行）
- execution_snapshot 按当前副本重新冻结，attempt_count 归零（否则 worker 领不到），
  retry_count +1，last_error/result 清空，历史留在 run_log 里
- 就绪度重新判定：副本缺字段 → needs_manual，不会被 worker 领取
- 可能已保存的任务（needs_confirmation / saved）拒绝重排——重跑会产生重复草稿
- 同一副本已有**另一条**活动任务时拒绝重排
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib import request

import pytest

from services.api import main, settings, storage
from tests.test_api_boundaries import _start_api
from tests.test_base_product_lifecycle import (
    _make_base,
    _make_template,
    _prepare_copy_for_execution,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _copy_with_job(tmp_path: Path, suffix: str = "A") -> tuple[dict, dict]:
    base = _make_base(tmp_path, folder_name=f"AL0700 Requeue {suffix}")
    template = _make_template(suffix)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    _prepare_copy_for_execution(copy["id"], base)
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    assert job["status"] == "ready", job.get("missing_fields")
    return copy, job


def _fail_job(job_id: str, *, error: str = "field_not_editable") -> dict:
    claim = storage.claim_next_automation_job("worker-test", lease_seconds=120, max_attempts=3)
    assert claim["job"] and claim["job"]["id"] == job_id, "expected claim to return the target job"
    return storage.finish_automation_job(
        job_id,
        worker_id="worker-test",
        lease_token=claim["lease_token"],
        status="failed",
        result={"ok": False, "status": "failed", "error": error, "publish_clicked": False},
        error=error,
    )


def _jobs_for_copy(copy_id: str) -> list[dict]:
    return [job for job in storage.list_automation_jobs() if job.get("listing_copy_id") == copy_id]


def test_requeue_reuses_the_same_job_row_and_is_claimable_again(tmp_path: Path) -> None:
    copy, job = _copy_with_job(tmp_path)
    failed = _fail_job(job["id"])
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 1

    requeued = storage.requeue_automation_job(job["id"])

    assert requeued["id"] == job["id"], "重排必须复用同一条任务"
    assert len(_jobs_for_copy(copy["id"])) == 1, "同一副本不该多出一条任务"
    assert requeued["status"] == "ready"
    assert requeued["attempt_count"] == 0, "attempt_count 是领取预算，不归零 worker 永远领不到"
    assert requeued["retry_count"] == 1
    assert requeued["last_error"] == ""
    assert requeued["result"] == {}
    assert requeued["worker_id"] == ""
    assert requeued["lease_token"] == ""
    assert requeued["finished_at"] == ""

    # 上一轮的失败留在时间线里，操作员在详情面板仍能看到原因。
    events = [event.get("event") for event in requeued["run_log"]]
    assert "failed" in events
    assert "requeued" in events
    requeue_event = next(event for event in requeued["run_log"] if event.get("event") == "requeued")
    assert requeue_event["error"] == "field_not_editable"
    assert requeue_event["previous_status"] == "failed"

    # 关键回归：重排后 worker 必须能再次领取这条任务。
    claim = storage.claim_next_automation_job("worker-test", lease_seconds=120, max_attempts=3)
    assert claim["job"] and claim["job"]["id"] == job["id"]
    assert claim["job"]["attempt_count"] == 1


def test_requeue_freezes_the_current_copy_state(tmp_path: Path) -> None:
    copy, job = _copy_with_job(tmp_path)
    _fail_job(job["id"])
    storage.update_store_listing_copy(copy["id"], {"title": "重排前改过的标题"})

    requeued = storage.requeue_automation_job(job["id"])

    snapshot_copy = requeued["execution_snapshot"]["listing_copy"]
    assert snapshot_copy["listing_title"] == "重排前改过的标题"
    assert requeued["execution_snapshot_hash"] == storage._stable_hash(requeued["execution_snapshot"])


def test_requeue_rechecks_readiness_and_stops_at_needs_manual(tmp_path: Path) -> None:
    copy, job = _copy_with_job(tmp_path)
    _fail_job(job["id"])
    storage.update_store_listing_copy(copy["id"], {"description": "", "description_status": ""})

    requeued = storage.requeue_automation_job(job["id"])

    assert requeued["status"] == "needs_manual"
    assert "description" in requeued["missing_fields"]

    # needs_manual 不是 ready，worker 不会领取（等操作员补齐后再点一次「再次尝试」）。
    claim = storage.claim_next_automation_job("worker-test", lease_seconds=120, max_attempts=3)
    assert claim["job"] is None


def test_requeue_refuses_jobs_that_may_already_be_saved(tmp_path: Path) -> None:
    _, job = _copy_with_job(tmp_path)
    claim = storage.claim_next_automation_job("worker-test", lease_seconds=120, max_attempts=3)
    assert claim["job"] and claim["job"]["id"] == job["id"]
    storage.finish_automation_job(
        job["id"],
        worker_id="worker-test",
        lease_token=claim["lease_token"],
        status="needs_confirmation",
        result={"ok": False, "status": "needs_confirmation", "save_clicked": True, "publish_clicked": False},
        error="save_result_ambiguous",
    )

    with pytest.raises(ValueError, match="terminated automation job"):
        storage.requeue_automation_job(job["id"])


def test_requeue_refuses_when_the_copy_has_another_active_job(tmp_path: Path) -> None:
    copy, first = _copy_with_job(tmp_path)
    _fail_job(first["id"])
    # 旧流程（每次「再次尝试」新建一条）留下的第二行还活着时，把旧行重排会造出两份草稿。
    second = storage.create_automation_job_from_listing_copy(copy["id"])
    assert second["id"] and second["id"] != first["id"]

    with pytest.raises(storage.ActiveAutomationJobExists):
        storage.requeue_automation_job(first["id"])


def test_self_heal_endpoint_requeues_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy, job = _copy_with_job(tmp_path)
    _fail_job(job["id"], error="needs_manual_login_or_verification")

    scheduled: list[dict] = []
    monkeypatch.setattr(main, "_schedule_retry_worker_wake", lambda job: scheduled.append(dict(job)))

    server, base_url = _start_api()
    try:
        req = request.Request(
            f"{base_url}/api/automation/jobs/{job['id']}/self-heal",
            method="POST",
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["ok"] is True
    assert payload["original_job_id"] == job["id"]
    assert payload["job_id"] == job["id"], "响应必须指向同一条任务，而不是新任务 id"
    assert payload["job_status"] == "ready"
    assert payload["retry_count"] == 1
    assert "hint" in payload, "登录/验证类失败要带人工处理提示"

    # 唤醒 Worker 是后台队列：请求只把任务塞进队列就返回，不再同步起进程/准备浏览器。
    assert len(scheduled) == 1
    assert scheduled[0]["id"] == job["id"]
    assert payload["worker_start"]["status"] == "starting"
    wake_step = next(step for step in payload["steps"] if step["step"] == "worker_wake")
    assert wake_step["background"] is True

    jobs = _jobs_for_copy(copy["id"])
    assert len(jobs) == 1, "接口不该为同一次重试新增任务行"
    assert jobs[0]["id"] == job["id"]


def test_self_heal_endpoint_reports_the_other_active_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一副本已有另一条活动任务：接口返回 ok=false + 可读原因，而不是 500。"""
    copy, first = _copy_with_job(tmp_path)
    _fail_job(first["id"])
    storage.create_automation_job_from_listing_copy(copy["id"])

    scheduled: list[dict] = []
    monkeypatch.setattr(main, "_schedule_retry_worker_wake", lambda job: scheduled.append(dict(job)))

    server, base_url = _start_api()
    try:
        req = request.Request(
            f"{base_url}/api/automation/jobs/{first['id']}/self-heal",
            method="POST",
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["ok"] is False
    bad = next(step for step in payload["steps"] if not step["ok"])
    assert bad["step"] == "requeue_job"
    assert "另一条活动任务" in bad["error"]
    assert scheduled == [], "重排没成功就不该唤醒 Worker"
    assert len(_jobs_for_copy(copy["id"])) == 2, "失败时不该新增任务行"
