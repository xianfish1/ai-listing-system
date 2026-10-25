from __future__ import annotations

import pytest

from services.api.runtime_configuration import (
    default_configuration,
    public_runtime_configuration,
    update_runtime_configuration,
)


def test_publish_policy_is_read_only() -> None:
    config = public_runtime_configuration(default_configuration())

    assert config["safety"] == {"save_draft_only": True, "publish_allowed": False, "editable": False}


def test_worker_connection_change_sets_restart_required() -> None:
    result = update_runtime_configuration(
        default_configuration(), {"worker": {"cdp_url": "http://127.0.0.1:9223"}}
    )

    assert result["worker"]["restart_required"] is True


def test_publish_permission_change_is_rejected() -> None:
    with pytest.raises(ValueError, match="禁止修改发布策略"):
        update_runtime_configuration(default_configuration(), {"safety": {"publish_allowed": True}})


def test_unknown_configuration_group_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知设置分组"):
        update_runtime_configuration(default_configuration(), {"secrets": {"api_key": "secret"}})


def test_mabang_login_binding_fields_are_accepted_and_masked() -> None:
    updated = update_runtime_configuration(
        default_configuration(),
        {"mabang": {"login_account": "ops@example.com", "login_password": "stored-token"}},
    )

    assert updated["mabang"]["login_account"] == "ops@example.com"
    assert updated["mabang"]["login_password"] == "stored-token"

    public = public_runtime_configuration(updated)
    assert "login_password" not in public["mabang"]
    assert public["mabang"]["login_account"] == "ops@example.com"


def test_mabang_login_binding_unknown_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知设置字段"):
        update_runtime_configuration(default_configuration(), {"mabang": {"login_password_plain": "x"}})


def test_mabang_headless_defaults_to_a_visible_window_and_is_toggleable() -> None:
    assert default_configuration()["mabang"]["headless"] is False

    updated = update_runtime_configuration(default_configuration(), {"mabang": {"headless": True}})

    assert updated["mabang"]["headless"] is True
    assert public_runtime_configuration(updated)["mabang"]["headless"] is True

    # Any non-boolean value coming from the API is normalized before the launcher
    # ever sees it.
    normalized = update_runtime_configuration(updated, {"mabang": {"headless": ""}})

    assert normalized["mabang"]["headless"] is False


def test_public_configuration_never_returns_secret_fields() -> None:
    config = default_configuration()
    config["ai"]["api_key"] = "secret"

    public = public_runtime_configuration(config)

    assert "secret" not in str(public)
    assert "api_key" not in public["ai"]


def test_cnsc_price_site_order_persists_and_is_validated() -> None:
    updated = update_runtime_configuration(
        default_configuration(),
        {"cnsc": {"price_site_order": ["PH", "TW", "PH", "br"]}},
    )

    assert updated["cnsc"]["price_site_order"] == ["PH", "TW", "BR"]


def test_cnsc_price_site_order_rejects_unknown_site() -> None:
    with pytest.raises(ValueError, match="未知站点"):
        update_runtime_configuration(default_configuration(), {"cnsc": {"price_site_order": ["TW", "US"]}})


def test_network_lan_toggle_is_persisted_and_normalized() -> None:
    updated = update_runtime_configuration(
        default_configuration(),
        {"network": {"lan_enabled": True}},
    )

    assert updated["network"]["lan_enabled"] is True
    assert public_runtime_configuration(updated)["network"]["lan_enabled"] is True

    normalized = update_runtime_configuration(updated, {"network": {"lan_enabled": "off"}})
    assert normalized["network"]["lan_enabled"] is False


def test_network_lan_toggle_rejects_non_boolean_text() -> None:
    with pytest.raises(ValueError, match="局域网访问开关"):
        update_runtime_configuration(
            default_configuration(),
            {"network": {"lan_enabled": "maybe"}},
        )
