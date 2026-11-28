from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any


def row_account_id(row: dict[str, Any]) -> str:
    return str(row.get("account_id") or "").strip()


def normalize_watermark_catalog(
    *,
    store_id: str,
    raw_items: list[dict[str, Any]],
    now: str,
    account_id: str = "",
) -> list[dict[str, Any]]:
    """归一化水印模板目录。

    水印模板是**马帮公司级**的（真实数据里每条都有 companyId、shopId 为 null），
    所以目录按「马帮账号」存一份，全站通用：同一账号下的本土店铺、跨境/3PF 店铺
    看到的是同一份目录，**不按店铺、不按刊登模式区分**。``store_id`` 只作为
    「这份目录是在哪个店铺的编辑页采到的」记录，不再用于筛选。
    """
    wanted_store = str(store_id or "").strip()
    wanted_account = str(account_id or "").strip()
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        template_id = _text(raw, "template_id", "templateId", "watermark_id", "watermarkId", "id")
        name = _text(raw, "name", "template_name", "templateName", "watermark_name")
        if not template_id or not name or template_id in seen:
            continue
        seen.add(template_id)
        normalized.append(
            {
                "id": str(uuid.uuid4()),
                "store_id": wanted_store,
                "account_id": wanted_account,
                "template_id": template_id,
                "name": name,
                "preview_url": _text(raw, "preview_url", "previewUrl", "image_url", "imageUrl", "url"),
                "status": "available",
                "collected_at": now,
                "updated_at": now,
            }
        )
    return sorted(normalized, key=lambda item: (item["name"].casefold(), item["template_id"]))


def replace_watermark_catalog(
    data: dict[str, Any],
    *,
    store_id: str,
    raw_items: list[dict[str, Any]],
    now: str,
    catalog_loaded: bool,
    account_id: str = "",
) -> list[dict[str, Any]]:
    """用一次采集结果替换该**账号**的水印目录。

    - 传了 ``account_id``：只换这个账号的那一份（没账号标记的老数据一并收编），
      别的账号的目录原样保留；
    - 没传 ``account_id``：只换「没有账号标记」的那一份（老数据 / 单账号环境）。

    真实页面上的水印模板是全公司共享的，所以这里**不按店铺替换**：从任意一个店铺的
    编辑页采集，都会刷新该账号下所有店铺（本土 + 跨境）看到的那一份目录。
    """
    if not catalog_loaded:
        raise ValueError("水印模板目录未加载")
    wanted_store = str(store_id or "").strip()
    wanted_account = str(account_id or "").strip()
    normalized = normalize_watermark_catalog(
        store_id=wanted_store,
        raw_items=raw_items,
        now=now,
        account_id=wanted_account,
    )
    existing = list(data.get("store_watermark_templates") or [])
    # 老数据（没有 account_id）视为「这个账号升级前的目录」：该账号第一次按账号采集时
    # 一并收编替换；如果这个账号已经有自己的行了，就不去动别人的老数据（可能是别的账号的）。
    adopt_legacy = not any(row_account_id(item) == wanted_account for item in existing)
    existing_by_id = {
        str(item.get("template_id") or ""): item
        for item in existing
        if row_account_id(item) == wanted_account or (adopt_legacy and row_account_id(item) == "")
    }
    for item in normalized:
        previous = existing_by_id.get(item["template_id"])
        if previous:
            item["id"] = previous.get("id") or item["id"]
            item["created_at"] = previous.get("created_at") or previous.get("collected_at") or now
        else:
            item["created_at"] = now
    data["store_watermark_templates"] = [
        item
        for item in existing
        if not (row_account_id(item) == wanted_account or (adopt_legacy and row_account_id(item) == ""))
    ] + normalized
    return deepcopy(normalized)


def list_watermarks(data: dict[str, Any], store_id: str = "", *, account_id: str = "") -> list[dict[str, Any]]:
    """列出该账号的水印目录（全站通用，不按店铺/刊登模式过滤）。

    - 有该账号的行 → 用它；
    - 该账号还没有行、但有老数据（没有 account_id 的历史行）→ 回退到老数据，
      避免升级后界面突然显示「未采集」；
    - 同一 template_id 有多行（历史上从不同店铺采过）→ 取 collected_at 最新的一条。
    """
    wanted_account = str(account_id or "").strip()
    rows = [item for item in data.get("store_watermark_templates") or [] if isinstance(item, dict)]
    if wanted_account:
        scoped = [item for item in rows if row_account_id(item) == wanted_account]
        if not scoped:
            scoped = [item for item in rows if row_account_id(item) == ""]
    else:
        scoped = rows
    by_template: dict[str, dict[str, Any]] = {}
    for item in scoped:
        template_id = str(item.get("template_id") or "").strip()
        if not template_id:
            continue
        previous = by_template.get(template_id)
        if previous is None or str(item.get("collected_at") or "") > str(previous.get("collected_at") or ""):
            by_template[template_id] = item
    return sorted(
        [deepcopy(item) for item in by_template.values()],
        key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("template_id") or "")),
    )


def resolve_watermark_reference(
    reference: dict[str, Any] | None,
    catalog: list[dict[str, Any]],
) -> dict[str, str]:
    raw = reference if isinstance(reference, dict) else {}
    wanted_id = str(raw.get("template_id") or raw.get("id") or "").strip()
    wanted_name = str(raw.get("name") or "").strip()
    matches = [item for item in catalog if str(item.get("template_id") or "") == wanted_id]
    if len(matches) == 1 and str(matches[0].get("name") or "").strip() == wanted_name:
        return {
            "status": "resolved",
            "template_id": wanted_id,
            "name": wanted_name,
            "matched_by": "template_id",
        }
    return {"status": "unresolved", "template_id": "", "name": wanted_name, "matched_by": ""}


def record_catalog_run(
    data: dict[str, Any],
    *,
    store_id: str,
    status: str,
    count: int = 0,
    error: str = "",
    catalog_loaded: bool = False,
    now: str,
    account_id: str = "",
) -> dict[str, Any]:
    """记录一次「采集水印目录」的结果（每店一条）。

    只写采集状态，**不碰目录本身**：采集失败必须能在界面上看到原因，同时旧目录原样保留
    （目录替换只走 replace_watermark_catalog）。``collected_at`` 只记最后一次成功时间，
    ``attempted_at`` 记最后一次尝试时间，界面据此区分「从来没采到」和「上次采失败了」。
    """
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        raise ValueError("水印模板目录缺少店铺")
    previous = next(
        (
            item
            for item in data.get("store_watermark_catalog_runs") or []
            if str(item.get("store_id") or "") == wanted_store
        ),
        None,
    ) or {}
    succeeded = str(status or "").strip().lower() == "ok"
    run = {
        "id": str(previous.get("id") or uuid.uuid4()),
        "store_id": wanted_store,
        "account_id": str(account_id or previous.get("account_id") or "").strip(),
        "status": "ok" if succeeded else "failed",
        "count": int(count or 0),
        "catalog_loaded": bool(catalog_loaded),
        "error": "" if succeeded else str(error or "").strip()[:500],
        "collected_at": now if succeeded else str(previous.get("collected_at") or ""),
        "attempted_at": now,
        "created_at": str(previous.get("created_at") or now),
        "updated_at": now,
    }
    data["store_watermark_catalog_runs"] = [
        item
        for item in data.get("store_watermark_catalog_runs") or []
        if str(item.get("store_id") or "") != wanted_store
    ] + [run]
    return deepcopy(run)


def catalog_status(data: dict[str, Any], store_id: str = "", *, account_id: str = "") -> dict[str, Any] | None:
    """水印目录的采集状态。

    目录是全站通用的，所以状态也按「账号」给：优先选同账号里最近一次**成功**的采集
    （「N 条 · 采集于 …」对所有店铺都成立）；只有在账号里从来没成功过时，才把失败记录
    摆出来——那时候失败原因是操作员唯一需要看的信息。本店铺自己的记录优先于同账号其它
    店铺的记录。
    """
    wanted_store = str(store_id or "").strip()
    wanted_account = str(account_id or "").strip()
    runs = [row for row in data.get("store_watermark_catalog_runs") or [] if isinstance(row, dict)]

    def sorted_desc(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda row: str(row.get("attempted_at") or ""), reverse=True)

    def pick(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        succeeded = [row for row in rows if str(row.get("status") or "").lower() == "ok"]
        if succeeded:
            return sorted_desc(succeeded)[0]
        return sorted_desc(rows)[0] if rows else None

    own = [row for row in runs if wanted_store and str(row.get("store_id") or "") == wanted_store]
    own_pick = pick(own)
    if own_pick and str(own_pick.get("status") or "").lower() == "ok":
        return _public_status(own_pick)
    if wanted_account:
        account_rows = [row for row in runs if row_account_id(row) == wanted_account]
        if not account_rows:
            # 升级前的老记录没有 account_id：当作该账号的历史记录一起参与挑选。
            account_rows = [row for row in runs if row_account_id(row) == ""]
        account_pick = pick(account_rows)
        if account_pick:
            return _public_status(account_pick)
    if own_pick:
        return _public_status(own_pick)
    global_pick = pick(runs)
    return _public_status(global_pick) if global_pick else None


def _public_status(item: dict[str, Any]) -> dict[str, Any]:
    status = deepcopy(item)
    status["count"] = int(status.get("count") or 0)
    return status


def _text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
