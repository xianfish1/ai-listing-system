"""Store collection scheduling and failure isolation.

The scheduler is deliberately storage-agnostic.  API handlers can provide the
configured stores and a collector callback, then persist snapshots and run
records through the application's existing storage layer.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any, Callable, Mapping, Sequence


Collector = Callable[[str], Mapping[str, Any]]


def store_collection_key(account_id: str, store_id: str) -> str:
    """采集时间的记账键：`<account_id>|<store_id>`。

    店铺 id 在账号之间本该唯一，但合成 id（例如 `cnsc-store-1`）会跨账号撞车；
    带账号后一个账号的采集时间不会冒充另一个账号的。没有账号时退回旧的「只按店铺」。
    """

    account = str(account_id or "").strip()
    store = str(store_id or "").strip()
    return f"{account}|{store}" if account else store


class AccountCollectionError(RuntimeError):
    """采集器抛出的「整个马帮账号不可用」错误（登录过期 / 页面结构变化）。

    调度器只暂停该账号名下的其余店铺，其他账号照常采集。店铺级错误（例如某个类目
    解析失败）请抛普通异常，那只影响这一个店铺。
    """

    def __init__(self, account_id: str, message: str) -> None:
        super().__init__(message)
        self.account_id = str(account_id or "")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class CollectionRun:
    """An immutable audit record for one attempted store collection."""

    id: str
    store_id: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime
    account_id: str = ""
    error: str | None = None
    retry_required: bool = False
    snapshot_keys: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "store_id": self.store_id,
            "account_id": self.account_id,
            "trigger": self.trigger,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "error": self.error,
            "retry_required": self.retry_required,
            "snapshot_keys": list(self.snapshot_keys),
        }


@dataclass
class _StoreState:
    enabled: bool = True
    paused: bool = False
    pause_reason: str | None = None
    account_id: str = ""
    snapshots: dict[str, Any] = field(default_factory=dict)


class StoreCollectionScheduler:
    """Schedule manual and periodic collection for independently configured stores.

    ``collector`` is called as ``collector(store_id)`` and must return a mapping
    of snapshot names to values.  A snapshot is committed only after the
    callback returns successfully; an exception cannot erase a previous good
    snapshot or stop collection of another store.
    """

    def __init__(
        self,
        stores: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        collector: Collector | None = None,
        interval: timedelta | float | int = timedelta(hours=24),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if collector is None:
            raise ValueError("collector is required")
        if isinstance(interval, timedelta):
            interval_value = interval
        else:
            interval_value = timedelta(seconds=float(interval))
        if interval_value.total_seconds() <= 0:
            raise ValueError("interval must be positive")
        self.collector = collector
        self.interval = interval_value
        self.clock = clock or _utcnow
        self._stores: dict[str, _StoreState] = {}
        self._runs: list[CollectionRun] = []
        self.next_run: datetime | None = None
        self.configure_stores(stores or {})

    def configure_stores(self, stores: Mapping[str, Mapping[str, Any]]) -> None:
        """Replace store configuration while retaining known snapshots/runs."""

        configured = set(stores)
        for store_id, raw in stores.items():
            previous = self._stores.get(store_id)
            snapshots = raw.get("snapshots", previous.snapshots if previous else {})
            self._stores[store_id] = _StoreState(
                enabled=bool(raw.get("enabled", previous.enabled if previous else True)),
                paused=bool(raw.get("paused", previous.paused if previous else False)),
                pause_reason=raw.get("pause_reason", previous.pause_reason if previous else None),
                account_id=str(
                    raw.get("account_id", previous.account_id if previous else "") or ""
                ).strip(),
                snapshots=deepcopy(dict(snapshots or {})),
            )
        for removed in set(self._stores) - configured:
            del self._stores[removed]

    def collect_now(self, store_ids: Sequence[str] | None = None) -> list[CollectionRun]:
        """Collect enabled, non-paused stores immediately (manual trigger)."""

        return self._collect(store_ids, trigger="manual")

    def tick(self, now: datetime | None = None) -> list[CollectionRun]:
        """Run a periodic collection when its due time has arrived."""

        current = _ensure_datetime(now or self.clock())
        if self.next_run is None:
            self.next_run = current + self.interval
            return []
        if current < self.next_run:
            return []
        runs = self._collect(None, trigger="scheduled")
        self.next_run = current + self.interval
        return runs

    def set_next_run(self, value: datetime | None) -> None:
        self.next_run = _ensure_datetime(value) if value is not None else None

    def pause_store(self, store_id: str, *, reason: str | None = None) -> None:
        state = self._require_store(store_id)
        state.paused = True
        state.pause_reason = reason

    def resume_store(self, store_id: str) -> None:
        state = self._require_store(store_id)
        state.paused = False
        state.pause_reason = None

    def stores_for_account(self, account_id: str) -> list[str]:
        wanted = str(account_id or "").strip()
        return [store_id for store_id, state in self._stores.items() if state.account_id == wanted]

    def pause_account(self, account_id: str, *, reason: str | None = None) -> list[str]:
        """暂停一个马帮账号名下的所有店铺，其他账号不受影响。"""

        paused = []
        for store_id in self.stores_for_account(account_id):
            state = self._stores[store_id]
            state.paused = True
            state.pause_reason = reason
            paused.append(store_id)
        return paused

    def resume_account(self, account_id: str) -> list[str]:
        resumed = []
        for store_id in self.stores_for_account(account_id):
            state = self._stores[store_id]
            state.paused = False
            state.pause_reason = None
            resumed.append(store_id)
        return resumed

    def account_status(self, account_id: str) -> dict[str, Any]:
        store_ids = self.stores_for_account(account_id)
        paused = [store_id for store_id in store_ids if self._stores[store_id].paused]
        return {
            "account_id": str(account_id or "").strip(),
            "store_count": len(store_ids),
            "paused_store_count": len(paused),
            "paused": bool(store_ids) and len(paused) == len(store_ids),
            "pause_reason": self._stores[paused[0]].pause_reason if paused else None,
        }

    def set_store_enabled(self, store_id: str, enabled: bool) -> None:
        self._require_store(store_id).enabled = bool(enabled)

    def snapshot(self, store_id: str) -> dict[str, Any]:
        return deepcopy(self._require_store(store_id).snapshots)

    def store_status(self, store_id: str) -> dict[str, Any]:
        state = self._require_store(store_id)
        return {
            "store_id": store_id,
            "account_id": state.account_id,
            "enabled": state.enabled,
            "paused": state.paused,
            "pause_reason": state.pause_reason,
            "has_snapshot": bool(state.snapshots),
        }

    def list_runs(
        self,
        *,
        store_id: str | None = None,
        account_id: str | None = None,
        limit: int | None = None,
    ) -> list[CollectionRun]:
        wanted_account = str(account_id or "").strip()
        runs = [
            run
            for run in reversed(self._runs)
            if (store_id is None or run.store_id == store_id)
            and (not account_id or run.account_id == wanted_account)
        ]
        return runs if limit is None else runs[: max(0, limit)]

    def get_run(self, run_id: str) -> CollectionRun | None:
        return next((run for run in self._runs if run.id == run_id), None)

    def _collect(self, store_ids: Sequence[str] | None, *, trigger: str) -> list[CollectionRun]:
        ids = list(store_ids) if store_ids is not None else list(self._stores)
        runs: list[CollectionRun] = []
        for store_id in ids:
            state = self._stores.get(store_id)
            if state is None or not state.enabled or state.paused:
                continue
            started = _ensure_datetime(self.clock())
            try:
                captured = self.collector(store_id)
                if not isinstance(captured, Mapping):
                    raise TypeError("collector must return a mapping")
                state.snapshots = deepcopy(dict(captured))
                finished = _ensure_datetime(self.clock())
                run = CollectionRun(
                    id=str(uuid.uuid4()),
                    store_id=store_id,
                    account_id=state.account_id,
                    trigger=trigger,
                    status="succeeded",
                    started_at=started,
                    finished_at=finished,
                    snapshot_keys=tuple(sorted(state.snapshots)),
                )
            except Exception as exc:  # isolate one store from the rest
                finished = _ensure_datetime(self.clock())
                run_account = state.account_id
                if isinstance(exc, AccountCollectionError):
                    # 账号级故障（登录过期 / 页面结构变化）：只暂停这个账号的其余店铺，
                    # 其他账号继续采。没有账号实体（""）时保持旧的单店铺隔离语义。
                    run_account = str(exc.account_id or run_account).strip()
                    if run_account:
                        self.pause_account(run_account, reason=str(exc) or "账号不可用")
                run = CollectionRun(
                    id=str(uuid.uuid4()),
                    store_id=store_id,
                    account_id=run_account,
                    trigger=trigger,
                    status="failed",
                    started_at=started,
                    finished_at=finished,
                    error=str(exc),
                    retry_required=True,
                    snapshot_keys=tuple(sorted(state.snapshots)),
                )
            self._runs.append(run)
            runs.append(run)
        return runs

    def _require_store(self, store_id: str) -> _StoreState:
        try:
            return self._stores[store_id]
        except KeyError as exc:
            raise KeyError(f"Unknown store: {store_id}") from exc


__all__ = ["AccountCollectionError", "CollectionRun", "StoreCollectionScheduler"]
