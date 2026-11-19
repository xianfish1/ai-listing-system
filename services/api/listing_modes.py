"""刊登模式能力层（site / cnsc_global / cnsc_3pf）。

三种模式不是一条轴上的三个档位，而是两组正交能力：

- **页面/入口**：`is_cnsc_page` —— 是否走马帮「CNSC 待发布商品」页签与 global 详情页，
  消费目标站点店铺、站点价格表、三仓库存、上架后自动发布。
- **上款规则**：`is_local_like` —— 是否按本土要求上款，消费默认 SKU(白T恤)、尺码表、
  描述末尾插图、默认视频、SKU 去括号、模板优先尺码。

`site` 只满足第二组，`cnsc_global` 只满足第一组，`cnsc_3pf`（跨境 3PF）两组都满足。
`is_pure_cnsc` 用来保留改动前的 `cnsc_global` 分支（例如单店折扣活动、类目属性宽松匹配）。

所有判断都必须走这里：值集合在 storage / 导入 / 执行器 / 前端各有一份，散落硬编码会
让第三种模式漏改。
"""

from __future__ import annotations

import re
from typing import Any

LISTING_MODE_SITE = "site"
LISTING_MODE_CNSC_GLOBAL = "cnsc_global"
LISTING_MODE_CNSC_3PF = "cnsc_3pf"

ALLOWED_LISTING_MODES = (
    LISTING_MODE_SITE,
    LISTING_MODE_CNSC_GLOBAL,
    LISTING_MODE_CNSC_3PF,
)

LISTING_MODE_LABELS = {
    LISTING_MODE_SITE: "本土",
    LISTING_MODE_CNSC_GLOBAL: "跨境",
    LISTING_MODE_CNSC_3PF: "跨境本土 3PF",
}


def normalize_listing_mode(value: Any, *, default: str = LISTING_MODE_SITE) -> str:
    """归一化成三种合法值之一；空值回落 ``default``，未知值原样返回小写串。

    未知值不在这里静默改成 site：调用方（模板校验、页面导入）需要看到原值才能报错。
    """

    text = re.sub(r"\s+", "", str(value or "")).lower()
    if not text:
        return default
    return text


def is_cnsc_page(value: Any) -> bool:
    """是否走 CNSC 全球商品页（页面/入口维度）。"""

    return normalize_listing_mode(value) in {
        LISTING_MODE_CNSC_GLOBAL,
        LISTING_MODE_CNSC_3PF,
    }


def is_local_like(value: Any) -> bool:
    """是否按本土要求上款（默认 SKU / 尺码表 / 描述插图 / 默认视频 / 去括号）。"""

    return normalize_listing_mode(value) in {
        LISTING_MODE_SITE,
        LISTING_MODE_CNSC_3PF,
    }


def is_pure_cnsc(value: Any) -> bool:
    """是否原有的 CNSC 全球商品（保留改动前的跨境分支）。"""

    return normalize_listing_mode(value) == LISTING_MODE_CNSC_GLOBAL


def listing_mode_label(value: Any) -> str:
    return LISTING_MODE_LABELS.get(normalize_listing_mode(value), "本土")


# 「类目方案」的作用域站点：本土站点用店铺自己的国家（PH/TW…），跨境页
# （cnsc_global / cnsc_3pf）用 GLOBAL。同一个类目在两侧是**两份**必填项规则：
# 跨境页的必填属性/选项与本土站点不是一套（2026-11-19 用户口径），
# 采集、绑定、校验都必须按这个作用域走，否则 CNSC 页采集会覆盖本土方案。
GLOBAL_CATEGORY_SCOPE = "GLOBAL"


def category_profile_country(listing_mode: Any, store_country: Any) -> str:
    """类目方案（category profile）应绑定的 country 作用域。"""

    if is_cnsc_page(listing_mode):
        return GLOBAL_CATEGORY_SCOPE
    return str(store_country or "").strip().upper()
