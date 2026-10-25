"""同账号多槽位并行：槽位运行时、目标展开、Worker 命令与槽位回收。

一个马帮账号在网上可以有十几个店铺，但老架构「一个账号 = 一只浏览器 = 一个 Worker」
只能一次跑一单。槽位（slot）就是给同一个账号再开一只浏览器：独立 CDP 端口 + 独立
Chrome Profile（独立 cookie jar），配套一个独立 Worker 进程。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.api import main, runtime_configuration, settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")
    main._MULTI_WORKER_SUPERVISOR = None
    main._MULTI_WORKER_RUNTIME_DIR = None
    yield
    main._MULTI_WORKER_SUPERVISOR = None
    main._MULTI_WORKER_RUNTIME_DIR = None


def _account(login: str = "acct-a", *, port: int | None = None) -> dict:
    payload = {"name": login, "login_account": login, "login_password": "pw"}
    if port is not None:
        payload["cdp_port"] = port
    return storage.create_mabang_account(payload)


def _give_store(account_id: str, *, name: str = "店铺A") -> None:
    storage.upsert_mabang_store_options(
        [
            {
                "account_id": account_id,
                "platform": "shopee",
                "store_name": name,
                "mabang_store_id": f"mb-{name}",
            }
        ],
        account_id=account_id,
    )


def test_slot_runtime_gets_its_own_port_and_profile_dir() -> None:
    account = _account("acct-a", port=9223)

    base = storage.resolve_mabang_account_runtime(account["id"])
    second = storage.resolve_mabang_account_runtime(account["id"], slot=1)

    assert base["slot"] == 0
    assert base["cdp_url"] == "http://127.0.0.1:9223"
    # 槽位 2：端口挪一个百位段，Profile 目录另起一份（cookie 隔离 = 各自要登录一次）
    assert second["slot"] == 1
    assert second["cdp_url"] == "http://127.0.0.1:9323"
    assert second["profile_dir"] != base["profile_dir"]
    assert second["profile_dir"].endswith("-slot2")
    # 同一个账号：登录账号不变，说明还是同一个马帮账号在跑
    assert second["login_account"] == base["login_account"] == "acct-a"


def test_slot_runtime_never_collides_with_the_next_account() -> None:
    first = _account("acct-a", port=9223)
    second_account = _account("acct-b", port=9224)

    ports = {
        storage.resolve_mabang_account_runtime(account["id"], slot=slot)["cdp_url"]
        for account in (first, second_account)
        for slot in range(2)
    }

    # 账号 9223/9224 + 各自槽位 2 的 9323/9324：四个目标四个端口，互不撞车
    assert ports == {
        "http://127.0.0.1:9223",
        "http://127.0.0.1:9224",
        "http://127.0.0.1:9323",
        "http://127.0.0.1:9324",
    }


def test_slots_per_account_is_validated_and_clamped() -> None:
    assert runtime_configuration.worker_slots_per_account({}) == 1
    assert runtime_configuration.worker_slots_per_account({"worker": {"slots_per_account": "2"}}) == 2
    # 脏值按 1 处理；超出上限按上限收，绝不因为一个手填数字把浏览器数量放飞
    assert runtime_configuration.worker_slots_per_account({"worker": {"slots_per_account": "x"}}) == 1
    assert runtime_configuration.worker_slots_per_account({"worker": {"slots_per_account": 99}}) == 4

    saved = storage.save_runtime_configuration({"worker": {"slots_per_account": 2}})
    assert saved["worker"]["slots_per_account"] == 2
    with pytest.raises(ValueError):
        storage.save_runtime_configuration({"worker": {"slots_per_account": 0}})
    with pytest.raises(ValueError):
        storage.save_runtime_configuration({"worker": {"slots_per_account": 9}})


def test_worker_targets_expand_accounts_by_slots() -> None:
    account = _account("acct-a", port=9223)
    _give_store(account["id"])

    assert main._worker_targets(slots=1) == [{"account_id": account["id"], "slot": 0}]
    assert main._worker_targets(slots=2) == [
        {"account_id": account["id"], "slot": 0},
        {"account_id": account["id"], "slot": 1},
    ]


def test_account_without_stores_never_gets_extra_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有店铺的账号不产生上架任务：只留它 1 号槽位那只浏览器，不白开一只。"""

    with_stores = _account("acct-a", port=9223)
    without_stores = _account("acct-b", port=9224)
    _give_store(with_stores["id"])

    assert main._worker_targets(slots=2) == [
        {"account_id": with_stores["id"], "slot": 0},
        {"account_id": with_stores["id"], "slot": 1},
        {"account_id": without_stores["id"], "slot": 0},
    ]

    # 店铺目录读不出来时 fail-open：宁可多开一只，也不悄悄把并行度降回 1
    def boom(**_: object) -> list:
        raise RuntimeError("store catalog unavailable")

    monkeypatch.setattr(storage, "list_mabang_store_options", boom)
    assert main._worker_targets(slots=2) == [
        {"account_id": with_stores["id"], "slot": 0},
        {"account_id": with_stores["id"], "slot": 1},
        {"account_id": without_stores["id"], "slot": 0},
        {"account_id": without_stores["id"], "slot": 1},
    ]


def test_single_account_with_two_slots_builds_two_workers() -> None:
    """一个账号 + 2 槽位就该有两个 Worker（这正是 13 个店铺同一账号的并行出口）。"""

    account = _account("acct-a", port=9223)
    _give_store(account["id"])
    storage.save_runtime_configuration({"worker": {"slots_per_account": 2}})

    multi = main._worker_supervisors()

    assert multi is not None
    targets = multi.targets()
    assert targets == [
        {"key": account["id"], "account_id": account["id"], "slot": 0},
        {"key": f"{account['id']}#slot2", "account_id": account["id"], "slot": 1},
    ]
    commands = {target["key"]: multi.get(target["key"]).command for target in targets}
    first = commands[account["id"]]
    second = commands[f"{account['id']}#slot2"]
    assert second[second.index("--worker-slot") + 1] == "1"
    assert first[first.index("--worker-slot") + 1] == "0"
    assert second[second.index("--cdp-url") + 1] == "http://127.0.0.1:9323"
    # 同账号两个 Worker 不能共用待上报文件与进程锁
    assert second[second.index("--pending-completion-file") + 1].endswith("pending-completion-slot2.json")
    assert first[first.index("--pending-completion-file") + 1].endswith("pending-completion.json")
    assert second[second.index("--lock-file") + 1] != first[first.index("--lock-file") + 1]


def test_single_account_with_one_slot_keeps_the_legacy_single_worker() -> None:
    _account("acct-a", port=9223)

    assert main._worker_supervisors() is None


def test_page_import_standoff_is_scoped_to_its_slot() -> None:
    """页面导入只占 1 号槽位的窗口：2 号槽位的 Worker 不该被它拖住。"""

    account = _account("acct-a", port=9223)
    storage.create_mabang_page_import(
        {"platform": "shopee", "country": "PH", "account_id": account["id"], "slot": 0}
    )

    assert storage.active_mabang_page_import(account["id"], slot=0) is not None
    assert storage.active_mabang_page_import(account["id"], slot=1) is None
    # 不传槽位时保持老语义：按账号整体判断
    assert storage.active_mabang_page_import(account["id"]) is not None


def test_retired_slots_have_their_browser_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    account = _account("acct-a", port=9223)
    _give_store(account["id"])
    stopped: list[dict] = []

    def fake_stop(cdp_url: str, *, profile_dir: Path) -> dict:
        stopped.append({"cdp_url": cdp_url, "profile_dir": str(profile_dir)})
        return {"ok": True, "status": "stopped", "port": 9323, "stopped_pids": [4321]}

    monkeypatch.setattr(main, "stop_mabang_browser", fake_stop)

    rows = main._retire_unused_slot_browsers(previous_slots=2, current_slots=1)

    assert [row["slot"] for row in rows] == [1]
    assert stopped == [
        {
            "cdp_url": "http://127.0.0.1:9323",
            "profile_dir": str(Path(storage.resolve_mabang_account_runtime(account["id"], slot=1)["profile_dir"])),
        }
    ]
    # 调大槽位时不动任何浏览器
    assert main._retire_unused_slot_browsers(previous_slots=1, current_slots=2) == []


def test_start_does_not_prepare_browsers_of_running_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """新建任务会唤醒 Worker：已经在跑的目标不能被重新「准备浏览器」清掉在途页面。"""

    account = _account("acct-a", port=9223)
    _give_store(account["id"])
    storage.save_runtime_configuration({"worker": {"slots_per_account": 2}})
    multi = main._worker_supervisors()
    assert multi is not None

    prepared: list[tuple] = []

    def fake_prepare(account_id=None, *, slot=0):
        prepared.append((account_id, slot))
        return {"ok": True, "auto_login": {"status": "already_logged_in"}, "browser_mode": "headless"}

    class SpySupervisor:
        def __init__(self, status: str) -> None:
            self.status_value = status
            self.started = 0

        def status(self) -> dict:
            return {"status": self.status_value, "pid": 1, "last_error": ""}

        def start(self) -> dict:
            self.started += 1
            self.status_value = "running"
            return {"status": "running", "pid": 99}

    running = SpySupervisor("running")
    stopped = SpySupervisor("stopped")
    monkeypatch.setattr(main, "_prepare_worker_browser_login", fake_prepare)
    monkeypatch.setattr(
        multi,
        "_supervisors",
        {account["id"]: running, f"{account['id']}#slot2": stopped},
    )

    result = main._run_account_worker_action(multi, "start")

    # 在跑的 1 号槽位不准备浏览器（否则复用会关掉它正在填的页面）
    assert prepared == [(account["id"], 1)]
    assert running.started == 1
    assert stopped.started == 1
    assert {row["slot"] for row in result["workers"]} == {0, 1}
    assert result["failures"] == []


def test_new_slots_start_only_the_new_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    account = _account("acct-a", port=9223)
    _give_store(account["id"])
    storage.save_runtime_configuration({"worker": {"slots_per_account": 2}})
    multi = main._worker_supervisors()
    assert multi is not None

    prepared: list[tuple] = []
    started: list[str] = []

    def fake_prepare(account_id=None, *, slot=0):
        prepared.append((account_id, slot))
        return {"ok": True, "auto_login": {"status": "already_logged_in"}, "browser_mode": "headless"}

    class SpySupervisor:
        def __init__(self, key: str, status: str) -> None:
            self.key = key
            self.status_value = status

        def status(self) -> dict:
            return {"status": self.status_value, "pid": 1, "last_error": ""}

        def start(self) -> dict:
            started.append(self.key)
            return {"status": "running", "pid": 99}

    monkeypatch.setattr(main, "_prepare_worker_browser_login", fake_prepare)
    monkeypatch.setattr(
        multi,
        "_supervisors",
        {
            account["id"]: SpySupervisor(account["id"], "running"),
            f"{account['id']}#slot2": SpySupervisor(f"{account['id']}#slot2", "stopped"),
        },
    )

    result = main._start_worker_targets([f"{account['id']}#slot2"])

    # 只准备/启动新槽位；已经在跑的 1 号槽位不碰（免得顶掉它正在填的页面）
    assert prepared == [(account["id"], 1)]
    assert started == [f"{account['id']}#slot2"]
    assert [row["slot"] for row in result["workers"]] == [1]
    assert result["failures"] == []


def test_login_gate_failure_keeps_the_auto_login_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """门禁失败要带上自动登录的原始原因，不能只报一个 failed。

    2026-10-12：从一个精简 PATH 的 shell 重启 API 后，自动登录返回
    ``{"status": "failed", "error": "无法启动 Node：[WinError 2] …"}``；多账号路径
    原先只把 status 拼进消息，界面显示的「马帮登录未确认：failed」完全看不出原因。
    """

    account = _account("acct-a", port=9223)
    _give_store(account["id"])
    # 两个目标（账号 × 2 槽位）才走多 Worker 分区路径，也就是现场报 409 的那条。
    storage.save_runtime_configuration({"worker": {"slots_per_account": 2}})
    multi = main._worker_supervisors()
    assert multi is not None

    def fake_prepare(account_id=None, *, slot=0):
        return {
            "ok": False,
            "auto_login": {"status": "failed", "error": "无法启动 Node：[WinError 2] 系统找不到指定的文件。"},
            "browser_mode": "headed",
        }

    monkeypatch.setattr(main, "_prepare_worker_browser_login", fake_prepare)

    with pytest.raises(RuntimeError) as excinfo:
        main._run_account_worker_action(multi, "start")

    message = str(excinfo.value)
    assert message.startswith("马帮登录未确认：failed")
    assert "无法启动 Node" in message


def test_login_gate_failure_message_has_no_dangling_dash_without_a_reason() -> None:
    assert main._login_error_detail({"status": "captcha_required"}) == ""
    assert main._login_error_detail({"status": "failed", "error": "  "}) == ""
    assert main._login_error_detail({"error": "boom"}) == " - boom"
