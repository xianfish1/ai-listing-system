from pathlib import Path

import pytest

from services.api import main, settings, storage, worker_supervisor
from services.api.worker_supervisor import WorkerSupervisor, _file_lock_is_held, config_requires_restart


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """这些用例会走 _run_worker_action，绝不能读到生产 store.json。

    生产库里一旦出现第二个马帮账号，_worker_supervisors() 就会切到「按账号并行」，
    单账号的登录门禁用例就不适用了 —— 用例不该被生产数据的条数左右。
    """

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")


class FakeProcess:
    _next_pid = 1000

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_permission_denied_open_means_worker_lock_is_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    lock_path = tmp_path / "automation-worker.lock"
    lock_path.write_bytes(b"047528")
    original_open = Path.open

    def denied_open(path, *args, **kwargs):
        if path == lock_path:
            raise PermissionError("locked by another process")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)

    assert _file_lock_is_held(lock_path) is True


def test_windows_pid_check_uses_a_query_handle() -> None:
    closed = []

    class FakeKernel32:
        def OpenProcess(self, access, inherit_handle, pid):
            assert access == 0x1000
            assert inherit_handle is False
            return 42 if pid == 47528 else 0

        def CloseHandle(self, handle):
            closed.append(handle)

    assert worker_supervisor._windows_pid_is_running(47528, kernel32=FakeKernel32()) is True
    assert worker_supervisor._windows_pid_is_running(123, kernel32=FakeKernel32()) is False
    assert closed == [42]


def test_start_pause_resume_stop_and_status(tmp_path: Path):
    created = []

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        created.append(process)
        return process

    supervisor = WorkerSupervisor(tmp_path, process_factory=factory)
    assert supervisor.status()["status"] == "stopped"

    started = supervisor.start()
    assert started["status"] == "running"
    assert started["pid"] == created[0].pid
    assert supervisor.pause()["status"] == "paused"
    assert created[0].terminated is True
    assert supervisor.resume()["status"] == "running"
    assert len(created) == 2
    assert supervisor.stop()["status"] == "stopped"


def test_start_worker_process_does_not_open_a_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    created = []

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(
        worker_supervisor,
        "no_console_window_kwargs",
        lambda: {"creationflags": 12345},
        raising=False,
    )
    supervisor = WorkerSupervisor(tmp_path, process_factory=factory)

    supervisor.start()

    assert created[0].kwargs["creationflags"] == 12345
    supervisor.stop()


def test_start_is_idempotent_and_single_instance(tmp_path: Path):
    supervisor = WorkerSupervisor(tmp_path, process_factory=lambda command, **kwargs: FakeProcess(command, **kwargs))
    first = supervisor.start()
    second = supervisor.start()
    assert second["pid"] == first["pid"]

    other = WorkerSupervisor(tmp_path, process_factory=lambda command, **kwargs: FakeProcess(command, **kwargs))
    with pytest.raises(RuntimeError, match="already running"):
        other.start()
    supervisor.stop()


def test_start_adopts_a_live_persisted_worker_after_api_restart(tmp_path: Path):
    (tmp_path / "worker-state.json").write_text(
        '{"status": "running", "pid": 47528, "last_error": ""}',
        encoding="utf-8",
    )
    created = []
    supervisor = WorkerSupervisor(
        tmp_path,
        process_factory=lambda command, **kwargs: created.append(FakeProcess(command, **kwargs)),
        pid_is_running=lambda pid: pid == 47528,
    )

    state = supervisor.start()

    assert state["status"] == "running"
    assert state["pid"] == 47528
    assert created == []


def test_start_recovers_a_live_worker_from_its_pid_file(tmp_path: Path):
    worker_pid_path = tmp_path / "automation-worker.lock"
    worker_pid_path.write_text("047528", encoding="ascii")
    (tmp_path / "worker-state.json").write_text(
        '{"status": "failed", "pid": null, "last_error": "worker exited with exit code 2"}',
        encoding="utf-8",
    )
    created = []
    supervisor = WorkerSupervisor(
        tmp_path,
        worker_pid_path=worker_pid_path,
        worker_lock_is_held=lambda path: path == worker_pid_path,
        process_factory=lambda command, **kwargs: created.append(FakeProcess(command, **kwargs)),
        pid_is_running=lambda pid: pid == 47528,
    )

    state = supervisor.start()

    assert state["status"] == "running"
    assert state["pid"] == 47528
    assert state["last_error"] == ""
    assert state["adopted"] is True
    assert created == []


def test_windows_terminate_process_uses_a_terminate_handle() -> None:
    closed = []
    terminated = []

    class FakeKernel32:
        def OpenProcess(self, access, inherit_handle, pid):
            assert access == 0x0001
            assert inherit_handle is False
            return 42 if pid == 47528 else 0

        def TerminateProcess(self, handle, code):
            terminated.append((handle, code))
            return 1

        def CloseHandle(self, handle):
            closed.append(handle)

    worker_supervisor._windows_terminate_process(47528, kernel32=FakeKernel32())

    assert terminated == [(42, 1)]
    assert closed == [42]

    with pytest.raises(OSError):
        worker_supervisor._windows_terminate_process(123, kernel32=FakeKernel32())


def _orphaned_worker_supervisor(tmp_path: Path, created: list, stopped: list):
    """A supervisor whose pid file names a live worker it never spawned.

    This is what the API sees after it is restarted while a worker keeps
    running: the Popen handle is gone, but the pid lock is still held.
    """

    worker_pid_path = tmp_path / "automation-worker.lock"
    worker_pid_path.write_text("047528", encoding="ascii")
    (tmp_path / "worker-state.json").write_text(
        '{"status": "running", "pid": 47528, "last_error": "", "adopted": true}',
        encoding="utf-8",
    )
    alive = {"value": True}

    def terminate_pid(pid):
        stopped.append(pid)
        alive["value"] = False

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        created.append(process)
        return process

    supervisor = WorkerSupervisor(
        tmp_path,
        worker_pid_path=worker_pid_path,
        worker_lock_is_held=lambda path: path == worker_pid_path and alive["value"],
        pid_is_running=lambda pid: pid == 47528 and alive["value"],
        terminate_pid=terminate_pid,
        process_factory=factory,
    )
    return supervisor, alive


def test_stop_kills_a_worker_that_outlived_the_api_process(tmp_path: Path):
    created: list = []
    stopped: list = []
    supervisor, _ = _orphaned_worker_supervisor(tmp_path, created, stopped)

    assert supervisor.stop()["status"] == "stopped"

    assert stopped == [47528]
    assert created == []


def test_restart_replaces_an_orphaned_worker_instead_of_adopting_it(tmp_path: Path):
    created: list = []
    stopped: list = []
    supervisor, _ = _orphaned_worker_supervisor(tmp_path, created, stopped)

    state = supervisor.restart()

    assert stopped == [47528]
    assert state["status"] == "running"
    assert state["adopted"] is False
    assert len(created) == 1
    assert state["pid"] == created[0].pid
    supervisor.stop()


def test_logs_returns_tail_and_records_process_error(tmp_path: Path):
    log = tmp_path / "worker.log"
    log.write_text("line 1\nline 2\n", encoding="utf-8")
    supervisor = WorkerSupervisor(tmp_path, process_factory=lambda command, **kwargs: FakeProcess(command, **kwargs))
    supervisor.start()
    supervisor.mark_error("browser disconnected")
    assert supervisor.status()["last_error"] == "browser disconnected"
    assert supervisor.logs()["lines"] == ["line 1", "line 2"]


def test_process_exit_is_reflected_as_error(tmp_path: Path):
    processes = []

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    supervisor = WorkerSupervisor(tmp_path, process_factory=factory)
    supervisor.start()
    processes[0].returncode = 3
    state = supervisor.status()
    assert state["status"] == "failed"
    assert "exit code 3" in state["last_error"]


def test_process_exit_releases_runtime_resources_for_restart(tmp_path: Path):
    processes = []

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    supervisor = WorkerSupervisor(tmp_path, process_factory=factory)
    supervisor.start()
    processes[0].returncode = 3

    assert supervisor.status()["status"] == "failed"
    assert supervisor._log_handle is None
    assert supervisor._lock.handle is None
    restarted = supervisor.start()

    assert restarted["status"] == "running"
    assert restarted["pid"] == processes[1].pid
    supervisor.stop()


def test_start_failure_is_recorded_and_can_be_retried(tmp_path: Path):
    attempts = 0

    def factory(command, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("spawn denied")
        return FakeProcess(command, **kwargs)

    supervisor = WorkerSupervisor(tmp_path, process_factory=factory)

    with pytest.raises(RuntimeError, match="Worker 启动失败：spawn denied"):
        supervisor.start()

    failed = supervisor.status()
    assert failed["status"] == "failed"
    assert failed["pid"] is None
    assert failed["last_error"] == "Worker 启动失败：spawn denied"

    restarted = supervisor.start()
    assert restarted["status"] == "running"
    supervisor.stop()


def test_successful_start_clears_a_previous_heal_suspension(tmp_path: Path):
    (tmp_path / "worker-state.json").write_text(
        '{"status": "failed", "pid": null, "last_error": "crash loop", "heal_suspended": true}',
        encoding="utf-8",
    )
    supervisor = WorkerSupervisor(
        tmp_path,
        process_factory=lambda command, **kwargs: FakeProcess(command, **kwargs),
    )

    state = supervisor.start()

    assert state["status"] == "running"
    assert state["heal_suspended"] is False
    supervisor.stop()


def test_status_marks_a_stale_persisted_worker_pid_as_failed(tmp_path: Path):
    (tmp_path / "worker-state.json").write_text(
        '{"status": "running", "pid": 38232, "last_error": "", "updated_at": "2026-07-28T07:21:28Z"}',
        encoding="utf-8",
    )

    supervisor = WorkerSupervisor(
        tmp_path,
        process_factory=lambda command, **kwargs: FakeProcess(command, **kwargs),
        pid_is_running=lambda pid: False,
    )

    state = supervisor.status()
    assert state["status"] == "failed"
    assert state["pid"] is None
    assert "no longer running" in state["last_error"]


def test_config_restart_required_only_for_runtime_keys():
    current = {"api_url": "http://a", "poll_seconds": 3, "worker_id": "one", "enabled": True}
    assert config_requires_restart(current, {**current, "poll_seconds": 5}) is True
    assert config_requires_restart(current, {**current, "enabled": False}) is False
    assert config_requires_restart(current, {**current, "worker_id": "two"}) is True


def test_restart_rebuilds_worker_with_latest_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    saved = storage.save_runtime_configuration({"worker": {"cdp_url": "http://127.0.0.1:9333"}})
    assert saved["worker"]["restart_required"] is True

    stopped = []
    created = []

    class ExistingSupervisor:
        def stop(self):
            stopped.append(True)

    class ReplacementSupervisor:
        def __init__(self, runtime_dir, *, command, worker_pid_path, working_directory):
            self.command = command
            self.worker_pid_path = worker_pid_path
            created.append(self)

        def restart(self):
            stopped.append("replacement")
            return {"status": "running", "pid": 1234, "last_error": ""}

    monkeypatch.setattr(main, "_WORKER_SUPERVISOR", ExistingSupervisor())
    monkeypatch.setattr(main, "_WORKER_RUNTIME_DIR", tmp_path / "worker-runtime")
    monkeypatch.setattr(main, "WorkerSupervisor", ReplacementSupervisor)

    result = main._restart_worker_supervisor()

    assert result["status"] == "running"
    assert stopped == [True, "replacement"]
    assert "http://127.0.0.1:9333" in created[0].command
    assert storage.get_runtime_configuration()["worker"]["restart_required"] is False


def test_start_worker_with_login_gate_prepares_browser_before_start(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []

    class Supervisor:
        def start(self):
            events.append("worker_start")
            return {"status": "running", "pid": 1234}

    monkeypatch.setattr(
        main,
        "_prepare_worker_browser_login",
        lambda: events.append("login") or {
            "ok": True,
            "browser_mode": "headless",
            "auto_login": {"status": "logged_in", "error": ""},
        },
    )
    monkeypatch.setattr(main, "_worker_supervisor", lambda: Supervisor())

    result = main._run_worker_action("start")

    assert events == ["login", "worker_start"]
    assert result["browser_mode"] == "headless"
    assert result["auto_login"]["status"] == "logged_in"


def test_start_worker_with_login_gate_does_not_start_when_login_fails(monkeypatch: pytest.MonkeyPatch):
    started: list[bool] = []

    class Supervisor:
        def start(self):
            started.append(True)
            return {"status": "running"}

    monkeypatch.setattr(
        main,
        "_prepare_worker_browser_login",
        lambda: {
            "ok": False,
            "browser_mode": "headless",
            "auto_login": {"status": "failed", "error": "login_did_not_complete_check_credentials"},
        },
    )
    monkeypatch.setattr(main, "_worker_supervisor", lambda: Supervisor())

    with pytest.raises(RuntimeError, match="马帮登录未确认"):
        main._run_worker_action("start")

    assert started == []


def test_start_worker_does_not_touch_browser_when_worker_is_already_active(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []

    class Supervisor:
        def status(self):
            return {"status": "running", "pid": 1234}

        def start(self):
            events.append("worker_start")
            return {"status": "running", "pid": 1234}

    supervisor = Supervisor()
    monkeypatch.setattr(main, "_worker_supervisor", lambda: supervisor)
    monkeypatch.setattr(main, "_latest_worker_activity", lambda current: {"activity": "failed"})
    monkeypatch.setattr(
        main,
        "_prepare_worker_browser_login",
        lambda: events.append("login") or {"ok": True},
    )

    result = main._run_worker_action("start")

    assert events == []
    assert result["status"] == "running"
    assert result["activity"] == "failed"
    assert result["already_running"] is True


def test_auto_heal_restarts_a_failed_worker(tmp_path: Path):
    processes = []

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    supervisor = WorkerSupervisor(tmp_path, process_factory=factory)
    supervisor.start()
    processes[0].returncode = 1  # simulate the crash

    assert supervisor.status()["status"] == "failed"
    healed = supervisor.auto_heal()

    assert healed["status"] == "running"
    assert healed["pid"] == processes[1].pid
    assert healed["heal_count"] == 1
    assert healed["last_heal_at"]
    supervisor.stop()


def test_auto_heal_ignores_intentionally_stopped_worker(tmp_path: Path):
    supervisor = WorkerSupervisor(tmp_path, process_factory=lambda command, **kwargs: FakeProcess(command, **kwargs))
    supervisor.start()
    supervisor.stop()

    result = supervisor.auto_heal()
    assert result["status"] == "stopped"
    assert result.get("heal_count") in (None, 0)


def test_auto_heal_suspends_after_crash_loop(tmp_path: Path):
    clock = {"now": 0.0}
    processes = []

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    supervisor = WorkerSupervisor(
        tmp_path,
        process_factory=factory,
        heal_window_seconds=600.0,
        max_heals_in_window=3,
        monotonic=lambda: clock["now"],
    )
    supervisor.start()

    # Every launch dies immediately; heal at most max_heals_in_window times.
    for _ in range(5):
        processes[-1].returncode = 1
        clock["now"] += 10.0
        supervisor.auto_heal()

    suspended = supervisor.status()
    assert suspended["status"] == "failed"
    assert suspended["heal_suspended"] is True
    assert "暂停自愈" in suspended["last_error"]
    # 3 heals attempted → 3 restart processes beyond the original.
    assert len(processes) == 1 + 3


def test_auto_heal_window_rolls_forward(tmp_path: Path):
    clock = {"now": 0.0}
    processes = []

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    supervisor = WorkerSupervisor(
        tmp_path,
        process_factory=factory,
        heal_window_seconds=600.0,
        max_heals_in_window=3,
        monotonic=lambda: clock["now"],
    )
    supervisor.start()

    for _ in range(3):
        processes[-1].returncode = 1
        clock["now"] += 10.0
        supervisor.auto_heal()

    # Jump past the window so earlier heals age out, then crash again.
    clock["now"] += 700.0
    processes[-1].returncode = 1
    healed = supervisor.auto_heal()
    assert healed["status"] == "running"
    supervisor.stop()


def test_watchdog_tick_respects_auto_restart_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")

    calls = []

    class FakeSupervisor:
        def status(self):
            return {"status": "failed"}

        def auto_heal(self):
            calls.append(True)
            return {"status": "running"}

    monkeypatch.setattr(main, "_worker_supervisor", lambda: FakeSupervisor())

    storage.save_runtime_configuration({"worker": {"auto_restart": False}})
    main._worker_watchdog_tick()
    assert calls == []  # toggle off → no heal

    storage.save_runtime_configuration({"worker": {"auto_restart": True}})
    main._worker_watchdog_tick()
    assert calls == [True]  # toggle on → heal once


def test_start_workers_retires_the_legacy_global_worker_when_accounts_own_the_browsers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多账号部署下不留旧的全局 Worker。

    它没有 --account-id：领取不按账号过滤（能把 B 账号的任务跑进 A 账号的登录态），
    而且它的浏览器恢复会和账号 Worker 抢同一只浏览器（现场 9223 被反复杀/起，
    把操作员的页面导入窗口收走）。
    """

    events: list[str] = []

    class Multi:
        def run(self, action: str) -> list[dict]:
            events.append(f"multi:{action}")
            return [{"account_id": "acct-a", "status": "running"}]

    class Legacy:
        def status(self) -> dict:
            return {"status": "running", "pid": 50232}

        def stop(self) -> dict:
            events.append("legacy:stop")
            return {"status": "stopped"}

    monkeypatch.setattr(main, "_worker_supervisors", lambda: Multi())
    monkeypatch.setattr(main, "_worker_supervisor", lambda: Legacy())

    result = main._start_workers()

    assert events == ["legacy:stop", "multi:start"]
    assert result == {"ok": True, "workers": [{"account_id": "acct-a", "status": "running"}]}


def test_start_workers_keeps_the_single_worker_when_no_account_partition(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Legacy:
        def start(self) -> dict:
            events.append("legacy:start")
            return {"status": "running", "pid": 1}

    monkeypatch.setattr(main, "_worker_supervisors", lambda: None)
    monkeypatch.setattr(main, "_worker_supervisor", lambda: Legacy())

    assert main._start_workers() == {"status": "running", "pid": 1}
    assert events == ["legacy:start"]


def test_watchdog_heals_account_workers_and_never_revives_the_legacy_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    events: list[str] = []

    class Multi:
        def auto_heal_all(self) -> list[dict]:
            events.append("heal_all")
            return [{"account_id": "acct-a", "status": "running"}]

    class Legacy:
        def status(self) -> dict:
            events.append("legacy:status")
            return {"status": "failed"}

        def auto_heal(self) -> dict:
            events.append("legacy:heal")
            return {"status": "running"}

    monkeypatch.setattr(main, "_worker_supervisors", lambda: Multi())
    monkeypatch.setattr(main, "_worker_supervisor", lambda: Legacy())

    storage.save_runtime_configuration({"worker": {"auto_restart": False}})
    main._worker_watchdog_tick()
    assert events == []

    storage.save_runtime_configuration({"worker": {"auto_restart": True}})
    main._worker_watchdog_tick()
    assert events == ["heal_all"]


def test_worker_status_attaches_each_account_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    """多账号模式下 Worker 面板要能看到「页面导入中 · 已让路」，不能只有进程存活。"""

    class Supervisor:
        def __init__(self, lines: list[str]) -> None:
            self._lines = lines

        def logs(self, *, tail: int | None = None) -> dict:
            return {"path": "worker.log", "lines": self._lines}

    account_a = Supervisor(['AUTOMATION_WORKER_RESULT {"status":"waiting_for_page_import","error":"页面导入进行中","at":"2026-10-05T05:29:27Z"}'])
    account_b = Supervisor(['AUTOMATION_WORKER_RESULT {"status":"idle","at":"2026-10-05T05:30:40Z"}'])

    class Multi:
        def __init__(self) -> None:
            self._by_account = {"acct-a": account_a, "acct-b": account_b}

        def get(self, account_id: str):
            return self._by_account.get(account_id)

        def summarize(self) -> dict:
            return {
                "status": "running",
                "pid": None,
                "last_error": "",
                "workers": [
                    {"account_id": "acct-a", "status": "running", "pid": 1},
                    {"account_id": "acct-b", "status": "running", "pid": 2},
                ],
            }

    status = main._attach_account_worker_activities(Multi(), Multi().summarize())

    by_account = {row["account_id"]: row for row in status["workers"]}
    assert by_account["acct-a"]["activity"] == "waiting_for_page_import"
    assert by_account["acct-b"]["activity"] == "idle"
    # 顶层沿用单账号时代的形状：一个账号在让路时不能被另一个账号的 idle 盖过去
    assert status["activity"] == "waiting_for_page_import"
    assert status["activity_at"] == "2026-10-05T05:29:27Z"


def test_latest_worker_activity_uses_payload_time_when_log_writes_are_out_of_order() -> None:
    class FakeSupervisor:
        def logs(self, *, tail):
            assert tail == 40
            return {
                "lines": [
                    'AUTOMATION_WORKER_RESULT {"status":"idle","at":"2026-08-20T09:42:01Z"}',
                    'AUTOMATION_WORKER_RESULT {"status":"already_running","at":"2026-08-20T09:39:39Z"}',
                ]
            }

    activity = main._latest_worker_activity(FakeSupervisor())

    assert activity["activity"] == "idle"
    assert activity["activity_at"] == "2026-08-20T09:42:01Z"

