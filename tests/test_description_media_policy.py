from __future__ import annotations

from services.api.description_media_policy import (
    DESCRIPTION_MEDIA_MAX_IMAGES_BY_COUNTRY,
    description_media_max_images,
    normalize_country,
)


def test_malaysia_and_indonesia_description_media_limits() -> None:
    # 运营实测的站点规则：MY 描述最多 3 张（含尺码表），ID 描述不能放图。
    assert description_media_max_images("MY") == 3
    assert description_media_max_images("my") == 3
    assert description_media_max_images(" ID ") == 0


def test_other_markets_have_no_extra_limit() -> None:
    # 其它站点沿用原行为（尺码表 + 全部产品图）：None = 不限。
    for country in ("PH", "TW", "SG", "TH", "MX", "VN", "BR", "GLOBAL", "", None):
        assert description_media_max_images(country) is None


def test_country_normalization_and_table_shape() -> None:
    assert normalize_country("  my ") == "MY"
    assert normalize_country(None) == ""
    # 上限只能是非负整数或 None（None=不限，0=不插），避免下发负数/字符串。
    for value in DESCRIPTION_MEDIA_MAX_IMAGES_BY_COUNTRY.values():
        assert value is None or (isinstance(value, int) and value >= 0)
