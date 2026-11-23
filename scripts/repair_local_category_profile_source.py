"""恢复被「马来店采集」污染的本土类目方案，并重绑本土模板。

背景
----
2026-11-19 本土JunsMY_Shopee（实为马来西亚本土店）的页面采集被当成 PH 写进了
shopee-ph-101757 / shopee-ph-101773（v4 / v12，18 字段的 MY/CNSC 集合）；11-20 的
类目作用域修复又把 6 个本土模板统一绑到了这份「当前发布版」。结果是本土模板的类目
属性（护理说明/适合/套筒类型/设计/闭合类型/拉伸/认证）全来自马来店页面，而 PH 本土页
根本没有这些字段。

修法
----
对每个本地站点方案：如果 active 版本的字段集合正好是那份坏的 18 字段集合，就从同一
方案里挑一个「含 PH 本土标记字段（尺寸 102530）、不含 CNSC 专有字段（护理说明 101298）」
的最近版本，复制成新的 active 版本；然后把该方案下的本土模板改绑过去，并按新字段集合
裁剪模板的 attribute_values / category_attributes。

已有的店铺刊登副本保持原快照不动（副本快照是锁定口径，重跑老副本不应被悄悄换规则）。

跨设备
------
同一份逻辑已经挂进 `services.api.storage._normalize_legacy_store_shape`，每台设备
升级后第一次冷加载会自动执行；这个脚本只是给「想先看计划 / 想立刻生效」的场景用。

用法
----
  python -I scripts/repair_local_category_profile_source.py --dry-run
  python -I scripts/repair_local_category_profile_source.py --apply
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
from services.api.store_migrations import repair_local_category_profile_sources  # noqa: E402

APPLY = "--apply" in sys.argv
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def load_raw() -> dict:
    with open(os.path.join(ROOT, "data", "store.json"), encoding="utf-8") as handle:
        return json.load(handle)


def describe(data_before: dict, data_after: dict) -> list[str]:
    lines: list[str] = []
    before_profiles = {str(p.get("id")): p for p in data_before.get("category_profiles") or []}
    after_profiles = {str(p.get("id")): p for p in data_after.get("category_profiles") or []}
    versions = {str(v.get("id")): v for v in data_after.get("category_profile_versions") or []}
    for profile_id, profile in after_profiles.items():
        old_id = str((before_profiles.get(profile_id) or {}).get("active_version_id") or "")
        new_id = str(profile.get("active_version_id") or "")
        if old_id == new_id:
            continue
        old = versions.get(old_id) or {}
        new = versions.get(new_id) or {}
        lines.append(
            "方案 %s：v%s → v%s（字段 %d → %d，来源 %s）" % (
                profile_id,
                old.get("version") or "?",
                new.get("version") or "?",
                len(old.get("fields") or []),
                len(new.get("fields") or []),
                (new.get("source") or {}).get("repaired_from_version_id", "")[:8],
            )
        )
    before_templates = {str(t.get("id")): t for t in data_before.get("store_templates") or []}
    for template in data_after.get("store_templates") or []:
        old = before_templates.get(str(template.get("id"))) or {}
        if old.get("category_profile_version_id") == template.get("category_profile_version_id"):
            continue
        lines.append(
            "模板 %s：绑定 %s → %s（v%s → v%s）" % (
                template.get("template_name"),
                str(old.get("category_profile_version_id") or "")[:8],
                str(template.get("category_profile_version_id") or "")[:8],
                old.get("version"),
                template.get("version"),
            )
        )
    return lines


def backup() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    target = os.path.join(ROOT, "data", "backups", f"pre-local-profile-repair-{stamp}")
    os.makedirs(target, exist_ok=True)
    for name in ("store.json", "store_template_versions.json"):
        source = os.path.join(ROOT, "data", name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(target, name))
    return target


def main() -> None:
    if APPLY:
        with storage.locked_store() as (data, save):
            before = deepcopy(data)
            changed = repair_local_category_profile_sources(data, now=storage.utc_now())
            lines = describe(before, data)
            if changed:
                path = backup()
                save(data)
                lines.append("已写入；备份目录：" + path)
            else:
                lines.append("没有需要修复的方案（已是本土采集版本）。")
        print("\n".join(lines))
        return

    data = load_raw()
    before = deepcopy(data)
    changed = repair_local_category_profile_sources(data, now=storage.utc_now())
    lines = ["== 计划"] if changed else ["没有需要修复的方案（已是本土采集版本）。"]
    lines.extend(describe(before, data))
    if changed:
        lines.append("\n[dry-run] 未写入。确认后停 API，再用 --apply 执行。")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
