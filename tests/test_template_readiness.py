from __future__ import annotations

from decimal import Decimal

from services.api.template_readiness import calculate_sale_price, check_store_template


def _category_version() -> dict:
    return {
        "id": "v-1",
        "fields": [
            {
                "field_id": "brand",
                "label": "品牌",
                "type": "text",
                "required": True,
                "automation": {"page_field_id": "brand"},
            }
        ],
    }


def test_sale_price_is_sixty_percent_with_currency_precision() -> None:
    assert calculate_sale_price("100", decimal_places=2) == Decimal("60.00")
    assert calculate_sale_price("99.99", decimal_places=0) == Decimal("60")


def test_template_reports_chinese_missing_fields_without_executor_fallbacks() -> None:
    result = check_store_template(
        {
            "category_profile_version_id": "v-1",
            "attribute_values": {"brand": "NoBrand"},
            "promotion_activity_id": "",
            "normal_price": "100",
            "sizes": [],
            "description_profile_version_id": "",
        },
        category_version=_category_version(),
        promotion=None,
    )

    assert result.status == "needs_supplement"
    assert {item.code for item in result.errors} >= {
        "stock_missing",
        "weight_missing",
        "package_length_missing",
        "package_width_missing",
        "package_height_missing",
        "sizes_missing",
        "description_profile_missing",
    }
    assert {item.code for item in result.warnings} == {"promotion_unconfigured"}
    assert all(item.message for item in result.errors)


def test_complete_template_is_ready() -> None:
    result = check_store_template(
        {
            "category_profile_version_id": "v-1",
            "attribute_values": {"brand": "NoBrand"},
            "promotion_activity_id": "activity-1",
            "normal_price": "100",
            "sale_price": "60",
            "stock": 20,
            "weight_kg": "0.35",
            "package_length_cm": "30",
            "package_width_cm": "24",
            "package_height_cm": "3",
            "sizes": ["S", "M"],
            "description_profile_version_id": "dpv-1",
            "size_chart_asset_id": "asset-1",
        },
        category_version=_category_version(),
        promotion={"id": "activity-1", "status": "available"},
    )

    assert result.ready is True
    assert result.status == "ready"
    assert result.errors == ()


def test_unresolved_and_expired_promotions_are_non_blocking_warnings() -> None:
    template = {
        "category_profile_version_id": "v-1",
        "attribute_values": {"brand": "NoBrand"},
        "promotion_activity_id": "activity-1",
        "stock": 20,
        "weight_kg": "0.35",
        "package_length_cm": "30",
        "package_width_cm": "24",
        "package_height_cm": "3",
        "sizes": ["S"],
        "description_profile_version_id": "dpv-1",
        "size_chart_asset_id": "asset-1",
    }

    unresolved = check_store_template(template, category_version=_category_version(), promotion=None)
    expired = check_store_template(
        template,
        category_version=_category_version(),
        promotion={"activity_id": "activity-1", "status": "expired"},
    )

    assert unresolved.ready is True
    assert {item.code for item in unresolved.warnings} == {"promotion_unresolved"}
    assert expired.ready is True
    assert {item.code for item in expired.warnings} == {"promotion_expired"}


def test_template_accepts_a_custom_option_category_attribute() -> None:
    """原产地=Philippines 这类枚举外的自选值不该把模板判成待补全（执行器走「自填选项」）。"""

    category_version = {
        "id": "v-1",
        "fields": [
            {
                "field_id": "100037",
                "label": "原产地",
                "type": "single_select",
                "required": True,
                "options": [{"value": "93", "label": "其他 (Others)"}],
                "automation": {"page_field_id": "100037"},
            }
        ],
    }
    result = check_store_template(
        {
            "category_profile_version_id": "v-1",
            "attribute_values": {"100037": "Philippines"},
            "promotion_activity_id": "activity-1",
            "stock": 20,
            "weight_kg": "0.35",
            "package_length_cm": "30",
            "package_width_cm": "24",
            "package_height_cm": "3",
            "sizes": ["S"],
            "description_profile_version_id": "dpv-1",
            "size_chart_asset_id": "asset-1",
        },
        category_version=category_version,
        promotion={"id": "activity-1", "status": "available"},
    )

    assert result.ready is True
    assert result.status == "ready"
    assert result.errors == ()
    assert {item.code for item in result.warnings} == {"category_custom_option"}
    warning = result.warnings[0]
    assert warning.field == "attribute_values.100037"
    assert "原产地" in warning.message and "Philippines" in warning.message


def test_cnsc_template_does_not_inherit_local_promotion_or_size_chart_requirements() -> None:
    result = check_store_template(
        {
            "listing_mode": "cnsc_global",
            "category_profile_version_id": "v-1",
            "attribute_values": {"brand": "NoBrand"},
            "promotion_activity_id": "local-activity",
            "stock": 20,
            "weight_kg": "0.35",
            "package_length_cm": "30",
            "package_width_cm": "24",
            "package_height_cm": "3",
            "sizes": ["S"],
            "description_profile_version_id": "dpv-1",
        },
        category_version=_category_version(),
        promotion=None,
    )

    assert result.ready is True
    assert result.errors == ()
    assert result.warnings == ()


def test_imported_incomplete_template_is_not_ready() -> None:
    category_version = {
        "id": "v-imported",
        "fields": [
            {"field_id": "100134", "label": "材料", "type": "multi_select", "required": True, "options": [{"value": "1149", "label": "Cotton"}], "automation": {"page_field_id": "100134"}},
            {"field_id": "required-empty", "label": "未选择必填项", "type": "single_select", "required": True, "options": [], "automation": {"page_field_id": "required-empty"}},
        ],
    }
    result = check_store_template(
        {
            "category_profile_version_id": "v-imported",
            "attribute_values": {"100134": ["1149"]},
            "promotion_activity_id": "",
            "sizes": ["S"],
            "missing_required_attributes": [{"field_id": "required-empty", "label": "未选择必填项"}],
        },
        category_version=category_version,
        promotion=None,
    )

    assert result.ready is False
    assert {item.code for item in result.errors} >= {
        "category_required",
        "stock_missing",
        "weight_missing",
        "package_length_missing",
        "package_width_missing",
        "package_height_missing",
    }
    assert {item.code for item in result.warnings} == {"promotion_unconfigured"}
