"""把一家店铺的「本土站点」整体迁到另一个国家（站点），并重绑模板与副本。

典型场景：本土JunsMY_Shopee 实际是马来西亚店，但历史导入被记成 PH，模板绑的也是
PH 方案（模板里显示条件/型号/尺寸这些 PH 字段，配了也写不进 MY 页面）。

做法：
1. 用**这家店自己页面采集出来的版本**作为字段来源（source run 的 snapshot.store.id
   等于目标店铺），不猜字段；
2. 在目标国家下建/复用 `shopee-<country>-<category>` 方案，把该版本复制成 active；
3. 模板改绑新方案，country/currency 跟着目标国家，属性值按新字段集合裁剪；
4. 该模板下的副本同步改绑、重建快照并重算 hash（只动元数据，不重新生成 SKU/价格）；
5. 店铺选项的 country/currency 一并更新。

已有任务快照不动；需要重跑的任务请重新生成。

用法：
  python -I scripts/migrate_store_site.py --store-id 2022248799 --country MY --dry-run
  python -I scripts/migrate_store_site.py --store-id 2022248799 --country MY --apply

--apply 前先停 API（进程内锁不能跨进程）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from copy import deepcopy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from services.api import storage  # noqa: E402
from services.api.category_rules import build_profile_snapshot, hash_profile_snapshot  # noqa: E402
from services.api.listing_modes import normalize_listing_mode  # noqa: E402
from services.api.site_rules import currency_for_country  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate a store's site templates to another Shopee site")
    parser.add_argument("--store-id", required=True, help="马帮店铺 id（mabang_store_id）")
    parser.add_argument("--country", required=True, help="目标站点，如 MY")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _runs(data: dict) -> dict[str, dict]:
    return {str(item.get("id")): item for item in data.get("mabang_page_import_runs") or []}


def _version_store_id(version: dict, runs: dict[str, dict]) -> str:
    run_id = str((version.get("source") or {}).get("run_id") or "")
    for prefix in ("page-import:", "category-scope-fix:"):
        if run_id.startswith(prefix):
            run_id = run_id[len(prefix):]
            break
    store = ((runs.get(run_id) or {}).get("snapshot") or {}).get("store") or {}
    return str(store.get("id") or "").strip()


def _field_ids(version: dict) -> set[str]:
    return {str(field.get("field_id")) for field in version.get("fields") or [] if field.get("field_id")}


def _source_version(data: dict, category_id: str, store_id: str, runs: dict[str, dict]) -> dict | None:
    """这家店自己页面采集出的、同一类目的最新版本。"""
    profiles = {str(item.get("id")): item for item in data.get("category_profiles") or []}
    candidates = []
    for version in data.get("category_profile_versions") or []:
        profile = profiles.get(str(version.get("profile_id"))) or {}
        if str(profile.get("marketplace_category_id") or "") != category_id:
            continue
        if str((version.get("source") or {}).get("type") or "") != "mabang_browser_collection":
            continue
        if _version_store_id(version, runs) != store_id:
            continue
        candidates.append(version)
    return max(
        candidates,
        key=lambda item: (int(item.get("version") or 0), str(item.get("created_at") or "")),
        default=None,
    )


def _ensure_profile(data: dict, *, platform: str, country: str, category: dict, source: dict, now: str) -> tuple[dict, dict]:
    profile_id = f"{platform}-{country}-{category['id']}".lower()
    profiles = data.setdefault("category_profiles", [])
    versions = data.setdefault("category_profile_versions", [])
    profile = next((item for item in profiles if str(item.get("id")) == profile_id), None)
    if profile is None:
        profile = {
            "id": profile_id,
            "platform": platform,
            "country": country,
            "marketplace_category_id": str(category["id"]),
            "marketplace_category_path": str(category.get("path") or ""),
            "display_name": str(category.get("path") or category["id"]),
            "created_at": now,
            "updated_at": now,
        }
        profiles.append(profile)
    same = next(
        (
            version for version in versions
            if str(version.get("profile_id")) == profile_id
            and _field_ids(version) == _field_ids(source)
            and version.get("status") in {"active", "published"}
        ),
        None,
    )
    if same:
        profile["active_version_id"] = same.get("id")
        profile["published_version_id"] = same.get("id")
        profile["updated_at"] = now
        return profile, same
    next_version = max(
        (int(item.get("version") or 0) for item in versions if str(item.get("profile_id")) == profile_id),
        default=0,
    ) + 1
    version = {
        "id": str(uuid.uuid4()),
        "profile_id": profile_id,
        "version": next_version,
        "status": "active",
        "fields": deepcopy(source.get("fields") or []),
        "variation": deepcopy(source.get("variation") or {}),
        "source": {
            "type": "site_migration",
            "from_version_id": str(source.get("id") or ""),
            "from_profile_id": str(source.get("profile_id") or ""),
            "migrated_at": now,
        },
        "schema_hash": "",
        "created_at": now,
        "activated_at": now,
        "updated_at": now,
    }
    version["schema_hash"] = hash_profile_snapshot(build_profile_snapshot(profile, version))
    versions.append(version)
    profile["active_version_id"] = version["id"]
    profile["published_version_id"] = version["id"]
    profile["updated_at"] = now
    return profile, version


def plan(data: dict, store_id: str, country: str) -> tuple[list[dict], list[str]]:
    runs = _runs(data)
    profiles = {str(item.get("id")): item for item in data.get("category_profiles") or []}
    versions = {str(item.get("id")): item for item in data.get("category_profile_versions") or []}
    steps: list[dict] = []
    errors: list[str] = []
    for template in data.get("store_templates") or []:
        if str(template.get("mabang_store_id") or "") != store_id:
            continue
        if normalize_listing_mode(template.get("listing_mode")) != "site":
            continue
        category_id = str(template.get("mabang_category_id") or "")
        profile_id = f"{template.get('platform') or 'shopee'}-{country}-{category_id}".lower().replace(" ", "-")
        if str(template.get("category_profile_id") or "") == profile_id and str(template.get("country") or "").upper() == country:
            continue
        source = _source_version(data, category_id, store_id, runs)
        if source is None:
            errors.append(f"模板 {template.get('template_name')}：找不到这家店自己采集的类目版本，请先做一次页面导入")
            continue
        steps.append({
            "template": template,
            "category": {
                "id": category_id,
                "path": str(template.get("marketplace_category_path") or ""),
            },
            "source": source,
            "profile_id": profile_id,
        })
    return steps, errors


def describe(steps: list[dict], errors: list[str], country: str) -> None:
    print(f"目标站点 {country}：{len(steps)} 个模板待迁移")
    runs = _runs_cache
    for step in steps:
        template = step["template"]
        source = step["source"]
        print(
            f"  模板 {template.get('template_name')}：{template.get('category_profile_id')}"
            f" v{versions_label(source)} → {step['profile_id']}（源 v{source.get('version')}，"
            f"{len(source.get('fields') or [])} 字段，采集店铺 {_version_store_id(source, runs)}）"
        )
    for error in errors:
        print("  !! " + error)


def versions_label(version: dict) -> str:
    return str(version.get("version") or "?")


_runs_cache: dict[str, dict] = {}


def main() -> None:
    args = parse_args()
    country = str(args.country or "").strip().upper()
    store_id = str(args.store_id or "").strip()
    if not country or not store_id:
        raise SystemExit("store-id / country required")
    if args.apply:
        with storage.locked_store() as (data, save):
            _runs_cache.clear()
            _runs_cache.update(_runs(data))
            steps, errors = plan(data, store_id, country)
            if errors:
                print("\n".join("!! " + error for error in errors))
                print("有模板没有可用的采集来源，已中止（未写入）。")
                return
            now = storage.utc_now()
            done = _apply(data, steps, country, now)
            option = next(
                (item for item in data.get("mabang_store_options") or [] if str(item.get("mabang_store_id")) == store_id),
                None,
            )
            if option is not None:
                option["country"] = country
                option["currency"] = currency_for_country(country)
                option["updated_at"] = now
            if done or option is not None:
                backup = _backup()
                save(data)
                print(f"已迁移 {done} 个模板；备份目录：{backup}")
            else:
                print("没有需要迁移的模板。")
        return

    with open(os.path.join(ROOT, "data", "store.json"), encoding="utf-8") as handle:
        data = json.load(handle)
    runs_path = os.path.join(ROOT, "data", "mabang_page_import_runs.json")
    if os.path.exists(runs_path):
        with open(runs_path, encoding="utf-8") as handle:
            runs_payload = json.load(handle)
        data["mabang_page_import_runs"] = (
            runs_payload.get("mabang_page_import_runs")
            if isinstance(runs_payload, dict)
            else runs_payload
        ) or []
    _runs_cache.clear()
    _runs_cache.update(_runs(data))
    steps, errors = plan(data, store_id, country)
    describe(steps, errors, country)
    if steps and not errors:
        print("\n[dry-run] 未写入。确认后停 API，再用 --apply 执行。")


def _apply(data: dict, steps: list[dict], country: str, now: str) -> int:
    version_ids: dict[str, dict] = {}
    done = 0
    for step in steps:
        template = step["template"]
        profile, version = _ensure_profile(
            data,
            platform=str(template.get("platform") or "shopee"),
            country=country,
            category=step["category"],
            source=step["source"],
            now=now,
        )
        version_ids[str(template.get("id"))] = version
        allowed = _field_ids(version)
        for key in ("attribute_values", "category_attributes"):
            values = template.get(key)
            if isinstance(values, dict):
                template[key] = {field_id: value for field_id, value in values.items() if str(field_id) in allowed}
        template["category_profile_id"] = profile["id"]
        template["category_profile_version_id"] = version["id"]
        template["country"] = country
        template["currency"] = currency_for_country(country)
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
        done += 1
        # 同模板副本：改绑/换快照/重算 hash，属性按新字段裁剪
        snapshot = build_profile_snapshot(profile, version)
        for copy in data.get("store_listing_copies") or []:
            if str(copy.get("template_id")) != str(template.get("id")):
                continue
            copy["category_profile_id"] = profile["id"]
            copy["category_profile_version_id"] = version["id"]
            copy["category_profile_snapshot"] = deepcopy(snapshot)
            copy["profile_snapshot_hash"] = hash_profile_snapshot(snapshot)
            copy["country"] = country
            copy["currency"] = currency_for_country(country)
            for key in ("attribute_values", "category_attributes"):
                values = copy.get(key)
                if isinstance(values, dict):
                    copy[key] = {field_id: value for field_id, value in values.items() if str(field_id) in allowed}
            copy["updated_at"] = now
    return done


def _backup() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    target = os.path.join(ROOT, "data", "backups", f"pre-site-migration-{stamp}")
    os.makedirs(target, exist_ok=True)
    for name in ("store.json", "store_listing_copies.json"):
        source = os.path.join(ROOT, "data", name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(target, name))
    return target


if __name__ == "__main__":
    main()
