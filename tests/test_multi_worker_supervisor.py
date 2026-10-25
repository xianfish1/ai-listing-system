from __future__ import annotations

from pathlib import Path

from services.api.worker_supervisor import MultiWorkerSupervisor


class FakeSupervisor:
    def __init__(self, runtime_dir, *, command=None, worker_pid_path=None, working_directory=None, status="stopped", fail=None):
        self.runtime_dir = Path(runtime_dir)
        self.command = list(command or [])
        self.status_value = status
        self.fail = fail
        self.events: list[str] = []

    def status(self):
        return {"status": self.status_value, "pid": None, "last_error": ""}

    def _act(self, name: str):
        if self.fail == name:
            raise RuntimeError(f"{name} failed")
        self.events.append(name)
        return {"status": "running", "pid": 1234}

    def start(self):
        return self._act("start")

    def stop(self):
        if self.fail == "stop":
            raise RuntimeError("stop failed")
        self.events.append("stop")
        return {"status": "stopped", "pid": None}

    def pause(self):
        return self._act("pause")

    def resume(self):
        return self._act("resume")

    def restart(self):
        return self._act("restart")

    def logs(self, *, tail=None):
        return {"path": str(self.runtime_dir / "worker.log"), "lines": [f"tail={tail}"]}

    def auto_heal(self):
        return {"status": self.status_value}


def _build(root: Path):
    made: dict[str, FakeSupervisor] = {}

    def build(target):
        supervisor = FakeSupervisor(root / str(target["key"]))
        made[str(target["key"])] = supervisor
        return supervisor

    return build, made


def test_configure_creates_one_supervisor_per_account(tmp_path: Path) -> None:
    build, made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)

    assert multi.configure(["acct-a", "acct-b", "acct-a", ""]) == ["acct-a", "acct-b"]
    assert multi.accounts() == ["acct-a", "acct-b"]
    assert set(made) == {"acct-a", "acct-b"}
    # 每个账号一个独立 runtime 目录
    assert made["acct-a"].runtime_dir != made["acct-b"].runtime_dir


def test_configure_creates_one_supervisor_per_account_slot(tmp_path: Path) -> None:
    build, made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)

    keys = multi.configure(
        [
            {"account_id": "acct-a", "slot": 0},
            {"account_id": "acct-a", "slot": 1},
            {"account_id": "acct-b", "slot": 0},
        ]
    )

    assert keys == ["acct-a", "acct-a#slot2", "acct-b"]
    assert multi.accounts() == ["acct-a", "acct-b"]
    assert len({made[key].runtime_dir for key in keys}) == 3
    assert [row["slot"] for row in multi.statuses()] == [0, 1, 0]
    assert [row["account_id"] for row in multi.statuses()] == ["acct-a", "acct-a", "acct-b"]


def test_configure_drops_only_the_retired_slot(tmp_path: Path) -> None:
    build, made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)
    multi.configure(
        [{"account_id": "acct-a", "slot": 0}, {"account_id": "acct-a", "slot": 1}]
    )

    multi.configure([{"account_id": "acct-a", "slot": 0}])

    assert made["acct-a#slot2"].events == ["stop"]
    assert made["acct-a"].events == []
    assert multi.targets() == [{"key": "acct-a", "account_id": "acct-a", "slot": 0}]


def test_configure_stops_only_the_account_that_disappeared(tmp_path: Path) -> None:
    build, made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)
    multi.configure(["acct-a", "acct-b"])

    multi.configure(["acct-b"])

    assert made["acct-a"].events == ["stop"]
    assert made["acct-b"].events == []
    assert multi.accounts() == ["acct-b"]


def test_run_isolates_one_account_failure(tmp_path: Path) -> None:
    build, made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)
    multi.configure(["acct-a", "acct-b"])
    made["acct-a"].fail = "start"

    rows = multi.run("start")

    by_account = {row["account_id"]: row for row in rows}
    assert by_account["acct-a"]["ok"] is False and "start failed" in by_account["acct-a"]["error"]
    assert by_account["acct-b"]["ok"] is True
    # A 起不来不影响 B
    assert made["acct-b"].events == ["start"]


def test_run_reports_the_slot_of_each_target(tmp_path: Path) -> None:
    build, _made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)
    multi.configure(
        [{"account_id": "acct-a", "slot": 0}, {"account_id": "acct-a", "slot": 1}]
    )

    rows = multi.run("start")

    assert [(row["account_id"], row["slot"]) for row in rows] == [("acct-a", 0), ("acct-a", 1)]
    assert [row["key"] for row in rows] == ["acct-a", "acct-a#slot2"]


def test_summarize_reports_partial_while_one_account_is_down(tmp_path: Path) -> None:
    build, made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)
    multi.configure(["acct-a", "acct-b"])
    made["acct-a"].status_value = "running"

    summary = multi.summarize()

    assert summary["status"] == "partial"
    assert [row["account_id"] for row in summary["workers"]] == ["acct-a", "acct-b"]


def test_logs_are_read_per_account(tmp_path: Path) -> None:
    build, _made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)
    multi.configure(["acct-a"])

    assert multi.logs("acct-a", tail=5)["lines"] == ["tail=5"]
    assert multi.logs("missing")["lines"] == []


def test_logs_are_read_per_slot(tmp_path: Path) -> None:
    build, _made = _build(tmp_path)
    multi = MultiWorkerSupervisor(tmp_path, build=build)
    multi.configure(
        [{"account_id": "acct-a", "slot": 0}, {"account_id": "acct-a", "slot": 1}]
    )

    # 1 号槽位的日志路径与账号同名，2 号槽位是独立目录
    assert multi.logs("acct-a")["path"] == str(tmp_path / "acct-a" / "worker.log")
    assert multi.logs("acct-a", slot=1)["path"] == str(tmp_path / "acct-a#slot2" / "worker.log")
    assert multi.get_target("acct-a", 1) is multi.get("acct-a#slot2")
