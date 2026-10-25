from __future__ import annotations

from copy import deepcopy

from services.api.category_rules import (
    build_profile_snapshot,
    hash_profile_snapshot,
    validate_attribute_values,
    validate_profile_version,
)


def _field(field_id: str, **overrides):
    item = {
        "field_id": field_id,
        "label": field_id.replace("_", " ").title(),
        "type": "text",
        "required": False,
        "required_when": None,
        "options": [],
        "default_value": "",
        "automation": {"strategy": "label_or_field_id", "page_field_id": field_id},
    }
    item.update(overrides)
    return item


def _version(*fields):
    return {
        "id": "version-1",
        "profile_id": "profile-1",
        "version": 1,
        "status": "candidate",
        "fields": list(fields),
        "variation": {"required": True, "axes": ["color", "size"], "allowed_axes": ["color", "size"]},
    }


def test_different_profiles_resolve_different_required_fields():
    mens = _version(_field("material", required=True))
    kids = _version(_field("age_group", required=True))

    assert validate_attribute_values(mens, {}).missing_field_ids == ["material"]
    assert validate_attribute_values(kids, {}).missing_field_ids == ["age_group"]


def test_conditional_required_field_uses_whitelisted_operators():
    version = _version(
        _field("age_group"),
        _field(
            "height",
            required_when={"field": "age_group", "op": "in", "value": ["baby"]},
        ),
    )

    missing = validate_attribute_values(version, {"age_group": "baby"})
    present = validate_attribute_values(version, {"age_group": "adult"})

    assert missing.missing_field_ids == ["height"]
    assert present.valid is True


def test_all_and_any_conditions_are_supported_without_nested_code():
    all_version = _version(
        _field("age_group"),
        _field("customized", type="boolean"),
        _field(
            "height",
            required_when={
                "all": [
                    {"field": "age_group", "op": "eq", "value": "baby"},
                    {"field": "customized", "op": "neq", "value": True},
                ]
            },
        ),
    )
    any_version = _version(
        _field("age_group"),
        _field("brand"),
        _field(
            "certificate",
            required_when={
                "any": [
                    {"field": "age_group", "op": "in", "value": ["baby"]},
                    {"field": "brand", "op": "present"},
                ]
            },
        ),
    )

    assert validate_attribute_values(
        all_version, {"age_group": "baby", "customized": False}
    ).missing_field_ids == ["height"]
    assert validate_attribute_values(
        any_version, {"age_group": "adult", "brand": "Known"}
    ).missing_field_ids == ["certificate"]


def test_unknown_condition_operator_blocks_profile_publication():
    version = _version(
        _field(
            "height",
            required_when={"field": "age_group", "op": "python", "value": "danger"},
        )
    )

    result = validate_profile_version(version)

    assert result.valid is False
    assert result.blocking_errors[0].code == "unsupported_condition"


def test_select_values_and_types_are_validated():
    version = _version(
        _field(
            "material",
            type="single_select",
            required=True,
            options=[{"value": "cotton", "label": "Cotton"}],
        ),
        _field("weight", type="number"),
    )

    result = validate_attribute_values(version, {"material": "silk", "weight": "heavy"})

    # 枚举外的值是马帮支持的「自填选项」（原产地=Philippines 这类自选值）：
    # 只提醒、不阻塞；类型不对才是硬错误。
    assert [error.code for error in result.errors] == ["invalid_type"]
    assert result.valid is False
    assert [warning.code for warning in result.warnings] == ["custom_option"]
    assert result.warnings[0].field_id == "material"
    assert "silk" in result.warnings[0].message


def test_custom_option_value_is_not_blocking():
    """枚举外的自选值：采集端会并入枚举、执行器走马帮「自填选项」，故不算错误。"""

    version = _version(
        _field(
            "100037",
            label="原产地",
            type="single_select",
            required=True,
            options=[{"value": "93", "label": "其他 (Others)"}],
        ),
    )

    result = validate_attribute_values(version, {"100037": "Philippines"})

    assert result.valid is True
    assert result.errors == []
    assert [warning.code for warning in result.warnings] == ["custom_option"]
    assert "Philippines" in result.warnings[0].message

    # 多选只列真正越界的值，命中枚举的值不出现在提示里。
    multi = _version(
        _field(
            "100134",
            label="材料",
            type="multi_select",
            required=True,
            options=[{"value": "1149", "label": "棉花"}],
        ),
    )

    mixed = validate_attribute_values(multi, {"100134": ["1149", "Silk"]})

    assert mixed.valid is True
    assert "Silk" in mixed.warnings[0].message
    assert "1149" not in mixed.warnings[0].message


def test_required_field_without_automation_mapping_blocks_publication():
    version = _version(_field("material", required=True, automation={}))

    result = validate_profile_version(version)

    assert result.valid is False
    assert result.blocking_errors[0].code == "missing_automation_mapping"


def test_profile_snapshot_hash_is_stable_and_changes_with_rules():
    profile = {
        "id": "profile-1",
        "platform": "shopee",
        "country": "PH",
        "marketplace_category_id": "100244",
        "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
    }
    version = _version(_field("material", required=True))
    snapshot = build_profile_snapshot(profile, version)
    reordered = {key: snapshot[key] for key in reversed(snapshot)}

    assert hash_profile_snapshot(snapshot) == hash_profile_snapshot(reordered)

    changed = deepcopy(snapshot)
    changed["fields"][0]["required"] = False
    assert hash_profile_snapshot(snapshot) != hash_profile_snapshot(changed)
