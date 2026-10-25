from __future__ import annotations

from services.api import storage


def _cnsc_copy(**overrides) -> dict:
    copy = {
        "id": "copy-1",
        "listing_mode": "cnsc_global",
        "listing_title": "全球卫衣",
        "description": "描述文案已就绪",
        "description_status": "ready",
        "description_profile_version_id": "",
        "store_name": "CNSC-Store",
        "mabang_store_id": "cnsc-store-1",
        "marketplace_category_path": "Men Clothes > Tops",
        "mabang_category_id": "category-1",
        "main_image": "main.jpg",
        "asset_root": "\\\\nas\\assets",
        "size_chart_image": "chart.png",
        "stock": "",
        "weight_kg": 0.5,
        "package_length_cm": 20,
        "package_width_cm": 15,
        "package_height_cm": 5,
    }
    copy.update(overrides)
    return copy


def test_cnsc_stock_is_ready_when_any_warehouse_has_stock() -> None:
    skus = [{"warehouse_stocks": {"TH": 0, "CN_GZ": 0, "PH": 3}}]

    result = storage._check_automation_readiness(_cnsc_copy(), skus=skus, prices=[{"id": "price-1"}])

    assert "stock" not in result["missing_fields"]


def test_cnsc_stock_still_missing_when_every_warehouse_is_empty() -> None:
    skus = [
        {"warehouse_stocks": {"TH": 0, "CN_GZ": 0, "PH": 0}},
        {"warehouse_stocks": {}},
    ]

    result = storage._check_automation_readiness(_cnsc_copy(), skus=skus, prices=[{"id": "price-1"}])

    assert result["ready"] is False
    assert "stock" in result["missing_fields"]


def test_cnsc_all_zero_warehouse_stocks_are_ready() -> None:
    # 马帮把 泰国/广州/菲律宾仓库存 三列当必填，0 是合法值（留空才报错）：
    # 三仓全 0 且每仓都可解析出非负整数时放行，执行器会把 0 逐仓写上页面。
    skus = [{"warehouse_stocks": {"TH": 0, "CN_GZ": 0, "PH": 0}}]

    result = storage._check_automation_readiness(_cnsc_copy(), skus=skus, prices=[{"id": "price-1"}])

    assert "stock" not in result["missing_fields"]


def test_cnsc_missing_sku_stocks_fall_back_to_copy_defaults() -> None:
    # SKU 自带库存缺仓时按副本级默认回退（与执行器解析顺序一致），全 0 默认同样放行。
    skus = [{"warehouse_stocks": {"CN_GZ": 300}}]

    result = storage._check_automation_readiness(
        _cnsc_copy(warehouse_stock_defaults={"TH": 0, "CN_GZ": 0, "PH": 0}),
        skus=skus,
        prices=[{"id": "price-1"}],
    )

    assert "stock" not in result["missing_fields"]


def test_description_profile_reminder_follows_description_presence() -> None:
    copy = _cnsc_copy()

    ready = storage._check_automation_readiness(copy, skus=[], prices=[{"id": "price-1"}])
    assert "description_profile_version_id" not in ready["missing_fields"]

    cleared = storage._check_automation_readiness(
        _cnsc_copy(description="", description_status=""), skus=[], prices=[{"id": "price-1"}]
    )
    assert "description_profile_version_id" in cleared["missing_fields"]
    assert "description_pending" not in cleared["missing_fields"]


def test_size_chart_ready_when_any_of_four_sources_present() -> None:
    """尺码图四源任一存在即算就绪：image / image_path / 上传 asset / 马帮模板。
    回归：后端 readiness 只认前两源，仅有 asset 或 mabang 模板的副本会误报缺。"""
    prices = [{"id": "price-1"}]
    local_copy = {"listing_mode": "site", "mabang_discount_id": "discount-1"}

    # 基线：四源全空 → 报缺
    base = storage._check_automation_readiness({**_cnsc_copy(size_chart_image="", size_chart_image_path=""), **local_copy}, skus=[], prices=prices)
    assert "size_chart_image" in base["missing_fields"]

    # 源 1：文件名（原本就支持）
    by_image = storage._check_automation_readiness({**_cnsc_copy(size_chart_image="chart.png"), **local_copy}, skus=[], prices=prices)
    assert "size_chart_image" not in by_image["missing_fields"]

    # 源 2：本地路径
    by_path = storage._check_automation_readiness(
        {**_cnsc_copy(size_chart_image="", size_chart_image_path=r"\nas\charts\chart.png"), **local_copy}, skus=[], prices=prices
    )
    assert "size_chart_image" not in by_path["missing_fields"]

    # 源 3：上传的尺码图 asset id
    by_asset = storage._check_automation_readiness(
        {**_cnsc_copy(size_chart_image="", size_chart_image_path="", size_chart_asset_id="chart-asset-1"), **local_copy}, skus=[], prices=prices
    )
    assert "size_chart_image" not in by_asset["missing_fields"]

    # 源 4：马帮尺码图模板 id
    by_template = storage._check_automation_readiness(
        {**_cnsc_copy(size_chart_image="", size_chart_image_path="", mabang_size_chart_template_id="tpl-1"), **local_copy}, skus=[], prices=prices
    )
    assert "size_chart_image" not in by_template["missing_fields"]


def test_readiness_keeps_local_and_cnsc_fields_separate() -> None:
    local = storage._check_automation_readiness(
        {
            "listing_mode": "site",
            "listing_title": "本土商品",
            "description": "描述",
            "store_name": "本土店",
            "mabang_store_id": "site-store",
            "marketplace_category_path": "Men > Tops",
            "mabang_category_id": "category-1",
            "main_image": "main.jpg",
            "asset_root": "\\\\nas\\assets",
            "size_chart_image": "chart.png",
            "stock": 1,
            "weight_kg": 0.5,
            "package_length_cm": 20,
            "package_width_cm": 15,
            "package_height_cm": 5,
            "target_site_stores": [],
            "price_tiers": [],
            "warehouse_stock_defaults": {},
        },
        skus=[{"id": "sku-1"}],
        prices=[{"id": "price-1"}],
    )
    assert not any(field.startswith("target_site_stores") for field in local["missing_fields"])
    assert "site_price_matrix" not in local["missing_fields"]
    assert "warehouse_stock_defaults" not in local["missing_fields"]

    cnsc = storage._check_automation_readiness(
        {
            **_cnsc_copy(size_chart_image="", size_chart_image_path=""),
            "promotion_activity_id": "local-activity",
            "mabang_discount_id": "local-discount",
        },
        skus=[{"id": "sku-1", "warehouse_stocks": {"PH": 1}}],
        prices=[{"id": "price-1", "cost_price": 1}],
    )
    assert "size_chart_image" not in cnsc["missing_fields"]
    assert "mabang_discount_id" not in cnsc["missing_fields"]
    assert "promotion_unresolved" not in cnsc["missing_fields"]


def test_readiness_warns_about_a_copy_without_any_category_profile_binding() -> None:
    """现场 2026-09-28：跨境副本 FBDC11 / B6BF84A8（100059 / 100115 Cosplay 服装）有类目、
    但档案引用与快照都是空的（这两个类目从未采集），就绪页却显示"就绪"。属性阶段因此没有
    任何规则可依，执行器只能靠现场读必填 + AI 现猜 → 保存被 pre_save_validation_failed 拦。
    这里只提醒、不阻塞：无档案副本历史上一直能生成任务（AI 现场补齐）。"""

    result = storage._check_automation_readiness(
        _cnsc_copy(),
        skus=[{"id": "sku-1", "warehouse_stocks": {"PH": 1}}],
        prices=[{"id": "price-1", "cost_price": 1}],
    )

    assert "category_profile_missing" in result["warnings"]
    assert "category_profile_missing" not in result["missing_fields"]


def test_readiness_does_not_warn_when_the_profile_is_bound_but_has_no_fields() -> None:
    """采过但确实没有属性的类目不算缺失：副本有绑定、只是快照字段为空 → 不提醒。"""

    result = storage._check_automation_readiness(
        {
            **_cnsc_copy(),
            "category_profile_id": "shopee-global-100244",
            "category_profile_version_id": "version-1",
            "category_profile_snapshot": {"fields": []},
        },
        skus=[{"id": "sku-1", "warehouse_stocks": {"PH": 1}}],
        prices=[{"id": "price-1", "cost_price": 1}],
    )

    assert "category_profile_missing" not in result["warnings"]


def test_readiness_does_not_warn_about_a_missing_profile_without_a_category() -> None:
    """还没选类目的副本不报"缺少类目方案"——先后关系由 marketplace_category_path/mabang_category_id 报。"""

    result = storage._check_automation_readiness(
        _cnsc_copy(marketplace_category_path="", mabang_category_id=""),
        skus=[{"id": "sku-1", "warehouse_stocks": {"PH": 1}}],
        prices=[{"id": "price-1", "cost_price": 1}],
    )

    assert "category_profile_missing" not in result["warnings"]


def test_readiness_keeps_required_category_attribute_ids() -> None:
    result = storage._check_automation_readiness(
        {
            **_cnsc_copy(),
            "category_profile_snapshot": {
                "fields": [
                    {"field_id": "100022", "required": True},
                    {"field_id": "100134", "required": True},
                    {"field_id": "100168", "required": True},
                ]
            },
            "attribute_values": {},
        },
        skus=[{"id": "sku-1", "warehouse_stocks": {"PH": 1}}],
        prices=[{"id": "price-1", "cost_price": 1}],
    )

    assert {
        "category_attributes.100022",
        "category_attributes.100134",
        "category_attributes.100168",
    }.issubset(result["missing_fields"])


def test_readiness_accepts_a_category_attribute_custom_option_value() -> None:
    """回归：原产地=Philippines 这类枚举外的自选值曾被当成「必填类目属性未填写」拦住
    （validate_attribute_values 报 invalid_option），副本无法生成自动化任务。
    实际执行器会走马帮「自填选项」写入，所以只提醒、不阻塞。"""

    result = storage._check_automation_readiness(
        {
            **_cnsc_copy(listing_mode="site"),
            "category_profile_snapshot": {
                "fields": [
                    {
                        "field_id": "100037",
                        "label": "原产地",
                        "type": "single_select",
                        "required": False,
                        "options": [{"value": "93", "label": "其他 (Others)"}],
                    },
                ]
            },
            "attribute_values": {"100037": "Philippines"},
        },
        skus=[{"id": "sku-1", "warehouse_stocks": {"PH": 1}}],
        prices=[{"id": "price-1", "cost_price": 1}],
    )

    assert "category_attributes.100037" not in result["missing_fields"]
    assert result["warnings"] == ["category_custom_option.100037"]

    # 值不在枚举里但类型对不了（这里是数字）仍要报错，别把类型问题也放行。
    broken = storage._check_automation_readiness(
        {
            **_cnsc_copy(listing_mode="site"),
            "category_profile_snapshot": {
                "fields": [
                    {
                        "field_id": "100037",
                        "label": "原产地",
                        "type": "single_select",
                        "required": True,
                        "options": [{"value": "93", "label": "其他 (Others)"}],
                    },
                ]
            },
            "attribute_values": {"100037": 93},
        },
        skus=[{"id": "sku-1", "warehouse_stocks": {"PH": 1}}],
        prices=[{"id": "price-1", "cost_price": 1}],
    )

    assert "category_attributes.100037" in broken["missing_fields"]
