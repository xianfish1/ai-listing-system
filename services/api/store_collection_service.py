"""店铺采集的执行入口：按账号跑采集、持久化运行记录、隔离单账号失败。

采集器只采「页面上当前打开的店铺」，不接受 store_id，所以默认采集器用该店铺所属账号的
浏览器去采，并**校验返回的店铺 id 与目标一致**——不一致就判为失败，绝不把一个店铺的
快照记到另一个店铺头上。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any, Callable, Mapping, Sequence

from services.api import storage
from services.api.store_collection_scheduler import (
    AccountCollectionError,
    StoreCollectionScheduler,
)

Collector = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]

# 采集器报错里出现这些字样 = 整个账号不可用（登录过期 / 验证码 / 页面结构变化），
# 只暂停该账号，其他账号继续。
_ACCOUNT_LEVEL_MARKERS = (
    "登录",
    "验证",
    "captcha",
    "login",
    "页面结构",
    "未找到马帮页面",
    "cdp_unreachable",
    "调试端口",
)


def _is_account_level(message: str) -> bool:
    text = str(message or "").lower()
    return any(marker.lower() in text for marker in _ACCOUNT_LEVEL_MARKERS)


def _default_collector(store_id: str, store: Mapping[str, Any]) -> Mapping[str, Any]:
    """用该店铺所属账号的浏览器采集，并校验页面上的店铺就是目标店铺。"""

    from scripts.browser_use_runner import collect_store_watermarks_via_playwright

    account_id = str(store.get("account_id") or "").strip()
    runtime = storage.resolve_mabang_account_runtime(account_id or None)
    result = collect_store_watermarks_via_playwright(cdp_url=str(runtime.get("cdp_url") or ""))
    if not result.get("ok"):
        message = str(result.get("error") or "水印采集失败")
        if _is_account_level(message):
            raise AccountCollectionError(account_id, message)
        raise RuntimeError(message)
    captured = result.get("store") or {}
    captured_id = str(captured.get("id") or "").strip()
    if captured_id and captured_id != str(store_id):
        raise RuntimeError(
            f"浏览器当前打开的是店铺 {captured.get('name') or captured_id}，"
            f"与目标店铺 {store_id} 不一致；请先在该账号的浏览器里打开目标店铺再采集。"
        )
    return {"watermarks": deepcopy(result.get("templates") or []), "store": deepcopy(captured)}


def run_store_collection(
    *,
    store_ids: Sequence[str] | None = None,
    trigger: str = "manual",
    collector: Collector | None = None,
    interval: timedelta | None = None,
) -> dict[str, Any]:
    """跑一轮采集，持久化运行记录，返回每个店铺的结果。

    单账号失败只暂停该账号名下的其余店铺，其他账号照常采集；失败不会覆盖上一次有效快照。
    """

    targets = storage.store_collection_targets()
    if store_ids is not None:
        wanted = {str(item).strip() for item in store_ids if str(item).strip()}
        targets = {key: value for key, value in targets.items() if key in wanted}
    if not targets:
        return {"ok": True, "runs": [], "errors": []}

    active = collector or _default_collector
    scheduler = StoreCollectionScheduler(
        targets,
        collector=lambda store_id: active(store_id, targets[store_id]),
        interval=interval or timedelta(hours=6),
    )
    runs = scheduler.collect_now(list(targets))
    payload = [run.to_dict() for run in runs]
    if payload:
        storage.record_store_collection_runs(payload)
    return {
        "ok": True,
        "runs": payload,
        "errors": [
            {"store_id": run.store_id, "account_id": run.account_id, "error": run.error}
            for run in runs
            if run.status == "failed"
        ],
        "accounts": {
            account_id: scheduler.account_status(account_id)
            for account_id in {str(item.get("account_id") or "") for item in targets.values()}
        },
    }
