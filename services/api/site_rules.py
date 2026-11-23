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


def normalize_country(value: Any) -> str:
    return str(value or "").strip().upper()


def currency_for_country(country: Any, fallback: str = "") -> str:
    """站点默认币种；GLOBAL/未知站点返回 ``fallback``（默认空，不猜）。"""

    return CURRENCY_BY_COUNTRY.get(normalize_country(country), fallback)
