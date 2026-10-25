from datetime import datetime, timedelta, timezone

import pytest

from services.api.store_collection_scheduler import StoreCollectionScheduler


def test_manual_collection_only_runs_enabled_stores_and_keeps_old_snapshot_on_failure():
    now = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    calls = []

    def collect(store_id):
        calls.append(store_id)
        if store_id == "failed":
            raise RuntimeError("登录已过期")
        return {"categories": [store_id], "promotions": [{"id": "p1"}]}

    scheduler = StoreCollectionScheduler(
        stores={
            "ok": {"enabled": True},
            "failed": {"enabled": True, "snapshots": {"categories": ["old"]}},
            "disabled": {"enabled": False},
        },
        collector=collect,
        clock=lambda: now,
    )

    runs = scheduler.collect_now()

    assert calls == ["ok", "failed"]
    assert [run.store_id for run in runs] == ["ok", "failed"]
    assert runs[0].status == "succeeded"
    assert runs[1].status == "failed"
    assert runs[1].error == "登录已过期"
    assert scheduler.snapshot("ok")["categories"] == ["ok"]
    assert scheduler.snapshot("failed")["categories"] == ["old"]


def test_manual_collection_can_target_one_store_and_paused_store_is_skipped():
    calls = []
    scheduler = StoreCollectionScheduler(
        stores={"a": {"enabled": True}, "b": {"enabled": True}},
        collector=lambda store_id: calls.append(store_id) or {"categories": []},
    )

    scheduler.pause_store("a", reason="手动暂停")
    runs = scheduler.collect_now(["a", "b"])

    assert calls == ["b"]
    assert len(runs) == 1 and runs[0].store_id == "b"
    assert scheduler.store_status("a")["paused"] is True
    assert scheduler.store_status("a")["pause_reason"] == "手动暂停"


def test_periodic_trigger_respects_interval_and_records_next_run():
    current = [datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)]
    scheduler = StoreCollectionScheduler(
        stores={"a": {"enabled": True}},
        collector=lambda store_id: {"categories": ["new"]},
        interval=timedelta(minutes=30),
        clock=lambda: current[0],
    )

    assert scheduler.tick() == []
    scheduler.set_next_run(current[0])
    assert len(scheduler.tick()) == 1
    assert scheduler.next_run > current[0]
    current[0] += timedelta(minutes=29)
    assert scheduler.tick() == []
    current[0] += timedelta(minutes=1)
    assert len(scheduler.tick()) == 1


def test_runs_are_audit_records_and_can_be_listed_newest_first():
    scheduler = StoreCollectionScheduler(
        stores={"a": {"enabled": True}},
        collector=lambda store_id: {"categories": []},
    )
    scheduler.collect_now()
    runs = scheduler.list_runs()

    assert len(runs) == 1
    record = runs[0].to_dict()
    assert record["store_id"] == "a"
    assert record["trigger"] == "manual"
    assert record["status"] == "succeeded"
    assert record["started_at"] <= record["finished_at"]


def test_collector_must_be_provided():
    with pytest.raises(ValueError, match="collector"):
        StoreCollectionScheduler(stores={"a": {"enabled": True}})
