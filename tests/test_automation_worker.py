from __future__ import annotations

from pathlib import Path

import pytest

from scripts import automation_worker


def _payload() -> dict:
    return {
        "ok": True,
        "job": {"id": "job-1", "listing_copy_id": "copy-1", "status": "claimed"},
        "listing_copy": {"id": "copy-1", "listing_title": "EL0002 shirt", "group_id": "EL0002"},
        "readiness": {"ready": True, "missing_fields": []},
        "skus": [{"sku_code": "EL0002_black||S", "stock": 999}],
        "prices": [{"sku_code": "EL0002_black||S", "normal_price": 89, "sale_price": 69}],
        "browser_inputs": {
            "store_name": "POD Philippines",
            "mabang_store_id": "2022625115",
            "mabang_category_id": "100244",
            "mabang_discount_id": "928232107737088",
            "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
            "title": "EL0002 shirt",
            "description": "description",
            "group_id": "EL0002",
            "category_attributes": {"brand": "NoBrand"},
            "product_image_paths": [],
            "variant_image_paths": {},
            "variation": {"type": "variant", "axes": ["color", "size"], "colors": ["black"], "sizes": ["S"]},
            "sku_count": 1,
        },
    }


class FakeClient:
    def __init__(self, *, claimed: bool = True, page_import_run: dict | None = None) -> None:
        self.claimed = claimed
        self.claim_count = 0
        self.heartbeats = []
        self.finishes = []
        self.auto_login_count = 0
        self.page_import_run = page_import_run
        self.page_import_queries = 0

    def claim(self, worker_id: str, lease_seconds: int, account_id: str | None = None) -> dict:
        self.claim_count += 1
        if not self.claimed:
            return {"job": None, "lease_token": ""}
        return {"job": {"id": "job-1", "status": "claimed"}, "lease_token": "lease-1"}

    def next_account(self) -> dict:
        return {"account_id": "", "runtime": {}}

    def payload(self, job_id: str) -> dict:
        return _payload()

    def heartbeat(self, job_id: str, worker_id: str, lease_token: str, phase: str, lease_seconds: int) -> dict:
        self.heartbeats.append(phase)
        return {"id": job_id, "status": "running", "current_phase": phase}

    def finish(self, job_id: str, worker_id: str, lease_token: str, status: str, result: dict, error: str) -> dict:
        self.finishes.append({"status": status, "result": result, "error": error})
        return {"id": job_id, "status": status, "result": result, "last_error": error}

    def auto_login_mabang(self) -> dict:
        self.auto_login_count += 1
        return {"ok": True, "auto_login": {"status": "already_logged_in", "error": ""}}

    def page_import_session(self, account_id: str | None = None, slot: int = 0) -> dict:
        self.page_import_queries += 1
        self.page_import_slots = getattr(self, "page_import_slots", []) + [int(slot or 0)]
        run = self.page_import_run
        return {"active": run is not None, "run": run}


def test_worker_run_once_returns_idle_without_ready_job(tmp_path: Path, monkeypatch) -> None:
    worker = automation_worker.AutomationWorker(
        FakeClient(claimed=False),
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: True)

    result = worker.run_once()

    assert result == {"ok": True, "status": "idle", "job_id": ""}


def test_worker_switching_account_changes_cdp_and_forces_a_fresh_login_check(tmp_path: Path) -> None:
    """换马帮账号 = 换 Chrome Profile（cookie）与登录态，必须重新走登录预检。"""

    worker = automation_worker.AutomationWorker(
        FakeClient(claimed=False),
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
    )
    worker._startup_login_ready = True
    worker._startup_browser_recovery_done = True

    worker._activate_account("acct-b", {"cdp_url": "http://127.0.0.1:9224", "login_account": "acct-b"})

    assert worker.account_id == "acct-b"
    assert worker.cdp_url == "http://127.0.0.1:9224"
    assert worker._startup_login_ready is False
    assert worker._startup_browser_recovery_done is False

    # 同一个账号再激活一次是空操作，不会反复打断登录预检。
    worker._startup_login_ready = True
    worker._activate_account("acct-b", {"cdp_url": "http://127.0.0.1:9224", "login_account": "acct-b"})
    assert worker._startup_login_ready is True


def test_worker_waits_for_browser_without_claiming_when_cdp_is_unreachable(tmp_path: Path, monkeypatch) -> None:
    client = FakeClient(claimed=True)
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: False)

    result = worker.run_once()

    assert result["ok"] is False
    assert result["status"] == "waiting_for_browser"
    assert result["job_id"] == ""
    # The worker must NOT consume a job while the browser is down.
    assert client.claim_count == 0
    assert client.finishes == []


def test_worker_stands_by_while_a_page_import_uses_the_browser(tmp_path: Path, monkeypatch) -> None:
    """「从马帮当前页导入」进行中：Worker 不碰浏览器、不领任务。

    现场问题（2026-10-05）：导入弹窗刚把可见窗口打开，Worker 的浏览器恢复就按设置里的
    无头把它杀掉重起，窗口凭空消失，操作员连点 5 次都没采到。
    """

    run = {"id": "run-12345678", "status": "pending", "account_id": "acct-a"}
    client = FakeClient(claimed=True, page_import_run=run)
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
        enable_hybrid_execution=False,
        account_id="acct-a",
    )
    recovered: list[bool] = []
    monkeypatch.setattr(
        worker,
        "_recover_browser_before_wait",
        lambda **kwargs: recovered.append(True) or {"auto_login": {"status": "already_logged_in"}},
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: False)

    result = worker.run_once()

    assert result["ok"] is False
    assert result["status"] == "waiting_for_page_import"
    assert "页面导入进行中" in result["error"]
    assert client.claim_count == 0
    assert client.auto_login_count == 0
    # 连浏览器恢复都不能碰（那正是把窗口收走的那一步）
    assert recovered == []

    # 导入确认/过期后自动恢复领取
    client.page_import_run = None
    client.claimed = False
    worker._page_import_session_cache = None
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: True)
    assert worker.run_once() == {"ok": True, "status": "idle", "job_id": ""}


def test_worker_page_import_probe_is_cached_between_polls(tmp_path: Path) -> None:
    client = FakeClient(claimed=False, page_import_run={"id": "run-1"})
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
        enable_hybrid_execution=False,
        account_id="acct-a",
    )

    worker._active_page_import_session()
    worker._active_page_import_session()

    assert client.page_import_queries == 1


def test_worker_claims_builds_save_plan_and_finishes_saved(tmp_path: Path) -> None:
    client = FakeClient()
    captured = {}

    def executor(plan: dict, **kwargs) -> dict:
        captured["plan"] = plan
        return {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
            "exit_code": 0,
            "evidence": {"success_message": True, "save_response": True},
        }

    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=executor,
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "saved"
    assert captured["plan"]["mode"] == "save_draft"
    assert captured["plan"]["safety"]["allow_save"] is True
    assert captured["plan"]["safety"]["allow_publish"] is False
    assert client.heartbeats == ["load_payload", "playwright_save_draft", "finish_saved"]
    assert client.finishes == [
        {
            "status": "saved",
            "result": {
                "ok": True,
                "status": "saved",
                "save_clicked": True,
                "publish_clicked": False,
                "page_closed": True,
                "close_method": "success_dialog",
                "exit_code": 0,
                "evidence": {"success_message": True, "save_response": True},
            },
            "error": "",
        }
    ]


def test_worker_finishes_saved_with_warnings_when_draft_is_proven_saved(tmp_path: Path) -> None:
    client = FakeClient()
    execution = {
        "ok": True,
        "status": "saved",
        "save_clicked": True,
        "publish_clicked": False,
        "page_closed": True,
        "close_method": "success_dialog",
        "exit_code": 0,
        "evidence": {"success_message": True, "save_response": True},
        "stages": {
            "apply_main_image_watermark": {"status": "warning", "code": "watermark_not_found"},
            "video_upload": {"status": "skipped", "code": "video_missing"},
        },
    }
    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path,
        executor=lambda plan, **kwargs: execution, heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "saved_with_warnings"
    assert client.finishes[0]["status"] == "saved_with_warnings"
    assert client.finishes[0]["result"]["publish_clicked"] is False


def test_worker_rejects_saved_result_without_closed_detail_page(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        pending_completion_path=tmp_path / "pending-completion.json",
        executor=lambda plan, **kwargs: {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "exit_code": 0,
        },
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "failed"
    assert client.heartbeats[-1] == "finish_failed"
    assert client.finishes[0]["status"] == "failed"
    assert client.finishes[0]["error"] == "Browser execution did not close the saved detail page"


def test_worker_finishes_harness_blocked_without_marking_saved(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=lambda plan, **kwargs: {
            "ok": False,
            "status": "harness_blocked",
            "error": "forbidden_action",
            "publish_clicked": False,
            "save_clicked": False,
        },
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "blocked"
    assert client.finishes[0]["status"] == "blocked"
    assert client.finishes[0]["result"]["publish_clicked"] is False


def test_worker_routes_harness_needs_confirmation_to_confirmation(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=lambda plan, **kwargs: {
            "ok": False,
            "status": "harness_blocked",
            "error": "needs_confirmation",
            "publish_clicked": False,
            "save_clicked": True,
        },
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "needs_confirmation"
    assert client.finishes[0]["status"] == "needs_confirmation"


def test_worker_rejects_saved_result_with_ok_false(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=lambda plan, **kwargs: {
            "ok": False,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "exit_code": 0,
        },
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "failed"
    assert client.finishes[0]["error"] == "Browser execution reported saved with ok=false"


def test_worker_rejects_saved_result_with_unknown_close_method(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=lambda plan, **kwargs: {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "unknown",
            "exit_code": 0,
        },
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "failed"
    assert client.finishes[0]["error"] == "Browser execution reported saved with an unknown close method"


def test_worker_preserves_proven_saved_result_when_background_heartbeat_failed(tmp_path: Path, monkeypatch) -> None:
    class FailedLeaseHeartbeat:
        last_error = "temporary heartbeat failure"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(automation_worker, "LeaseHeartbeat", FailedLeaseHeartbeat)
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=lambda plan, **kwargs: {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
            "exit_code": 0,
        },
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "saved"
    assert client.finishes[0]["status"] == "saved"
    assert client.finishes[0]["result"]["lease_heartbeat_error"] == "temporary heartbeat failure"


def test_worker_retries_pending_saved_completion_before_claiming_next_job(tmp_path: Path) -> None:
    class FinishFailsOnceClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.finish_attempts = 0

        def finish(self, job_id: str, worker_id: str, lease_token: str, status: str, result: dict, error: str) -> dict:
            self.finish_attempts += 1
            if self.finish_attempts == 1:
                raise RuntimeError("temporary finish failure")
            return super().finish(job_id, worker_id, lease_token, status, result, error)

    client = FinishFailsOnceClient()
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=lambda plan, **kwargs: {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
            "exit_code": 0,
        },
        heartbeat_interval=0,
    )

    pending = worker.run_once()
    assert (tmp_path / "pending-completion.json").is_file()
    finished = worker.run_once()

    assert pending["status"] == "completion_pending"
    assert finished["status"] == "saved"
    assert not (tmp_path / "pending-completion.json").exists()
    assert client.claim_count == 1
    assert client.finish_attempts == 2
    assert client.finishes[0]["status"] == "saved"


def test_worker_restores_pending_saved_completion_after_restart(tmp_path: Path) -> None:
    class RecoverableFinishClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.finish_available = False
            self.finish_worker_ids = []

        def finish(self, job_id: str, worker_id: str, lease_token: str, status: str, result: dict, error: str) -> dict:
            self.finish_worker_ids.append(worker_id)
            if not self.finish_available:
                raise RuntimeError("finish unavailable")
            return super().finish(job_id, worker_id, lease_token, status, result, error)

    pending_path = tmp_path / "pending-completion.json"
    client = RecoverableFinishClient()
    first_worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-before-restart",
        upload_root=tmp_path,
        pending_completion_path=pending_path,
        executor=lambda plan, **kwargs: {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
            "exit_code": 0,
        },
        heartbeat_interval=0,
    )

    pending = first_worker.run_once()
    client.finish_available = True
    restarted_worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-after-restart",
        upload_root=tmp_path,
        pending_completion_path=pending_path,
        executor=lambda plan, **kwargs: (_ for _ in ()).throw(AssertionError("must not execute again")),
        heartbeat_interval=0,
    )
    recovered = restarted_worker.run_once()

    assert pending["status"] == "completion_pending"
    assert recovered["status"] == "saved"
    assert client.claim_count == 1
    assert client.finish_worker_ids[-1] == "worker-before-restart"
    assert not pending_path.exists()


def test_worker_finishes_browser_block_as_blocked(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=lambda plan, **kwargs: {
            "ok": False,
            "status": "blocked",
            "error": "needs_manual_login_or_verification",
            "save_clicked": False,
            "publish_clicked": False,
        },
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "paused"
    assert client.finishes[0]["status"] == "blocked"
    assert client.finishes[0]["error"] == "needs_manual_login_or_verification"
    assert client.finishes[0]["result"]["publish_clicked"] is False
    assert worker.paused_reason == "needs_manual_login_or_verification"


def test_ambiguous_save_becomes_needs_confirmation_and_does_not_retry(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path, heartbeat_interval=0,
        executor=lambda plan, **kwargs: {"ok": False, "status": "blocked", "error": "save_result_ambiguous", "save_clicked": True, "publish_clicked": False},
    )
    result = worker.run_once()
    assert result["status"] == "needs_confirmation"
    assert client.finishes[0]["status"] == "needs_confirmation"
    assert client.claim_count == 1


@pytest.mark.parametrize("error", ["save_result_not_proven", "saved_success_dialog_not_found"])
def test_clicked_save_without_proof_requires_confirmation(tmp_path: Path, error: str) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path, heartbeat_interval=0,
        executor=lambda plan, **kwargs: {"ok": False, "status": "blocked", "error": error, "save_clicked": True, "publish_clicked": False},
    )
    assert worker.run_once()["status"] == "needs_confirmation"
    assert client.finishes[0]["status"] == "needs_confirmation"


def test_system_failure_pauses_worker_before_claiming_next_job(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path, heartbeat_interval=0,
        executor=lambda plan, **kwargs: {"ok": False, "status": "blocked", "error": "page_structure_changed", "system_failure": True, "save_clicked": False, "publish_clicked": False},
    )
    first = worker.run_once()
    second = worker.run_once()
    assert first["status"] == "paused"
    assert second["status"] == "paused"
    assert client.claim_count == 1


@pytest.mark.parametrize("error", ["mabang_detail_iframe_not_found", "trade_info_table_not_unique"])
def test_page_structure_failures_pause_worker(tmp_path: Path, error: str) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path, heartbeat_interval=0,
        executor=lambda plan, **kwargs: {"ok": False, "status": "blocked", "error": error, "save_clicked": False, "publish_clicked": False},
    )
    assert worker.run_once()["status"] == "paused"
    assert worker.run_once()["status"] == "paused"
    assert client.claim_count == 1


def test_worker_finishes_executor_exception_as_failed(tmp_path: Path) -> None:
    client = FakeClient()

    def executor(plan: dict, **kwargs) -> dict:
        raise RuntimeError("executor crashed")

    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        executor=executor,
        heartbeat_interval=0,
    )

    result = worker.run_once()

    assert result["status"] == "failed"
    assert client.finishes[0]["status"] == "failed"
    assert client.finishes[0]["error"] == "executor crashed"
    assert client.finishes[0]["result"] == {"save_clicked": False, "publish_clicked": False}


def test_worker_defaults_to_three_second_polling() -> None:
    assert automation_worker.DEFAULT_POLL_SECONDS == 3.0


def test_single_instance_lock_can_be_reacquired_after_previous_worker_exits(tmp_path: Path) -> None:
    lock_path = tmp_path / "automation-worker.lock"

    with automation_worker.SingleInstanceLock(lock_path):
        with lock_path.open("rb") as metadata:
            metadata.seek(1)
            assert int(metadata.read().decode("ascii")) > 0
    assert lock_path.read_bytes().startswith(b"0")


def test_single_instance_lock_reports_concurrent_windows_reader_as_already_running(tmp_path: Path) -> None:
    lock_path = tmp_path / "automation-worker.lock"

    with automation_worker.SingleInstanceLock(lock_path):
        with pytest.raises(RuntimeError, match="Another automation worker is already running"):
            with automation_worker.SingleInstanceLock(lock_path):
                pass
    with automation_worker.SingleInstanceLock(lock_path):
        pass


def test_windows_startup_script_leaves_worker_lifecycle_to_api_supervisor() -> None:
    project_root = Path(__file__).resolve().parents[1]
    worker_script = (project_root / "scripts" / "run_automation_worker.ps1").read_text(encoding="utf-8")
    api_script = (project_root / "scripts" / "run_api.ps1").read_text(encoding="utf-8")

    assert "scripts.automation_worker" in worker_script
    assert "--poll-seconds" in worker_script
    assert "--lease-seconds" in worker_script
    assert "--cdp-url" in worker_script
    assert "Start-Process" not in api_script
    assert "run_automation_worker.ps1" not in api_script
    assert "Worker lifecycle is owned by the API supervisor" in api_script
    assert "python -m services.api.main" in api_script


_SAVED_EXECUTION = {
    "ok": True,
    "status": "saved",
    "save_clicked": True,
    "publish_clicked": False,
    "page_closed": True,
    "close_method": "success_dialog",
    "exit_code": 0,
    "evidence": {"success_message": True, "save_response": True},
}


class AutoLoginClient(FakeClient):
    def __init__(self, *, status: str = "logged_in", claimed: bool = True) -> None:
        super().__init__(claimed=claimed)
        self.auto_login_calls = 0
        self.auto_login_status = status

    def auto_login_mabang(self) -> dict:
        self.auto_login_calls += 1
        return {"ok": self.auto_login_status in ("logged_in", "already_logged_in"), "auto_login": {"status": self.auto_login_status, "error": ""}}


def test_worker_retries_executor_once_after_successful_auto_login(tmp_path: Path) -> None:
    client = AutoLoginClient(status="logged_in")
    calls = {"count": 0}

    def executor(plan: dict, **kwargs) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "ok": False,
                "status": "blocked",
                "error": "needs_manual_login_or_verification",
                "save_clicked": False,
                "publish_clicked": False,
            }
        return dict(_SAVED_EXECUTION)

    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path, heartbeat_interval=0, executor=executor,
    )

    result = worker.run_once()

    assert result["status"] == "saved"
    assert calls["count"] == 2
    assert client.auto_login_calls == 1
    assert client.finishes[0]["status"] == "saved"
    assert client.finishes[0]["result"]["auto_login_retry"] is True
    assert client.finishes[0]["result"]["auto_login"]["status"] == "logged_in"


def test_worker_keeps_login_block_paused_when_auto_login_reports_captcha(tmp_path: Path) -> None:
    client = AutoLoginClient(status="captcha_required")
    calls = {"count": 0}

    def executor(plan: dict, **kwargs) -> dict:
        calls["count"] += 1
        return {
            "ok": False,
            "status": "blocked",
            "error": "needs_manual_login_or_verification",
            "save_clicked": False,
            "publish_clicked": False,
        }

    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path, heartbeat_interval=0, executor=executor,
    )

    result = worker.run_once()

    assert result["status"] == "paused"
    assert calls["count"] == 1
    assert client.auto_login_calls == 1
    assert client.finishes[0]["status"] == "blocked"
    assert client.finishes[0]["error"] == "needs_manual_login_or_verification"
    assert client.finishes[0]["result"]["auto_login"]["status"] == "captcha_required"
    assert worker.paused_reason == "needs_manual_login_or_verification"


def test_worker_preflight_recovers_browser_within_cooldown(monkeypatch, tmp_path: Path) -> None:
    client = AutoLoginClient(status="no_credentials")
    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path, heartbeat_interval=0,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: False)

    first = worker.run_once()
    second = worker.run_once()

    assert first["status"] == "waiting_for_browser"
    assert second["status"] == "waiting_for_browser"
    # 冷却期内不重复触发自动拉起/登录
    assert client.auto_login_calls == 1
    assert client.claim_count == 0
    assert "no_credentials" in first["error"]


def test_worker_preflight_auto_started_browser_claims_next_job(monkeypatch, tmp_path: Path) -> None:
    state = {"reachable": False}
    client = AutoLoginClient(status="logged_in")

    def auto_login_mabang() -> dict:
        state["reachable"] = True
        client.auto_login_calls += 1
        return {"ok": True, "auto_login": {"status": "logged_in", "error": ""}}

    client.auto_login_mabang = auto_login_mabang

    def reach(**_) -> bool:
        return state["reachable"]

    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path, heartbeat_interval=0,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", reach)
    monkeypatch.setattr(worker, "_invoke_executor", lambda plan: dict(_SAVED_EXECUTION))

    result = worker.run_once()

    assert state["reachable"] is True
    assert client.auto_login_calls == 1
    assert result["status"] == "saved"
    assert client.finishes[0]["status"] == "saved"


def test_worker_startup_login_failure_does_not_claim_even_when_cdp_is_reachable(
    monkeypatch, tmp_path: Path
) -> None:
    client = AutoLoginClient(status="failed")
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: True)

    result = worker.run_once()

    assert result["status"] == "waiting_for_login"
    assert client.auto_login_calls == 1
    assert client.claim_count == 0


def test_worker_startup_login_success_claims_after_gate(monkeypatch, tmp_path: Path) -> None:
    client = AutoLoginClient(status="already_logged_in")
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: True)
    monkeypatch.setattr(worker, "_invoke_executor", lambda plan: dict(_SAVED_EXECUTION))

    result = worker.run_once()

    assert client.auto_login_calls == 1
    assert client.claim_count == 1
    assert result["status"] == "saved"


def test_worker_passes_its_slot_to_login_recovery_and_page_import_probe(
    monkeypatch, tmp_path: Path
) -> None:
    """同账号多槽位：每个 Worker 只能恢复/让路自己那个槽位。"""

    client = AutoLoginClient(status="logged_in", claimed=False)
    seen: list[tuple] = []

    def auto_login_mabang(account_id=None, slot=0) -> dict:
        seen.append(("login", account_id, slot))
        client.auto_login_calls += 1
        return {"ok": True, "auto_login": {"status": "logged_in", "error": ""}}

    client.auto_login_mabang = auto_login_mabang
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="mabang-acct-s2",
        upload_root=tmp_path,
        heartbeat_interval=0,
        enable_hybrid_execution=False,
        cdp_url="http://127.0.0.1:9323",
        account_id="acct-a",
        slot=1,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: True)

    assert worker.run_once() == {"ok": True, "status": "idle", "job_id": ""}

    assert client.page_import_slots == [1]
    assert seen == [("login", "acct-a", 1)]
    # 槽位 Worker 用的还是启动参数给它的那只浏览器
    assert worker.cdp_url == "http://127.0.0.1:9323"
