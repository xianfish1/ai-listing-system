"""「产品图最后一张放尺码表」（本土模板开关，默认开）。

规则（操作员 2026-10-01 定）：
  产品图满 9 张 → 第 9 张被尺码表替换；不足 9 张 → 尺码表追加到末尾（不替换任何产品图）。
  「产品图」区实际传的那一份（product_image_form_paths）末位才是尺码表；
  副本自带的 product_images 保持真实产品图不动——描述末尾那批图按真实产品图插，
  末位那张尺码表由执行器跳过（见 tests/mabang_playwright_executor.test.cjs）。

顺带覆盖：开关默认值（缺字段＝开）、跨境强制关、副本级开关保存、
尺码图文件不存在 / 完全没配尺码图时不硬塞（避免上传阶段 upload_file_missing）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _transaction_defaults() -> dict:
    return {
        "stock": 100,
        "weight_kg": 0.3,
        "package_length_cm": 30,
        "package_width_cm": 20,
        "package_height_cm": 5,
    }


def _make_copy(
    tmp_path: Path,
    *,
    image_count: int,
    template_overrides: dict | None = None,
    folder_name: str = "AL0901 Size Chart",
) -> tuple[dict, dict, dict]:
    folder = tmp_path / folder_name
    folder.mkdir()
    names = [f"model{index}.jpg" for index in range(1, image_count + 1)]
    for name in names:
        (folder / name).write_bytes(b"image")
    chart = tmp_path / f"{folder_name}-size-chart.png"
    chart.write_bytes(b"chart")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    storage.update_base_product(
        base["id"],
        {"product_images": names, "main_image": names[0] if names else ""},
    )
    template = storage.create_store_template(
        {
            "store_name": "POD 菲律宾",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": "Size Chart Tee",
            "product_line": "POD 男装T恤",
            "marketplace_category_path": "Men Clothes > Tops > T-shirts",
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S", "M"],
            "default_size_chart_path": str(chart),
            "description": "Size chart description.",
            **_transaction_defaults(),
            **(template_overrides or {}),
        }
    )
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    copy = storage.get_item("store_listing_copies", result["created"][0]["id"])
    return template, copy, base


def _browser_inputs(copy: dict) -> dict:
    skus = storage.list_listing_skus(copy["id"])
    prices = storage.list_listing_prices(copy["id"])
    assets = storage.assets_for_group(copy.get("group_id") or "")
    return storage._build_browser_inputs(copy, skus, prices, assets)


def _names(paths: list[str]) -> list[str]:
    return [Path(item).name for item in paths]


def test_switch_defaults_to_on_and_even_missing_field_means_on() -> None:
    """字段缺失＝开：存量模板/副本没有这个字段时按新的默认行为走。"""

    assert storage._size_chart_last_product_image_enabled({}) is True
    assert storage._size_chart_last_product_image_enabled({"size_chart_as_last_product_image": None}) is True
    assert storage._size_chart_last_product_image_enabled({"size_chart_as_last_product_image": True}) is True
    for off in (False, "false", 0, "0"):
        assert storage._size_chart_last_product_image_enabled({"size_chart_as_last_product_image": off}) is False


def test_cnsc_template_forces_the_switch_off() -> None:
    """跨境模板没有这条规则：保存时强制关闭（本土默认开）。"""

    now = "2026-10-01T00:00:00+00:00"
    site = storage._normalize_store_template_payload(
        {"store_name": "POD 菲律宾", "listing_mode": "site", "size_chart_as_last_product_image": True},
        now=now,
    )
    assert site["size_chart_as_last_product_image"] is True

    default = storage._normalize_store_template_payload(
        {"store_name": "POD 菲律宾", "listing_mode": "site"},
        now=now,
    )
    assert default["size_chart_as_last_product_image"] is True, "字段缺失＝开"

    global_template = storage._normalize_store_template_payload(
        {"store_name": "CNSC 全球", "listing_mode": "cnsc_global", "size_chart_as_last_product_image": True},
        now=now,
    )
    assert global_template["size_chart_as_last_product_image"] is False
    assert global_template["listing_mode"] == "cnsc_global"


def test_nine_product_images_replace_the_ninth_slot(tmp_path: Path) -> None:
    """满 9 张：第 9 张被尺码表替换（总数仍是 9，末位是尺码表）。"""

    template, copy, _base = _make_copy(tmp_path, image_count=9)
    assert copy["size_chart_as_last_product_image"] is True
    assert copy["product_images"] == [f"model{index}.jpg" for index in range(1, 10)]

    inputs = _browser_inputs(copy)
    assert inputs["size_chart_last_product_image"] is True
    assert inputs["size_chart_slot_skipped"] == ""
    assert len(inputs["product_image_form_paths"]) == storage.PRODUCT_IMAGE_LIMIT
    assert _names(inputs["product_image_form_paths"]) == [
        *[f"model{index}.jpg" for index in range(1, 9)],
        Path(template["default_size_chart_path"]).name,
    ]
    # 真实产品图列表不动：描述那批图仍然按 9 张真实产品图插。
    assert _names(inputs["product_image_paths"]) == [f"model{index}.jpg" for index in range(1, 10)]


def test_fewer_than_nine_appends_the_size_chart(tmp_path: Path) -> None:
    """不足 9 张：追加到末尾，不替换任何产品图。"""

    _template, copy, _base = _make_copy(tmp_path, image_count=4, folder_name="AL0902 Fewer")
    inputs = _browser_inputs(copy)

    assert inputs["size_chart_last_product_image"] is True
    assert _names(inputs["product_image_form_paths"]) == [
        "model1.jpg",
        "model2.jpg",
        "model3.jpg",
        "model4.jpg",
        "AL0902 Fewer-size-chart.png",
    ]
    assert _names(inputs["product_image_paths"]) == ["model1.jpg", "model2.jpg", "model3.jpg", "model4.jpg"]


def test_copy_level_switch_off_keeps_product_images_only(tmp_path: Path) -> None:
    """副本上关掉：产品图区就是真实产品图，原样上传。"""

    _template, copy, _base = _make_copy(tmp_path, image_count=4, folder_name="AL0903 Off")

    saved = storage.update_store_listing_copy(copy["id"], {"size_chart_as_last_product_image": False})
    assert saved["size_chart_as_last_product_image"] is False

    inputs = _browser_inputs(storage.get_item("store_listing_copies", copy["id"]))
    assert inputs["size_chart_last_product_image"] is False
    assert _names(inputs["product_image_form_paths"]) == ["model1.jpg", "model2.jpg", "model3.jpg", "model4.jpg"]


def test_copy_level_switch_coerces_truthy_values(tmp_path: Path) -> None:
    """副本保存时的取值归一：只有显式关闭才算关。"""

    _template, copy, _base = _make_copy(tmp_path, image_count=3, folder_name="AL0904 Coerce")

    assert storage.update_store_listing_copy(copy["id"], {"size_chart_as_last_product_image": "0"})["size_chart_as_last_product_image"] is False
    assert storage.update_store_listing_copy(copy["id"], {"size_chart_as_last_product_image": True})["size_chart_as_last_product_image"] is True


def test_template_switch_flows_into_new_copies(tmp_path: Path) -> None:
    """模板上关掉 → 之后复制出来的副本快照也是关。"""

    _template, copy, _base = _make_copy(
        tmp_path,
        image_count=3,
        folder_name="AL0905 TemplateOff",
        template_overrides={"size_chart_as_last_product_image": False},
    )
    assert copy["size_chart_as_last_product_image"] is False
    assert _names(_browser_inputs(copy)["product_image_form_paths"]) == ["model1.jpg", "model2.jpg", "model3.jpg"]


def test_missing_size_chart_file_is_not_pushed_into_product_images(tmp_path: Path) -> None:
    """尺码图路径掉线（文件不存在）时不硬塞：否则上传阶段 upload_file_missing 整单 blocked。"""

    _template, copy, _base = _make_copy(
        tmp_path,
        image_count=3,
        folder_name="AL0906 MissingFile",
        template_overrides={"default_size_chart_path": str(tmp_path / "gone.png")},
    )
    inputs = _browser_inputs(copy)

    assert inputs["size_chart_last_product_image"] is False
    assert inputs["size_chart_slot_skipped"] == "size_chart_file_missing"
    assert _names(inputs["product_image_form_paths"]) == ["model1.jpg", "model2.jpg", "model3.jpg"]


def test_no_size_chart_at_all_keeps_product_images(tmp_path: Path) -> None:
    """模板没配尺码图：跳过并说明原因（readiness 仍会照旧拦「缺少尺码图」）。"""

    _template, copy, _base = _make_copy(
        tmp_path,
        image_count=3,
        folder_name="AL0907 NoChart",
        template_overrides={"default_size_chart_path": "", "size_chart_asset_id": ""},
    )
    inputs = _browser_inputs(copy)

    assert inputs["size_chart_last_product_image"] is False
    assert inputs["size_chart_slot_skipped"] == "size_chart_missing"
    assert _names(inputs["product_image_form_paths"]) == ["model1.jpg", "model2.jpg", "model3.jpg"]

def test_ui_exposes_the_switch_on_template_and_copy_editors() -> None:
    """静态契约：本土店铺模板 + 副本检查弹窗各有一个开关，跨境隐藏。"""

    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="store-template-size-chart-last-image"' in html
    assert 'id="store-template-size-chart-last-image-line"' in html
    assert 'id="listing-copy-size-chart-last-image"' in html
    assert 'id="listing-copy-size-chart-last-image-line"' in html
    for label in ["产品图最后一张放尺码表"]:
        assert label in html

    # 模板：保存时跨境强制关，回显时缺字段＝开，跨境隐藏整行。
    assert 'size_chart_as_last_product_image: listingMode === "cnsc_global"' in script
    assert 'template.size_chart_as_last_product_image !== false' in script
    assert '$("store-template-size-chart-last-image-line")?.toggleAttribute("hidden", global)' in script

    # 副本：同样保存/回显，跨境隐藏。
    assert 'payload.size_chart_as_last_product_image = cnsc' in script
    assert 'copy.size_chart_as_last_product_image !== false' in script
    assert '$("listing-copy-size-chart-last-image-line")?.toggleAttribute("hidden", isCNSCListingCopy(copy))' in script
