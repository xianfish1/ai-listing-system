"""一次性、幂等的存量数据修复。

这些修复由 :func:`services.api.storage._normalize_legacy_store_shape` 在每次冷
加载时调用，所以**每台部署升级到同一版本后都会自己修好本机的 data/store.json**，
不需要逐台手工跑脚本、也不会把某一台的数据拷来拷去。

每个修复都必须满足：
- 幂等：修完再跑一遍返回 False，否则每次启动都会重写主库；
- 足够具体：只对已知的坏数据形态生效，不能靠"猜"去改正常数据；
- 不碰大集合：只处理类目方案 / 模板这类小集合，绝不遍历 automation_job_snapshots。
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from services.api.category_profile_service import classify_profile_diff
from services.api.category_rules import build_profile_snapshot, hash_profile_snapshot
from services.api.site_rules import currency_for_country

# 2026-11-19 10:23 / 10:27，本土JunsMY_Shopee（实为马来西亚本土店）的页面采集
# 被当成 PH 写进了 shopee-ph-101757 / shopee-ph-101773，形成 v4 / v12。这份 18 字段
# 集合带「护理说明/适合/套筒类型/设计/闭合类型/拉伸/认证」，是 MY/CNSC 页面的属性，
# PH 本土页没有。11-20 的类目作用域修复又把 6 个本土模板统一绑到了这份「当前发布版」，
# 于是本土模板的类目属性全部来自马来店页面。
_MY_CAPTURE_FIELD_IDS = frozenset({
    "100022", "100037", "100134", "100135", "100154", "100162", "100163",
    "100168", "100169", "100170", "100174", "101298", "101299", "101300",
    "101303", "101305", "101343", "102370",
})
# PH 本土页特有的「尺寸」字段：用来确认候选版本真的是本土采集，而不是另一份
# CNSC/MY 采集。
_LOCAL_CAPTURE_MARKER = "102530"
_CNSC_ONLY_MARKER = "101298"
_REPAIR_SOURCE_TYPE = "local_profile_source_repair"


def repair_local_category_profile_sources(data: dict[str, Any], *, now: str) -> bool:
    """把被马来店采集污染的本土类目方案恢复成同国家的本土采集版本，并重绑模板。

    只在 active 版本的字段集合**完全等于**已知坏集合时触发；触发后新建一个 active
    版本（字段来自同一 profile 里最近的本土采集），并把该方案下的本土模板改绑过去。
    已有的店铺刊登副本保持原快照不动（副本快照是锁定口径，重跑老副本不应被悄悄换规则）。
    """
    profiles = data.get("category_profiles") or []
    versions = data.get("category_profile_versions") or []
    if not profiles or not versions:
        return False
    by_id = {str(item.get("id")): item for item in versions}
    runs = {str(item.get("id")): item for item in data.get("mabang_page_import_runs") or []}
    # 每个方案只修一次：已经有过修复版本的方案不再检查，避免以后页面真的换成这套字段时
    # 又把操作员新采的版本悄悄改回去。
    already_repaired = {
        str(item.get("profile_id"))
        for item in versions
        if str((item.get("source") or {}).get("type") or "") == _REPAIR_SOURCE_TYPE
    }
    repaired: dict[str, str] = {}
    for profile in profiles:
        if str(profile.get("id") or "") in already_repaired:
            continue
        if str(profile.get("platform") or "").strip().lower() != "shopee":
            continue
        country = str(profile.get("country") or "").strip().upper()
        if country != "PH":
            # 这次污染的指纹只出现在 PH 方案上；马来/泰国/印尼/新加坡以后有各自的
            # 本土采集，字段集合可能天然就是这 18 个，绝不能跨国家套用这条修复。
            continue
        active_id = str(profile.get("active_version_id") or profile.get("published_version_id") or "")
        active = by_id.get(active_id)
        if not active or not _is_foreign_local_capture(active):
            continue
        candidate = _find_local_candidate(versions, profile, active, runs)
        if candidate is None:
            # 没有可信的本土采集可回退：宁可不动，也不拿另一份可疑采集去替换。
            continue
        new_version = _new_active_version(profile, active, candidate, versions, now=now)
        versions.append(new_version)
        by_id[new_version["id"]] = new_version
        active["status"] = "superseded"
        profile["active_version_id"] = new_version["id"]
        profile["published_version_id"] = new_version["id"]
        profile["updated_at"] = now
        repaired[str(profile.get("id"))] = new_version["id"]
    if not repaired:
        return False
    _rebind_site_templates(data, repaired, now=now)
    return True


def _field_ids(version: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        str(field.get("field_id"))
        for field in version.get("fields") or []
        if isinstance(field, dict) and field.get("field_id") is not None
    )


def _is_foreign_local_capture(version: dict[str, Any]) -> bool:
    if str((version.get("source") or {}).get("type") or "") == _REPAIR_SOURCE_TYPE:
        return False
    return _field_ids(version) == _MY_CAPTURE_FIELD_IDS


def _run_for_version(version: dict[str, Any], runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    run_id = str((version.get("source") or {}).get("run_id") or "").strip()
    for prefix in ("page-import:", "category-scope-fix:"):
        if run_id.startswith(prefix):
            run_id = run_id[len(prefix):]
            break
    return runs.get(run_id) or {}


def _run_store_id(version: dict[str, Any], runs: dict[str, dict[str, Any]]) -> str:
    store = (_run_for_version(version, runs).get("snapshot") or {}).get("store") or {}
    return str(store.get("id") or store.get("mabang_store_id") or "").strip()


def _run_store_country(version: dict[str, Any], runs: dict[str, dict[str, Any]]) -> str:
    run = _run_for_version(version, runs)
    store = (run.get("snapshot") or {}).get("store") or {}
    return str(store.get("country") or run.get("country") or "").strip().upper()


def _find_local_candidate(
    versions: list[dict[str, Any]],
    profile: dict[str, Any],
    active: dict[str, Any],
    runs: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    profile_id = str(profile.get("id") or "")
    profile_country = str(profile.get("country") or "").strip().upper()
    active_store = _run_store_id(active, runs)
    candidates: list[dict[str, Any]] = []
    for version in versions:
        if str(version.get("profile_id") or "") != profile_id:
            continue
        if str(version.get("id")) == str(active.get("id")):
            continue
        ids = _field_ids(version)
        if _LOCAL_CAPTURE_MARKER not in ids or _CNSC_ONLY_MARKER in ids:
            continue
        candidate_country = _run_store_country(version, runs)
        if candidate_country and profile_country and candidate_country != profile_country:
            continue
        # 同一家店连续采集出来的版本不算「另一个站点的可信来源」。
        if active_store and _run_store_id(version, runs) == active_store:
            continue
        candidates.append(version)
    if not candidates:
        return None
    return max(candidates, key=lambda item: (int(item.get("version") or 0), str(item.get("created_at") or "")))


def _new_active_version(
    profile: dict[str, Any],
    wrong: dict[str, Any],
    candidate: dict[str, Any],
    versions: list[dict[str, Any]],
    *,
    now: str,
) -> dict[str, Any]:
    next_version = max((int(item.get("version") or 0) for item in versions), default=0) + 1
    original_source = deepcopy(candidate.get("source") or {})
    version = {
        "id": str(uuid.uuid4()),
        "profile_id": str(profile.get("id") or ""),
        "version": next_version,
        "status": "active",
        "fields": deepcopy(candidate.get("fields") or []),
        "variation": deepcopy(candidate.get("variation") or {}),
        "source": {
            "type": _REPAIR_SOURCE_TYPE,
            "repaired_from_version_id": str(candidate.get("id") or ""),
            "wrong_version_id": str(wrong.get("id") or ""),
            "original_source": original_source,
            "repaired_at": now,
        },
        "activated_at": now,
        "updated_at": now,
        "created_at": now,
    }
    version["schema_hash"] = hash_profile_snapshot(build_profile_snapshot(profile, version))
    version["diff"] = classify_profile_diff(wrong, version)
    return version


def drop_unsupported_collar_type(data: dict[str, Any], *, now: str) -> bool:
    """删掉模板里「衣领类型=Crew Neck」这个页面上不存在的自选值。

    现场（2026-11-23，Oct.1N 在途详情页）的「衣领类型（Collar Type）」枚举是 12 个
    数值选项，没有 Crew Neck；历史模板把它存成自定义值，马帮下拉里根本选不到
    （操作员看到的「下拉里没有这个选项」）。这个字段本身是可选的，而且「领口
    （Neckline）」已经单独填了「水手领 (Crew Neck)」，所以直接清掉最稳。
    幂等：删掉后 key 不存在，再跑不命中。
    """
    changed = False
    for template in data.get("store_templates") or []:
        values = template.get("attribute_values")
        if not isinstance(values, dict):
            continue
        raw = values.get("100174")
        if raw is None:
            continue
        if isinstance(raw, list):
            kept = [item for item in raw if str(item or "").strip().lower() != "crew neck"]
            if len(kept) == len(raw):
                continue
            if kept:
                values["100174"] = kept
            else:
                values.pop("100174", None)
        else:
            if str(raw or "").strip().lower() != "crew neck":
                continue
            values.pop("100174", None)
        attributes = template.get("category_attributes")
        if isinstance(attributes, dict):
            attributes.pop("100174", None)
        _bump_template_version(data, template, now=now)
        changed = True
    return changed


def normalize_store_option_currency(data: dict[str, Any], *, now: str) -> bool:
    """给存量店铺选项补上站点币种（以前只有 country，没有 currency）。"""
    changed = False
    for option in data.get("mabang_store_options") or []:
        desired = currency_for_country(option.get("country"))
        if not desired:
            continue
        if str(option.get("currency") or "").strip().upper() == desired:
            continue
        option["currency"] = desired
        option["updated_at"] = now
        changed = True
    return changed


def normalize_template_currency(data: dict[str, Any], *, now: str) -> bool:
    """模板币种按站点纠正（历史缺省一律写成了 MYR）。

    币种跟着 ``country`` 走：PH→PHP、MY→MYR、TH→THB、ID→IDR、SG→SGD…。只改模板，
    不动已锁定的副本快照；之后新建的副本会继承正确币种。幂等：值一致就跳过。
    """
    changed = False
    for template in data.get("store_templates") or []:
        desired = currency_for_country(template.get("country"))
        if not desired:
            continue
        current = str(template.get("currency") or "").strip().upper()
        if current == desired:
            continue
        template["currency"] = desired
        _bump_template_version(data, template, now=now)
        changed = True
    return changed


def _bump_template_version(data: dict[str, Any], template: dict[str, Any], *, now: str) -> None:
    template["version"] = int(template.get("version") or 1) + 1
    template["current_version_id"] = str(uuid.uuid4())
    template["updated_at"] = now
    data.setdefault("store_template_versions", []).append({
        "id": template["current_version_id"],
        "template_id": str(template.get("id") or ""),
        "version": template["version"],
        "snapshot": deepcopy(template),
        "created_at": now,
    })


def _rebind_site_templates(data: dict[str, Any], repaired: dict[str, str], *, now: str) -> None:
    versions = {str(item.get("id")): item for item in data.get("category_profile_versions") or []}
    field_ids = {version_id: _field_ids(versions[version_id]) for version_id in repaired.values() if version_id in versions}
    for template in data.get("store_templates") or []:
        profile_id = str(template.get("category_profile_id") or "")
        new_version_id = repaired.get(profile_id)
        if not new_version_id:
            continue
        if str(template.get("listing_mode") or "site") != "site":
            continue
        if str(template.get("category_profile_version_id") or "") == new_version_id:
            continue
        allowed = field_ids.get(new_version_id) or frozenset()
        for key in ("attribute_values", "category_attributes"):
            values = template.get(key)
            if isinstance(values, dict):
                template[key] = {
                    field_id: value for field_id, value in values.items() if str(field_id) in allowed
                }
        template["category_profile_version_id"] = new_version_id
        _bump_template_version(data, template, now=now)
