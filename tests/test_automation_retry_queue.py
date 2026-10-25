"""自动化页「再次尝试」的串行队列 + 后台唤醒。

现场问题：连点多个「再次尝试」时，每个请求都同步唤醒 Worker（起进程 + 准备采集浏览器 +
探测马帮登录，几秒到几十秒），请求线程和浏览器登录互相抢，界面就卡。

覆盖：
- 前端点击只入队，队列泵一次只发一条 self-heal，连点期间继续按序处理，跑完只刷新一次；
- 排队/重排期间按钮显示「排队中… / 重排中…」并置灰；
- 后端 self-heal 先原地重排，再把唤醒 Worker 丢进后台队列（响应立即返回）；
- 唤醒队列合并连点，一轮只准备一次浏览器；
- 已经在跑的 Worker 恢复时不再准备浏览器（否则会顶掉在途页面）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.api import main, settings, storage
from tests.test_base_product_lifecycle import (
    _make_base,
    _make_template,
    _prepare_copy_for_execution,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _failed_job(tmp_path: Path) -> dict:
    base = _make_base(tmp_path, folder_name="AL0710 Retry Queue")
    template = _make_template("RetryQueue")
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    _prepare_copy_for_execution(copy["id"], base)
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    claim = storage.claim_next_automation_job("worker-test", lease_seconds=120, max_attempts=3)
    assert claim["job"] and claim["job"]["id"] == job["id"]
    storage.finish_automation_job(
        job["id"],
        worker_id="worker-test",
        lease_token=claim["lease_token"],
        status="failed",
        result={"ok": False, "status": "failed", "error": "field_not_editable"},
        error="field_not_editable",
    )
    return next(item for item in storage.list_automation_jobs() if item["id"] == job["id"])


def test_retry_clicks_are_serialized_through_a_queue() -> None:
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    assert "automationRetryQueue" in script
    assert "automationRetryActiveId" in script
    assert "enqueueAutomationRetry" in script
    assert "runAutomationRetryQueue" in script
    # 重复点击同一条任务只入队一次。
    assert "state.automationRetryActiveId === id || state.automationRetryQueue.includes(id)" in script

    # 点击处理只入队，不再直接 await 单条 self-heal。
    handler_start = script.index('const selfHealButton = target.closest("[data-self-heal-automation-job]")')
    handler = script[handler_start: script.index("const nudgeButton", handler_start)]
    assert "enqueueAutomationRetry(" in handler
    assert "await selfHealAutomationJob(" not in handler

    # 队列泵一次只发一条：await 在 while (queue.length) 循环里，前一条结束后才取下一条。
    pump = script[
        script.index("async function runAutomationRetryQueue()"): script.index("// 单条重排请求")
    ]
    assert "while (state.automationRetryQueue.length)" in pump
    assert "await requestSelfHealAutomationJob(jobId)" in pump
    assert pump.index("await requestSelfHealAutomationJob(jobId)") > pump.index(
        "while (state.automationRetryQueue.length)"
    )
    # 跑完统一刷新一次，而不是每处理一条就整表重绘。
    assert "await loadAutomationJobs().catch(() => {})" in pump

    # 排队/重排状态在按钮上可见且置灰。
    assert 'retryState === "processing" ? "重排中…"' in script
    assert '"排队中…"' in script
    assert "refreshAutomationRetryButtons" in script
    assert "#automation .is-queued" in styles


def test_self_heal_responds_without_touching_the_worker_synchronously(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _failed_job(tmp_path)
    scheduled: list[dict] = []
    monkeypatch.setattr(main, "_schedule_retry_worker_wake", lambda item: scheduled.append(dict(item)))
    # 同步路径如果真的去碰 Worker，这里会直接炸掉测试。
    monkeypatch.setattr(
        main,
        "_worker_supervisor",
        lambda: (_ for _ in ()).throw(AssertionError("self-heal 不应再同步启动/恢复 Worker")),
    )

    payload = main.ApiHandler._self_heal_automation_job(None, job["id"])

    assert payload["ok"] is True
    assert payload["job_status"] == "ready"
    assert payload["worker_start"] == {"ok": None, "status": "starting", "count": 1}
    assert [step["step"] for step in payload["steps"]] == ["requeue_job", "worker_wake"]
    assert payload["steps"][1]["background"] is True
    assert len(scheduled) == 1 and scheduled[0]["id"] == job["id"]


def test_retry_wake_queue_coalesces_rapid_clicks_and_drops_heavy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    class FakeThreading:
        class Thread:
            def __init__(self, target=None, name=None, daemon=None, args=()):
                started.append(name)

            def start(self) -> None:
                pass

    monkeypatch.setattr(main, "threading", FakeThreading)
    monkeypatch.setattr(main, "_RETRY_WAKE_JOBS", [])
    monkeypatch.setattr(main, "_RETRY_WAKE_RUNNING", False)

    job = {
        "id": "job-1",
        "status": "ready",
        "account_id": "acc",
        "listing_copy_id": "copy",
        "execution_snapshot": {"browser_inputs": {"huge": "x"}},  # 不能挂在队列里
    }
    main._schedule_retry_worker_wake(job)
    main._schedule_retry_worker_wake({**job, "id": "job-2"})

    assert started == ["retry-worker-wake"], "已经在排队的唤醒不该再起第二个后台线程"
    assert [item["id"] for item in main._RETRY_WAKE_JOBS] == ["job-1", "job-2"]
    assert all("execution_snapshot" not in item for item in main._RETRY_WAKE_JOBS)


def test_retry_wake_drain_hands_one_batch_to_the_wake_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches: list[list[dict]] = []
    monkeypatch.setattr(
        main,
        "_RETRY_WAKE_JOBS",
        [{"id": "job-1", "status": "ready"}, {"id": "job-2", "status": "ready"}],
    )
    monkeypatch.setattr(main, "_RETRY_WAKE_RUNNING", True)
    monkeypatch.setattr(
        main,
        "_wake_worker_for_created_jobs_background",
        lambda jobs: batches.append(list(jobs)),
    )

    main._drain_retry_worker_wake()

    assert len(batches) == 1, "一轮唤醒只准备一次浏览器"
    assert [item["id"] for item in batches[0]] == ["job-1", "job-2"]
    assert main._RETRY_WAKE_JOBS == []
    assert main._RETRY_WAKE_RUNNING is False


def test_resume_skips_browser_prep_for_a_running_single_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSupervisor:
        def __init__(self) -> None:
            self.resumed = 0

        def status(self) -> dict:
            return {"status": "running", "pid": 4242}

        def resume(self) -> dict:
            self.resumed += 1
            return self.status()

    supervisor = FakeSupervisor()
    monkeypatch.setattr(main, "_worker_supervisors", lambda: None)
    monkeypatch.setattr(main, "_worker_supervisor", lambda: supervisor)

    def _boom(*args, **kwargs):
        raise AssertionError("已经在跑的 Worker 恢复时不应再准备浏览器")

    monkeypatch.setattr(main, "_prepare_worker_browser_login", _boom)

    outcome = main._run_worker_action("resume")

    assert outcome["already_running"] is True
    assert supervisor.resumed == 0, "resume 对运行中的进程是空操作，别再陪跑一次"


def test_resume_skips_browser_prep_for_running_multi_worker_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSupervisor:
        def __init__(self) -> None:
            self.resumed = 0

        def status(self) -> dict:
            return {"status": "running", "pid": 4242}

        def resume(self) -> dict:
            self.resumed += 1
            return self.status()

    supervisor = FakeSupervisor()

    class FakeMulti:
        def targets(self) -> list[dict]:
            return [{"key": "acc#slot1", "account_id": "acc", "slot": 0}]

        def get(self, key: str):
            return supervisor

    def _boom(*args, **kwargs):
        raise AssertionError("已经在跑的目标恢复时不应再准备浏览器")

    monkeypatch.setattr(main, "_prepare_worker_browser_login", _boom)

    outcome = main._run_account_worker_action(FakeMulti(), "resume")

    assert outcome["ok"] is True
    assert outcome["workers"][0]["ok"] is True
    assert supervisor.resumed == 1
