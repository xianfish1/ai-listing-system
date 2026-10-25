"""本土店铺模板的「默认 SKU(白T恤)」与「SKU 去括号」规则。

覆盖点：
- 本土 SKU 编码去掉尺码的括号补充值，跨境(CNSC)保持原样；
- 默认 SKU 作为最后一个颜色跟随尺码展开，价格/图片/SKU 独立；
- 浏览器输入里默认颜色与其 SKU 行都在末尾（执行器按行号填写）；
- 就绪检查在启用后拦截缺项，跨境副本不做任何检查；
- 保存模板时同步到已有本土副本，跨境模板/副本不受影响。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


SIZES = ["130cm(21-26kg)", "140cm(24-30kg)"]


def _make_base(tmp_path: Path, colors: tuple[str, ...] = ("Black", "Blue", "Red")) -> dict:
    folder = tmp_path / "AL0353 童装T恤"
    folder.mkdir()
    (folder / "main.jpg").write_bytes(b"image")
    for color in colors:
        (folder / f"{color}.jpg").write_bytes(b"image")
    return storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD Kids T-Shirt"}
    )


def _default_sku_config(tmp_path: Path, **overrides) -> dict:
    image = tmp_path / "white-tee.png"
    image.write_bytes(b"image")
    config = {
        "enabled": True,
        "color_name": "PureWhite",
        "sku": "AL9999-PureWhite",
        "image_path": str(image),
        "normal_price": 120,
        "sale_price": 79,
    }
    config.update(overrides)
    return config


def _site_template(tmp_path: Path, **overrides) -> dict:
    payload = {
        "store_name": "POD PH",
        "template_name": "Kids Tee",
        "product_line": "POD Kids T-Shirt",
        "marketplace_category_path": "Baby & Kids Fashion > Tops > T-Shirts",
        "normal_price": 100,
        "sale_price": 60,
        "sizes": list(SIZES),
        "variation_rule": "color_size",
        "sku_template": "{main_sku}-{color}-{size}",
        "default_sku": _default_sku_config(tmp_path),
        "sku_strip_parentheses": True,
    }
    payload.update(overrides)
    return storage.create_store_template(payload)


def _cnsc_template(tmp_path: Path, **overrides) -> dict:
    payload = {
        "store_name": "广州小苗服饰有限公司",
        "template_name": "CNSC 童装",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "sizes": list(SIZES),
        "sku_template": "{main_sku}-{color}-{size}",
        "default_site_discount_percent": 40,
        "target_site_stores": [
            {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}
        ],
        "price_tiers": [
            {
                "id": "uniform",
                "sizes": [],
                "global_price_cny": 19.2,
                "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
            }
        ],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        "default_sku": _default_sku_config(tmp_path),
    }
    payload.update(overrides)
    return storage.create_store_template(payload)


def test_site_sku_codes_drop_parenthetical_size_values(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = _site_template(tmp_path, default_sku={"enabled": False})
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    skus = storage.list_listing_skus(copy["id"])

    assert [sku["sku_code"] for sku in skus] == ["AL0353-Black-130cm", "AL0353-Black-140cm"]
    # 展示用尺码保持原值（页面变种轴/类目属性不变）。
    assert [sku["size"] for sku in skus] == SIZES


def test_site_sku_codes_keep_parentheses_when_flag_disabled(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = _site_template(tmp_path, default_sku={"enabled": False}, sku_strip_parentheses=False)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    skus = storage.list_listing_skus(copy["id"])

    assert [sku["sku_code"] for sku in skus] == [
        "AL0353-Black-130cm(21-26kg)",
        "AL0353-Black-140cm(24-30kg)",
    ]


def test_cnsc_skus_and_default_sku_stay_untouched(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = _cnsc_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    skus = storage.list_listing_skus(copy["id"])

    # 跨境：括号保留，且不追加默认 SKU 颜色。
    assert [sku["sku_code"] for sku in skus] == [
        "AL0353-Black-130cm(21-26kg)",
        "AL0353-Black-140cm(24-30kg)",
    ]
    assert all(str(sku.get("sku_source") or "") != "default_sku" for sku in skus)
    assert storage._active_default_sku(template, copy) is None


def test_default_sku_rows_are_appended_with_own_price_and_image(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    skus = storage.list_listing_skus(copy["id"])
    default_rows = [sku for sku in skus if sku.get("sku_source") == "default_sku"]

    assert len(skus) == 3 * 2 + 2  # 3 个商品颜色 + 1 个默认颜色，各 2 个尺码
    assert [sku["sku_code"] for sku in default_rows] == [
        "AL9999-PureWhite-130cm",
        "AL9999-PureWhite-140cm",
    ]
    assert {sku["color_code"] for sku in default_rows} == {"PureWhite"}
    assert all(sku["variant_image"] == "white-tee.png" for sku in default_rows)

    prices_by_code = {price["sku_code"]: price for price in storage.list_listing_prices(copy["id"])}
    assert prices_by_code["AL9999-PureWhite-130cm"]["normal_price"] == 120.0
    assert prices_by_code["AL9999-PureWhite-130cm"]["sale_price"] == 79.0
    # 其他颜色仍是模板原价 + 六折售价，不受默认 SKU 影响。
    assert prices_by_code["AL0353-Black-130cm"]["normal_price"] == 100.0
    assert prices_by_code["AL0353-Black-130cm"]["sale_price"] == 60.0


def test_browser_inputs_put_default_sku_color_and_rows_last(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    skus = storage.list_listing_skus(copy["id"])
    prices = storage.list_listing_prices(copy["id"])

    inputs = storage._build_browser_inputs(copy, skus, prices, [])

    assert inputs["variation"]["colors"] == ["Black", "Blue", "Red", "PureWhite"]
    assert inputs["variation"]["sizes"] == SIZES
    assert [sku["sku_code"] for sku in inputs["skus"][-2:]] == [
        "AL9999-PureWhite-130cm",
        "AL9999-PureWhite-140cm",
    ]
    assert inputs["sku_count"] == len(inputs["skus"])
    image_path = str(tmp_path / "white-tee.png")
    assert inputs["variant_image_map"]["PureWhite"] == "white-tee.png"
    assert inputs["variant_image_paths"]["PureWhite"] == image_path


def test_automation_snapshot_uses_grid_order_for_skus_and_prices(tmp_path: Path) -> None:
    """自动化快照的 skus/prices 必须是网格顺序（默认 SKU 行在末尾）。

    执行器按行号逐行填写，而网格由颜色轴生成（默认颜色末尾）。快照顶层若沿用
    list_listing_skus 的 color_code 排序，默认 SKU 块会夹在中间，从它往后整块错行——
    90cm 覆盖价会落到别的颜色行上（真机表现：白T 那一块显示成白色 SKU + 228）。
    """

    base = _make_base(tmp_path)
    template = _site_template(
        tmp_path,
        sizes=["90cm(13-18kg)", "130cm(21-26kg)"],
        default_sku=_default_sku_config(
            tmp_path,
            size_price_overrides={"90cm(13-18kg)": {"normal_price": 90, "sale_price": 45}},
        ),
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    snapshot = job["execution_snapshot"]
    skus = snapshot["skus"]
    prices = snapshot["prices"]
    colors = snapshot["browser_inputs"]["variation"]["colors"]

    # PureWhite 排在 Red 之后（字母序里 P 在 R 前），所以排序法一定把它夹在中间。
    assert colors == ["Black", "Blue", "Red", "PureWhite"]
    # browser_inputs 里那份重复行已在写入时去重；payload 返回时会把顶层这份（网格顺序）
    # 补回 browser_inputs，执行器看到的顺序不变。
    assert skus == storage.build_automation_payload(job["id"])["browser_inputs"]["skus"]
    # 网格顺序 = 颜色轴顺序，每个颜色一个连续块。
    assert [sku["color_code"] for sku in skus[0::2]] == colors
    # 价格逐行跟着同一顺序，不再错位。
    assert [price["sku_code"] for price in prices] == [sku["sku_code"] for sku in skus]
    prices_by_code = {price["sku_code"]: price for price in prices}
    assert prices_by_code["AL9999-PureWhite-90cm"]["sale_price"] == 45.0


def test_browser_inputs_match_previous_output_without_default_sku(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template(tmp_path, default_sku={"enabled": False})
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    skus = storage.list_listing_skus(copy["id"])
    prices = storage.list_listing_prices(copy["id"])

    inputs = storage._build_browser_inputs(copy, skus, prices, [])

    assert inputs["variation"]["colors"] == ["Black", "Blue", "Red"]
    assert all(str(sku.get("sku_source") or "") != "default_sku" for sku in inputs["skus"])
    assert "PureWhite" not in inputs["variant_image_paths"]


def test_readiness_blocks_incomplete_default_sku(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = _site_template(
        tmp_path,
        default_sku={
            "enabled": True,
            "color_name": "PureWhite",
            "sku": "",
            "image_path": "",
            "normal_price": "",
            "sale_price": "",
        },
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    missing = storage.get_store_listing_copy_detail(copy["id"])["readiness"]["missing_fields"]

    assert "default_sku_code" in missing
    assert "default_sku_image" in missing
    assert "default_sku_price" in missing


def test_readiness_flags_default_sku_color_conflict(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = _site_template(tmp_path, default_sku=_default_sku_config(tmp_path, color_name="Black"))
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    missing = storage.get_store_listing_copy_detail(copy["id"])["readiness"]["missing_fields"]

    assert "default_sku_color_conflict" in missing


def test_cnsc_copy_readiness_ignores_default_sku(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = _cnsc_template(
        tmp_path,
        default_sku={
            "enabled": True,
            "color_name": "PureWhite",
            "sku": "",
            "image_path": "",
            "normal_price": "",
            "sale_price": "",
        },
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    missing = storage.get_store_listing_copy_detail(copy["id"])["readiness"]["missing_fields"]

    assert not [field for field in missing if field.startswith("default_sku")]


def test_template_update_syncs_default_sku_to_existing_site_copies(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = _site_template(tmp_path, default_sku={"enabled": False})
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    assert not [sku for sku in storage.list_listing_skus(copy["id"]) if sku.get("sku_source") == "default_sku"]

    storage.update_store_template(template["id"], {"default_sku": _default_sku_config(tmp_path)})

    synced = storage.list_listing_skus(copy["id"])
    assert [sku["sku_code"] for sku in synced] == [
        "AL0353-Black-130cm",
        "AL0353-Black-140cm",
        "AL9999-PureWhite-130cm",
        "AL9999-PureWhite-140cm",
    ]
    prices_by_code = {price["sku_code"]: price for price in storage.list_listing_prices(copy["id"])}
    assert prices_by_code["AL9999-PureWhite-140cm"]["sale_price"] == 79.0
    assert prices_by_code["AL0353-Black-140cm"]["sale_price"] == 60.0  # 其他颜色价格未被重建
    assert storage.get_store_listing_copy_detail(copy["id"])["default_sku"]["enabled"] is True

    storage.update_store_template(template["id"], {"default_sku": {"enabled": False}})

    assert [sku["sku_code"] for sku in storage.list_listing_skus(copy["id"])] == [
        "AL0353-Black-130cm",
        "AL0353-Black-140cm",
    ]
    assert [price["sku_code"] for price in storage.list_listing_prices(copy["id"])] == [
        "AL0353-Black-130cm",
        "AL0353-Black-140cm",
    ]


def test_unchanged_template_save_does_not_rebuild_default_rows(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    config = _default_sku_config(tmp_path)
    template = _site_template(tmp_path, default_sku=config)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    before = {sku["sku_code"]: sku["id"] for sku in storage.list_listing_skus(copy["id"])}

    # 只改库存（默认 SKU 配置原样回填）→ 不应重建默认 SKU 行。
    storage.update_store_template(template["id"], {"default_sku": config, "stock": 12})

    after = {sku["sku_code"]: sku["id"] for sku in storage.list_listing_skus(copy["id"])}
    assert after == before


def test_cnsc_template_update_does_not_touch_cnsc_copies(tmp_path: Path) -> None:
    base = _make_base(tmp_path, colors=("Black",))
    template = _cnsc_template(tmp_path, default_sku={"enabled": False})
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    before = [sku["sku_code"] for sku in storage.list_listing_skus(copy["id"])]

    storage.update_store_template(template["id"], {"default_sku": _default_sku_config(tmp_path)})

    after = storage.list_listing_skus(copy["id"])
    assert [sku["sku_code"] for sku in after] == before
    assert all(str(sku.get("sku_source") or "") != "default_sku" for sku in after)


def _call(method: str, url: str, payload: dict | None = None, *, raw: bool = False):
    body = None if payload is None else json.dumps(payload).encode()
    req = request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            status, content, headers = response.status, response.read(), dict(response.headers.items())
    except error.HTTPError as exc:
        status, content, headers = exc.code, exc.read(), dict(exc.headers.items())
    return status, (content if raw else json.loads(content.decode())), headers


def test_api_persists_default_sku_and_sku_strip_flag(tmp_path: Path) -> None:
    image = tmp_path / "white-tee.png"
    image.write_bytes(b"image")
    server, base_url = _start_api()
    try:
        status, template, _ = _call(
            "POST",
            f"{base_url}/api/store-templates",
            {
                "store_name": "POD PH",
                "template_name": "Kids Tee",
                "product_line": "POD Kids T-Shirt",
                "marketplace_category_path": "Baby & Kids Fashion > Tops > T-Shirts",
                "normal_price": 100,
                "sizes": list(SIZES),
                "sku_template": "{main_sku}-{color}-{size}",
                "default_sku": {
                    "enabled": True,
                    "color_name": "PureWhite",
                    "sku": "AL9999-PureWhite",
                    "image_path": str(image),
                    "normal_price": 120,
                    "sale_price": 79,
                },
            },
        )
        assert status == 201
        assert template["default_sku"]["enabled"] is True
        assert template["default_sku"]["sku"] == "AL9999-PureWhite"
        assert template["default_sku"]["normal_price"] == 120.0
        assert template["sku_strip_parentheses"] is True

        status, updated, _ = _call(
            "PUT",
            f"{base_url}/api/store-templates/{template['id']}",
            {"default_sku": {"enabled": False}, "sku_strip_parentheses": False},
        )
        assert status == 200
        assert updated["default_sku"]["enabled"] is False
        assert updated["sku_strip_parentheses"] is False
    finally:
        server.shutdown()


def test_default_sku_image_upload_endpoint() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"fake-image-body"
    server, base_url = _start_api()
    try:
        status, created, _ = _call(
            "POST",
            f"{base_url}/api/uploads/default-sku-image",
            {
                "file_name": "white-tee.png",
                "content_type": "image/png",
                "content_base64": base64.b64encode(png).decode(),
            },
        )
        assert status == 201
        assert created["file_name"] == "white-tee.png"
        assert created["preview_url"] == f"/api/uploads/default-sku-image/{created['id']}"

        served_status, served, headers = _call("GET", f"{base_url}{created['preview_url']}", raw=True)
        assert served_status == 200
        assert headers.get("Content-Type") == "image/png"
        assert served == png
    finally:
        server.shutdown()


def test_default_sku_size_price_override_only_affects_that_size(tmp_path: Path) -> None:
    """90cm 覆盖价只落在 90cm 行；同色其他码与其他颜色的价格完全不变。"""

    base = _make_base(tmp_path)
    template = _site_template(
        tmp_path,
        sizes=["90cm(13-18kg)", "130cm(21-26kg)"],
        default_sku=_default_sku_config(
            tmp_path,
            size_price_overrides={"90cm(13-18kg)": {"normal_price": 90, "sale_price": 45}},
        ),
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    prices_by_code = {price["sku_code"]: price for price in storage.list_listing_prices(copy["id"])}

    # 覆盖价只作用于默认 SKU 的 90cm。
    assert prices_by_code["AL9999-PureWhite-90cm"]["normal_price"] == 90.0
    assert prices_by_code["AL9999-PureWhite-90cm"]["sale_price"] == 45.0
    # 同色其他码沿用默认 SKU 自己的售价/折扣价。
    assert prices_by_code["AL9999-PureWhite-130cm"]["normal_price"] == 120.0
    assert prices_by_code["AL9999-PureWhite-130cm"]["sale_price"] == 79.0
    # 其他颜色仍是模板原价 + 六折售价。
    assert prices_by_code["AL0353-Black-90cm"]["normal_price"] == 100.0
    assert prices_by_code["AL0353-Black-90cm"]["sale_price"] == 60.0
    assert prices_by_code["AL0353-Black-130cm"]["normal_price"] == 100.0


def test_default_sku_size_price_override_half_fill_auto_completes(tmp_path: Path) -> None:
    """半填覆盖价与主表格同一套语义：只填原价＝折扣价按默认 SKU 比例换算；
    只填折扣价＝原价继承默认 SKU 售价。不再作为 readiness 问题拦截。"""
    base = _make_base(tmp_path)
    template = _site_template(
        tmp_path,
        sizes=["90cm(13-18kg)", "130cm(21-26kg)"],
        default_sku=_default_sku_config(
            tmp_path,
            size_price_overrides={
                "90cm(13-18kg)": {"normal_price": 90, "sale_price": ""},
                "130cm(21-26kg)": {"normal_price": "", "sale_price": 50},
            },
        ),
    )

    assert template["default_sku"]["size_price_overrides"] == {
        "90cm(13-18kg)": {"normal_price": 90.0, "sale_price": ""},
        "130cm(21-26kg)": {"normal_price": "", "sale_price": 50.0},
    }

    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    detail = storage.get_store_listing_copy_detail(copy["id"])

    # 半填不再拦截自动化。
    assert "default_sku_size_price" not in detail["readiness"]["missing_fields"]
    prices_by_code = {price["sku_code"]: price for price in detail["prices"]}
    # 90cm：只填原价 90 → 折扣价按默认 SKU 自己的 79/120 比例换算 = 59.25。
    assert prices_by_code["AL9999-PureWhite-90cm"]["normal_price"] == 90.0
    assert prices_by_code["AL9999-PureWhite-90cm"]["sale_price"] == 59.25
    # 130cm：只填折扣价 50 → 原价继承默认 SKU 的 120。
    assert prices_by_code["AL9999-PureWhite-130cm"]["normal_price"] == 120.0
    assert prices_by_code["AL9999-PureWhite-130cm"]["sale_price"] == 50.0


def test_default_sku_size_price_override_is_dropped_when_empty(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _site_template(
        tmp_path,
        default_sku=_default_sku_config(
            tmp_path,
            size_price_overrides={"90cm": {"normal_price": "", "sale_price": ""}},
        ),
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    assert template["default_sku"]["size_price_overrides"] == {}
    detail = storage.get_store_listing_copy_detail(copy["id"])
    assert "default_sku_size_price" not in detail["readiness"]["missing_fields"]
    prices_by_code = {price["sku_code"]: price for price in detail["prices"]}
    assert prices_by_code["AL9999-PureWhite-130cm"]["normal_price"] == 120.0


def test_default_sku_editor_exposes_the_per_size_price_table() -> None:
    """默认 SKU 的分尺码价格必须在模板编辑器里可填、可回填、可提交（与主表格
    「按尺码设置价格」同一套行结构，全部尺码都可单独调整）。"""

    html = (settings.WEB_DIR / "index.html").read_text(encoding="utf-8")
    script = (settings.WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="store-template-default-sku-size-prices"' in html
    assert "renderDefaultSkuSizePriceOverrides" in script
    assert "collectDefaultSkuSizePriceOverrides" in script
    assert "data-default-sku-size-price" in script
    assert "data-default-sku-size-sale-price" in script
    # 与主表格同一套留空=自动语义（占位提示），不再有成对拦截文案。
    assert "自动 ${auto}" in script
    assert "一起填写（留空表示沿用上方价格）" not in script
    assert "store-template-default-sku-size-normal-price" not in html


def test_default_sku_90cm_override_matches_the_label_with_weight_suffix(tmp_path: Path) -> None:
    """编辑器里填的键是 90cm，模板尺码实际是 90cm(10-12kg)：必须匹配到同一行。"""

    base = _make_base(tmp_path)
    template = _site_template(
        tmp_path,
        # 真实模板（HN_POD 童装）用的就是带括号补充值的尺码标签。
        sizes=["90cm(10-12kg)", "100cm(12-14kg)"],
        default_sku=_default_sku_config(
            tmp_path,
            size_price_overrides={"90cm": {"normal_price": 90, "sale_price": 45}},
        ),
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    prices_by_code = {price["sku_code"]: price for price in storage.list_listing_prices(copy["id"])}

    assert prices_by_code["AL9999-PureWhite-90cm"]["normal_price"] == 90.0
    assert prices_by_code["AL9999-PureWhite-90cm"]["sale_price"] == 45.0
    assert prices_by_code["AL9999-PureWhite-100cm"]["normal_price"] == 120.0
    assert prices_by_code["AL9999-PureWhite-100cm"]["sale_price"] == 79.0


def test_saving_the_template_without_touching_default_sku_keeps_copy_rows(tmp_path: Path) -> None:
    """存量模板缺少后来新增的子字段时，普通保存不能把副本的默认 SKU 行当成"改过"重建。"""

    base = _make_base(tmp_path)
    template = _site_template(tmp_path)
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    before = {row["id"]: row for row in storage.list_listing_skus(copy["id"])}

    # 模拟老数据：存起来的 default_sku 没有 size_price_overrides 子字段。
    data = storage.load_store()
    for item in data.get("store_templates", []):
        if isinstance(item.get("default_sku"), dict):
            item["default_sku"].pop("size_price_overrides", None)
    for item in data.get("store_listing_copies", []):
        if isinstance(item.get("default_sku"), dict):
            item["default_sku"].pop("size_price_overrides", None)
    storage.save_store(data)

    storage.update_store_template(template["id"], {"template_name": "Kids Tee v2", "default_sku": template["default_sku"]})

    after = {row["id"]: row for row in storage.list_listing_skus(copy["id"])}
    assert set(after) == set(before)


def test_default_sku_override_accepts_the_string_values_the_editor_sends(tmp_path: Path) -> None:
    """编辑器提交的是输入框字符串（"90" / "45"），后端必须照常归一化成数字。"""

    base = _make_base(tmp_path)
    template = _site_template(
        tmp_path,
        sizes=["90cm(10-12kg)", "100cm(12-14kg)"],
        default_sku=_default_sku_config(
            tmp_path,
            normal_price="120",
            sale_price="79",
            size_price_overrides={"90cm": {"normal_price": "90.5", "sale_price": "45.25"}},
        ),
    )
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    prices_by_code = {price["sku_code"]: price for price in storage.list_listing_prices(copy["id"])}

    assert template["default_sku"]["normal_price"] == 120.0
    assert prices_by_code["AL9999-PureWhite-90cm"]["normal_price"] == 90.5
    assert prices_by_code["AL9999-PureWhite-90cm"]["sale_price"] == 45.25
    assert prices_by_code["AL9999-PureWhite-100cm"]["normal_price"] == 120.0
    assert prices_by_code["AL9999-PureWhite-100cm"]["sale_price"] == 79.0


def test_enum_attribute_accepts_a_custom_value_the_enum_does_not_offer() -> None:
    """原产地这类枚举里没有的自选值：编辑器要能录入，且执行器走"添加自填选项"。"""

    script = (settings.WEB_DIR / "app.js").read_text(encoding="utf-8")
    executor = (settings.PROJECT_ROOT / "scripts" / "mabang_playwright_executor.cjs").read_text(encoding="utf-8")
    runner = (settings.PROJECT_ROOT / "scripts" / "browser_use_runner.py").read_text(encoding="utf-8")

    # 编辑器：下拉里有"添加自选值"，并且已存的自选值不会退化成文本框。
    assert "CUSTOM_ATTRIBUTE_OPTION" in script
    assert "＋ 添加自选值…" in script
    assert "needsTextFallback = isSelectField && selectOptions.length === 0" in script
    # 执行器：找不到选项时改走"搜索 → 添加 → 点自填项"。
    assert "async function addCustomSelectOption" in executor
    assert "自填选项" in executor
    # runner：枚举 + 值不在枚举里 ⇒ select_custom（不能当文本框打字）。
    assert 'input_kind = "select_custom"' in runner
