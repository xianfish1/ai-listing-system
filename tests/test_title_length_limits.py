"""商品标题的站点长度上限：PH 实测 100，其余站点预估 120，多站点取最严。"""

from __future__ import annotations

from services.api.site_rules import (
    DEFAULT_TITLE_MAX_CHARS,
    title_max_chars_for_country,
    title_max_chars_for_sites,
)


def test_ph_uses_measured_100_and_unknown_sites_default_to_120() -> None:
    assert title_max_chars_for_country("PH") == 100
    assert title_max_chars_for_country("ph") == 100
    assert title_max_chars_for_country("MY") == DEFAULT_TITLE_MAX_CHARS == 120
    assert title_max_chars_for_country("TH") == 120
    assert title_max_chars_for_country("") == 120


def test_multi_site_copy_takes_the_strictest_target_site() -> None:
    assert title_max_chars_for_sites(["PH", "MY"]) == 100
    assert title_max_chars_for_sites([{"site_code": "PH"}, {"site_code": "TH"}]) == 100
    assert title_max_chars_for_sites(["MY", "SG"]) == 120
    # 没有目标站点（本土副本）时按副本国家。
    assert title_max_chars_for_sites([], "PH") == 100
    assert title_max_chars_for_sites(None, "MY") == 120
