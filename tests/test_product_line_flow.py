from __future__ import annotations

import json
from pathlib import Path
from urllib import request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api
from workers.asset_scanner.scanner import scan_asset_root


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def test_create_base_product_recognizes_main_sku_from_folder_name(tmp_path: Path) -> None:
    folder = tmp_path / "AL0002 我的世界"
    folder.mkdir()
    for name in ["main.jpg", "white.jpg"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["group_id"] == "AL0002"
    assert product["main_sku"] == "AL0002"
    assert product["product_name_cn"] == "我的世界"
    assert product["product_images"][0] == "main.jpg"


def _make_import_folder(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "main.jpg").write_bytes(b"image")
    (folder / "white.jpg").write_bytes(b"image")
    return folder


def test_auto_assigned_main_sku_increments_per_import(tmp_path: Path) -> None:
    """Auto-assign on + a folder name with no SKU-like token: each import takes
    the next prefix+number, and skips SKUs that already exist. (A folder name
    that DOES carry a SKU wins over auto-assign — see
    test_chinese_tight_folder_name_derives_main_sku.)"""
    storage.save_runtime_configuration({
        "product_sku": {"auto_assign_on_import": True, "prefix": "FL", "number_width": 4, "next_number": 21},
    })
    # FL0021 already exists — allocation must skip it.
    storage.create_base_product_from_folder({
        "root_path": str(_make_import_folder(tmp_path / "IGNORED-01 商品A")),
        "main_sku": "FL0021",
        "category": "POD Mens T-Shirt",
    })

    second = storage.create_base_product_from_folder({
        "root_path": str(_make_import_folder(tmp_path / "WRONG-99 商品B")),
        "category": "POD Mens T-Shirt",
    })
    third = storage.create_base_product_from_folder({
        "root_path": str(_make_import_folder(tmp_path / "WRONG-98 商品C")),
        "category": "POD Mens T-Shirt",
    })

    assert second["main_sku"] == "FL0022"
    assert third["main_sku"] == "FL0023"
    config = storage.get_runtime_configuration()
    assert config["product_sku"]["next_number"] == 24


def test_auto_assign_disabled_keeps_folder_name_sku(tmp_path: Path) -> None:
    folder = _make_import_folder(tmp_path / "AL0007 商品")
    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    assert product["main_sku"] == "AL0007"


def test_chinese_tight_folder_name_derives_main_sku(tmp_path: Path) -> None:
    """SKU glued to a Chinese product name (AL0301男语言月) must still yield the
    main SKU AL0301 — the old word-boundary regex saw CJK as a word char and
    found no boundary, so these folders fell through to auto-assign. The folder
    name wins even when auto-assign is enabled."""
    storage.save_runtime_configuration({
        "product_sku": {"auto_assign_on_import": True, "prefix": "FL", "number_width": 4, "next_number": 11},
    })
    product = storage.create_base_product_from_folder({
        "root_path": str(_make_import_folder(tmp_path / "AL0301男语言月")),
        "category": "POD Mens T-Shirt",
    })

    assert product["main_sku"] == "AL0301"  # not FL0011: folder name wins
    assert product["group_id"] == "AL0301"
    assert product["product_name_cn"] == "男语言月"  # SKU prefix stripped, Chinese kept
    # auto-assign counter untouched because no fallback allocation ran
    config = storage.get_runtime_configuration()
    assert config["product_sku"]["next_number"] == 11


def test_folder_name_sku_without_spaces_and_non_sku_folders(tmp_path: Path) -> None:
    """More folder-name shapes: DL0001超天酱 (tight Chinese, no space) derives;
    an explicit main_sku payload still beats the folder name; a year-first name
    (2024夏季新款) has no SKU-like token and falls back to auto-assign."""
    storage.save_runtime_configuration({
        "product_sku": {"auto_assign_on_import": True, "prefix": "FL", "number_width": 4, "next_number": 31},
    })

    tight = storage.create_base_product_from_folder({
        "root_path": str(_make_import_folder(tmp_path / "DL0001超天酱")),
        "category": "POD 假发",
    })
    assert tight["main_sku"] == "DL0001"
    assert tight["product_name_cn"] == "超天酱"

    explicit = storage.create_base_product_from_folder({
        "root_path": str(_make_import_folder(tmp_path / "AL0099测试")),
        "main_sku": "CUSTOM9",
        "category": "POD Mens T-Shirt",
    })
    assert explicit["main_sku"] == "CUSTOM9"  # explicit payload wins over folder name

    fallback = storage.create_base_product_from_folder({
        "root_path": str(_make_import_folder(tmp_path / "2024夏季新款")),
        "category": "POD Mens T-Shirt",
    })
    # No letter-led SKU token in the name → auto-assign fallback kicks in.
    assert fallback["main_sku"] == "FL0031"


def test_base_product_update_normalizes_recognized_attributes(tmp_path: Path) -> None:
    folder = tmp_path / "AL9003 连帽卫衣"
    folder.mkdir()
    for name in ["main.jpg", "black.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens Hoodie"})

    updated = storage.update_base_product(
        base["id"],
        {
            "recognized_attributes": {
                "Applicable Season": "Four Seasons",
                "Material": "Polyester",
                "Item Type": "Hoodies",
                "  ": "ignored",
                "Empty": "  ",
            },
        },
    )

    assert updated["recognized_attributes"] == {
        "Applicable Season": "Four Seasons",
        "Material": "Polyester",
        "Item Type": "Hoodies",
    }

    replaced = storage.update_base_product(base["id"], {"recognized_attributes": {"Style": "Casual"}})
    assert replaced["recognized_attributes"] == {"Style": "Casual"}

    cleared = storage.update_base_product(base["id"], {"recognized_attributes": "not a dict"})
    assert cleared["recognized_attributes"] == {}


def test_store_template_sku_template_generates_listing_skus(tmp_path: Path) -> None:
    folder = tmp_path / "YSC008P 童装"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Kids T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "template_name": "Kids Tee",
            "product_line": "POD Kids T-Shirt",
            "marketplace_category_path": "Baby & Kids Fashion > Tops > T-Shirts",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["110"],
            "variation_rule": "color_size",
            "sku_template": "{main_sku}_{color}||{size}",
        }
    )

    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    skus = storage.list_listing_skus(result["created"][0]["id"])

    assert skus[0]["sku_code"] == "YSC008P_White||110"


def test_incomplete_cnsc_template_still_creates_a_copy_marked_with_issues(tmp_path: Path) -> None:
    # Decision A (照建): an incomplete CNSC template still creates the listing copy so
    # the user can finish the configuration in the editor. The missing configuration is
    # surfaced via created_with_issues instead of silently creating nothing. The job gate
    # (create_automation_job_from_listing_copy) stays fail-closed on the same problem set.
    folder = tmp_path / "CNSC1001 全球商品"
    folder.mkdir()
    (folder / "主图.jpg").write_bytes(b"image")
    (folder / "白色.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    template = storage.create_store_template({
        "store_name": "广州小苗服饰有限公司",
        "template_name": "CNSC 缺站点活动",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "sizes": ["S"],
        "default_site_discount_percent": 40,
        "target_site_stores": [{"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}],
        "price_tiers": [{
            "id": "standard", "sizes": ["S"], "global_price_cny": 19.2,
            "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
        }],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
    })
    assert template["status"] == "needs_configuration"
    assert template["missing_configuration"] == ["target_site_stores.PH.discount_activity"]

    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])

    assert result["errors"] == []
    assert result["error_count"] == 0
    assert result["created_count"] == 1
    assert result["created_with_issues_count"] == 1
    issue = result["created_with_issues"][0]
    assert issue["template_id"] == template["id"]
    assert issue["missing_configuration"] == ["target_site_stores.PH.discount_activity"]
    copy = result["created"][0]
    assert copy["listing_mode"] == "cnsc_global"
    persisted = storage.get_item("store_listing_copies", copy["id"])
    assert persisted is not None and persisted["listing_mode"] == "cnsc_global"


def test_flat_chinese_product_folder_recognizes_asset_roles(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "模特图1.jpg", "白色.jpg", "黑色.jpg", "尺码表.png", "视频.mp4", "源文件.psd"]:
        (folder / name).write_bytes(b"not-a-real-image")

    result = scan_asset_root(folder, category_override="POD 男装T恤", mode="recursive")
    by_name = {item["file_name"]: item for item in result["assets"]}

    assert by_name["主图.jpg"]["asset_type"] == "main"
    assert by_name["模特图1.jpg"]["asset_type"] == "model"
    assert by_name["白色.jpg"]["asset_type"] == "variant"
    assert by_name["白色.jpg"]["sku_option"] == "White"
    assert by_name["黑色.jpg"]["asset_type"] == "variant"
    assert by_name["黑色.jpg"]["sku_option"] == "Black"
    assert by_name["尺码表.png"]["asset_type"] == "size_chart"
    assert by_name["视频.mp4"]["asset_type"] == "video"
    assert by_name["源文件.psd"]["asset_type"] == "source"
    assert {item["group_id"] for item in result["assets"]} == {"DL0001"}


def test_filename_prefixes_classify_main_and_sku_color(tmp_path: Path) -> None:
    folder = tmp_path / "AL0099测试"
    folder.mkdir()
    for name in ["主图-正面.jpg", "Main-侧面.jpg", "mAiN-细节.jpg", "SKU-红色-01.jpg", "sku-奶油白.jpg"]:
        (folder / name).write_bytes(b"image")

    result = scan_asset_root(folder, mode="recursive")
    by_name = {item["file_name"]: item for item in result["assets"]}

    assert by_name["主图-正面.jpg"]["asset_type"] == "main"
    assert by_name["Main-侧面.jpg"]["asset_type"] == "main"
    assert by_name["mAiN-细节.jpg"]["asset_type"] == "main"
    assert by_name["SKU-红色-01.jpg"]["asset_type"] == "variant"
    assert by_name["SKU-红色-01.jpg"]["sku_option"] == "Red"
    assert by_name["sku-奶油白.jpg"]["asset_type"] == "variant"
    assert by_name["sku-奶油白.jpg"]["sku_option"] == "奶油白"


def test_numeric_sku_index_is_not_saved_as_color(tmp_path: Path) -> None:
    folder = tmp_path / "AL0102测试"
    folder.mkdir()
    for name in ["Main-front.jpg", "SKU-01-dress only.jpg", "SKU-02-dress only.jpg"]:
        (folder / name).write_bytes(b"image")

    result = scan_asset_root(folder, mode="recursive")
    by_name = {item["file_name"]: item for item in result["assets"]}

    assert by_name["SKU-01-dress only.jpg"]["asset_type"] == "variant"
    assert by_name["SKU-01-dress only.jpg"]["sku_option"] == ""
    assert by_name["SKU-02-dress only.jpg"]["sku_option"] == ""


def test_numeric_sku_index_still_finds_color_later_in_filename(tmp_path: Path) -> None:
    folder = tmp_path / "AL0103测试"
    folder.mkdir()
    (folder / "SKU-01-red.jpg").write_bytes(b"image")

    result = scan_asset_root(folder, mode="recursive")
    asset = result["assets"][0]

    assert asset["asset_type"] == "variant"
    assert asset["sku_option"] == "Red"


def test_supplier_sku_colors_are_read_from_filename(tmp_path: Path) -> None:
    """供应商文件名 `AL0481-07-darkgreen.png`（SKU-序号-颜色）里的颜色词要能识别，
    深浅复合色按店铺 SKU 写法返回 CamelCase：darkgreen → DarkGreen。"""
    folder = tmp_path / "AL0481测试"
    folder.mkdir()
    for name in [
        "AL0481-01-white.png",
        "AL0481-04-apricot.png",
        "AL0481-07-darkgreen.png",
        "AL0481-09-brown.png",
        "AL0481-10-lightblue.png",
    ]:
        (folder / name).write_bytes(b"image")

    result = scan_asset_root(folder, mode="recursive")
    by_name = {item["file_name"]: item for item in result["assets"]}

    assert by_name["AL0481-01-white.png"]["sku_option"] == "White"
    assert by_name["AL0481-04-apricot.png"]["asset_type"] == "variant"
    assert by_name["AL0481-04-apricot.png"]["sku_option"] == "Apricot"
    assert by_name["AL0481-07-darkgreen.png"]["asset_type"] == "variant"
    assert by_name["AL0481-07-darkgreen.png"]["sku_option"] == "DarkGreen"
    assert by_name["AL0481-09-brown.png"]["sku_option"] == "Brown"
    assert by_name["AL0481-10-lightblue.png"]["sku_option"] == "LightBlue"


def test_chinese_color_names_map_to_canonical_english(tmp_path: Path) -> None:
    """中文颜色名要和英文走同一套规范名：杏色 → Apricot、深蓝色 → DarkBlue、
    棕色 → Brown（「深蓝」不会被「蓝色」抢先命中成 Blue）。词表没收录的自造色名
    （奶油白/米白）保持原样，仍交给视觉模型翻译。"""
    folder = tmp_path / "AL0500测试"
    folder.mkdir()
    for name in ["杏色.png", "棕色.png", "深蓝色.png", "浅蓝色.png", "SKU-奶油白.jpg", "米白.png"]:
        (folder / name).write_bytes(b"image")

    result = scan_asset_root(folder, mode="recursive")
    by_name = {item["file_name"]: item for item in result["assets"]}

    assert by_name["杏色.png"]["asset_type"] == "variant"
    assert by_name["杏色.png"]["sku_option"] == "Apricot"
    assert by_name["棕色.png"]["asset_type"] == "variant"
    assert by_name["棕色.png"]["sku_option"] == "Brown"
    assert by_name["深蓝色.png"]["sku_option"] == "DarkBlue"
    assert by_name["浅蓝色.png"]["sku_option"] == "LightBlue"
    assert by_name["SKU-奶油白.jpg"]["sku_option"] == "奶油白"
    assert by_name["米白.png"]["sku_option"] == ""


def test_chinese_color_filenames_flow_into_base_product(tmp_path: Path) -> None:
    """中文颜色文件名导入后，颜色列表与颜色图映射都用规范英文名。"""
    folder = tmp_path / "AL0501测试"
    folder.mkdir()
    for name in ["杏色.png", "棕色.png", "深蓝色.png"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder)})

    assert product is not None
    assert product["recognized_colors"] == ["Apricot", "Brown", "DarkBlue"]
    assert product["variant_image_map"] == {
        "Apricot": "杏色.png",
        "Brown": "棕色.png",
        "DarkBlue": "深蓝色.png",
    }


def test_spaced_modifier_color_words_collapse_to_one_camel_case_name(tmp_path: Path) -> None:
    """`dark green` 与 `darkgreen` 必须落到同一个颜色名，否则同一颜色会生成两个变体键；
    中文的深浅前缀同理：浅蓝 → LightBlue。"""
    folder = tmp_path / "AL0104测试"
    folder.mkdir()
    for name in ["SKU-01-dark green.jpg", "SKU-02-darkgreen.jpg", "SKU-03-浅蓝.jpg"]:
        (folder / name).write_bytes(b"image")

    result = scan_asset_root(folder, mode="recursive")
    by_name = {item["file_name"]: item for item in result["assets"]}

    assert by_name["SKU-01-dark green.jpg"]["sku_option"] == "DarkGreen"
    assert by_name["SKU-02-darkgreen.jpg"]["sku_option"] == "DarkGreen"
    assert by_name["SKU-03-浅蓝.jpg"]["sku_option"] == "LightBlue"


def test_recognized_colors_keep_camel_case_names() -> None:
    """颜色规范化不能把已有的驼峰名压平：DarkGreen/PureWhite 原样保留，全小写
    或带空格的复合色按文件名识别的同一套规范名补齐（darkgreen/dark green →
    DarkGreen）；两个颜色词不合并（Black Gold 保持分词）。"""
    normalized = storage._normalize_recognized_colors(
        ["darkgreen", "dark green", "DarkGreen", "PureWhite", "Black Gold", "黑色"]
    )

    assert normalized == ["DarkGreen", "PureWhite", "Black Gold", "黑色"]


def test_supplier_sku_colors_flow_into_base_product(tmp_path: Path) -> None:
    """导入 AL0481 风格文件夹后，颜色列表与颜色图映射都用 CamelCase 复合色名。"""
    folder = tmp_path / "AL0481测试"
    folder.mkdir()
    for name in ["AL0481-07-darkgreen.png", "AL0481-10-lightblue.png", "AL0481-01-white.png"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder)})

    assert product is not None
    assert product["recognized_colors"] == ["DarkGreen", "LightBlue", "White"]
    assert product["variant_image_map"] == {
        "DarkGreen": "AL0481-07-darkgreen.png",
        "LightBlue": "AL0481-10-lightblue.png",
        "White": "AL0481-01-white.png",
    }


def test_filename_colors_flow_into_base_product_variants(tmp_path: Path) -> None:
    folder = tmp_path / "AL0100测试"
    folder.mkdir()
    for name in ["Main-正面.jpg", "SKU-红色-01.jpg", "SKU-奶油白.jpg"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder)})

    assert product is not None
    assert product["main_image"] == "Main-正面.jpg"
    # 颜色图（SKU-…）排在模特图之后进默认商品图（2026-10-09 起）。
    assert product["product_images"] == ["Main-正面.jpg", "SKU-奶油白.jpg", "SKU-红色-01.jpg"]
    assert product["recognized_colors"] == ["Red", "奶油白"]
    assert product["variant_image_map"] == {
        "Red": "SKU-红色-01.jpg",
        "奶油白": "SKU-奶油白.jpg",
    }


def test_all_main_images_are_added_to_product_images(tmp_path: Path) -> None:
    folder = tmp_path / "AL0101测试"
    folder.mkdir()
    for name in ["Main-01.jpg", "Main-02.jpg", "主图-03.jpg", "模特图1.jpg", "详情-01.jpg"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder)})

    assert product is not None
    assert product["product_images"] == [
        "Main-01.jpg", "Main-02.jpg", "主图-03.jpg", "模特图1.jpg"
    ]


def test_store_templates_can_be_listed_and_created() -> None:
    template = storage.create_store_template(
        {
            "store_name": "Shopee-MY-A",
            "platform": "shopee",
            "country": "MY",
            "currency": "MYR",
            "template_name": "Mens Tee",
            "product_line": "POD 男装T恤",
            "marketplace_category_path": "Men Clothes > Tops > T-shirts",
            "normal_price": 89,
            "sale_price": 69,
            "discount_label": "Default 7.7",
            "sizes": ["S", "M", "L"],
            "title_suffix": "Cotton T-Shirt Men Casual Tee",
            "description": "Store-specific description.",
        }
    )

    assert template["id"]
    assert template["store_name"] == "Shopee-MY-A"
    assert template["sizes"] == ["S", "M", "L"]
    assert storage.list_store_templates()[0]["template_name"] == "Mens Tee"


def test_category_profiles_include_pod_ph_mens_tshirt_defaults() -> None:
    profiles = storage.list_category_profiles()
    mens = next(item for item in profiles if item["profile_id"] == "pod-ph-mens-tee")

    assert mens["display_name"] == "POD菲律宾 男装 > 上衣 > T恤"
    assert mens["marketplace_category_path"] == "Men Clothes > Tops > T-Shirts"
    assert mens["mabang_category_id"] == "100244"
    assert mens["required_attributes"]["brand"] == "NoBrand"
    assert mens["required_attributes"]["pattern"] == "Print"
    assert mens["required_attributes"]["material"] == "Cotton"
    assert mens["required_attributes"]["sleeve_length"] == "Short Sleeves"
    assert mens["required_attributes"]["plus_size"] == "Yes"
    assert mens["variation"]["type"] == "variant"
    assert mens["variation"]["axes"] == ["color", "size"]


def test_create_pod_ph_store_template_from_category_profile_defaults() -> None:
    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "mabang_store_id": "2022625115",
            "template_name": "POD PH Mens Tee",
            "category_profile_id": "pod-ph-mens-tee",
        }
    )

    assert template["store_name"] == "POD菲律宾"
    assert template["mabang_store_id"] == "2022625115"
    assert template["country"] == "PH"
    assert template["currency"] == "PHP"
    assert template["product_line"] == "POD Mens T-Shirt"
    assert template["marketplace_category_path"] == "Men Clothes > Tops > T-Shirts"
    assert template["mabang_category_id"] == "100244"
    assert template["discount_label"] == "40%"
    assert template["mabang_discount_id"] == "928232107737088"
    assert template["normal_price"] == 89
    assert template["sale_price"] == 69
    assert template["sizes"] == ["S", "M", "L", "XL", "2XL"]
    assert template["category_attributes"]["brand"] == "NoBrand"
    assert template["variation"]["type"] == "variant"
    assert template["variation"]["axes"] == ["color", "size"]


def test_create_base_product_from_assets_records_shared_assets(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder(
        {
            "root_path": str(folder),
            "category": "POD 男装T恤",
        }
    )

    assert product["group_id"] == "DL0001"
    assert product["asset_root"] == str(folder)
    assert product["main_image"] == "主图.jpg"
    assert product["recognized_colors"] == ["Black", "White"]
    assert product["status"] == "base_ready"


def test_local_folder_import_keeps_size_sheet_and_source_out_of_product_images(tmp_path: Path) -> None:
    """AI 生图产出的本地商品文件夹（AL0474-01-white.png / -sheet / -source）导入时，
    尺码表要进 size_chart_image、源图只能留档，两者都不能被当成商品图或主图。"""
    folder = tmp_path / "AL0471"
    folder.mkdir()
    for name in [
        "AL0474-01-white.png",
        "AL0474-02-black.png",
        "AL0474-sheet.png",
        "AL0474-source.png",
        "AL0474-模特图.png",
    ]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["size_chart_image"] == "AL0474-sheet.png"
    # 没有 主图- 文件时按素材角色回退：模特图 > 颜色图（见 POD_ASSET_RULES 的推荐图片顺序），
    # 尺码表虽然排在素材列表最前面也不能当主图。
    assert product["main_image"] == "AL0474-模特图.png"
    assert "AL0474-sheet.png" not in product["product_images"]
    assert "AL0474-source.png" not in product["product_images"]
    # 默认顺序：主图 → 模特图 → 颜色图。
    assert product["product_images"] == ["AL0474-模特图.png", "AL0474-01-white.png", "AL0474-02-black.png"]
    assert product["recognized_colors"] == ["Black", "White"]


def test_local_folder_import_falls_back_to_color_photo_when_only_variants_exist(tmp_path: Path) -> None:
    """只有颜色图 + 尺码表 + 源图（没有 主图-/模特图）时，主图要落到第一张颜色图。"""
    folder = tmp_path / "AL0477"
    folder.mkdir()
    for name in [
        "AL0477-02-black.png",
        "AL0477-10-lightblue.png",
        "AL0477-sheet.png",
        "AL0477-source.png",
    ]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["size_chart_image"] == "AL0477-sheet.png"
    assert product["main_image"] == "AL0477-02-black.png"
    assert product["product_images"] == ["AL0477-02-black.png", "AL0477-10-lightblue.png"]
    assert product["recognized_colors"] == ["Black", "LightBlue"]


def test_asset_types_recognize_size_sheets_and_source_images(tmp_path: Path) -> None:
    """尺码表/源图的新旧命名都不能落到默认的 main，普通商品图也不能被误判。"""
    folder = tmp_path / "AL0474"
    folder.mkdir()
    for name in [
        "AL0474-01-white.png",
        "AL0474-02-black.png",
        "AL0474-sheet.png",
        "AL0474-source.png",
        "AL0474-size-chart.png",
        "AL0474-尺码表.png",
        "AL0474-源图.png",
        "AL0474-模特图.png",
        "AL0474-详情1.png",
        "服装布面颜色更换 (1).png",
        "1 (1).png",
    ]:
        (folder / name).write_bytes(b"image")

    result = scan_asset_root(folder, category_override="POD Mens T-Shirt", mode="recursive")
    types = {asset["file_name"]: asset["asset_type"] for asset in result["assets"]}

    assert types["AL0474-sheet.png"] == "size_chart"
    assert types["AL0474-size-chart.png"] == "size_chart"
    assert types["AL0474-尺码表.png"] == "size_chart"
    assert types["AL0474-source.png"] == "source"
    assert types["AL0474-源图.png"] == "source"
    assert types["AL0474-01-white.png"] == "variant"
    assert types["AL0474-模特图.png"] == "model"
    assert types["AL0474-详情1.png"] == "detail"
    # 没有素材角色线索的文件保持原样：仍是商品图候选。
    assert types["服装布面颜色更换 (1).png"] == "main"
    assert types["1 (1).png"] == "main"


def test_print_artwork_never_becomes_a_product_image_or_color(tmp_path: Path) -> None:
    """生产用印刷图（前/后印花、透明印花层）不是商品照片：不进商品图、不当主图，
    也不做颜色图；文件名里的颜色词是印花色，不参与颜色识别。

    现场文件夹（AL0567/AL0571 这类）只有颜色图和印刷图，主图要落到第一张颜色图上。"""
    folder = tmp_path / "AL0571悟空"
    folder.mkdir()
    for name in [
        "A白色.jpg",
        "A黑色.jpg",
        "前胸印花图.png",
        "后胸印花图.png",
        "后印花 (黑色衣服).png",
        "后印花 (粉色衣服).png",
        "主印花.png",
        "前胸透明图.png",
        "袖口透明图.png",
        "后背透明图.png",
        "主透明图.png",
        "前胸透明图黑色.psd",
        "儿童通用印花视频2.mp4",
    ]:
        (folder / name).write_bytes(b"image")

    scan = scan_asset_root(folder, mode="recursive")
    types = {asset["file_name"]: asset["asset_type"] for asset in scan["assets"]}
    assert types["前胸印花图.png"] == "print"
    assert types["后胸印花图.png"] == "print"
    assert types["后印花 (黑色衣服).png"] == "print"
    assert types["主印花.png"] == "print"
    assert types["前胸透明图.png"] == "print"
    assert types["袖口透明图.png"] == "print"
    assert types["主透明图.png"] == "print"
    # 源图/视频角色优先：印刷图的 psd 留档，印花视频仍是视频。
    assert types["前胸透明图黑色.psd"] == "source"
    assert types["儿童通用印花视频2.mp4"] == "video"
    # 印刷图不算主图，但颜色图可以顶主图，所以这个文件夹不报缺主图。
    assert scan["summary"]["groups"]["AL0571"]["missing_required"] == []

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["main_image"] == "A白色.jpg"
    assert product["product_images"] == ["A白色.jpg", "A黑色.jpg"]
    # 印刷图里的「黑色/粉色」是印花色，不能变成衣服颜色。
    assert product["recognized_colors"] == ["Black", "White"]
    assert product["variant_image_map"] == {"White": "A白色.jpg", "Black": "A黑色.jpg"}


def test_only_print_artwork_still_reports_missing_main_and_variant(tmp_path: Path) -> None:
    """只有印刷图的文件夹确实缺主图和颜色图：印刷图顶不了这两个角色。"""
    folder = tmp_path / "AL0573"
    folder.mkdir()
    for name in ["后印花.png", "袖口透明图.png", "前胸透明图黑色.psd"]:
        (folder / name).write_bytes(b"image")

    scan = scan_asset_root(folder, mode="recursive")

    assert scan["summary"]["groups"]["AL0573"]["missing_required"] == ["main", "variant"]
    assert scan["summary"]["groups"]["AL0573"]["counts"]["print"] == 2


def test_explicit_main_image_wins_over_print_artwork_marker(tmp_path: Path) -> None:
    """文件名自己声明主图的（主图-印花细节.jpg）仍然是主图。"""
    folder = tmp_path / "AL0572"
    folder.mkdir()
    for name in ["主图-印花细节.jpg", "A白色.jpg", "后印花.png"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["main_image"] == "主图-印花细节.jpg"
    assert product["product_images"] == ["主图-印花细节.jpg", "A白色.jpg"]


def test_print_named_product_photos_are_not_production_artwork(tmp_path: Path) -> None:
    """「T恤印花改图任务」及编号版本是商品照，不是生产用印刷图。

    这类文件夹只有这批照片时，旧规则会把它们全部当成 print：不报缺主图/颜色图
    （print 不算），商品却一张图、一个颜色都没有。修正后它们进入商品照分类和
    未命名颜色图识别流程。"""
    folder = tmp_path / "JB0357"
    folder.mkdir()
    for name in [
        "T恤印花改图任务.png",
        "T恤印花改图任务 (1).png",
        "T恤印花改图任务（2）.png",
        "T恤印花改图任务 3.png",
        # 真正的生产印刷图仍然排除
        "前胸印花图.png",
        "后印花 (黑色衣服).png",
        "前胸透明图.png",
        "袖口透明图.png",
        "T恤印花改图任务书.png",
    ]:
        (folder / name).write_bytes(b"image")

    scan = scan_asset_root(folder, mode="recursive")
    types = {asset["file_name"]: asset["asset_type"] for asset in scan["assets"]}

    # 四张确认的商品照命名都不是 print；整组无其他线索时按未命名颜色图处理。
    for name in ["T恤印花改图任务.png", "T恤印花改图任务 (1).png", "T恤印花改图任务（2）.png", "T恤印花改图任务 3.png"]:
        assert types[name] == "variant"
    # 真正的印刷图/透明图仍然排除；名字接近但不是已确认命名的也保持 print。
    for name in ["前胸印花图.png", "后印花 (黑色衣服).png", "前胸透明图.png", "袖口透明图.png", "T恤印花改图任务书.png"]:
        assert types[name] == "print"

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["main_image"] == "T恤印花改图任务.png"
    assert product["product_images"][0] == "T恤印花改图任务.png"
    assert set(product["product_images"]) == {
        "T恤印花改图任务.png",
        "T恤印花改图任务 (1).png",
        "T恤印花改图任务（2）.png",
        "T恤印花改图任务 3.png",
    }
    assert product["variant_count"] == 4
    assert product["recognized_colors"] == []


def test_print_named_product_photos_join_color_recognition_next_to_color_images(tmp_path: Path) -> None:
    """有颜色图的文件夹里，商品照仍按商品图保留；已命名的颜色图继续负责颜色映射，
    商品照不会因为名字里的「印花」被丢掉，也不会被当成颜色。"""
    folder = tmp_path / "JB0142"
    folder.mkdir()
    for name in [
        "T恤印花改图任务.png",
        "T恤印花改图任务 (1).png",
        "A白色.jpg",
        "A黑色.jpg",
        "前胸透明图.png",
    ]:
        (folder / name).write_bytes(b"image")

    scan = scan_asset_root(folder, mode="recursive")
    types = {asset["file_name"]: asset["asset_type"] for asset in scan["assets"]}
    assert types["T恤印花改图任务.png"] == "main"
    assert types["T恤印花改图任务 (1).png"] == "main"
    assert types["前胸透明图.png"] == "print"

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["main_image"] == "T恤印花改图任务.png"
    assert product["product_images"] == ["T恤印花改图任务.png", "T恤印花改图任务 (1).png", "A白色.jpg", "A黑色.jpg"]
    assert product["recognized_colors"] == ["Black", "White"]
    assert product["variant_image_map"] == {"White": "A白色.jpg", "Black": "A黑色.jpg"}


def test_unnamed_numbered_folder_becomes_an_unnamed_variant_set(tmp_path: Path) -> None:
    """AI 生图工具导出的整套图片（1 (1).png…1 (10).png）文件名不含任何素材角色线索，
    整组按「未命名颜色图」处理：交给颜色识别命名，同时颜色图本身也进默认商品图
    （最多 9 张，第 10 张留在「可选产品图」里）。"""
    folder = tmp_path / "AL0472"
    folder.mkdir()
    for index in range(1, 11):
        (folder / f"1 ({index}).png").write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["main_image"] == "1 (1).png"
    assert product["product_images"] == [f"1 ({index}).png" for index in range(1, 10)]
    assert product["variant_count"] == 10
    assert product["variant_image_map"] == {}
    assert product["recognized_colors"] == []


def test_numbered_images_stay_gallery_when_the_folder_has_role_hints(tmp_path: Path) -> None:
    """有显式主图或颜色线索的文件夹（多角度商品图）不受未命名颜色图策略影响。"""
    with_main = tmp_path / "AL0105主图"
    with_main.mkdir()
    for name in ["主图1.jpg", "1.jpg", "2.jpg", "3.jpg"]:
        (with_main / name).write_bytes(b"image")
    with_color = tmp_path / "AL0106颜色"
    with_color.mkdir()
    for name in ["白色.jpg", "1.jpg", "2.jpg"]:
        (with_color / name).write_bytes(b"image")

    main_types = {asset["file_name"]: asset["asset_type"] for asset in scan_asset_root(with_main, mode="recursive")["assets"]}
    color_types = {asset["file_name"]: asset["asset_type"] for asset in scan_asset_root(with_color, mode="recursive")["assets"]}

    assert main_types == {"主图1.jpg": "main", "1.jpg": "main", "2.jpg": "main", "3.jpg": "main"}
    assert color_types == {"白色.jpg": "variant", "1.jpg": "main", "2.jpg": "main"}

    main_product = storage.create_base_product_from_folder({"root_path": str(with_main), "category": "POD Mens T-Shirt"})
    color_product = storage.create_base_product_from_folder({"root_path": str(with_color), "category": "POD Mens T-Shirt"})

    assert main_product["product_images"] == ["主图1.jpg", "1.jpg", "2.jpg", "3.jpg"]
    assert main_product["variant_count"] == 0
    # 颜色图排在商品图之后：无信息命名的 1/2.jpg 仍是商品图。
    assert color_product["product_images"] == ["1.jpg", "2.jpg", "白色.jpg"]
    assert color_product["recognized_colors"] == ["White"]
    # 只有 白色.jpg 一张颜色图；无信息命名的 1/2.jpg 没有被并进颜色图。
    assert color_product["variant_count"] == 1
    assert color_product["variant_image_map"] == {"White": "白色.jpg"}


def test_reimported_folder_does_not_borrow_assets_from_the_previous_import(tmp_path: Path) -> None:
    """同一个 SKU 文件夹重新导入后，调用方按本次导入目录取素材，上一次导入的
    同名 SKU 素材不能混进商品图选择器和颜色识别。"""
    first = tmp_path / "AL0472首次"
    first.mkdir()
    (first / "服装布面颜色更换 (1).png").write_bytes(b"image")
    (first / "服装布面颜色更换 (2).png").write_bytes(b"image")
    second = tmp_path / "AL0472重导"
    second.mkdir()
    (second / "1 (1).png").write_bytes(b"image")
    (second / "1 (2).png").write_bytes(b"image")

    storage.create_base_product_from_folder({"root_path": str(first), "category": "POD Mens T-Shirt"})
    product = storage.create_base_product_from_folder({"root_path": str(second), "category": "POD Mens T-Shirt"})

    assert len(storage.assets_for_group("AL0472")) == 4
    assert [asset["file_name"] for asset in storage.assets_for_base_product(product)] == ["1 (1).png", "1 (2).png"]


def test_base_product_records_product_images_and_size_chart_from_nas(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in [
        "主图.jpg",
        "白色.jpg",
        "黑色.jpg",
        "红色.jpg",
        "蓝色.jpg",
        "绿色.jpg",
        "粉色.jpg",
        "模特图1.jpg",
        "模特图2.jpg",
        "模特图3.jpg",
        "尺码表.png",
    ]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})

    assert product["size_chart_image"] == "尺码表.png"
    assert product["product_images"][0] == "主图.jpg"
    assert "尺码表.png" not in product["product_images"]
    # 默认顺序：主图 → 模特图 → 颜色图（颜色图按文件名自然序补位，最多 9 张）。
    assert product["product_images"] == [
        "主图.jpg",
        "模特图1.jpg",
        "模特图2.jpg",
        "模特图3.jpg",
        "白色.jpg",
        "粉色.jpg",
        "红色.jpg",
        "绿色.jpg",
        "蓝色.jpg",
    ]
    assert "黑色.jpg" not in product["product_images"]


def test_base_product_video_is_snapshotted_to_listing_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "DL0001 video product"
    folder.mkdir()
    (folder / "main.jpg").write_bytes(b"image")
    video_path = folder / "视频.mp4"
    video_path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20)
    monkeypatch.setattr(
        "services.api.storage.validate_video",
        lambda path: {
            "mime_type": "video/mp4", "size_bytes": 32, "duration_seconds": 30,
            "width": 720, "height": 720, "validation_status": "valid", "validation_errors": [],
        },
    )

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    template = storage.create_store_template(
        {
            "store_name": "Video Store",
            "template_name": "Video template",
            "product_line": "POD 男装T恤",
            "marketplace_category_path": "Men > Tops",
            "normal_price": 100,
            "sale_price": 60,
            "sizes": ["S"],
        }
    )
    listing_copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    assert base["video"]["file_name"] == "视频.mp4"
    assert base["video"]["validation_status"] == "valid"
    assert listing_copy["video"] == base["video"]

    base["video"]["validation_status"] = "invalid"
    assert listing_copy["video"]["validation_status"] == "valid"


def test_base_product_defaults_product_images_to_main_then_numbered_model_images(tmp_path: Path) -> None:
    folder = tmp_path / "AL1001 Product Images"
    folder.mkdir()
    for name in [
        "detail1.jpg",
        "main.jpg",
        "White.jpg",
        "model10.jpg",
        "model2.jpg",
        "model1.jpg",
        "model3.jpg",
        "model4.jpg",
        "model5.jpg",
        "model6.jpg",
        "model7.jpg",
        "model8.jpg",
        "model9.jpg",
    ]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})

    assert product["main_image"] == "main.jpg"
    assert product["product_images"] == [
        "main.jpg",
        "model1.jpg",
        "model2.jpg",
        "model3.jpg",
        "model4.jpg",
        "model5.jpg",
        "model6.jpg",
        "model7.jpg",
        "model8.jpg",
    ]
    assert "model9.jpg" not in product["product_images"]
    # 主图 + 8 张模特图已经占满 9 张，颜色图/详情图排在后面进不来。
    assert "White.jpg" not in product["product_images"]
    assert "detail1.jpg" not in product["product_images"]


def test_base_product_variant_image_map_flows_to_listing_skus_and_payload(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "A白色.jpg", "A黑色.jpg", "白色备用.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})

    assert base["variant_image_map"] == {"Black": "A黑色.jpg", "White": "A白色.jpg"}

    updated = storage.update_base_product(base["id"], {"variant_image_map": {"Black": "A黑色.jpg", "White": "白色备用.jpg"}})
    template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": "Mens Tee",
            "product_line": "POD 男装T恤",
            "marketplace_category_path": "Men Clothes > Tops > T-shirts",
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "discount_label": "40%",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S"],
            "title_suffix": "Cotton T-Shirt Men",
            "description": "Mens tee description.",
        }
    )
    result = storage.copy_base_product_to_store_templates(updated["id"], [template["id"]])
    listing_copy = result["created"][0]
    skus = storage.list_listing_skus(listing_copy["id"])
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert listing_copy["variant_image_map"] == {"Black": "A黑色.jpg", "White": "白色备用.jpg"}
    assert {sku["color_code"]: sku["variant_image"] for sku in skus} == {"Black": "A黑色.jpg", "White": "白色备用.jpg"}
    assert payload["browser_inputs"]["variant_image_map"] == {"Black": "A黑色.jpg", "White": "白色备用.jpg"}
    assert {color: Path(path).name for color, path in payload["browser_inputs"]["variant_image_paths"].items()} == {
        "Black": "A黑色.jpg",
        "White": "白色备用.jpg",
    }
    assert payload["browser_inputs"]["discount_label"] == "40%"


def test_base_product_maintenance_keeps_existing_copy_and_job_snapshot(tmp_path: Path) -> None:
    folder = tmp_path / "AL9001 Sample"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg", "Black.jpg", "size-chart.png"]:
        (folder / name).write_bytes(b"image")
    template_chart = tmp_path / "template-size-chart.png"
    template_chart.write_bytes(b"chart")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": "Kids Tee",
            "product_line": "POD Kids T-Shirt",
            "marketplace_category_path": "Baby & Kids Fashion > Tops > T-Shirts",
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["110"],
            "sku_template": "{main_sku}_{color}||{size}",
            "default_size_chart_path": str(template_chart),
            **_transaction_defaults(),
            "title_suffix": "Template suffix",
            "description": "Template description.",
        }
    )
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    listing_copy = result["created"][0]
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    updated = storage.update_base_product(
        base["id"],
        {
            "listing_title": "Custom listing title",
            "description": "Custom product details.",
            "recognized_colors": ["Ivory", "Navy", "Ivory", ""],
            "variant_image_map": {"Ivory": "White.jpg", "Navy": "Black.jpg"},
        },
    )
    refreshed_copy = storage.get_item("store_listing_copies", listing_copy["id"])
    refreshed_job = storage.get_item("automation_jobs", job["id"])
    skus = storage.list_listing_skus(listing_copy["id"])
    prices = storage.list_listing_prices(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert updated["listing_title"] == "Custom listing title"
    assert updated["description"] == "Custom product details."
    assert updated["recognized_colors"] == ["Ivory", "Navy"]
    assert refreshed_copy == listing_copy
    assert [sku["sku_code"] for sku in skus] == ["AL9001_Black||110", "AL9001_White||110"]
    assert [price["sku_code"] for price in prices] == ["AL9001_Black||110", "AL9001_White||110"]
    assert refreshed_job["status"] == "ready"
    assert payload["browser_inputs"]["title"] == listing_copy["listing_title"]
    assert payload["browser_inputs"]["description"] == listing_copy["description"]


def test_listing_copy_uses_template_fixed_description_when_set(tmp_path: Path) -> None:
    # 店铺模板「描述模板（固定描述）」：填了之后，所有复制到该模板的详情统一
    # 用这份文案（优先于基础商品自己的详情）；留空则回退基础商品详情。
    folder = tmp_path / "TX2001 固定描述"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg", "Black.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD Mens T-Shirt"}
    )
    storage.update_base_product(base["id"], {"description": "基础商品自己的详情。"})
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})

    fixed = storage.create_store_template({
        "store_name": "POD PH", "platform": "shopee", "country": "PH", "currency": "PHP",
        "template_name": "固定描述模板", "product_line": "POD Mens T-Shirt",
        "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
        "mabang_store_id": "store-fixed", "mabang_category_id": "cat-fixed",
        "normal_price": 89, "sale_price": 69, "sizes": ["S"],
        "description": "All items under this template share this fixed description.",
    })
    copy_fixed = storage.copy_base_product_to_store_templates(base["id"], [fixed["id"]])["created"][0]
    assert copy_fixed["description"] == "All items under this template share this fixed description."

    blank = storage.create_store_template({
        "store_name": "POD PH", "platform": "shopee", "country": "PH", "currency": "PHP",
        "template_name": "无固定描述模板", "product_line": "POD Mens T-Shirt",
        "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
        "mabang_store_id": "store-blank", "mabang_category_id": "cat-blank",
        "normal_price": 89, "sale_price": 69, "sizes": ["S"],
        "description": "",
    })
    copy_blank = storage.copy_base_product_to_store_templates(base["id"], [blank["id"]])["created"][0]
    assert copy_blank["description"] == "基础商品自己的详情。"


def test_listing_copy_inherits_category_profile_and_browser_payload_uses_it(tmp_path: Path) -> None:
    folder = tmp_path / "AL0071"
    folder.mkdir()
    for name in ["main.jpg", "black.jpg", "white.jpg", "model1.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "mabang_store_id": "2022625115",
            "template_name": "POD PH Mens Tee",
            "category_profile_id": "pod-ph-mens-tee",
            **_transaction_defaults(),
        }
    )
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    listing_copy = result["created"][0]
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert listing_copy["category_profile_id"] == "pod-ph-mens-tee"
    assert listing_copy["store_name"] == "POD菲律宾"
    assert listing_copy["mabang_store_id"] == "2022625115"
    assert listing_copy["mabang_category_id"] == "100244"
    assert listing_copy["marketplace_category_path"] == "Men Clothes > Tops > T-Shirts"
    assert listing_copy["category_attributes"]["brand"] == "NoBrand"
    assert listing_copy["variation"]["type"] == "variant"
    assert job["status"] == "ready"
    assert payload["browser_inputs"]["category_attributes"]["brand"] == "NoBrand"
    assert payload["browser_inputs"]["category_attributes"]["sleeve_length"] == "Short Sleeves"
    assert payload["browser_inputs"]["category_attributes"]["plus_size"] == "Yes"
    assert payload["browser_inputs"]["variation"]["type"] == "variant"
    assert payload["browser_inputs"]["variation"]["axes"] == ["color", "size"]
    assert payload["browser_inputs"]["mabang_discount_id"] == "928232107737088"


def test_listing_copy_falls_back_to_category_profile_size_chart_when_nas_missing(tmp_path: Path) -> None:
    default_chart = tmp_path / "default-size-chart.jpg"
    default_chart.write_bytes(b"chart")
    folder = tmp_path / "AL0072"
    folder.mkdir()
    for name in ["main.jpg", "black.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "mabang_store_id": "2022625115",
            "template_name": "POD PH Mens Tee",
            "category_profile_id": "pod-ph-mens-tee",
            "default_size_chart_path": str(default_chart),
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    listing_copy = result["created"][0]
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert listing_copy["size_chart_image"] == default_chart.name
    assert listing_copy["size_chart_image_path"] == str(default_chart)
    assert payload["browser_inputs"]["size_chart_image"] == default_chart.name
    assert payload["browser_inputs"]["size_chart_image_path"] == str(default_chart)


def test_store_template_size_chart_update_keeps_existing_copy_snapshot(tmp_path: Path) -> None:
    folder = tmp_path / "AL9100 Sample"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg", "size-chart.png"]:
        (folder / name).write_bytes(b"image")
    template_chart = tmp_path / "template-size-chart.png"
    updated_template_chart = tmp_path / "template-size-chart-v2.png"
    template_chart.write_bytes(b"chart")
    updated_template_chart.write_bytes(b"chart-v2")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": "Mens Tee",
            "product_line": "POD Mens T-Shirt",
            "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S"],
            "default_size_chart_path": str(template_chart),
            **_transaction_defaults(),
            "title_suffix": "Cotton T-Shirt Men",
            "description": "Mens tee description.",
        }
    )
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    listing_copy = result["created"][0]
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert base["size_chart_image"] == "size-chart.png"
    assert listing_copy["size_chart_image"] == template_chart.name
    assert listing_copy["size_chart_image_path"] == str(template_chart)
    assert payload["browser_inputs"]["size_chart_image"] == template_chart.name
    assert payload["browser_inputs"]["size_chart_image_path"] == str(template_chart)

    storage.update_base_product(base["id"], {"size_chart_image": "size-chart.png"})
    after_base_update = storage.get_item("store_listing_copies", listing_copy["id"])

    assert after_base_update["size_chart_image"] == template_chart.name
    assert after_base_update["size_chart_image_path"] == str(template_chart)

    storage.update_store_template(template["id"], {"default_size_chart_path": str(updated_template_chart)})
    refreshed_copy = storage.get_item("store_listing_copies", listing_copy["id"])
    refreshed_job = storage.get_item("automation_jobs", job["id"])
    refreshed_payload = storage.build_automation_payload(job["id"])

    assert refreshed_copy["size_chart_image"] == template_chart.name
    assert refreshed_copy["size_chart_image_path"] == str(template_chart)
    assert refreshed_job["status"] == "ready"
    assert refreshed_payload["browser_inputs"]["size_chart_image_path"] == str(template_chart)


def test_category_profile_without_mabang_category_id_blocks_automation(tmp_path: Path) -> None:
    folder = tmp_path / "AL0073"
    folder.mkdir()
    for name in ["main.jpg", "black.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Womens T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "mabang_store_id": "2022625115",
            "template_name": "POD PH Womens Tee",
            "category_profile_id": "pod-ph-womens-tee",
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    listing_copy = result["created"][0]
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    assert listing_copy["mabang_category_id"] == ""
    assert job["status"] == "needs_manual"
    assert "mabang_category_id" in job["missing_fields"]


def test_reordering_base_product_images_flows_to_listing_copy_and_automation_payload(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg", "模特图1.jpg", "尺码表.png"]:
        (folder / name).write_bytes(b"image")
    template_chart = tmp_path / "template-size-chart.png"
    template_chart.write_bytes(b"chart")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    updated = storage.update_base_product(
        base["id"],
        {
            "product_images": ["黑色.jpg", "主图.jpg", "白色.jpg", "模特图1.jpg"],
            "size_chart_image": "尺码表.png",
        },
    )
    template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": "Mens Tee",
            "product_line": "POD 男装T恤",
            "marketplace_category_path": "Men Clothes > Tops > T-shirts",
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S", "M"],
            "default_size_chart_path": str(template_chart),
            "title_suffix": "Cotton T-Shirt Men",
            "description": "Mens tee description.",
        }
    )
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(updated["id"], [template["id"]])
    listing_copy = result["created"][0]
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert listing_copy["product_images"] == ["黑色.jpg", "主图.jpg", "白色.jpg", "模特图1.jpg"]
    assert listing_copy["size_chart_image"] == template_chart.name
    assert listing_copy["size_chart_image_path"] == str(template_chart)
    assert [Path(item).name for item in payload["browser_inputs"]["product_image_paths"]] == [
        "黑色.jpg",
        "主图.jpg",
        "白色.jpg",
        "模特图1.jpg",
    ]
    assert payload["browser_inputs"]["size_chart_image_path"] == str(template_chart)
    assert payload["browser_inputs"]["category_attributes"]["brand"] == "NoBrand"
    assert payload["browser_inputs"]["category_attributes"]["pattern"] == "Print"
    assert payload["browser_inputs"]["category_attributes"]["material"] == "Cotton"
    assert payload["browser_inputs"]["category_attributes"]["sleeve_length"] == "Short Sleeves"
    assert payload["browser_inputs"]["category_attributes"]["plus_size"] == "Yes"
    assert payload["browser_inputs"]["variation"]["type"] == "variant"
    assert payload["browser_inputs"]["variation"]["axes"] == ["color", "size"]
    assert payload["browser_inputs"]["variation"]["sizes"] == ["S", "M"]
    assert payload["browser_inputs"]["variation"]["colors"] == ["Black", "White"]


def test_variation_colors_follow_base_product_color_order_not_alphabetical(tmp_path: Path) -> None:
    """颜色轴顺序 = 商品维护里的颜色顺序，不是字母序。

    马帮执行器按 ``variation.colors`` 逐个「添加自定义」颜色值，颜色轴就按这个顺序
    生成；而 ``list_listing_skus`` 出库按 color_code 排序，直接拿它建轴会变成字母序
    （Apricot/Black/Brown…），与操作员在商品维护里排好的颜色顺序对不上。
    SKU 行跟同一顺序，保证「网格顺序 = 颜色轴顺序」。
    """
    folder = tmp_path / "DL0002超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "粉色.jpg", "杏色.jpg", "黑色.jpg", "尺码表.png"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    # 操作员在商品维护里把颜色排成 粉 → 杏 → 黑（字母序会是 Apricot → Black → Pink）。
    storage.update_base_product(base["id"], {"recognized_colors": ["Pink", "Apricot", "Black"]})
    template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": "Mens Tee",
            "product_line": "POD 男装T恤",
            "marketplace_category_path": "Men Clothes > Tops > T-shirts",
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S", "M"],
            "title_suffix": "Cotton T-Shirt Men",
            "description": "Mens tee description.",
        }
    )
    storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    inputs = storage.build_automation_payload(job["id"])["browser_inputs"]

    assert inputs["variation"]["colors"] == ["Pink", "Apricot", "Black"]
    assert [sku["color_code"] for sku in inputs["skus"]] == [
        "Pink", "Pink", "Apricot", "Apricot", "Black", "Black",
    ]
    # 价格行跟着同一顺序，不能错位。
    assert [price["sku_code"] for price in inputs["prices"]] == [sku["sku_code"] for sku in inputs["skus"]]


def test_copy_base_product_to_multiple_store_templates_creates_listing_copies(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg", "尺码表.png"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    mens = storage.create_store_template(
        {
            "store_name": "Shopee-MY-A",
            "platform": "shopee",
            "country": "MY",
            "currency": "MYR",
            "template_name": "Mens Tee",
            "product_line": "POD 男装T恤",
            "marketplace_category_path": "Men Clothes > Tops > T-shirts",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S", "M"],
            "title_suffix": "Cotton T-Shirt Men",
            "description": "Mens tee description.",
        }
    )
    kids = storage.create_store_template(
        {
            "store_name": "Shopee-MY-A",
            "platform": "shopee",
            "country": "MY",
            "currency": "MYR",
            "template_name": "Kids Tee",
            "product_line": "POD 童装T恤",
            "marketplace_category_path": "Baby & Kids Fashion > Tops > T-shirts",
            "normal_price": 79,
            "sale_price": 59,
            "sizes": ["100", "110"],
            "title_suffix": "Kids Cotton T-Shirt",
            "description": "Kids tee description.",
        }
    )

    result = storage.copy_base_product_to_store_templates(base["id"], [mens["id"], kids["id"]])

    assert result["created_count"] == 2
    copies = storage.list_store_listing_copies()
    assert {copy["template_id"] for copy in copies} == {mens["id"], kids["id"]}
    assert all(copy["base_product_id"] == base["id"] for copy in copies)
    assert len(storage.list_listing_skus(copies[0]["id"])) == 4
    assert len(storage.list_listing_prices(copies[0]["id"])) == 4


def test_copy_carries_main_sku_into_parent_sku_when_it_differs_from_group_id(tmp_path: Path) -> None:
    """Regression: when the operator-edited main_sku differs from the
    folder-derived group_id, copying to a store template must keep the main_sku
    as the copy identity AND use it for the automation Parent SKU — previously
    browser_inputs/basic_info.parent_sku fell back to group_id, so Mabang
    drafts were created under the folder code while every child SKU used the
    main_sku prefix.

    Since the folder-name SKU now wins over auto-assign (the folder is named
    ``AC4B7920 假发``), the split is created by editing main_sku afterwards."""
    from scripts.browser_use_runner import build_browser_use_task_plan

    folder = tmp_path / "AC4B7920 假发"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg"]:
        (folder / name).write_bytes(b"image")

    # 主 SKU auto-assign on: the folder-name SKU still wins on import.
    storage.save_runtime_configuration({
        "product_sku": {"auto_assign_on_import": True, "prefix": "FL", "number_width": 4, "next_number": 34},
    })
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 假发"})
    assert base["group_id"] == "AC4B7920"
    assert base["main_sku"] == "AC4B7920"  # folder name beats auto-assign
    # The operator edits the main SKU explicitly (mirrors the base editor),
    # creating the group_id / main_sku split this test guards.
    storage.update_base_product(base["id"], {"main_sku": "FL0034", "recognized_colors": ["white"]})
    # Give the base a cost price so the copy's automation readiness passes.
    storage.update_base_product(base["id"], {"price_tiers": [
        {"id": "white", "sizes": [], "colors": ["white"], "cost_price": 12.5}
    ]})
    (folder / "size-chart.png").write_bytes(b"image")

    template = storage.create_store_template({
        "store_name": "POD PH",
        "template_name": "Wig",
        "product_line": "POD 假发",
        "marketplace_category_path": "Hair > Wigs",
        "mabang_store_id": "store-1",
        "mabang_category_id": "cat-1",
        "mabang_discount_id": "discount-1",
        "default_size_chart_path": str(folder / "size-chart.png"),
        "normal_price": 10,
        "sale_price": 6,
        "sizes": ["S"],
        "variation_rule": "color_size",
        "sku_template": "{main_sku}_{color}||{size}",
        "description": "假发描述",
        **_transaction_defaults(),
    })
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]

    # 1) the copy record + SKU rows keep the main_sku (parent) identity
    assert copy["group_id"] == "AC4B7920"
    assert copy["main_sku"] == "FL0034"
    skus = storage.list_listing_skus(copy["id"])
    assert skus and all(sku["sku_code"].startswith("FL0034_") for sku in skus)

    # 2) the automation browser_inputs carries main_sku for the Parent SKU
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    payload = storage.build_automation_payload(job["id"])
    assert payload["ok"] is True
    assert payload["browser_inputs"]["group_id"] == "AC4B7920"
    assert payload["browser_inputs"]["main_sku"] == "FL0034"

    # 3) the executor task plan writes Parent SKU = main_sku (not group_id)
    task = build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)
    assert task["ok"] is True
    assert task["basic_info"]["parent_sku"] == "FL0034"


def test_product_line_api_creates_base_template_and_listing_copy(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")

    server, base_url = _start_api()
    try:
        base_product = _post_json(
            f"{base_url}/api/base-products/from-folder",
            {"root_path": str(folder), "category": "POD 男装T恤"},
        )
        template = _post_json(
            f"{base_url}/api/store-templates",
            {
                "store_name": "Shopee-MY-A",
                "template_name": "Mens Tee",
                "product_line": "POD 男装T恤",
                "normal_price": 89,
                "sale_price": 69,
                "sizes": "S,M",
            },
        )
        result = _post_json(
            f"{base_url}/api/store-listing-copies/from-base",
            {"base_product_id": base_product["id"], "template_ids": [template["id"]]},
        )
        copies = _get_json(f"{base_url}/api/store-listing-copies")
    finally:
        server.shutdown()
        server.server_close()

    assert result["created_count"] == 1
    assert copies["items"][0]["base_product_id"] == base_product["id"]
    assert copies["items"][0]["template_id"] == template["id"]


def test_automation_job_api_creates_job_and_payload(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )

    server, base_url = _start_api()
    try:
        result = _post_json(
            f"{base_url}/api/automation/jobs/from-listing-copies",
            {"listing_copy_ids": [listing_copy["id"]]},
        )
        jobs = _get_json(f"{base_url}/api/automation/jobs")
        payload = _get_json(f"{base_url}/api/automation/jobs/{result['created'][0]['id']}/payload")
    finally:
        server.shutdown()
        server.server_close()

    assert result["created_count"] == 1
    assert jobs["items"][0]["status"] == "ready"
    assert payload["browser_inputs"]["mabang_store_id"] == "store-123"
    assert payload["browser_inputs"]["sku_count"] == 4


def test_create_automation_job_from_listing_copy_marks_missing_mabang_ids(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(tmp_path)

    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert job["listing_copy_id"] == listing_copy["id"]
    assert job["status"] == "needs_manual"
    assert {"mabang_store_id", "mabang_category_id", "mabang_discount_id"}.issubset(set(job["missing_fields"]))
    assert payload["readiness"]["ready"] is False
    assert len(payload["skus"]) == 4
    assert len(payload["prices"]) == 4
    assert payload["listing_copy"]["listing_title"]


def test_ready_automation_job_payload_includes_browser_inputs(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )

    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert job["status"] == "ready"
    assert job["missing_fields"] == []
    assert payload["readiness"]["ready"] is True
    assert payload["browser_inputs"]["mabang_store_id"] == "store-123"
    assert payload["browser_inputs"]["mabang_category_id"] == "cat-456"
    assert payload["browser_inputs"]["mabang_discount_id"] == "discount-789"
    assert payload["browser_inputs"]["main_image"]
    assert payload["browser_inputs"]["sku_count"] == 4
    assert payload["browser_inputs"]["transaction_info"] == {
        "stock": 20,
        "weight_kg": 0.35,
        "package_length_cm": 30.0,
        "package_width_cm": 24.0,
        "package_height_cm": 3.0,
    }
    assert {sku["stock"] for sku in payload["skus"]} == {20}


def test_template_update_does_not_mutate_existing_copy_or_job(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    original_copy = storage.get_item("store_listing_copies", listing_copy["id"])
    original_payload = storage.build_automation_payload(job["id"])

    updated = storage.update_store_template(listing_copy["template_id"], {"stock": 25, "normal_price": 200})

    assert updated["version"] == 2
    # 模板编辑只同步「交易信息」的原价/六折售价到副本价格行（
    # _sync_site_template_prices_to_store_copies），副本其余字段与已建任务的执行
    # 快照都保持冻结——生产参数不因模板编辑而改变，要用新价就重新建任务。
    mutated = storage.get_item("store_listing_copies", listing_copy["id"])
    assert {key: value for key, value in mutated.items() if key != "updated_at"} == {
        key: value for key, value in original_copy.items() if key != "updated_at"
    }
    assert {price["normal_price"] for price in storage.list_listing_prices(listing_copy["id"])} == {200.0}
    assert storage.build_automation_payload(job["id"])["execution_snapshot"] == original_payload["execution_snapshot"]


def test_one_listing_copy_allows_only_one_active_job(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    first = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    with pytest.raises(storage.ActiveAutomationJobExists):
        storage.create_automation_job_from_listing_copy(listing_copy["id"])

    canceled = storage.cancel_automation_job(first["id"], reason="operator_rebuild")
    second = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    assert canceled["status"] == "canceled"
    assert second["id"] != first["id"]
    assert second["execution_snapshot_hash"]


def test_delete_automation_job_removes_only_non_running_tasks(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    ready_job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    assert storage.delete_automation_job(ready_job["id"]) == {"id": ready_job["id"], "deleted": True}
    assert storage.get_item("automation_jobs", ready_job["id"]) is None

    claimed_job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    storage.claim_next_automation_job("worker-a")
    with pytest.raises(ValueError, match="Only a non-running"):
        storage.delete_automation_job(claimed_job["id"])


def test_delete_base_product_removes_regenerable_data_and_retains_terminal_jobs(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    base_product_id = listing_copy["base_product_id"]
    saved_job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    claimed = storage.claim_next_automation_job("worker-a")
    storage.finish_automation_job(
        saved_job["id"],
        "worker-a",
        claimed["lease_token"],
        status="saved",
        result={"save_clicked": True, "publish_clicked": False},
    )
    ready_job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    sku_count = len(storage.list_listing_skus(listing_copy["id"]))
    price_count = len(storage.list_listing_prices(listing_copy["id"]))

    deleted = storage.delete_base_product(base_product_id)

    assert deleted == {
        "id": base_product_id,
        "deleted_listing_copy_count": 1,
        "deleted_listing_sku_count": sku_count,
        "deleted_listing_price_count": price_count,
        "deleted_automation_job_count": 1,
        "retained_automation_job_count": 1,
    }
    assert storage.get_item("base_products", base_product_id) is None
    assert storage.get_item("store_listing_copies", listing_copy["id"]) is None
    assert storage.list_listing_skus(listing_copy["id"]) == []
    assert storage.list_listing_prices(listing_copy["id"]) == []
    assert storage.get_item("automation_jobs", ready_job["id"]) is None
    assert storage.get_item("automation_jobs", saved_job["id"])["status"] == "saved"


def test_automation_job_lease_allows_only_one_worker_and_saved_is_terminal(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    first = storage.claim_next_automation_job("worker-a", lease_seconds=60, now="2026-07-13T05:00:00+00:00")
    second = storage.claim_next_automation_job("worker-b", lease_seconds=60, now="2026-07-13T05:00:01+00:00")

    assert first["job"]["id"] == job["id"]
    assert first["job"]["status"] == "claimed"
    assert first["lease_token"]
    assert second["job"] is None

    heartbeat = storage.heartbeat_automation_job(
        job["id"],
        "worker-a",
        first["lease_token"],
        phase="fill_basic_info",
        lease_seconds=60,
        now="2026-07-13T05:00:30+00:00",
    )
    assert heartbeat["status"] == "running"
    assert heartbeat["current_phase"] == "fill_basic_info"

    with pytest.raises(ValueError, match="lease"):
        storage.finish_automation_job(
            job["id"],
            "worker-b",
            "wrong-token",
            status="saved",
            result={"save_clicked": True, "publish_clicked": False},
            now="2026-07-13T05:00:40+00:00",
        )

    saved = storage.finish_automation_job(
        job["id"],
        "worker-a",
        first["lease_token"],
        status="saved",
        result={"save_clicked": True, "publish_clicked": False},
        now="2026-07-13T05:00:45+00:00",
    )
    assert saved["status"] == "saved"
    assert saved["saved_at"] == "2026-07-13T05:00:45+00:00"
    repeated = storage.finish_automation_job(
        job["id"],
        "worker-a",
        first["lease_token"],
        status="saved",
        result={"save_clicked": True, "publish_clicked": False},
        now="2026-07-13T05:00:46+00:00",
    )
    assert repeated == saved
    assert storage.claim_next_automation_job("worker-b", now="2026-07-13T05:01:00+00:00")["job"] is None


def test_automation_job_lease_recovers_expired_claim_without_exceeding_attempt_limit(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    first = storage.claim_next_automation_job("worker-a", lease_seconds=5, now="2026-07-13T05:00:00+00:00")

    recovered = storage.claim_next_automation_job("worker-b", lease_seconds=5, now="2026-07-13T05:00:06+00:00")
    assert recovered["job"]["id"] == job["id"]
    assert recovered["job"]["attempt_count"] == 2
    assert recovered["lease_token"] != first["lease_token"]

    storage.finish_automation_job(
        job["id"],
        "worker-b",
        recovered["lease_token"],
        status="failed",
        error="second failure",
        now="2026-07-13T05:00:07+00:00",
    )
    assert storage.claim_next_automation_job("worker-c", now="2026-07-13T05:00:08+00:00")["job"] is None


def test_expired_lease_accepts_only_strict_saved_receipt_before_reclaim(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    claimed = storage.claim_next_automation_job(
        "worker-a",
        lease_seconds=5,
        now="2026-07-13T05:00:00+00:00",
    )

    with pytest.raises(ValueError, match="lease"):
        storage.finish_automation_job(
            job["id"],
            "worker-a",
            claimed["lease_token"],
            status="saved",
            result={"save_clicked": True, "publish_clicked": False},
            now="2026-07-13T05:00:06+00:00",
        )

    strict_result = {
        "ok": True,
        "status": "saved",
        "exit_code": 0,
        "save_clicked": True,
        "publish_clicked": False,
        "page_closed": True,
        "close_method": "success_dialog",
    }
    saved = storage.finish_automation_job(
        job["id"],
        "worker-a",
        claimed["lease_token"],
        status="saved",
        result=strict_result,
        now="2026-07-13T05:00:07+00:00",
    )

    assert saved["status"] == "saved"
    assert saved["result"] == strict_result


def test_saved_with_warnings_requires_save_proof_and_normalizes_stages(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {"mabang_store_id": "store-123", "mabang_category_id": "cat-456", "mabang_discount_id": "discount-789"},
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    claimed = storage.claim_next_automation_job("worker-a", now="2026-07-13T05:00:00+00:00")
    result = {
        "ok": True, "status": "saved", "exit_code": 0,
        "save_clicked": True, "publish_clicked": False, "page_closed": True,
        "close_method": "success_dialog",
        "stages": {
            "video_upload": {
                "status": "warning", "code": "video_upload_failed", "message": "upload failed",
                "traceback": "secret trace", "token": "secret", "details": {
                    "file_name": "product.mp4", "api_key": "secret", "mabang_url": "https://www.mabangerp.com/private",
                },
            },
        },
    }

    saved = storage.finish_automation_job(
        job["id"], "worker-a", claimed["lease_token"], status="saved_with_warnings",
        result=result, now="2026-07-13T05:00:01+00:00",
    )

    assert saved["status"] == "saved_with_warnings"
    assert saved["saved_at"] == "2026-07-13T05:00:01+00:00"
    assert saved["result"]["stages"]["video_upload"] == {
        "status": "warning", "code": "video_upload_failed", "message": "upload failed",
        "details": {"file_name": "product.mp4"},
    }


def test_saved_with_warnings_rejects_missing_save_evidence(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {"mabang_store_id": "store-123", "mabang_category_id": "cat-456", "mabang_discount_id": "discount-789"},
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    claimed = storage.claim_next_automation_job("worker-a", now="2026-07-13T05:00:00+00:00")
    with pytest.raises(ValueError, match="save evidence"):
        storage.finish_automation_job(
            job["id"], "worker-a", claimed["lease_token"], status="saved_with_warnings",
            result={"save_clicked": False, "publish_clicked": False},
        )


def test_automation_job_lease_api_claims_heartbeats_and_finishes(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    server, base_url = _start_api()
    try:
        claimed = _post_json(
            f"{base_url}/api/automation/jobs/claim",
            {"worker_id": "worker-api", "lease_seconds": 60},
        )
        heartbeat = _post_json(
            f"{base_url}/api/automation/jobs/{job['id']}/heartbeat",
            {
                "worker_id": "worker-api",
                "lease_token": claimed["lease_token"],
                "phase": "iframe_safe_fill",
                "lease_seconds": 60,
            },
        )
        saved = _post_json(
            f"{base_url}/api/automation/jobs/{job['id']}/finish",
            {
                "worker_id": "worker-api",
                "lease_token": claimed["lease_token"],
                "status": "saved",
                "result": {"save_clicked": True, "publish_clicked": False},
            },
        )
    finally:
        server.shutdown()
        server.server_close()

    assert claimed["job"]["id"] == job["id"]
    assert heartbeat["status"] == "running"
    assert saved["status"] == "saved"


def test_automation_job_treats_placeholder_mabang_ids_as_missing(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "2022625115",
            "mabang_category_id": "category id",
            "mabang_discount_id": "discount id",
        },
    )

    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert job["status"] == "needs_manual"
    assert {"mabang_category_id", "mabang_discount_id"}.issubset(set(payload["readiness"]["missing_fields"]))


def test_store_template_update_api_keeps_existing_copy_snapshot(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(tmp_path)
    first_job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    server, base_url = _start_api()
    try:
        updated = _put_json(
            f"{base_url}/api/store-templates/{listing_copy['template_id']}",
            {
                "store_name": "POD菲律宾",
                "country": "PH",
                "currency": "PHP",
                "mabang_store_id": "store-123",
                "mabang_category_id": "cat-456",
                "mabang_discount_id": "discount-789",
            },
        )
    finally:
        server.shutdown()
        server.server_close()

    second_job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(second_job["id"])

    assert first_job["status"] == "needs_manual"
    assert storage.get_item("store_listing_copies", listing_copy["id"])["store_name"] == "Shopee-MY-A"
    assert updated["mabang_store_id"] == "store-123"
    assert second_job["status"] == "needs_manual"
    assert payload["readiness"]["ready"] is False
    assert payload["browser_inputs"]["mabang_category_id"] == ""
    prices = storage.list_listing_prices(listing_copy["id"])
    assert prices
    assert {price["store"] for price in prices} == {"Shopee-MY-A"}
    assert {price["country"] for price in prices} == {"MY"}
    assert {price["currency"] for price in prices} == {"MYR"}


def test_store_template_category_profile_update_keeps_existing_copy_and_job(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(tmp_path)
    first_job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    updated = storage.update_store_template(
        listing_copy["template_id"],
        {
            "store_name": "POD菲律宾",
            "mabang_store_id": "2022625115",
            "template_name": "POD PH Mens Tee",
            "category_profile_id": "pod-ph-mens-tee",
        },
    )
    refreshed_copy = storage.get_item("store_listing_copies", listing_copy["id"])
    refreshed_job = storage.get_item("automation_jobs", first_job["id"])
    payload = storage.build_automation_payload(first_job["id"])

    assert updated["category_profile_id"] == "pod-ph-mens-tee"
    assert refreshed_copy == listing_copy
    # 任务逐字段没变（执行快照现在存在独立集合里，任务对象上不再带它）。
    stored_job = {key: value for key, value in refreshed_job.items() if key != "execution_snapshot"}
    created_job = {key: value for key, value in first_job.items() if key != "execution_snapshot"}
    assert stored_job == created_job, "改模板不该动已有任务"
    assert (storage.load_store().get("automation_job_snapshots") or {}).get(first_job["id"]), "执行快照要还在"
    assert payload["browser_inputs"]["store_name"] == listing_copy["store_name"]


def test_store_template_default_sku_sync_preserves_saved_job_status(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    claimed = storage.claim_next_automation_job("worker-a", now="2026-07-13T05:00:00+00:00")
    saved = storage.finish_automation_job(
        job["id"],
        "worker-a",
        claimed["lease_token"],
        status="saved",
        result={
            "ok": True,
            "status": "saved",
            "exit_code": 0,
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
        },
        now="2026-07-13T05:00:01+00:00",
    )

    storage.update_store_template(
        listing_copy["template_id"],
        {
            "default_sku": {
                "enabled": True,
                "color_name": "White",
                "sku": "DEFAULT-{size}",
                "normal_price": 100,
                "sale_price": 60,
            },
        },
    )

    refreshed = storage.get_item("automation_jobs", job["id"])
    assert refreshed["status"] == "saved"
    assert refreshed["saved_at"] == saved["saved_at"]
    assert refreshed["result"] == saved["result"]


def test_store_template_default_sku_sync_preserves_active_job_lease(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    claimed = storage.claim_next_automation_job("worker-a", now="2026-07-13T05:00:00+00:00")

    storage.update_store_template(
        listing_copy["template_id"],
        {
            "default_sku": {
                "enabled": True,
                "color_name": "White",
                "sku": "DEFAULT-{size}",
                "normal_price": 100,
                "sale_price": 60,
            },
        },
    )

    refreshed = storage.get_item("automation_jobs", job["id"])
    assert refreshed["status"] == "claimed"
    assert refreshed["worker_id"] == "worker-a"
    assert refreshed["lease_token"] == claimed["lease_token"]
    assert refreshed["lease_expires_at"] == claimed["job"]["lease_expires_at"]


def test_explicit_profile_snapshot_refresh_keeps_existing_copy_and_job(tmp_path: Path) -> None:
    from scripts.migrate_category_profiles import migrate_store

    data = storage.load_store()
    migrate_store(data, storage.CATEGORY_PROFILES, now="2026-07-13T00:00:00Z")
    storage.save_store(data)
    singular = {
        "brand": "NoBrand",
        "pattern": "Print",
        "material": "Cotton",
        "sleeve_length": "Short Sleeve",
        "plus_size": "Plus",
    }
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "category_profile_id": "pod-ph-mens-tee",
            "attribute_values": singular,
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    plural = {**singular, "sleeve_length": "Short Sleeves"}
    storage.update_store_template(
        listing_copy["template_id"],
        {
            "attribute_values": plural,
            "refresh_profile_snapshot": True,
        },
    )

    refreshed_copy = storage.get_item("store_listing_copies", listing_copy["id"])
    refreshed_job = storage.get_item("automation_jobs", job["id"])
    payload = storage.build_automation_payload(job["id"])

    assert refreshed_copy["attribute_values"]["sleeve_length"] == "Short Sleeve"
    assert refreshed_copy["category_profile_snapshot"]["fields"]
    assert refreshed_job["profile_snapshot_hash"] == refreshed_copy["profile_snapshot_hash"]
    assert "profile_snapshot_changed" not in payload["readiness"]["missing_fields"]
    assert payload["browser_inputs"]["category_attributes"]["sleeve_length"] == "Short Sleeve"


def test_category_profile_update_keeps_existing_copy_without_size_chart(tmp_path: Path) -> None:
    folder = tmp_path / "AL0074"
    folder.mkdir()
    for name in ["main.jpg", "black.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "mabang_store_id": "2022625115",
            "template_name": "Legacy Mens Tee",
            "product_line": "POD Mens T-Shirt",
            "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
            "mabang_category_id": "100244",
            "mabang_discount_id": "928232107737088",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S"],
            **_transaction_defaults(),
            "title_suffix": "Cotton T-Shirt Men",
            "description": "Legacy template without size chart.",
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    listing_copy = result["created"][0]
    first_job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    assert first_job["status"] == "needs_manual"
    assert "size_chart_image" in first_job["missing_fields"]

    storage.update_store_template(template["id"], {"category_profile_id": "pod-ph-mens-tee"})
    refreshed_copy = storage.get_item("store_listing_copies", listing_copy["id"])
    refreshed_job = storage.get_item("automation_jobs", first_job["id"])
    payload = storage.build_automation_payload(first_job["id"])

    assert refreshed_copy["size_chart_image"] == ""
    assert refreshed_copy["size_chart_image_path"] == ""
    assert refreshed_job["status"] == "needs_manual"
    assert "size_chart_image" in refreshed_job["missing_fields"]
    assert payload["browser_inputs"]["size_chart_image_path"] == ""


def test_mabang_store_options_api_saves_real_store_ids() -> None:
    server, base_url = _start_api()
    try:
        saved = _post_json(
            f"{base_url}/api/mabang/store-options",
            {
                "items": [
                    {"platform": "shopee", "store_name": "POD菲律宾", "mabang_store_id": "2022625115"},
                    {"platform": "shopee", "store_name": "TH本土Shopee-海宁TH", "mabang_store_id": "2022117278"},
                ]
            },
        )
        options = _get_json(f"{base_url}/api/mabang/store-options")
        template = _post_json(
            f"{base_url}/api/store-templates",
            {
                "store_name": "POD菲律宾",
                "mabang_store_id": "2022625115",
                "template_name": "POD PH",
                "product_line": "POD 男装T恤",
                "marketplace_category_path": "Men Clothes > Tops > T-shirts",
                "normal_price": 89,
                "sale_price": 69,
                "discount_label": "Default",
                "sizes": ["S", "M"],
            },
        )
    finally:
        server.shutdown()
        server.server_close()

    assert saved["saved_count"] == 2
    assert [item["store_name"] for item in options["items"]] == ["POD菲律宾", "TH本土Shopee-海宁TH"]
    assert options["items"][0]["mabang_store_id"] == "2022625115"
    assert template["store_name"] == "POD菲律宾"
    assert template["mabang_store_id"] == "2022625115"


def _create_listing_copy(tmp_path: Path, template_overrides: dict | None = None) -> dict:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    template_chart = folder / "size-chart.png"
    template_chart.write_bytes(b"image")
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    template = storage.create_store_template(
        {
            "store_name": "Shopee-MY-A",
            "platform": "shopee",
            "country": "MY",
            "currency": "MYR",
            "template_name": "Mens Tee",
            "product_line": "POD 男装T恤",
            "marketplace_category_path": "Men Clothes > Tops > T-shirts",
            "normal_price": 89,
            "sale_price": 69,
            "discount_label": "Default 7.7",
            "sizes": ["S", "M"],
            "default_size_chart_path": str(template_chart),
            "description_profile_version_id": "legacy-description-v1",
            "stock": 20,
            "weight_kg": "0.35",
            "package_length_cm": "30",
            "package_width_cm": "24",
            "package_height_cm": "3",
            "title_suffix": "Cotton T-Shirt Men",
            "description": "Mens tee description.",
            **(template_overrides or {}),
        }
    )
    # Default fixtures carry a complete base price table (cost per color × size)
    # so legacy tests keep the pre-price-table readiness semantics; tests that
    # exercise the cost-price gate create copies without this helper.
    if not template_overrides or "site_price_matrix" not in (template_overrides or {}):
        storage.update_base_product(base["id"], {"price_tiers": [{"id": "uniform", "sizes": [], "colors": [], "cost_price": 12.5}]})
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    return result["created"][0]


def _transaction_defaults() -> dict:
    return {
        "description_profile_version_id": "legacy-description-v1",
        "stock": 20,
        "weight_kg": "0.35",
        "package_length_cm": "30",
        "package_width_cm": "24",
        "package_height_cm": "3",
    }


def test_cnsc_listing_copy_keeps_price_snapshot_and_allows_per_sku_warehouse_override(tmp_path: Path) -> None:
    folder = tmp_path / "CNSC1001 全球卫衣"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    template = storage.create_store_template(
        {
            "store_name": "广州小苗服饰有限公司",
            "mabang_store_id": "cnsc-store",
            "template_name": "CNSC Hoodie",
            "listing_mode": "cnsc_global",
            "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
            "default_site_discount_percent": 40,
            "target_site_stores": [{
                "site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH",
                "mabang_discount_id": "discount-ph", "discount_name": "PH 40%", "discount_percent": 40,
            }],
            "sizes": ["S", "XL"],
            "sku_template": "{main_sku}_{color}||{size}",
            "price_tiers": [
                {"id": "standard", "sizes": ["S"], "global_price_cny": 19.2, "site_prices": {"PH": {"sale_price": 746, "original_price": 1243}}},
                {"id": "large", "sizes": ["XL"], "global_price_cny": 22.2, "site_prices": {"PH": {"sale_price": 811, "original_price": 1352}}},
            ],
            "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
            **_transaction_defaults(),
        }
    )
    listing_copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    detail = storage.get_store_listing_copy_detail(listing_copy["id"])
    assert detail is not None
    assert detail["default_site_discount_percent"] == 40
    assert detail["target_site_stores"][0]["mabang_discount_id"] == "discount-ph"
    # CNSC SKUs are color × size (base has no sizes → falls back to template S/XL).
    assert {sku["size"] for sku in detail["skus"]} == {"S", "XL"}
    assert {sku["color_code"] for sku in detail["skus"]} == {"White", "Black"}
    white = next(sku for sku in detail["skus"] if sku["color_code"] == "White" and sku["size"] == "S")
    # Base has no color tiers → template standard/large tiers are kept as-is and
    # the copy binds by size (S → standard, XL → large).
    assert white["sku_code"] == "CNSC1001_White||S"
    # 复制尊重模板档手填售价 746（不再按 %OFF 重算为 745.8）。
    assert white["site_price_matrix"]["PH"]["sale_price"] == 746.0
    assert white["warehouse_stocks"] == {"TH": 300, "CN_GZ": 0, "PH": 0}

    updated = storage.update_store_listing_copy(
        listing_copy["id"],
        {"skus": [{"id": white["id"], "warehouse_stocks": {"TH": 123, "CN_GZ": 4, "PH": 5}}]},
    )
    assert updated is not None
    changed = next(sku for sku in updated["skus"] if sku["id"] == white["id"])
    assert changed["warehouse_stocks"] == {"TH": 123, "CN_GZ": 4, "PH": 5}

    with pytest.raises(ValueError, match="仓库库存必须包含 TH、CN_GZ、PH"):
        storage.update_store_listing_copy(
            listing_copy["id"],
            {"skus": [{"id": white["id"], "warehouse_stocks": {"TH": 1, "PH": 1}}]},
        )

def test_listing_copy_snapshots_size_chart_source_and_two_watermark_roles(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "size_chart_source": "mabang_capture",
            "size_chart_asset_id": "chart-1",
            "main_image_watermark": {"template_id": "wm-main", "name": "主图水印"},
            "product_image_watermark": {"template_id": "wm-product", "name": "产品图水印"},
        },
    )

    assert listing_copy["size_chart_snapshot"]["source"] == "mabang_capture"
    assert listing_copy["size_chart_snapshot"]["asset_id"] == "chart-1"
    # 水印引用会规范化成 {template_id, name, mode, positions}（mode 默认 auto、
    # positions 默认空），这里只锁「副本按创建时的模板快照」这一契约。
    assert listing_copy["main_image_watermark"]["template_id"] == "wm-main"
    assert listing_copy["main_image_watermark"]["name"] == "主图水印"
    assert listing_copy["main_image_watermark"]["mode"] == "auto"
    assert listing_copy["product_image_watermark"]["template_id"] == "wm-product"
    assert listing_copy["product_image_watermark"]["name"] == "产品图水印"

    source_template = storage.get_item("store_templates", listing_copy["template_id"])
    assert source_template is not None
    storage.update_store_template(
        source_template["id"],
        {
            "main_image_watermark": {"template_id": "wm-new", "name": "新主图水印"},
            "size_chart_source": "upload",
        },
    )

    unchanged = storage.get_item("store_listing_copies", listing_copy["id"])
    assert unchanged["size_chart_snapshot"]["source"] == "mabang_capture"
    assert unchanged["main_image_watermark"]["template_id"] == "wm-main"


def test_listing_copy_snapshots_a_selected_mabang_size_chart_template(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "size_chart_source": "template",
            "mabang_size_chart_template_id": "106239228",
            "mabang_size_chart_template_name": "男装T恤",
        },
    )
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert listing_copy["size_chart_snapshot"]["template_id"] == "106239228"
    # 尺码图表选「选择模板」时表单页上没有可引用的尺码图地址，描述末尾要靠店铺模板里
    # 一并上传的那张本地图（2026-09-23）：模板带着 default_size_chart_path 时，副本与
    # 自动化参数都要把它带上，template_id 保留给页面上的「选择模板」用。
    assert listing_copy["size_chart_image_path"].endswith("size-chart.png")
    assert payload["browser_inputs"]["size_chart"] == {
        "source": "template",
        "template_id": "106239228",
        "template_name": "男装T恤",
        "image_path": listing_copy["size_chart_image_path"],
    }


def test_listing_copy_with_a_template_only_size_chart_has_no_local_image(tmp_path: Path) -> None:
    # 只选了马帮模板、没上传本地图（模板记录 metadata_only 的常见情况）：副本没有本地
    # 尺码图路径，描述末尾那张尺码表图降级为警告，不影响其余插图与保存。
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "size_chart_source": "template",
            "default_size_chart_path": "",
            "mabang_size_chart_template_id": "106239228",
            "mabang_size_chart_template_name": "男装T恤",
        },
    )

    assert listing_copy["size_chart_image"] == ""
    assert listing_copy["size_chart_image_path"] == ""
    assert listing_copy["mabang_size_chart_template_id"] == "106239228"


def _post_json(url: str, payload: dict) -> dict:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str) -> dict:
    with request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _put_json(url: str, payload: dict) -> dict:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_listing_copy_detail_and_atomic_maintenance_update(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "category-456",
            "mabang_discount_id": "promotion-789",
            "promotion_activity_id": "promotion-789",
        },
    )
    skus = storage.list_listing_skus(listing_copy["id"])
    prices = storage.list_listing_prices(listing_copy["id"])

    detail = storage.get_store_listing_copy_detail(listing_copy["id"])

    assert detail is not None
    assert detail["id"] == listing_copy["id"]
    assert len(detail["skus"]) == len(skus) == 4
    assert len(detail["prices"]) == len(prices) == 4
    assert detail["skus"][0]["price"]["sku_id"] == detail["skus"][0]["id"]
    assert detail["readiness"]["missing_fields"] == []
    assert detail["readiness"]["ready"] is True

    reordered_images = list(reversed(detail["product_images"]))
    target_sku = detail["skus"][0]
    target_price = target_sku["price"]
    updated = storage.update_store_listing_copy(
        listing_copy["id"],
        {
            "title": "人工检查后的标题",
            "description": "人工检查后的描述",
            "description_status": "ready",
            "description_profile_version_id": "description-v2",
            "promotion_activity_id": "promotion-999",
            "mabang_discount_id": "promotion-999",
            "discount_label": "九月店铺活动",
            "product_images": reordered_images,
            "main_image": reordered_images[0],
            "size_chart_asset_id": "chart-asset-2",
            "size_chart_preview_url": "/api/uploads/size-chart/chart-asset-2",
            "size_chart_image": "new-size-chart.png",
            "size_chart_image_path": "",
            "stock": 18,
            "weight_kg": 0.42,
            "package_length_cm": 25,
            "package_width_cm": 20,
            "package_height_cm": 2,
            "skus": [{"id": target_sku["id"], "stock": 7}],
            "prices": [
                {
                    "id": target_price["id"],
                    "sku_id": target_sku["id"],
                    "normal_price": 90,
                    "sale_price": 54,
                }
            ],
        },
    )

    assert updated is not None
    assert updated["listing_title"] == "人工检查后的标题"
    assert updated["product_images"] == reordered_images
    assert updated["main_image"] == reordered_images[0]
    assert updated["size_chart_preview_url"] == "/api/uploads/size-chart/chart-asset-2"
    assert updated["skus"][0]["stock"] == 7
    assert updated["skus"][0]["price"]["normal_price"] == 90.0
    assert updated["skus"][0]["price"]["sale_price"] == 54.0

    with pytest.raises(ValueError, match="SKU 不属于当前店铺刊登副本"):
        storage.update_store_listing_copy(
            listing_copy["id"],
            {
                "title": "不应写入的标题",
                "skus": [{"id": "foreign-sku", "stock": 1}],
            },
        )

    unchanged = storage.get_store_listing_copy_detail(listing_copy["id"])
    assert unchanged is not None
    assert unchanged["listing_title"] == "人工检查后的标题"


def test_description_profile_and_expired_promotion_are_non_blocking_warnings(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(
        tmp_path,
        {
            "mabang_store_id": "store-123",
            "mabang_category_id": "category-456",
            "mabang_discount_id": "promotion-expired",
            "promotion_activity_id": "promotion-expired",
        },
    )
    data = storage.load_store()
    data["store_promotion_activities"] = [{
        "id": "promotion-expired",
        "activity_id": "promotion-expired",
        "store_id": "store-123",
        "name": "已过期活动",
        "status": "expired",
        "end_at": "2020-01-01T00:00:00Z",
    }]
    storage.save_store(data)
    storage.update_store_listing_copy(
        listing_copy["id"],
        {
            "description_profile_version_id": "",
            "promotion_activity_id": "promotion-expired",
            "mabang_discount_id": "promotion-expired",
        },
    )

    detail = storage.get_store_listing_copy_detail(listing_copy["id"])
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])
    payload = storage.build_automation_payload(job["id"])

    assert detail is not None
    assert detail["readiness"]["ready"] is True
    # 副本已有描述文案：不再提醒"请选择描述生成方案"。fixture 的类目没有绑定
    # 类目方案档案，category_profile_missing 是 2026-09-28 起的固定非阻塞提醒。
    assert set(detail["readiness"]["warnings"]) == {"category_profile_missing", "promotion_expired"}
    assert detail["readiness"]["missing_fields"] == []
    assert job["status"] == "ready"
    assert job["missing_fields"] == []
    assert set(job["warnings"]) == set(detail["readiness"]["warnings"])
    assert payload["readiness"]["ready"] is True
    assert set(payload["readiness"]["warnings"]) == set(job["warnings"])

    # 描述被清空后，描述生成方案提醒恢复（提示运营去生成描述）。
    storage.update_store_listing_copy(
        listing_copy["id"],
        {"description": "", "description_status": ""},
    )
    cleared = storage.get_store_listing_copy_detail(listing_copy["id"])
    assert cleared is not None
    assert set(cleared["readiness"]["warnings"]) == {
        "category_profile_missing",
        "description_profile_version_id",
        "promotion_expired",
    }


def test_expired_category_profile_version_is_a_non_blocking_warning(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(tmp_path, {
        "mabang_store_id": "store-expired-test",
        "mabang_category_id": "category-expired-test",
        "mabang_discount_id": "promotion-expired-test",
        "promotion_activity_id": "promotion-expired-test",
    })
    data = storage.load_store()
    profile_id = "profile-expired-test"
    version_id = "version-expired-test"
    data.setdefault("category_profiles", []).append({
        "id": profile_id,
        "active_version_id": version_id,
        "published_version_id": version_id,
    })
    data.setdefault("category_profile_versions", []).append({
        "id": version_id,
        "profile_id": profile_id,
        "status": "active",
        "fields": [],
    })
    copy_row = next(item for item in data["store_listing_copies"] if item.get("id") == listing_copy["id"])
    copy_row["category_profile_id"] = profile_id
    copy_row["category_profile_version_id"] = version_id
    storage.save_store(data)
    listing_copy = storage.get_store_listing_copy_detail(listing_copy["id"])
    assert listing_copy is not None
    data = storage.load_store()
    profile = next(item for item in data["category_profiles"] if item.get("id") == profile_id)
    profile["active_version_id"] = "newer-category-version"
    storage.save_store(data)

    detail = storage.get_store_listing_copy_detail(listing_copy["id"])
    job = storage.create_automation_job_from_listing_copy(listing_copy["id"])

    assert detail is not None
    assert detail["readiness"]["ready"] is True
    assert detail["readiness"]["missing_fields"] == []
    assert "category_profile_version_expired" in detail["readiness"]["warnings"]
    assert job["status"] == "ready"
    assert "category_profile_version_expired" in job["warnings"]


def test_listing_prices_use_size_specific_original_prices_and_sixty_percent_sale_prices() -> None:
    prices = storage._generate_listing_prices(
        {"normal_price": 180, "sale_price": 108, "size_price_overrides": {"S": 90}},
        {"id": "copy-1", "base_product_id": "base-1"},
        [
            {"id": "sku-s", "sku_code": "TEE-S", "size": "S"},
            {"id": "sku-m", "sku_code": "TEE-M", "size": "M"},
        ],
    )

    by_sku = {item["sku_code"]: item for item in prices}
    assert (by_sku["TEE-S"]["normal_price"], by_sku["TEE-S"]["sale_price"]) == (90.0, 54.0)
    assert (by_sku["TEE-M"]["normal_price"], by_sku["TEE-M"]["sale_price"]) == (180.0, 108.0)


def test_regenerate_listing_endpoint_persists_error_without_ai_connection(tmp_path: Path) -> None:
    """Retry endpoint re-runs the vision enrichment; with no default AI connection it
    persists the failure on the product instead of raising, so the UI can show a
    retryable error."""
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg"]:
        (folder / name).write_bytes(b"image")

    server, base_url = _start_api()
    try:
        base_product = _post_json(
            f"{base_url}/api/base-products/from-folder",
            {"root_path": str(folder), "category": "POD Mens T-Shirt", "generate_title": False, "generate_description": False},
        )
        result = _post_json(
            f"{base_url}/api/base-products/{base_product['id']}/regenerate-listing",
            {"generate_title": True},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["id"] == base_product["id"]
    assert "AI" in result["ai_generation_error"]


def test_from_folder_import_reports_ai_stages_and_keeps_scanner_colors(tmp_path: Path) -> None:
    """Colors fully recognized from filenames skip the color AI stage entirely
    (ai_stages.colors == "skipped") and keep the scanner names; title/description
    report done only when they actually ran."""
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "SKU-01-Beige.jpg", "SKU-02-Black.jpg"]:
        (folder / name).write_bytes(b"image")

    server, base_url = _start_api()
    try:
        product = _post_json(
            f"{base_url}/api/base-products/from-folder",
            {"root_path": str(folder), "category": "POD Mens T-Shirt", "generate_title": False, "generate_description": False},
        )
    finally:
        server.shutdown()
        server.server_close()

    # SKU 分支的文件名规则返回首字母大写规范名（scanner._detect_english_color_option）。
    assert product["recognized_colors"] == ["Beige", "Black"]
    assert product["ai_stages"] == {"title": "skipped", "colors": "skipped", "audience": "skipped"}


def test_base_price_rows_flow_to_copies_with_site_trimming(tmp_path: Path) -> None:
    """Copy: cost_price always from base price tiers; CNSC copy site prices trimmed
    to the template's target sites (full-site template A vs PH-only template B)."""
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    # Base page stores one tier per color/款式 (color-bound); site prices already use
    # the copy-consumed keys (sale_price/original_price), trimmed per template below.
    colors = base.get("recognized_colors") or ["white", "black"]
    storage.update_base_product(base["id"], {"price_tiers": [
        {"id": color, "sizes": [], "colors": [color], "cost_price": 12.5,
         "site_prices": {"PH": {"sale_price": 420, "original_price": 696},
                          "TW": {"sale_price": 189, "original_price": 315}}}
        for color in colors
    ]})
    common = {
        "platform": "shopee",
        "product_line": "POD 男装T恤",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "mabang_store_id": "store-1",
        "mabang_category_id": "cat-1",
        "mabang_discount_id": "discount-1",
        "sizes": ["S"],
        "description": "desc",
        "warehouse_stock_defaults": {"TH": 10, "CN_GZ": 10, "PH": 10},
        "price_tiers": [{"id": "tier-std", "sizes": [], "colors": [], "global_price_cny": 30, "site_prices": {"PH": {"sale_price": 1, "original_price": 2}, "TW": {"sale_price": 1, "original_price": 2}}}],
        **_transaction_defaults(),
    }
    template_a = storage.create_store_template({
        "store_name": "Store A", "template_name": "Full site", "currency": "TWD",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store-1", "name": "CNSC A"},
        "target_site_stores": [
            {"site_code": "PH", "store_id": "s-ph", "store_name": "PH店"},
            {"site_code": "TW", "store_id": "s-tw", "store_name": "TW店"},
        ],
        **common,
    })
    template_b = storage.create_store_template({
        "store_name": "Store B", "template_name": "PH only", "currency": "PHP",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store-2", "name": "CNSC B"},
        "target_site_stores": [{"site_code": "PH", "store_id": "s-ph", "store_name": "PH店"}],
        "site_price_order": ["PH"],
        "price_tiers": [{"id": "tier-std", "sizes": [], "colors": [], "global_price_cny": 30, "site_prices": {
            "PH": {"sale_price": 1, "original_price": 2},
        }}],
        **common,
    })
    result = storage.copy_base_product_to_store_templates(base["id"], [template_a["id"], template_b["id"]])
    assert result["created_count"] == 2

    copies = {copy["template_id"]: copy for copy in result["created"]}
    prices_a = storage.list_listing_prices(copies[template_a["id"]]["id"])
    prices_b = storage.list_listing_prices(copies[template_b["id"]]["id"])
    price_a = prices_a[0]
    price_b = prices_b[0]

    assert price_a["cost_price"] == 12.5 and price_b["cost_price"] == 12.5
    assert price_a["price_source"] == "base_product"
    assert set(price_a["site_price_matrix"]) == {"PH", "TW"}
    # 复制尊重 base 颜色档手填售价 420（留空才按 %OFF 推导）。
    assert price_a["site_price_matrix"]["PH"] == {"original_price": 696.0, "sale_price": 420.0}
    # Template B only targets PH: TW price must be trimmed away.
    assert set(price_b["site_price_matrix"]) == {"PH"}
    assert price_b["site_price_matrix"]["PH"] == {"original_price": 696.0, "sale_price": 420.0}


def test_base_price_color_rows_api_roundtrip_and_cost_gate(tmp_path: Path) -> None:
    """Color-row tiers (one tier per color/款式) roundtrip; a tier without cost
    blocks automation readiness."""
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    tiers = [{
        "id": color, "sizes": [], "colors": [color], "cost_price": 12.5,
        "site_prices": {"PH": {"sale_price": 420, "original_price": 696}, "TW": {"sale_price": 189, "original_price": 315}},
    } for color in base.get("recognized_colors") or ["white", "black"]]
    updated = storage.update_base_product(base["id"], {"price_tiers": tiers})
    assert updated["price_tiers"][0]["cost_price"] == 12.5
    assert updated["price_tiers"][0]["site_prices"]["PH"] == {"sale_price": 420.0, "original_price": 696.0}

    template = storage.create_store_template({
        "store_name": "POD PH", "platform": "shopee", "country": "PH", "currency": "PHP",
        "template_name": "Mens Tee", "product_line": "POD 男装T恤",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "mabang_store_id": "s", "mabang_category_id": "c", "mabang_discount_id": "d",
        "sizes": ["S"], "description": "desc", **_transaction_defaults(),
        "listing_mode": "cnsc_global", "cnsc_store": {"id": "c1", "name": "C"},
        "target_site_stores": [{"site_code": "PH", "store_id": "s-ph", "store_name": "PH店"}],
        "warehouse_stock_defaults": {"TH": 1, "CN_GZ": 1, "PH": 1},
        "price_tiers": [{"id": "t", "sizes": [], "colors": [], "global_price_cny": 30,
                         "site_prices": {"PH": {"sale_price": 1, "original_price": 2}}}],
    })
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    prices = storage.list_listing_prices(result["created"][0]["id"])
    assert prices and prices[0]["cost_price"] == 12.5
    assert prices[0]["price_source"] == "base_product"
    assert all(p["cost_price"] == 12.5 for p in prices)

    # Cost gate: a copy whose price rows carry no cost blocks automation readiness.
    storage.update_base_product(base["id"], {"price_tiers": [{
        "id": color, "sizes": [], "colors": [color], "cost_price": "",
    } for color in base.get("recognized_colors") or ["white", "black"]]})
    no_cost_template = storage.create_store_template({
        "store_name": "POD TW", "platform": "shopee", "country": "TW", "currency": "TWD",
        "template_name": "No Cost", "product_line": "POD 男装T恤",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "mabang_store_id": "s", "mabang_category_id": "c", "mabang_discount_id": "d",
        "sizes": ["S"], "description": "desc", **_transaction_defaults(),
        "listing_mode": "cnsc_global", "cnsc_store": {"id": "c2", "name": "C2"},
        "target_site_stores": [{"site_code": "PH", "store_id": "s-ph", "store_name": "PH店"}],
        "warehouse_stock_defaults": {"TH": 1, "CN_GZ": 1, "PH": 1},
        "price_tiers": [{"id": "t", "sizes": [], "colors": [], "global_price_cny": 30,
                         "site_prices": {"PH": {"sale_price": 1, "original_price": 2}}}],
    })
    result2 = storage.copy_base_product_to_store_templates(base["id"], [no_cost_template["id"]])
    copy2_id = result2["created"][0]["id"]
    readiness = storage._check_automation_readiness(
        storage.get_item("store_listing_copies", copy2_id),
        skus=storage.list_listing_skus(copy2_id),
        prices=storage.list_listing_prices(copy2_id),
    )
    assert "base_product_cost_price" in readiness["missing_fields"]


def test_local_site_copy_without_cost_price_does_not_report_cost_gap(tmp_path: Path) -> None:
    """本土（非跨境 site）店铺的成本价走模板/导出兜底，不要求在基础商品价格表补
    成本价——readiness 不应报 base_product_cost_price。跨境(CNSC)仍要求。"""
    folder = tmp_path / "DL0002本土"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    # 基础商品颜色档不填成本价（本土不要求）
    storage.update_base_product(base["id"], {"price_tiers": [
        {"id": color, "sizes": [], "colors": [color], "cost_price": ""}
        for color in base.get("recognized_colors") or ["white", "black"]
    ]})

    local_template = storage.create_store_template({
        "store_name": "POD PH 本土", "platform": "shopee", "country": "PH", "currency": "PHP",
        "template_name": "Local Tee", "product_line": "POD 男装T恤",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "mabang_store_id": "s", "mabang_category_id": "c", "mabang_discount_id": "d",
        "sizes": ["S"], "description": "desc", "normal_price": 100, "sale_price": 60,
        **_transaction_defaults(),
    })
    result = storage.copy_base_product_to_store_templates(base["id"], [local_template["id"]])
    copy_id = result["created"][0]["id"]
    prices = storage.list_listing_prices(copy_id)
    assert prices and all(p["cost_price"] in ("", None) for p in prices)

    readiness = storage._check_automation_readiness(
        storage.get_item("store_listing_copies", copy_id),
        skus=storage.list_listing_skus(copy_id),
        prices=prices,
    )
    assert "base_product_cost_price" not in readiness["missing_fields"]


def test_cnsc_color_copy_payload_passes_executor_payload_requirements(tmp_path: Path) -> None:
    """A color-mode CNSC copy built from base rows that carry only original_price
    (v28+ base page) must produce a job payload the Playwright executor's
    validateCNSCTaskPayload accepts. Regression: the executor guard previously
    demanded per-tier global_price_cny and a hand-filled sale matching the %OFF on
    every site, blocking the exact payload this flow generates."""
    folder = tmp_path / "CNSC5001 颜色原价"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")
    (folder / "size-chart.png").write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD 男装T恤"}
    )
    colors = base.get("recognized_colors") or ["white", "black"]
    # Base color rows store original only — the sale is derived from the site %OFF
    # when the copy is generated (v28 semantics). Each color tier carries a RMB
    # cost_price which becomes the 全球商品价格 on the generated SKUs.
    storage.update_base_product(base["id"], {"price_tiers": [
        {"id": color, "sizes": [], "colors": [color], "cost_price": 12.5,
         "site_prices": {"PH": {"original_price": 696}}}
        for color in colors
    ]})
    template = storage.create_store_template({
        "store_name": "广州小苗服饰有限公司",
        "mabang_store_id": "cnsc-store",
        "template_name": "CNSC 颜色原价",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "mabang_category_id": "cat-1",
        "description": "描述文案已就绪",
        "default_size_chart_path": str(folder / "size-chart.png"),
        "sizes": ["M"],
        "default_site_discount_percent": 40,
        "target_site_stores": [
            {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH",
             "mabang_discount_id": "discount-ph", "discount_name": "PH 40%", "discount_percent": 40}
        ],
        "price_tiers": [{
            "id": "fallback", "sizes": [], "colors": [], "global_price_cny": 30,
            "site_prices": {"PH": {"original_price": 999}},
        }],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        **_transaction_defaults(),
    })
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    copy = result["created"][0]
    assert copy.get("cnsc_binding_mode") == "colors"
    # Color-tier copy tiers carry the color cost as 全球商品价格.
    for tier in copy["price_tiers"]:
        assert tier.get("global_price_cny") == 12.5
    # Color-tier SKUs: per-site matrix derived (sale = 696 × (1 - 40%) = 417.6);
    # 全球商品价格 = the color tier's RMB cost (the *全球商品价格 CNSC column is
    # required per row — verified on the live page 2026-09-04).
    skus = storage.list_listing_skus(copy["id"])
    assert skus and {sku["color_code"] for sku in skus} == set(colors)
    for sku in skus:
        assert sku["global_price_cny"] == 12.5
        assert sku["site_price_matrix"]["PH"] == {
            "original_price": 696.0,
            "sale_price": 417.6,
        }

    # Readiness (the Python shared CNSC source of truth) accepts the copy.
    copy_row = storage.get_item("store_listing_copies", copy["id"])
    prices = storage.list_listing_prices(copy["id"])
    readiness = storage._check_automation_readiness(copy_row, skus=skus, prices=prices)
    assert readiness["ready"] is True, readiness["missing_fields"]

    # The payload the executor receives carries the same color-tier shape.
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    payload = storage.build_automation_payload(job["id"])
    assert payload["ok"] and payload["readiness"]["ready"] is True
    browser_inputs = payload["browser_inputs"]
    assert browser_inputs["listing_mode"] == "cnsc_global"
    assert {tier.get("colors")[0] for tier in browser_inputs["price_tiers"]} == set(colors)
    for tier in browser_inputs["price_tiers"]:
        assert tier.get("sizes") == []
        assert tier.get("global_price_cny") == 12.5
        # The copy keeps the sale blank at the tier level (base page stored only
        # the original); the per-SKU matrix is what carries the derived sale.
        assert tier["site_prices"]["PH"]["original_price"] == 696.0
        assert tier["site_prices"]["PH"].get("sale_price") == ""
    for sku in payload["skus"]:
        assert sku["global_price_cny"] == 12.5
        assert sku["site_price_matrix"]["PH"]["original_price"] == 696.0
        assert sku["site_price_matrix"]["PH"]["sale_price"] == 417.6
    task_skus = browser_inputs.get("skus") or payload["skus"]
    for sku in task_skus:
        assert sku["warehouse_stocks"] == {"TH": 300, "CN_GZ": 0, "PH": 0}


def test_recognized_sizes_normalize_roundtrip(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    (folder / "主图.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    assert base.get("recognized_sizes") == []

    updated = storage.update_base_product(base["id"], {"recognized_sizes": "S, M, m, L, XL, S"})
    assert updated["recognized_sizes"] == ["S", "M", "L", "XL"]

    updated2 = storage.update_base_product(base["id"], {"recognized_sizes": ["均码", "均码", "160/84A"]})
    assert updated2["recognized_sizes"] == ["均码", "160/84A"]


def test_copy_skus_use_template_sizes_before_base_sizes(tmp_path: Path) -> None:
    """本土 site：复制到店铺模板后 SKU 按模板尺码生成（模板 chip 就是刊登尺码轴）。

    2026-10-26 起商品勾选/识别尺码只在模板没配尺码时兜底。此前商品的 AI 识别字母码
    （XXS–XXXL）会覆盖童装模板的 cm 码，现场 HN0083/HN0084 → Oct.3H 就是这个问题。
    """
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    # 识别 5 个尺码，只勾选 2 个
    storage.update_base_product(base["id"], {"recognized_sizes": ["S", "M", "L", "XL", "XXL"], "selected_sizes": ["S", "XL"]})
    template = storage.create_store_template({
        "store_name": "POD PH", "platform": "shopee", "country": "PH", "currency": "PHP",
        "template_name": "Mens Tee", "product_line": "POD 男装T恤",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "mabang_store_id": "s", "mabang_category_id": "c", "mabang_discount_id": "d",
        "sizes": ["M", "L"], "description": "desc", **_transaction_defaults(),
    })
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    skus = storage.list_listing_skus(result["created"][0]["id"])
    # 模板 M/L × 2 colors = 4 SKU（商品勾选的 S/XL 不再覆盖模板尺码）
    assert {sku["size"] for sku in skus} == {"M", "L"}
    assert len(skus) == 4


def test_copy_skus_fall_back_to_base_sizes_when_template_has_none(tmp_path: Path) -> None:
    """本土 site：模板没配尺码时仍按商品勾选 → 识别尺码生成 SKU。"""
    folder = tmp_path / "DL0002无模板尺码"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    storage.update_base_product(base["id"], {"recognized_sizes": ["S", "M", "L"], "selected_sizes": ["S", "L"]})
    template = storage.create_store_template({
        "store_name": "POD PH", "platform": "shopee", "country": "PH", "currency": "PHP",
        "template_name": "No Sizes Tee", "product_line": "POD 男装T恤",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "mabang_store_id": "s", "mabang_category_id": "c", "mabang_discount_id": "d",
        "sizes": [], "description": "desc", **_transaction_defaults(),
    })
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    skus = storage.list_listing_skus(result["created"][0]["id"])
    assert {sku["size"] for sku in skus} == {"S", "L"}


def test_cnsc_copy_skus_keep_base_sizes_before_template_sizes(tmp_path: Path) -> None:
    """跨境 CNSC 不走模板尺码优先：网格按行号填写，优先级保持商品尺码优先，
    跨境行为与改动前一致。"""
    folder = tmp_path / "CNSC4001字母码"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    storage.update_base_product(base["id"], {"recognized_sizes": ["S", "XL"]})
    template = storage.create_store_template({
        "store_name": "广州小苗服饰有限公司", "template_name": "CNSC 尺码源",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "sizes": ["M", "L"],
        "default_site_discount_percent": 40,
        "target_site_stores": [
            {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}
        ],
        "price_tiers": [
            {
                "id": "uniform", "sizes": [], "global_price_cny": 19.2,
                "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
            }
        ],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
    })
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert result["created"], result
    skus = storage.list_listing_skus(result["created"][0]["id"])
    assert {sku["size"] for sku in skus} == {"S", "XL"}


def test_from_folder_mixed_numeric_leading_and_main_filenames_do_not_crash(tmp_path: Path) -> None:
    """A folder mixing digit-leading names with alpha-leading main names used to
    raise TypeError ('<' not supported between instances of 'str' and 'int') in
    _product_image_sort_key. Ordering must never compare an int against a str."""
    folder = tmp_path / "AL0008混排"
    folder.mkdir()
    for name in ["01-main.jpg", "main-01.jpg", "主图-2.jpg", "2.jpg", "Main-10.jpg"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})

    assert product is not None
    # 主图挑选不再因 int/str 混排崩溃；main 标记相同后按文本前缀自然序取第一个。
    assert product["main_image"] == "main-01.jpg"
    # 所有 main/model 图都进了商品图列表
    assert set(product["product_images"]) == {"01-main.jpg", "main-01.jpg", "主图-2.jpg", "2.jpg", "Main-10.jpg"}
