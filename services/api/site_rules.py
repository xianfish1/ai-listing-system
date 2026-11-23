"""各本土站点的固定规则（币种等）。

店铺模板/副本的 ``country`` 决定币种、描述配图上限、类目方案作用域。这里只放
**与站点强绑定、不随店铺变化**的规则，避免各条链路再各自写死 ``MYR``。

新增站点时先在这里补币种；描述配图上限仍以
:mod:`services.api.description_media_policy` 的实测表为准（未知站点沿用不限，
可用模板/副本上的 ``description_media_max_images`` 单独收紧）。
"""

from __future__ import annotations

from typing import Any

CURRENCY_BY_COUNTRY: dict[str, str] = {
    "PH": "PHP",
    "MY": "MYR",
    "TH": "THB",
    "ID": "IDR",
    "SG": "SGD",
    "TW": "TWD",
    "VN": "VND",
    "BR": "BRL",
    "MX": "MXN",
}

# 马帮「商品标题」输入框的站点上限。PH 2026-11-24 在本机马帮页面实测
# maxLength=100；其余站点先按 Shopee 的 120 作为预估上限。最终以马帮页面
# 实际 maxLength 为准——执行器填标题时会读取控件限长并按词边界裁剪。
TITLE_MAX_CHARS_BY_COUNTRY: dict[str, int] = {
    "PH": 100,
}
DEFAULT_TITLE_MAX_CHARS = 120


def normalize_country(value: Any) -> str:
    return str(value or "").strip().upper()


def currency_for_country(country: Any, fallback: str = "") -> str:
    """站点默认币种；GLOBAL/未知站点返回 ``fallback``（默认空，不猜）。"""

    return CURRENCY_BY_COUNTRY.get(normalize_country(country), fallback)


def title_max_chars_for_country(country: Any, fallback: int | None = None) -> int:
    """站点商品标题的预估字符上限；未知站点用 120 兜底。"""

    default = DEFAULT_TITLE_MAX_CHARS if fallback is None else int(fallback)
    return TITLE_MAX_CHARS_BY_COUNTRY.get(normalize_country(country), default)


def title_max_chars_for_sites(sites: Any, country: Any = "") -> int:
    """多站点副本取所有目标站点里最严的上限；没有目标站点时按副本国家。"""

    codes: list[str] = []
    for site in sites or []:
        if isinstance(site, dict):
            code = site.get("site_code") or site.get("country")
        else:
            code = site
        code = normalize_country(code)
        if code:
            codes.append(code)
    if codes:
        return min(title_max_chars_for_country(code) for code in codes)
    return title_max_chars_for_country(country)

