"""把店铺模板的「类目属性方案」按作用域修干净。

口径（services/api/listing_modes.py::category_profile_country）：
  本土 site            → 店铺国家的方案（PH 店用 shopee-ph-*）
  跨境 cnsc_global/3pf → GLOBAL 方案（shopee-global-*）

历史上两处漂移：
  1. 2026-11-19 之前，跨境模板被记成店铺国家作用域（PH）；
  2. 2026-11-17 的 CNSC 采集把 40 字段灌进了 PH 方案（v3–v11），修回 18 字段后，
     两个本土模板还绑着旧版（15 字段）。

本脚本按上面的口径：
  - 缺 GLOBAL 方案就用「最近一次跨境页采集快照」建一份（字段/版本都来自当时页面）；
  - 每个模板改绑到「正确作用域的当前发布版」；
  - 模板改绑不会联动副本（sync 块在 early return 之后），这里一并改副本并重算
    profile_snapshot_hash，否则新任务会报 profile_snapshot_changed。

用法：
  python -I scripts/fix_category_profile_scopes.py --dry-run
  python -I scripts/fix_category_profile_scopes.py --apply
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from copy import deepcopy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from services.api import storage  # noqa: E402
from services.api.category_profile_service import create_candidate_from_capture  # noqa: E402
from services.api.listing_modes import (  # noqa: E402
    GLOBAL_CATEGORY_SCOPE,
    category_profile_country,
    is_cnsc_page,
    normalize_listing_mode,
)

APPLY = '--apply' in sys.argv
# Windows 控制台默认 GBK：店铺名里有 8️⃣（带变体选择符），直接 print 会 UnicodeEncodeError。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass


def load_raw() -> tuple[dict, list[dict]]:
    """dry-run 用：直接读文件，不经过 storage 的锁/迁移逻辑（API 在跑时不写盘）。"""
    with open(os.path.join(ROOT, 'data', 'store.json'), encoding='utf-8') as handle:
        store_payload = json.load(handle)
    with open(os.path.join(ROOT, 'data', 'mabang_page_import_runs.json'), encoding='utf-8') as handle:
        runs_payload = json.load(handle)
    if isinstance(runs_payload, dict):
        runs_payload = runs_payload.get('mabang_page_import_runs') or []
    return store_payload, runs_payload or []


def latest_cnsc_capture(runs: list[dict], category_id: str) -> dict | None:
    candidates = []
    for run in runs:
        snapshot = run.get('snapshot') or {}
        if run.get('status') != 'completed' or not snapshot:
            continue
        if not is_cnsc_page(snapshot.get('listing_mode')):
            continue
        if str((snapshot.get('category') or {}).get('id') or '') != str(category_id):
            continue
        candidates.append((str(run.get('created_at') or ''), normalize_listing_mode(snapshot.get('listing_mode')), snapshot))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1] == 'cnsc_3pf'))
    return {'snapshot': candidates[-1][2], 'when': candidates[-1][0]}


def published_version(versions: list[dict], profile_id: str, preferred: str) -> dict | None:
    if preferred:
        hit = next((v for v in versions if v.get('id') == preferred), None)
        if hit:
            return hit
    active = [v for v in versions if v.get('profile_id') == profile_id and v.get('status') in ('active', 'published')]
    return active[-1] if active else None


def backup_files() -> str:
    stamp = time.strftime('%Y%m%dT%H%M%S')
    target = os.path.join(ROOT, 'data', 'backups', f'pre-category-scope-fix-{stamp}')
    os.makedirs(target, exist_ok=True)
    for name in ('store.json', 'store_listing_copies.json', 'listing_skus.json', 'listing_prices.json'):
        source = os.path.join(ROOT, 'data', name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(target, name))
    return target


def plan_targets(templates, data, runs) -> tuple[dict, dict, list[str]]:
    """算出每个模板应该绑的方案/版本；必要时记录要新建的 GLOBAL 方案。"""
    profiles = {p.get('id'): p for p in data.get('category_profiles') or []}
    versions = data.get('category_profile_versions') or []
    need_global: dict[str, dict] = {}
    plan: dict[str, dict] = {}
    notes: list[str] = []
    for template in templates:
        mode = normalize_listing_mode(template.get('listing_mode'))
        scope = category_profile_country(mode, template.get('country'))
        category_id = str(template.get('mabang_category_id') or '')
        if not category_id:
            continue
        profile_id = None
        for candidate in profiles.values():
            if (str(candidate.get('platform') or '').lower() == 'shopee'
                    and str(candidate.get('country') or '').upper() == scope
                    and str(candidate.get('marketplace_category_id') or '') == category_id):
                profile_id = candidate.get('id')
                break
        if profile_id is None:
            if scope != GLOBAL_CATEGORY_SCOPE:
                notes.append('!! %s：找不到 %s 作用域的类目方案（cat=%s），跳过' % (
                    template.get('template_name'), scope, category_id))
                continue
            capture = latest_cnsc_capture(runs, category_id)
            if not capture:
                notes.append('!! %s：没有跨境页采集快照，建不了 GLOBAL 方案（cat=%s），跳过' % (
                    template.get('template_name'), category_id))
                continue
            need_global[category_id] = capture
            plan[template['id']] = {'scope': scope, 'category_id': category_id, 'profile_id': '',
                                    'profile_pending': True}
            continue
        version = published_version(versions, profile_id, str((profiles.get(profile_id) or {}).get('published_version_id') or ''))
        if version is None:
            notes.append('!! %s：方案 %s 没有已发布版本，跳过' % (template.get('template_name'), profile_id))
            continue
        plan[template['id']] = {'scope': scope, 'category_id': category_id, 'profile_id': profile_id,
                                'version_id': version.get('id'), 'version': version.get('version')}
    return plan, need_global, notes


def main() -> None:
    lines: list[str] = []
    if APPLY:
        with storage.locked_store(
            split_keys=('mabang_page_import_runs', 'store_listing_copies', 'listing_skus', 'listing_prices')
        ) as (data, _save):
            templates = [deepcopy(t) for t in data.get('store_templates') or []]
            runs = [deepcopy(item) for item in data.get('mabang_page_import_runs') or []]
            snapshot_for_plan = deepcopy(data)
    else:
        store_payload, runs = load_raw()
        templates = store_payload.get('store_templates') or []
        snapshot_for_plan = store_payload

    plan, need_global, notes = plan_targets(templates, snapshot_for_plan, runs)
    lines.append('== 计划（%d 个模板，%d 份缺失的 GLOBAL 方案）' % (len(templates), len(need_global)))
    for template in templates:
        target = plan.get(template['id']) or {}
        current = '%s@%s' % (template.get('category_profile_id'), str(template.get('category_profile_version_id') or '')[:8])
        want = target.get('profile_id') or ('GLOBAL 待建(shopee-global-%s)' % target.get('category_id'))
        want += '@' + str(target.get('version_id') or '')[:8]
        changed = bool(target) and (target.get('profile_id') != template.get('category_profile_id')
                                    or target.get('version_id') != template.get('category_profile_version_id'))
        lines.append('  %s %-10s %-22s 现在 %-46s -> %s' % (
            'CHANGE' if changed else 'keep  ', template.get('listing_mode'),
            (template.get('template_name') or '')[-22:], current, want if changed else '(不变)'))
    for note in notes:
        lines.append('  ' + note)

    if not APPLY:
        lines.append('\n[dry-run] 未写入。加 --apply 执行。')
        print('\n'.join(lines))
        return

    backup = backup_files()
    lines.append('备份目录：' + backup)

    # ---------- 1) 建缺失的 GLOBAL 方案 ----------
    created: dict[str, dict] = {}
    for category_id, capture in need_global.items():
        snapshot = capture['snapshot']
        store = snapshot.get('store') or {}
        category = snapshot.get('category') or {}
        raw_capture = {
            'platform': 'shopee',
            'country': GLOBAL_CATEGORY_SCOPE,
            'store_id': str(store.get('id') or ''),
            'marketplace_category_id': category_id,
            'marketplace_category_path': str(category.get('path') or ''),
            'display_name': str(category.get('path') or category_id),
            'page_url': str((snapshot.get('page') or {}).get('url') or ''),
            'fields': deepcopy(snapshot.get('category_fields') or []),
            'variation': deepcopy(snapshot.get('variation') or {}),
        }
        with storage.locked_store() as (data, save):
            result = create_candidate_from_capture(
                data,
                run_id='category-scope-fix:%s:%s' % (category_id, capture['when']),
                raw_capture=raw_capture,
            )
            save(data)
        created[category_id] = {'profile_id': result['profile']['id'], 'version_id': result['version']['id']}
        lines.append('  新建 GLOBAL 方案 %s v%s（%d 字段）' % (
            result['profile']['id'], result['version'].get('version'), len(result['version'].get('fields') or [])))

    for template_id, target in plan.items():
        if target.get('profile_pending'):
            built = created.get(target['category_id'])
            if built:
                target.update({'profile_id': built['profile_id'], 'version_id': built['version_id'],
                               'profile_pending': False})

    # ---------- 2) 改绑模板 ----------
    rebound_templates = []
    for template in templates:
        target = plan.get(template['id']) or {}
        if not target.get('profile_id') or not target.get('version_id'):
            continue
        if target['profile_id'] == template.get('category_profile_id') and \
                target['version_id'] == template.get('category_profile_version_id'):
            continue
        payload = dict(template)
        payload['category_profile_id'] = target['profile_id']
        payload['category_profile_version_id'] = target['version_id']
        updated = storage.update_store_template(template['id'], payload)
        if not updated:
            lines.append('!! 模板改绑失败：%s' % template.get('template_name'))
            continue
        rebound_templates.append(updated['id'])
        lines.append('  模板改绑 %-24s -> %s v%s（模板 version=%s）' % (
            (updated.get('template_name') or '')[-24:], updated.get('category_profile_id'),
            (plan[template['id']].get('version') or '?'), updated.get('version')))

    # ---------- 3) 同步副本（模板改绑不联动副本） ----------
    if rebound_templates:
        with storage.locked_store(
            split_keys=('store_listing_copies', 'listing_skus', 'listing_prices', 'automation_jobs')
        ) as (data, save):
            templates_by_id = {t.get('id'): t for t in data.get('store_templates') or []}
            touched = 0
            for copy in data.get('store_listing_copies') or []:
                if copy.get('template_id') not in rebound_templates:
                    continue
                template = templates_by_id.get(copy.get('template_id')) or {}
                snapshot = storage._profile_snapshot_for_template(template, data)  # noqa: SLF001 - 迁移脚本
                if not snapshot:
                    lines.append('!! 副本 %s 拿不到新方案快照，跳过' % str(copy.get('id'))[:8])
                    continue
                copy['category_profile_id'] = template.get('category_profile_id')
                copy['category_profile_version_id'] = template.get('category_profile_version_id')
                copy['category_profile_snapshot'] = snapshot
                copy['profile_snapshot_hash'] = storage.hash_profile_snapshot(snapshot)
                copy['updated_at'] = storage.utc_now()
                touched += 1
            save(data)
            lines.append('  同步副本 %d 份' % touched)

    print('\n'.join(lines))


if __name__ == '__main__':
    main()
