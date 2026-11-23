"""商品描述配图上限（按站点/国家）。

Shopee 各站点对「商品描述」里能放的图片数量要求不一样（运营实测，2026-11-18）：

- **MY（马来西亚）**：描述最多 3 张图；
- **ID（印度尼西亚）**：描述里不能上传图片（0 张）。

PH 实测 10 张仍可保存（2026-11-23 全模板测试），因此 PH 不在表里＝不限。TH/SG
目前没有实测数据，同样按不限处理；上这些站点时若马帮提示上限，直接在店铺模板/
副本上填 ``description_media_max_images``（如 3）即可收紧，不用改代码。

计数口径＝描述里这一批图的**总张数**，尺码表那张也算一张：上限 3 时描述是
「尺码表 + 2 张产品图」（没有尺码图时是 3 张产品图）。其它站点没有额外限制
（返回 ``None``，沿用执行器的收集护栏：尺码表 + 全部产品图）。

只有本土/3PF（:func:`services.api.listing_modes.is_local_like`）会往描述里插图，
纯跨境 CNSC 的描述页没有这套入口，不读这个上限。

改这张表要同时看 ``scripts/mabang_playwright_executor.cjs`` 里的
``buildDescriptionMediaPlan``：执行器只执行任务里下发的 ``description_media``，
不自己维护国家表，避免两份口径漂移。
"""

from __future__ import annotations

from typing import Any

# None = 不限（沿用执行器收集上限）。
DESCRIPTION_MEDIA_MAX_IMAGES_BY_COUNTRY: dict[str, int | None] = {
    "MY": 3,
    "ID": 0,
}


def normalize_country(value: Any) -> str:
    return str(value or "").strip().upper()


def description_media_max_images(country: Any) -> int | None:
    """该站点描述允许的图片总张数；``None`` 表示不限，``0`` 表示一张都不插。"""

    return DESCRIPTION_MEDIA_MAX_IMAGES_BY_COUNTRY.get(normalize_country(country))
