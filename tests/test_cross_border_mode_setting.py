"""跨境模式（CNSC）界面开关的运行时配置契约：默认开、可关闭/重开、脏值归一化、公开透出。"""

from __future__ import annotations

from services.api.runtime_configuration import (
    default_configuration,
    merge_configuration,
    public_runtime_configuration,
    update_runtime_configuration,
)


def test_cross_border_mode_defaults_to_enabled() -> None:
    """默认开＝本土/跨境都显示，与改动前行为一致；旧库缺 display 组时合并补默认值。"""

    config = default_configuration()
    assert config["display"]["cross_border_enabled"] is True

    merged = merge_configuration({"worker": {}})
    assert merged["display"]["cross_border_enabled"] is True


def test_cross_border_mode_can_be_disabled_and_reenabled() -> None:
    current = update_runtime_configuration(
        default_configuration(),
        {"display": {"cross_border_enabled": False}},
    )
    assert current["display"]["cross_border_enabled"] is False

    reopened = update_runtime_configuration(
        current,
        {"display": {"cross_border_enabled": True}},
    )
    assert reopened["display"]["cross_border_enabled"] is True


def test_cross_border_mode_coerces_non_bool_values_to_bool() -> None:
    """脏值（字符串、数字、None）一律归一化为 bool，避免前端读到非布尔态。"""

    for raw in ("false", "true", 1, 0, None):
        current = update_runtime_configuration(
            default_configuration(),
            {"display": {"cross_border_enabled": raw}},
        )
        assert isinstance(current["display"]["cross_border_enabled"], bool)


def test_cross_border_mode_is_exposed_in_public_configuration() -> None:
    public = public_runtime_configuration(default_configuration())

    assert "display" in public
    assert "cross_border_enabled" in public["display"]


def test_unknown_display_field_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        update_runtime_configuration(default_configuration(), {"display": {"other": 1}})
