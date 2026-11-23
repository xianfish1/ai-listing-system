"""多站点店铺模板一致性自检。

检查口径（与运行时一致）：
- 模板/店铺选项的币种必须等于站点默认币种（site_rules.CURRENCY_BY_COUNTRY）；
- 本土(site)模板绑定的类目方案 country 必须等于模板 country；
- 跨境(cnsc_global/cnsc_3pf)模板绑定 GLOBAL 方案；
- 模板里不能再出现「衣领类型=Crew Neck」这种页面上不存在的自选值。

用法：
  python -I scripts/verify_site_template_rules.py
退出码 0 = 全部通过；1 = 有违规项。
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from services.api.listing_modes import is_cnsc_page, normalize_listing_mode  # noqa: E402
from services.api.site_rules import CURRENCY_BY_COUNTRY, currency_for_country  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    with open(os.path.join(ROOT, "data", "store.json"), encoding="utf-8") as handle:
        data = json.load(handle)
    profiles = {str(item.get("id")): item for item in data.get("category_profiles") or []}
    versions = {str(item.get("id")): item for item in data.get("category_profile_versions") or []}
    problems: list[str] = []
    warnings: list[str] = []

    for option in data.get("mabang_store_options") or []:
        country = str(option.get("country") or "").upper()
        expected = currency_for_country(country)
        actual = str(option.get("currency") or "").upper()
        if expected and actual != expected:
            problems.append(f"店铺选项 {option.get('store_name')}：country={country} currency={actual or '-'}，应为 {expected}")

    for template in data.get("store_templates") or []:
        name = template.get("template_name") or template.get("id")
        country = str(template.get("country") or "").upper()
        mode = normalize_listing_mode(template.get("listing_mode"))
        expected = currency_for_country(country)
        actual = str(template.get("currency") or "").upper()
        if expected and actual != expected:
            problems.append(f"模板 {name}：country={country} currency={actual or '-'}，应为 {expected}")
        profile = profiles.get(str(template.get("category_profile_id") or "")) or {}
        version = versions.get(str(template.get("category_profile_version_id") or "")) or {}
        profile_country = str(profile.get("country") or "").upper()
        if not profile or not version:
            problems.append(f"模板 {name}：缺少已发布的类目方案/版本")
        elif is_cnsc_page(mode):
            if profile_country != "GLOBAL":
                problems.append(f"模板 {name}：{mode} 应绑 GLOBAL 方案，实际 {profile_country}")
        elif profile_country != country:
            problems.append(f"模板 {name}：country={country} 绑了 {profile_country} 的方案")
        if profile and version:
            current = str(profile.get("active_version_id") or profile.get("published_version_id") or "")
            if current and current != str(version.get("id")):
                problems.append(f"模板 {name}：绑定的方案版本不是当前发布版")
        if str((template.get("attribute_values") or {}).get("100174") or "").strip().lower() == "crew neck":
            problems.append(f"模板 {name}：衣领类型仍是页面上不存在的 Crew Neck")

    print("支持站点币种：" + ", ".join(f"{code}={cur}" for code, cur in sorted(CURRENCY_BY_COUNTRY.items())))
    for warning in warnings:
        print("WARN " + warning)
    for problem in problems:
        print("FAIL " + problem)
    print("OK" if not problems else f"{len(problems)} 项不一致")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
