from __future__ import annotations

import pytest

from services.api.runtime_configuration import (
    default_configuration,
    public_runtime_configuration,
    update_runtime_configuration,
)


def test_defaults_include_operational_controls() -> None:
    config = default_configuration()

    assert config["worker"]["auto_start"] is True
    assert config["worker"]["max_attempts"] == 3
    assert config["store_collection"]["enabled"] is True
    assert config["store_collection"]["interval_minutes"] == 360
    assert config["store_collection"]["max_attempts"] == 3
    assert config["nas"]["scan_mode"] == "auto"
    assert config["nas"]["max_files"] == 3000
    assert config["ai_listing_generation"]["fill_attributes_into_description"] is True


def test_fill_attributes_toggle_is_persisted() -> None:
    result = update_runtime_configuration(
        default_configuration(),
        {"ai_listing_generation": {"fill_attributes_into_description": False}},
    )

    assert result["ai_listing_generation"]["fill_attributes_into_description"] is False

    reenabled = update_runtime_configuration(
        result,
        {"ai_listing_generation": {"fill_attributes_into_description": True}},
    )
    assert reenabled["ai_listing_generation"]["fill_attributes_into_description"] is True


def test_operational_patch_validates_bounds_and_marks_worker_restart() -> None:
    result = update_runtime_configuration(
        default_configuration(),
        {
            "worker": {"auto_start": False, "max_attempts": 5},
            "store_collection": {"enabled": False, "interval_minutes": 60},
            "nas": {"scan_mode": "recursive", "max_files": 500},
        },
    )

    assert result["worker"]["auto_start"] is True
    assert result["worker"]["max_attempts"] == 5
    assert result["worker"]["restart_required"] is True
    assert result["store_collection"]["interval_minutes"] == 60
    assert result["nas"]["scan_mode"] == "recursive"


def test_worker_auto_start_is_always_enabled_for_legacy_configuration() -> None:
    config = public_runtime_configuration({"worker": {"auto_start": False}})

    assert config["worker"]["auto_start"] is True


@pytest.mark.parametrize(
    "patch",
    [
        {"worker": {"max_attempts": 0}},
        {"store_collection": {"interval_minutes": 0}},
        {"nas": {"max_files": 0}},
        {"nas": {"scan_mode": "unsupported"}},
    ],
)
def test_invalid_operational_values_are_rejected(patch: dict) -> None:
    with pytest.raises(ValueError):
        update_runtime_configuration(default_configuration(), patch)


def test_public_configuration_has_no_secrets_and_keeps_safety_fixed() -> None:
    config = default_configuration()
    config["ai"]["api_key"] = "secret"

    public = public_runtime_configuration(config)

    assert "secret" not in str(public)
    assert public["safety"] == {
        "save_draft_only": True,
        "publish_allowed": False,
        "editable": False,
    }
