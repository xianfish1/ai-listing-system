"""按需启动 Worker：平时不跑、有任务才唤醒、空闲 10 分钟自动关闭。

现场要求（2026-10-09）：自动化 Worker 平时关闭，有任务再唤醒；执行完 10 分钟没有
新任务就自动关闭。这份测试盯住四条规则：

1. 配置默认「按需启动 + 空闲 10 分钟」，关掉开关就回到老行为；
2. 唤醒数量按该账号待执行任务数对齐（不是有任务就把所有槽位拉起来）；
3. 操作员手动停止 = 压制，看护轮不唤醒它；新任务或手动启动才解除；
4. 空闲够久 → 停进程 + 关掉它那只采集浏览器；有在途任务/可领任务/正在页面导入时不关。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.api import main, runtime_configuration, settings, storage
from services.api.worker_supervisor import MultiWorkerSupervisor, WorkerSupervisor


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")
    main._MULTI_WORKER_SUPERVISOR = None
    main._MULTI_WORKER_RUNTIME_DIR = None
    main._WORKER_SUPERVISOR = None
    main._WORKER_RUNTIME_DIR = None
    main._WORKER_IDLE_SHUTDOWN_STATE.update({"at": "", "stopped": []})
    yield
    main._MULTI_WORKER_SUPERVISOR = None
    main._MULTI_WORKER_RUNTIME_DIR = None
    main._WORKER_SUPERVISOR = None
    main._WORKER_RUNTIME_DIR = None


class FakeSupervisor:
    """只实现 main.py 用到的 Worker 生命周期接口。"""

    def __init__(self, key: str = "", *, status: str = "stopped", updated_at: str = "") -> None:
        self.key = key
        self._status = status
        self._updated_at = updated_at or _iso(datetime.now(timezone.utc))
        self._hold = ""
        self.stops = 0
        self.starts = 0

    def status(self) -> dict:
        return {
            "status": self._status,
            "pid": 4242 if self._status == "running" else None,
            "updated_at": self._updated_at,
            "manual_hold_at": self._hold,
        }

    def hold(self, *, reason: str = "") -> dict:
        self._hold = _iso(datetime.now(timezone.utc))
        return self.status()

    def release_hold(self) -> dict:
        self._hold = ""
        return self.status()

    def manual_hold_at(self) -> str:
        return self._hold

    def is_manually_held(self) -> bool:
        return bool(self._hold)

    def start(self) -> dict:
        self.starts += 1
        self._status = "running"
        self._hold = ""
        return self.status()

    def stop(self) -> dict:
        self.stops += 1
        self._status = "stopped"
        return self.status()

    def logs(self, *, tail: int | None = None) -> dict:
        return {"path": "", "lines": []}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _multi(monkeypatch: pytest.MonkeyPatch, targets: list[tuple[str, int]]) -> tuple[MultiWorkerSupervisor, dict[str, FakeSupervisor]]:
    """一个真实 MultiWorkerSupervisor + 假 Worker 进程，供唤醒/关闭逻辑调用。"""

    supervisors: dict[str, FakeSupervisor] = {}

    def build(target: dict) -> FakeSupervisor:
        supervisor = FakeSupervisor(str(target.get("key") or ""))
        supervisors[supervisor.key] = supervisor
        return supervisor

    multi = MultiWorkerSupervisor(Path("unused"), build=build)
    multi.configure([{"account_id": account, "slot": slot} for account, slot in targets])
    monkeypatch.setattr(main, "_worker_supervisors", lambda: multi)
    return multi, supervisors


def _add_job(job_id: str, *, account_id: str = "acct-a", status: str = "ready", **extra: object) -> dict:
    now = _iso(datetime.now(timezone.utc))
    job = {
        "id": job_id,
        "account_id": account_id,
        "status": status,
        "attempt_count": 0,
        "created_at": now,
        "updated_at": now,
        "lease_expires_at": "",
        **extra,
    }
    with storage.locked_store(split_keys=("automation_jobs",)) as (data, save):
        data.setdefault("automation_jobs", []).append(job)
        save(data)
    return job


def test_configuration_defaults_to_on_demand_with_ten_minute_idle_shutdown() -> None:
    config = runtime_configuration.public_runtime_configuration({})

    assert config["worker"]["start_on_demand"] is True
    assert config["worker"]["idle_shutdown_minutes"] == 10
    assert runtime_configuration.worker_start_on_demand(config) is True
    assert runtime_configuration.worker_idle_shutdown_seconds(config) == 600.0
    # 关掉开关 = 老行为（API 起来就拉 Worker、不自动关）。
    assert runtime_configuration.worker_start_on_demand({"worker": {"start_on_demand": False}}) is False
    # 0 = 不自动关闭；脏值按默认 10 分钟。
    assert runtime_configuration.worker_idle_shutdown_seconds({"worker": {"idle_shutdown_minutes": 0}}) == 0.0
    assert runtime_configuration.worker_idle_shutdown_seconds({"worker": {"idle_shutdown_minutes": "abc"}}) == 600.0


def test_saving_on_demand_settings_round_trips_and_rejects_negative_minutes() -> None:
    updated = runtime_configuration.update_runtime_configuration(
        {},
        {"worker": {"start_on_demand": False, "idle_shutdown_minutes": 25}},
    )

    assert updated["worker"]["start_on_demand"] is False
    assert updated["worker"]["idle_shutdown_minutes"] == 25
    public = runtime_configuration.public_runtime_configuration(updated)
    assert runtime_configuration.worker_start_on_demand(public) is False
    assert runtime_configuration.worker_idle_shutdown_seconds(public) == 1500.0

    with pytest.raises(ValueError):
        runtime_configuration.update_runtime_configuration({}, {"worker": {"idle_shutdown_minutes": -1}})


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 5150

    def poll(self):  # noqa: ANN201 - 永远在跑
        return None

    def terminate(self) -> None:  # pragma: no cover - 测试里不触发
        return None

    def wait(self, timeout: float | None = None) -> int:  # pragma: no cover
        return 0

    def kill(self) -> None:  # pragma: no cover
        return None


def test_manual_stop_hold_survives_restart_and_start_releases_it(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "worker-runtime"
    supervisor = WorkerSupervisor(runtime_dir, process_factory=lambda command, **kwargs: _FakeProcess())
    supervisor.hold(reason="pause")
    assert supervisor.is_manually_held() is True

    # API 重启后读同一个 state 文件：压制还在（不然看护轮会拿旧任务把它拉起来）。
    reloaded = WorkerSupervisor(runtime_dir, process_factory=lambda command, **kwargs: _FakeProcess())
    assert reloaded.is_manually_held() is True

    reloaded.start()
    assert reloaded.is_manually_held() is False
    assert WorkerSupervisor(runtime_dir).is_manually_held() is False


def test_queue_snapshot_counts_claimable_in_flight_and_latest_activity() -> None:
    old = _iso(datetime.now(timezone.utc) - timedelta(minutes=30))
    _add_job("job-ready")
    _add_job(
        "job-running",
        status="running",
        updated_at=_iso(datetime.now(timezone.utc)),
        lease_expires_at=_iso(datetime.now(timezone.utc) + timedelta(minutes=5)),
    )
    _add_job("job-done", status="saved_with_warnings", updated_at=old)

    snapshot = storage.automation_worker_queue_snapshot()

    row = snapshot["acct-a"]
    assert row["claimable"] == 1
    assert row["in_flight"] == 1
    assert row["latest_activity_at"] > old


def test_wake_plan_starts_only_as_many_slots_as_pending_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    multi, supervisors = _multi(monkeypatch, [("acct-a", 0), ("acct-a", 1)])
    monkeypatch.setattr(
        main,
        "_worker_queue_snapshot",
        lambda config=None: {"acct-a": {"claimable": 1, "in_flight": 0, "latest_activity_at": ""}},
    )

    assert main._worker_wake_plan(multi) == ["acct-a"]

    monkeypatch.setattr(
        main,
        "_worker_queue_snapshot",
        lambda config=None: {"acct-a": {"claimable": 3, "in_flight": 0, "latest_activity_at": ""}},
    )
    assert main._worker_wake_plan(multi) == ["acct-a", "acct-a#slot2"]

    # 已经在跑的槽位不重复计，也不需要重启。
    supervisors["acct-a"].start()
    assert main._worker_wake_plan(multi) == ["acct-a#slot2"]

    # 手动停的槽位不唤醒；两个都压住时一个都不起。
    supervisors["acct-a#slot2"].hold(reason="stop")
    assert main._worker_wake_plan(multi) == []
    supervisors["acct-a"].hold(reason="stop")
    assert main._worker_wake_plan(multi) == []


def test_new_jobs_release_the_manual_hold_for_their_own_account(monkeypatch: pytest.MonkeyPatch) -> None:
    multi, supervisors = _multi(monkeypatch, [("acct-a", 0), ("acct-b", 0)])
    supervisors["acct-a"].hold(reason="stop")
    supervisors["acct-b"].hold(reason="stop")

    released = main._release_worker_holds_for_jobs([{"account_id": "acct-a", "status": "ready"}])

    assert released == ["acct-a"]
    assert supervisors["acct-a"].is_manually_held() is False
    assert supervisors["acct-b"].is_manually_held() is True


def test_manual_stop_records_a_hold_on_every_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _, supervisors = _multi(monkeypatch, [("acct-a", 0), ("acct-a", 1)])

    result = main._hold_workers_after_manual_stop("stop")

    assert [row["ok"] for row in result] == [True, True]
    assert all(supervisor.is_manually_held() for supervisor in supervisors.values())


def _running_worker(monkeypatch: pytest.MonkeyPatch, *, idle_minutes: float, activity: str = "idle"):
    multi, supervisors = _multi(monkeypatch, [("acct-a", 0)])
    supervisor = supervisors["acct-a"]
    supervisor.start()
    activity_at = _iso(datetime.now(timezone.utc) - timedelta(minutes=idle_minutes))
    supervisor._updated_at = activity_at
    monkeypatch.setattr(
        main,
        "_latest_worker_activity",
        lambda current: {"activity": activity, "activity_at": activity_at, "activity_job_id": ""},
    )
    browser_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        main,
        "_stop_target_browser",
        lambda account_id, slot: browser_calls.append((account_id, slot)) or {"ok": True, "status": "stopped"},
    )
    return supervisor, browser_calls


def test_idle_worker_is_stopped_and_its_browser_closed_after_the_idle_window(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, browser_calls = _running_worker(monkeypatch, idle_minutes=11)
    monkeypatch.setattr(main, "_worker_queue_snapshot", lambda config=None: {})

    stopped = main._shutdown_idle_workers()

    assert supervisor.stops == 1
    assert browser_calls == [("acct-a", 0)]
    assert stopped and stopped[0]["account_id"] == "acct-a"
    assert main._worker_on_demand_status()["last_idle_shutdown"]["stopped"]


@pytest.mark.parametrize(
    "snapshot, activity, held, page_import",
    [
        ({"acct-a": {"claimable": 1, "in_flight": 0, "latest_activity_at": ""}}, "idle", False, False),
        ({"acct-a": {"claimable": 0, "in_flight": 1, "latest_activity_at": ""}}, "idle", False, False),
        ({}, "idle", True, False),
        ({}, "idle", False, True),
    ],
)
def test_idle_shutdown_skips_busy_held_and_importing_workers(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: dict,
    activity: str,
    held: bool,
    page_import: bool,
) -> None:
    supervisor, browser_calls = _running_worker(monkeypatch, idle_minutes=11, activity=activity)
    monkeypatch.setattr(main, "_worker_queue_snapshot", lambda config=None: snapshot)
    monkeypatch.setattr(main.storage, "active_mabang_page_import", lambda account_id, slot=0: {"id": "run"} if page_import else None)
    if held:
        supervisor.hold(reason="stop")

    assert main._shutdown_idle_workers() == []
    assert supervisor.stops == 0
    assert browser_calls == []


def test_recent_activity_keeps_the_worker_running(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, browser_calls = _running_worker(monkeypatch, idle_minutes=3)
    monkeypatch.setattr(main, "_worker_queue_snapshot", lambda config=None: {})

    assert main._shutdown_idle_workers() == []
    assert supervisor.stops == 0
    assert browser_calls == []


def test_idle_heartbeat_does_not_reset_the_idle_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """空闲的 Worker 每 30 秒重复写一条 idle 结果，activity_at 永远是「刚刚」——
    空闲起点必须看「最后一单的活动时间 / 进程启动时间」，否则永远关不掉。"""

    supervisor, browser_calls = _running_worker(monkeypatch, idle_minutes=30)
    # activity_at 是刚刚（心跳），进程启动是 30 分钟前 → 仍然该关。
    monkeypatch.setattr(
        main,
        "_latest_worker_activity",
        lambda current: {"activity": "idle", "activity_at": _iso(datetime.now(timezone.utc)), "activity_job_id": ""},
    )
    monkeypatch.setattr(main, "_worker_queue_snapshot", lambda config=None: {})

    stopped = main._shutdown_idle_workers()

    assert supervisor.stops == 1
    assert browser_calls == [("acct-a", 0)]
    assert stopped


def test_freshly_started_worker_is_not_shut_down_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, browser_calls = _running_worker(monkeypatch, idle_minutes=0)
    monkeypatch.setattr(main, "_worker_queue_snapshot", lambda config=None: {})

    assert main._shutdown_idle_workers() == []
    assert supervisor.stops == 0
    assert browser_calls == []


def test_idle_shutdown_skips_while_store_collection_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """店铺采集用的是同一只采集浏览器；采集在途时关掉它会把这一轮采集断在半路。"""

    supervisor, browser_calls = _running_worker(monkeypatch, idle_minutes=30)
    monkeypatch.setattr(main, "_worker_queue_snapshot", lambda config=None: {})
    main._store_collection_enter()
    try:
        assert main._shutdown_idle_workers() == []
    finally:
        main._store_collection_leave()

    assert supervisor.stops == 0
    assert browser_calls == []


def test_watchdog_tick_schedules_housekeeping_in_on_demand_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        main,
        "_wake_workers_for_pending_jobs",
        lambda **kwargs: calls.append("wake") or {"ok": True, "action": "no_work", "keys": []},
    )
    monkeypatch.setattr(main, "_shutdown_idle_workers", lambda: calls.append("shutdown") or [])
    monkeypatch.setattr(main, "_worker_supervisors", lambda: None)
    monkeypatch.setattr(main, "_worker_supervisor", lambda: FakeSupervisor())

    main._worker_watchdog_tick()

    assert calls == ["wake", "shutdown"]


def test_watchdog_tick_leaves_workers_alone_when_on_demand_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    storage.save_runtime_configuration({"worker": {"start_on_demand": False, "auto_restart": False}})
    calls: list[str] = []
    monkeypatch.setattr(main, "_wake_workers_for_pending_jobs", lambda **kwargs: calls.append("wake"))
    monkeypatch.setattr(main, "_shutdown_idle_workers", lambda: calls.append("shutdown"))
    monkeypatch.setattr(main, "_worker_supervisors", lambda: None)
    monkeypatch.setattr(main, "_worker_supervisor", lambda: FakeSupervisor())

    main._worker_watchdog_tick()

    assert calls == []


def test_wake_does_nothing_without_queued_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_worker_supervisors", lambda: None)
    monkeypatch.setattr(main, "_worker_supervisor", lambda: FakeSupervisor())

    assert main._wake_workers_for_pending_jobs()["action"] == "no_work"


def test_wake_starts_the_single_worker_when_a_job_is_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    _add_job("job-ready")
    supervisor = FakeSupervisor()
    actions: list[str] = []
    monkeypatch.setattr(main, "_worker_supervisors", lambda: None)
    monkeypatch.setattr(main, "_worker_supervisor", lambda: supervisor)
    monkeypatch.setattr(main, "_run_worker_action", lambda action: actions.append(action) or {"status": "running"})

    outcome = main._wake_workers_for_pending_jobs()

    assert actions == ["start"]
    assert outcome["action"] == "start"
