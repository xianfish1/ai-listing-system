from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from services.api.model_gateway import GenerationImage, GenerationRequest
from services.api import main, settings, storage, vision_listing
from services.api.main import _color_translation_patch


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """标题生成链路现在会读取词库/规则并写生成记录（keyword_workbench.record_generation
    直接走 locked_store），必须隔离 STORE_FILE，否则测试会把记录写进真实 data/store.json。"""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


# 1×1 red PNG — small enough to embed inline for image-read tests.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63f80fff00000101010045fdf6d40000000049454e"
    "44ae426082"
)


@dataclass
class _Generated:
    text: str
    model_id: str = "test-model"


class _StubGateway:
    def __init__(self, text: str) -> None:
        self.text = text
        self.last_request: GenerationRequest | None = None
        # 颜色趟现在会给「模型漏掉的那几张」补发一次：断言首发内容看 requests[0]。
        self.requests: list[GenerationRequest] = []

    def generate(self, connection: Any, request: GenerationRequest) -> _Generated:
        self.last_request = request
        self.requests.append(request)
        return _Generated(text=self.text)


class _FailingGateway:
    def generate(self, connection: Any, request: GenerationRequest) -> _Generated:
        raise RuntimeError("boom")


def _write_image(tmp_path: Path, name: str = "main.png") -> Path:
    p = tmp_path / name
    p.write_bytes(TINY_PNG)
    return p


def test_selected_flags_reads_runtime_configuration() -> None:
    assert vision_listing.selected_flags(None) == (False, False)
    assert vision_listing.selected_flags({}) == (False, False)
    assert vision_listing.selected_flags({"ai_listing_generation": {}}) == (False, False)
    assert vision_listing.selected_flags(
        {"ai_listing_generation": {"generate_title_on_import": True}}
    ) == (True, False)
    assert vision_listing.selected_flags(
        {"ai_listing_generation": {"generate_title_on_import": True, "generate_description_on_import": True}}
    ) == (True, True)


def test_fill_attributes_flag_defaults_on_and_can_be_disabled() -> None:
    assert vision_listing.fill_attributes_flag(None) is True
    assert vision_listing.fill_attributes_flag({}) is True
    assert vision_listing.fill_attributes_flag({"ai_listing_generation": {}}) is True
    assert vision_listing.fill_attributes_flag(
        {"ai_listing_generation": {"fill_attributes_into_description": False}}
    ) is False
    assert vision_listing.fill_attributes_flag(
        {"ai_listing_generation": {"fill_attributes_into_description": True}}
    ) is True


def test_format_attributes_block_matches_erp_style() -> None:
    block = vision_listing.format_attributes_block(
        {"Applicable Season": "Four Seasons", "Material": "Polyester", "Item Type": "Hoodies"}
    )
    assert block == (
        "Applicable Season : Four Seasons\n\n"
        "Material : Polyester\n\n"
        "Item Type : Hoodies"
    )


def test_attributes_replace_description_prose(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    gateway = _StubGateway(
        '{"title": "Casual Pullover Hoodie", "description": "Soft polyester hoodie for daily wear.",'
        ' "attributes": {"Applicable Season": "Four Seasons", "Applicable Scene": "Daily",'
        ' "Detachable Part": "NONE", "Material": "Polyester", "Style": "Casual",'
        ' "Sleeve Length(cm)": "Full", "Item Type": "Hoodies"}}'
    )
    product = {"main_image": "main.png", "product_images": ["main.png"], "id": "bp1"}

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c", "model_id": "claude"},
        base_product=product, assets=assets,
        want_title=True, want_description=True,
    )

    assert result.ok
    assert result.title == "Casual Pullover Hoodie"
    assert result.attributes is not None
    assert result.attributes["Item Type"] == "Hoodies"
    assert result.attributes["Sleeve Length(cm)"] == "Full"
    assert result.description == (
        "Applicable Season : Four Seasons\n\n"
        "Applicable Scene : Daily\n\n"
        "Detachable Part : NONE\n\n"
        "Material : Polyester\n\n"
        "Style : Casual\n\n"
        "Sleeve Length(cm) : Full\n\n"
        "Item Type : Hoodies"
    )
    assert "Soft polyester hoodie for daily wear." not in result.description


def test_color_translations_are_returned_for_filename_color_hints(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "SKU-奶油白.jpg", "file_path": str(path)}]
    gateway = _StubGateway(
        '{"title": "Hoodie", "description": "D", "attributes": {},'
        ' "color_translations": {"奶油白": "Cream White"}}'
    )
    product = {"main_image": "SKU-奶油白.jpg", "product_images": ["SKU-奶油白.jpg"], "recognized_colors": ["奶油白"]}

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=False, want_description=True,
    )

    assert result.ok
    # 自造色名 "Cream White" 归到固定色板里的 White。
    assert result.color_translations == {"奶油白": "White"}


def test_snap_color_to_palette_maps_only_known_colors() -> None:
    """AI 颜色名归到 9 个固定色板：色板名、同义色、修饰前缀/多词写法都能认；
    套装标签和色板外的颜色（灰/黄/卡其）保持原样，不能被悄悄改成别的颜色。"""
    assert vision_listing.FIXED_PRODUCT_COLORS == (
        "Apricot", "Purple", "Blue", "Red", "Black", "White", "Pink", "Brown", "Green",
    )
    assert vision_listing.snap_color_to_palette("purple") == "Purple"
    assert vision_listing.snap_color_to_palette("Light Purple") == "Purple"
    assert vision_listing.snap_color_to_palette("darkgreen") == "Green"
    assert vision_listing.snap_color_to_palette("Cream White") == "White"
    assert vision_listing.snap_color_to_palette("navy") == "Blue"
    assert vision_listing.snap_color_to_palette("rose gold") == "Pink"
    assert vision_listing.snap_color_to_palette("Costume + Wig") == ""
    assert vision_listing.snap_color_to_palette("Gray") == ""
    assert vision_listing.snap_color_to_palette("") == ""


def test_color_translation_normalization_keeps_set_labels_and_off_palette_colors() -> None:
    translations = vision_listing._normalize_color_translations(
        {
            "SKU-01-wig only.png": "Wig",
            "SKU-02-dress only.png": "Costume",
            "SKU-03-dress wig.png": "Costume + Wig",
            "SKU-04-gray.png": "Gray",
            "SKU-05-blue.png": "Dark Blue",
        }
    )
    assert translations == {
        "SKU-01-wig only.png": "Wig",
        "SKU-02-dress only.png": "Costume",
        "SKU-03-dress wig.png": "Costume + Wig",
        "SKU-04-gray.png": "Gray",
        "SKU-05-blue.png": "Blue",
    }


def test_color_pass_prompt_lists_the_fixed_palette(tmp_path: Path) -> None:
    """颜色那一趟的提示词把固定色板直接给模型；标题/详情那一趟不带色板，
    免得标题被拽去写颜色。"""
    variant = _write_image(tmp_path, "1 (1).png")
    assets = [{"file_name": variant.name, "file_path": str(variant), "asset_type": "variant"}]
    product = {"main_image": variant.name, "recognized_colors": [], "variant_count": 1}

    color_gateway = _StubGateway('{"title": "", "description": "", "color_translations": {}}')
    vision_listing.generate_listing_from_images(
        gateway=color_gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=False, want_description=False,
    )
    assert color_gateway.last_request is not None
    assert "Fixed color palette" in color_gateway.last_request.user
    for color in vision_listing.FIXED_PRODUCT_COLORS:
        assert color in color_gateway.last_request.user
    assert "use ONLY these fixed color names" in color_gateway.last_request.system
    for color in vision_listing.FIXED_PRODUCT_COLORS:
        assert color in color_gateway.last_request.system

    title_gateway = _StubGateway('{"title": "T", "description": "D", "attributes": {}}')
    named_assets = [
        {
            "file_name": variant.name,
            "file_path": str(variant),
            "asset_type": "variant",
            "sku_option": "Pink",
        }
    ]
    title_product = {"main_image": variant.name, "recognized_colors": ["Pink"], "variant_count": 1}
    vision_listing.generate_listing_from_images(
        gateway=title_gateway, connection={"id": "c"}, base_product=title_product,
        assets=named_assets, want_title=True, want_description=False,
    )
    assert title_gateway.last_request is not None
    assert "Fixed color palette" not in title_gateway.last_request.user


def test_numeric_sku_variants_are_sent_to_vision_for_color_naming(tmp_path: Path) -> None:
    main = _write_image(tmp_path, "Main-front.jpg")
    variant_one = _write_image(tmp_path, "SKU-01-dress only.jpg")
    variant_two = _write_image(tmp_path, "SKU-02-dress only.jpg")
    assets = [
        {"file_name": main.name, "file_path": str(main), "asset_type": "main"},
        {"file_name": variant_one.name, "file_path": str(variant_one), "asset_type": "variant"},
        {"file_name": variant_two.name, "file_path": str(variant_two), "asset_type": "variant"},
    ]
    gateway = _StubGateway('{"title": "Dress", "description": "D", "attributes": {}}')

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"},
        base_product={"main_image": main.name, "recognized_colors": [], "variant_count": 2},
        assets=assets, want_title=False, want_description=True,
    )

    assert result.ok
    assert result.input_image_count == 3
    assert gateway.last_request is not None
    assert len(gateway.last_request.images) == 3


def test_title_request_is_not_swallowed_by_unnamed_variant_images(tmp_path: Path) -> None:
    """未命名的数字 SKU 图不能把「要标题」的调用自动切成纯颜色趟。

    2026-11-02 修：基础商品维护的 AI 生成标题曾因此拿到空 titles（error 也为空）。
    """
    main = _write_image(tmp_path, "Main-front.jpg")
    variant = _write_image(tmp_path, "1 (1).png")
    assets = [
        {"file_name": main.name, "file_path": str(main), "asset_type": "main"},
        {"file_name": variant.name, "file_path": str(variant), "asset_type": "variant"},
    ]
    gateway = _StubGateway('{"title": "Boys Graphic Tee", "description": "D", "alternatives": ["Boys Cartoon Tee"]}')

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"},
        base_product={"main_image": main.name, "recognized_colors": [], "variant_count": 1},
        assets=assets, want_title=True, want_description=False, candidate_count=2,
    )

    assert result.ok
    assert result.title_candidates
    assert result.title_candidates[0]["title"] == "Boys Graphic Tee"
    assert gateway.last_request is not None
    assert "Fixed color palette" not in gateway.last_request.system


def test_color_translation_patch_keeps_variant_image_mapping() -> None:
    patch = _color_translation_patch(
        {"recognized_colors": ["red", "奶油白"], "variant_image_map": {"red": "SKU-red.jpg", "奶油白": "SKU-奶油白.jpg"}},
        {"奶油白": "Cream White"},
    )
    assert patch == {
        "recognized_colors": ["red", "Cream White"],
        "variant_image_map": {"red": "SKU-red.jpg", "Cream White": "SKU-奶油白.jpg"},
    }


def test_variant_set_labels_are_ordered_costume_wig_set() -> None:
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {
            "SKU-03-dress wig.png": "Costume + Wig",
            "SKU-01-wig only.png": "Wig",
            "SKU-02-dress only.png": "Costume",
        },
        assets=[
            {"file_name": "SKU-01-wig only.png", "asset_type": "variant"},
            {"file_name": "SKU-02-dress only.png", "asset_type": "variant"},
            {"file_name": "SKU-03-dress wig.png", "asset_type": "variant"},
        ],
    )

    assert patch == {
        "recognized_colors": ["Costume", "Wig", "Costume + Wig"],
        "variant_image_map": {
            "Costume": "SKU-02-dress only.png",
            "Wig": "SKU-01-wig only.png",
            "Costume + Wig": "SKU-03-dress wig.png",
        },
    }


def test_variant_set_labels_kept_when_only_subset_present() -> None:
    """Sometimes only the wig and costume exist (no full set), or only the set."""
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {"SKU-01-dress only.png": "Costume", "SKU-02-wig only.png": "Wig"},
        assets=[
            {"file_name": "SKU-01-dress only.png", "asset_type": "variant"},
            {"file_name": "SKU-02-wig only.png", "asset_type": "variant"},
        ],
    )
    assert patch["recognized_colors"] == ["Costume", "Wig"]

    patch_full_only = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {"SKU-01-dress wig.png": "Costume + Wig"},
        assets=[{"file_name": "SKU-01-dress wig.png", "asset_type": "variant"}],
    )
    assert patch_full_only["recognized_colors"] == ["Costume + Wig"]


def test_color_labels_are_not_reordering_real_colors() -> None:
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {"SKU-01-red.png": "Red", "SKU-02-black.png": "Black"},
        assets=[
            {"file_name": "SKU-01-red.png", "asset_type": "variant"},
            {"file_name": "SKU-02-black.png", "asset_type": "variant"},
        ],
    )
    assert patch["recognized_colors"] == ["Red", "Black"]


def test_color_translation_patch_can_name_numeric_sku_images() -> None:
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {"01": "Black", "SKU-02-dress only.jpg": "Cream White"},
        assets=[
            {"file_name": "SKU-01-dress only.jpg", "asset_type": "variant"},
            {"file_name": "SKU-02-dress only.jpg", "asset_type": "variant"},
        ],
    )

    assert patch == {
        "recognized_colors": ["Black", "Cream White"],
        "variant_image_map": {
            "Black": "SKU-01-dress only.jpg",
            "Cream White": "SKU-02-dress only.jpg",
        },
    }


def test_numeric_variant_files_are_ordered_naturally_for_hints_and_images(tmp_path: Path) -> None:
    """文件名带序号的 SKU 图必须按自然序（1 (2) 在 1 (10) 前）发给模型。

    字典序会把 ``1 (10).png`` 排到第二位，模型却按数字读文件名，于是提示里的第 N 个
    文件名配到第 N 张其实不同的图上——整批颜色图错位一格（AL0508「红色配到别的颜色」）。
    """
    names = ["1 (1).png", "1 (2).png", "1 (10).png"]
    assets = []
    for name in names:
        path = _write_image(tmp_path, name)
        assets.append({"file_name": name, "file_path": str(path), "asset_type": "variant"})
    # 模型按提示里给出的文件名逐张回答（这是我们期望它做的）。
    translations = {"1 (1).png": "DarkGreen", "1 (2).png": "Brown", "1 (10).png": "LightBlue"}
    gateway = _StubGateway(
        '{"title": "", "description": "", "color_translations": '
        + json.dumps(translations)
        + "}"
    )
    product = {
        "main_image": "1 (1).png",
        "recognized_colors": [],
        "variant_image_map": {},
        "variant_count": 3,
    }

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=False, want_description=False,
    )

    assert result.ok
    assert gateway.last_request is not None
    # 图片顺序 = 自然序，并且提示里逐张列了编号，模型不用猜。
    assert [image.data_base64 for image in gateway.last_request.images] == [
        base64.b64encode((tmp_path / name).read_bytes()).decode("ascii") for name in names
    ]
    assert "#1 1 (1).png" in gateway.last_request.user
    assert "#3 1 (10).png" in gateway.last_request.user

    patch = _color_translation_patch(product, result.color_translations or {}, assets=assets)
    # 模型回的自造色名（DarkGreen/LightBlue）先归到固定色板，再按文件名绑定。
    assert result.color_translations == {
        "1 (1).png": "Green",
        "1 (2).png": "Brown",
        "1 (10).png": "Blue",
    }
    assert patch["variant_image_map"] == {
        "Green": "1 (1).png",
        "Brown": "1 (2).png",
        "Blue": "1 (10).png",
    }


def test_composite_color_names_keep_their_image_binding() -> None:
    """模型把复合色写成「Light Purple」时，颜色列表与颜色图映射要落到同一个键上。

    之前颜色列表会被归一化成 LightPurple，而映射键还是 "Light Purple"，
    ``_variant_image_map_for_colors`` 认不出是同一个颜色就把整条映射丢掉，
    结果颜色在、图没了（AL0507）。
    """
    assets = [
        {"file_name": "1 (1).png", "asset_type": "variant", "sku_option": ""},
        {"file_name": "1 (2).png", "asset_type": "variant", "sku_option": ""},
        {"file_name": "1 (3).png", "asset_type": "variant", "sku_option": ""},
    ]
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {"1 (1).png": "Dark Green", "1 (2).png": "Light Purple", "1 (3).png": "Red"},
        assets=assets,
    )

    assert patch["recognized_colors"] == ["DarkGreen", "LightPurple", "Red"]
    assert patch["variant_image_map"] == {
        "DarkGreen": "1 (1).png",
        "LightPurple": "1 (2).png",
        "Red": "1 (3).png",
    }

    # 保存链路（update_base_product）会再归一化一次，映射不能在这时被丢掉。
    colors = storage._normalize_recognized_colors(patch["recognized_colors"])
    image_map = storage._variant_image_map_for_colors(patch["variant_image_map"], colors)
    assert colors == ["DarkGreen", "LightPurple", "Red"]
    assert image_map == patch["variant_image_map"]


def test_variant_image_map_matches_color_labels_ignoring_spacing() -> None:
    """历史数据里 "Light Purple" 与归一化后的 "LightPurple" 也要认得是同一个颜色。"""
    image_map = storage._variant_image_map_for_colors(
        {"Light Purple": "1 (2).png", "Dark Green": "1 (1).png"},
        ["LightPurple", "DarkGreen"],
    )
    assert image_map == {"LightPurple": "1 (2).png", "DarkGreen": "1 (1).png"}


def test_bare_numbers_map_by_image_position_not_by_shared_prefix() -> None:
    """``1 (3).png`` 这类文件名里前导的 "1" 每个文件都有，不能拿来当匹配键。

    模型若按图片编号回答（#1/#2），要落到自然序的第 N 张图上。"""
    assets = [
        {"file_name": "1 (1).png", "asset_type": "variant", "sku_option": ""},
        {"file_name": "1 (2).png", "asset_type": "variant", "sku_option": ""},
        {"file_name": "1 (10).png", "asset_type": "variant", "sku_option": ""},
    ]
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {"1": "DarkGreen", "2": "Brown", "3": "LightBlue"},
        assets=assets,
    )

    assert patch == {
        "recognized_colors": ["DarkGreen", "Brown", "LightBlue"],
        "variant_image_map": {
            "DarkGreen": "1 (1).png",
            "Brown": "1 (2).png",
            "LightBlue": "1 (10).png",
        },
    }


def test_retry_replaces_stale_color_image_bindings() -> None:
    """重试识别时，上次留下的错误绑定要让位给这次识别出来的颜色，否则永远修不好。"""
    assets = [
        {"file_name": "1 (1).png", "asset_type": "variant", "sku_option": ""},
        {"file_name": "1 (2).png", "asset_type": "variant", "sku_option": ""},
    ]
    stale = {
        "recognized_colors": ["Brown", "White"],
        # 上次的错误绑定：Brown 配到了第二张图（本该是第一张）
        "variant_image_map": {"Brown": "1 (2).png", "White": "1 (1).png"},
    }
    patch = _color_translation_patch(
        stale,
        {"1 (1).png": "Brown", "1 (2).png": "White"},
        assets=assets,
    )

    assert patch["variant_image_map"] == {"Brown": "1 (1).png", "White": "1 (2).png"}
    assert patch["recognized_colors"] == ["Brown", "White"]


def test_import_colors_only_use_this_imports_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个 SKU 目录重新导入时，上一次导入留下的素材不能混进这次的颜色识别：
    发给模型的图片和写回的颜色图都必须来自本次导入目录。"""
    old_dir = tmp_path / "old-import"
    new_dir = tmp_path / "new-import"
    old_dir.mkdir()
    new_dir.mkdir()
    old_main = _write_image(old_dir, "服装布面颜色更换.png")
    old_variant = _write_image(old_dir, "服装布面颜色更换 (1).png")
    new_main = _write_image(new_dir, "1 (1).png")
    new_variant = _write_image(new_dir, "1 (2).png")
    assets = [
        {"file_name": old_main.name, "file_path": str(old_main), "asset_type": "main"},
        {"file_name": old_variant.name, "file_path": str(old_variant), "asset_type": "variant"},
        {"file_name": new_main.name, "file_path": str(new_main), "asset_type": "variant"},
        {"file_name": new_variant.name, "file_path": str(new_variant), "asset_type": "variant"},
    ]
    product = {
        "id": "bp-reimport",
        "group_id": "AL0472",
        "asset_root": str(new_dir),
        "main_image": new_main.name,
        "recognized_colors": [],
        "variant_image_map": {},
        "variant_count": 2,
    }
    gateway = _StubGateway('{"title": "", "description": "", "color_translations": {"1": "Black"}}')

    monkeypatch.setattr(storage, "get_runtime_configuration", lambda: {"ai_listing_generation": {}})
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", lambda: {"id": "conn"})
    monkeypatch.setattr(storage, "assets_for_group", lambda _group_id: assets)
    monkeypatch.setattr(storage, "update_base_product", lambda _id, patch, **_kwargs: {**product, **patch})
    monkeypatch.setattr(main, "ModelGateway", lambda **_kwargs: gateway)

    result = main._enrich_with_vision_listing(product, {})

    assert gateway.requests, "颜色趟必须至少发一次"
    first = gateway.requests[0]
    assert [image.media_type for image in first.images] == ["image/png", "image/png"]
    assert [image.data_base64 for image in first.images] == [
        base64.b64encode(new_main.read_bytes()).decode("ascii"),
        base64.b64encode(new_variant.read_bytes()).decode("ascii"),
    ]
    # 补发只针对本次导入里没命名的图，旧的导入目录仍然不能混进来。
    assert all(len(request.images) <= 2 for request in gateway.requests)
    assert result["variant_image_map"]
    assert old_variant.name not in result["variant_image_map"].values()
    assert set(result["variant_image_map"].values()) <= {new_main.name, new_variant.name}


def test_import_names_numeric_color_images_even_when_listing_generation_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Color recognition must not depend on title/description generation toggles."""
    main_image = _write_image(tmp_path, "Main-front.png")
    variant_paths = [
        _write_image(tmp_path, "SKU-01-wig only.png"),
        _write_image(tmp_path, "SKU-02-dress only.png"),
        _write_image(tmp_path, "SKU-03-dress wig.png"),
    ]
    assets = [
        {"file_name": main_image.name, "file_path": str(main_image), "asset_type": "main"},
        *[
            {"file_name": path.name, "file_path": str(path), "asset_type": "variant"}
            for path in variant_paths
        ],
    ]
    product = {
        "id": "bp-numeric-colors",
        "group_id": "C4E10A25",
        "main_image": main_image.name,
        "recognized_colors": [],
        "variant_image_map": {},
        "variant_count": 3,
    }
    gateway = _StubGateway(
        '{"title": "", "description": "", "attributes": {"Material": "Polyester"}, '
        '"color_translations": {"01": "Pink", "02": "Purple", "03": "Green"}}'
    )

    monkeypatch.setattr(storage, "get_runtime_configuration", lambda: {"ai_listing_generation": {}})
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", lambda: {"id": "conn"})
    monkeypatch.setattr(storage, "assets_for_group", lambda _group_id: assets)
    monkeypatch.setattr(storage, "update_base_product", lambda _id, patch, **_kwargs: {**product, **patch})
    monkeypatch.setattr(main, "ModelGateway", lambda **_kwargs: gateway)

    result = main._enrich_with_vision_listing(product, {})

    assert gateway.last_request is not None
    # 颜色那一趟只发颜色图：主图不是颜色图，混进来会顶掉一张改色图、还会让 #N
    # 编号与写回下标错开一格。
    assert len(gateway.last_request.images) == 3
    assert result["recognized_colors"] == ["Pink", "Purple", "Green"]
    assert result["variant_image_map"] == {
        "Pink": "SKU-01-wig only.png",
        "Purple": "SKU-02-dress only.png",
        "Green": "SKU-03-dress wig.png",
    }
    assert "recognized_attributes" not in result


def test_import_persists_ai_generation_error_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AI failure must survive a page refresh instead of only flashing once."""
    path = _write_image(tmp_path)
    assets = [{"file_name": path.name, "file_path": str(path), "asset_type": "main"}]
    product = {"id": "bp-error", "group_id": "C4E10A26", "main_image": path.name}
    gateway = _FailingGateway()

    saved: list[tuple[str, dict]] = []

    def fake_update(product_id: str, patch: dict, **kwargs) -> dict:
        saved.append((product_id, patch))
        return {**product, **patch}

    monkeypatch.setattr(storage, "get_runtime_configuration", lambda: {"ai_listing_generation": {"generate_title_on_import": True}})
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", lambda: {"id": "conn"})
    monkeypatch.setattr(storage, "assets_for_group", lambda _group_id: assets)
    monkeypatch.setattr(storage, "update_base_product", fake_update)
    monkeypatch.setattr(main, "ModelGateway", lambda **_kwargs: gateway)

    result = main._enrich_with_vision_listing(product, {})

    assert "AI 调用失败" in result["ai_generation_error"]
    assert saved == [("bp-error", {"ai_generation_error": result["ai_generation_error"]})]


def test_import_clears_ai_generation_error_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": path.name, "file_path": str(path), "asset_type": "main"}]
    product = {
        "id": "bp-recovered",
        "group_id": "C4E10A27",
        "main_image": path.name,
        "ai_generation_error": "AI 调用失败: AI 服务没有返回文本内容",
    }
    gateway = _StubGateway('{"title": "T", "description": "D"}')

    saved: list[tuple[str, dict]] = []
    calls: list[dict] = []

    def fake_update(product_id: str, patch: dict, **kwargs) -> dict:
        saved.append((product_id, patch))
        calls.append(kwargs)
        return {**product, **patch}

    monkeypatch.setattr(storage, "get_runtime_configuration", lambda: {"ai_listing_generation": {"generate_title_on_import": True}})
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", lambda: {"id": "conn"})
    monkeypatch.setattr(storage, "assets_for_group", lambda _group_id: assets)
    monkeypatch.setattr(storage, "update_base_product", fake_update)
    monkeypatch.setattr(main, "ModelGateway", lambda **_kwargs: gateway)

    result = main._enrich_with_vision_listing(product, {})

    assert result["listing_title"] == "T"
    assert result["ai_generation_error"] == ""
    assert saved and saved[0][1]["ai_generation_error"] == ""
    # 导入时的 AI 识别不算人工编辑：不打「编辑过」标记（商品维护的四段分类要用）。
    assert calls[0].get("mark_edited") is not True


def test_attributes_fill_disabled_keeps_description_plain(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    gateway = _StubGateway(
        '{"title": "T", "description": "D", "attributes": {"Material": "Polyester"}}'
    )
    product = {"main_image": "main.png", "id": "bp"}

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=True, want_description=True,
        fill_attributes_into_description=False,
    )
    assert result.ok
    assert result.description == "D"
    # Recognition still happens — only the description injection is skipped.
    assert result.attributes == {"Material": "Polyester"}


def test_attributes_fill_description_when_prose_is_empty(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    gateway = _StubGateway(
        '{"title": "Hoodie", "description": "", "attributes": {"Material": "Cotton", "Item Type": "Hoodies"}}'
    )
    product = {"main_image": "main.png", "id": "bp"}

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=False, want_description=True,
    )
    assert result.ok
    assert result.title == ""
    assert result.description == "Material : Cotton\n\nItem Type : Hoodies"


def test_malformed_attributes_are_dropped(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    gateway = _StubGateway(
        '{"title": "T", "description": "D", "attributes": ["not", "a dict"], "extra": true}'
    )
    product = {"main_image": "main.png", "id": "bp"}

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=True, want_description=True,
    )
    assert result.ok
    assert result.attributes == {}
    assert result.description == "D"


def test_returns_error_when_both_flags_off() -> None:
    result = vision_listing.generate_listing_from_images(
        gateway=_StubGateway(""), connection={"id": "c"}, base_product={}, assets=[],
        want_title=False, want_description=False,
    )
    assert not result.ok
    assert "开关未启用" in result.error


def test_returns_error_when_no_connection() -> None:
    result = vision_listing.generate_listing_from_images(
        gateway=_StubGateway(""), connection=None, base_product={}, assets=[],
        want_title=True, want_description=True,
    )
    assert not result.ok
    assert "没有默认" in result.error


def test_returns_error_when_no_images(tmp_path: Path) -> None:
    result = vision_listing.generate_listing_from_images(
        gateway=_StubGateway(""), connection={"id": "c"},
        base_product={"main_image": "nonexistent.png"}, assets=[],
        want_title=True, want_description=True,
    )
    assert not result.ok
    assert "没有可用" in result.error


def test_success_path_parses_json_and_returns_title_and_description(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    gateway = _StubGateway('{"title": "Vintage Tee", "description": "Soft cotton tee for daily wear."}')
    product = {"main_image": "main.png", "product_images": ["main.png"], "id": "bp1"}

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c", "model_id": "claude"},
        base_product=product, assets=assets,
        want_title=True, want_description=True,
    )

    assert result.ok
    assert result.title == "Vintage Tee"
    assert result.description == "Soft cotton tee for daily wear."
    assert result.input_image_count == 1
    # Vision request should carry a GenerationImage
    assert gateway.last_request is not None
    assert len(gateway.last_request.images) == 1
    assert gateway.last_request.images[0].media_type == "image/png"


def test_json_fenced_output_still_parses(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    gateway = _StubGateway('```json\n{"title":"A","description":"B"}\n```')
    product = {"main_image": "main.png", "id": "bp"}

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=True, want_description=True,
    )
    assert result.ok
    assert result.title == "A"
    assert result.description == "B"


def test_want_flags_suppress_output(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    gateway = _StubGateway('{"title": "T", "description": "D"}')
    product = {"main_image": "main.png", "id": "bp"}

    only_title = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=True, want_description=False,
    )
    assert only_title.ok
    assert only_title.title == "T"
    assert only_title.description == ""

    only_desc = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=False, want_description=True,
    )
    assert only_desc.ok
    assert only_desc.title == ""
    assert only_desc.description == "D"


def test_gateway_failure_is_captured_not_raised(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    product = {"main_image": "main.png", "id": "bp"}

    result = vision_listing.generate_listing_from_images(
        gateway=_FailingGateway(), connection={"id": "c", "model_id": "m"},
        base_product=product, assets=assets,
        want_title=True, want_description=True,
    )
    assert not result.ok
    assert "AI 调用失败" in result.error


def test_hints_are_folded_into_prompt(tmp_path: Path) -> None:
    path = _write_image(tmp_path)
    assets = [{"file_name": "main.png", "file_path": str(path)}]
    gateway = _StubGateway('{"title": "T", "description": "D"}')
    product = {"main_image": "main.png", "id": "bp", "category": "T-Shirt"}

    vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        attribute_hints={"来源": "动漫", "性别": "女"},
        want_title=True, want_description=True,
    )
    assert gateway.last_request is not None
    prompt = gateway.last_request.user
    assert "来源: 动漫" in prompt
    assert "性别: 女" in prompt
    assert "Category: T-Shirt" in prompt


def test_multiple_images_capped_at_max(tmp_path: Path) -> None:
    assets = []
    for i in range(6):
        p = _write_image(tmp_path, f"img{i}.png")
        assets.append({"file_name": f"img{i}.png", "file_path": str(p)})
    gateway = _StubGateway('{"title": "T", "description": "D"}')
    product = {
        "main_image": "img0.png",
        "variant_image_map": {"red": "img1.png", "blue": "img2.png", "green": "img3.png"},
        "product_images": ["img4.png", "img5.png"],
        "id": "bp",
    }

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=True, want_description=True,
    )
    assert result.ok
    assert result.input_image_count == vision_listing.MAX_IMAGES
    assert gateway.last_request is not None
    assert len(gateway.last_request.images) == vision_listing.MAX_IMAGES


def test_scanner_english_colors_are_never_renamed_by_ai() -> None:
    """File name scanner colors (Beige/Black) are authoritative — the AI may not
    rename them even when its translation differs."""
    patch = _color_translation_patch(
        {"recognized_colors": ["Beige", "Black"], "variant_image_map": {"Beige": "SKU-01.jpg", "Black": "SKU-02.jpg"}},
        {"beige": "Apricot Cream", "black": "Midnight Black"},
        assets=[],
    )

    assert patch == {
        "recognized_colors": ["Beige", "Black"],
        "variant_image_map": {"Beige": "SKU-01.jpg", "Black": "SKU-02.jpg"},
    }


def test_chinese_colors_are_still_translated_by_ai() -> None:
    patch = _color_translation_patch(
        {"recognized_colors": ["米色"], "variant_image_map": {"米色": "SKU-01.jpg"}},
        {"米色": "Beige"},
        assets=[],
    )

    assert patch["recognized_colors"] == ["Beige"]
    assert patch["variant_image_map"] == {"Beige": "SKU-01.jpg"}


def test_unnamed_variants_fall_back_to_style_labels_when_ai_fails() -> None:
    """Numeric SKU images the AI could not name get deterministic 款式A/B/C labels
    so an import is never left with unnamed variants."""
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {},
        assets=[
            {"file_name": "SKU-01-dress only.jpg", "asset_type": "variant"},
            {"file_name": "SKU-02-dress only.jpg", "asset_type": "variant"},
        ],
    )

    assert patch == {
        "recognized_colors": ["款式A", "款式B"],
        "variant_image_map": {"款式A": "SKU-01-dress only.jpg", "款式B": "SKU-02-dress only.jpg"},
    }


def test_fallback_style_label_skips_ai_named_colors() -> None:
    """The fallback sequence never collides with a color the AI already named."""
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {"SKU-01-dress only.jpg": "款式A"},
        assets=[
            {"file_name": "SKU-01-dress only.jpg", "asset_type": "variant"},
            {"file_name": "SKU-02-dress only.jpg", "asset_type": "variant"},
        ],
    )

    assert patch["recognized_colors"] == ["款式A", "款式B"]
    assert patch["variant_image_map"] == {"款式A": "SKU-01-dress only.jpg", "款式B": "SKU-02-dress only.jpg"}


def test_parse_size_chart_result_extracts_sizes() -> None:
    from services.api.vision_listing import _parse_size_chart_result

    assert _parse_size_chart_result('{"sizes": ["S", "M", "L", "XL"]}') == ["S", "M", "L", "XL"]
    assert _parse_size_chart_result('```json\n{"sizes": ["均码"]}\n```') == ["均码"]
    assert _parse_size_chart_result('some prose {"sizes": ["27", "28", "29"]} more') == ["27", "28", "29"]


def test_recognize_size_chart_stub_gateway(tmp_path: Path) -> None:
    from services.api.vision_listing import recognize_size_chart

    img = _write_image(tmp_path, "size-chart.png")

    class _FakeGateway:
        def generate(self, connection, request):
            return _Generated(text='{"sizes": ["S", "M", "L"]}')

    result = recognize_size_chart(gateway=_FakeGateway(), connection=None, image_path=img)
    assert result.ok
    assert result.sizes == ["S", "M", "L"]


def _write_big_photo(tmp_path: Path, name: str, edge: int = 1600) -> Path:
    """Random noise resists PNG compression, so the file lands well above the 3 MB cap."""
    from PIL import Image

    path = tmp_path / name
    image = Image.frombytes("RGB", (edge, edge), os.urandom(edge * edge * 3))
    image.save(path, format="PNG")
    return path


def test_oversized_product_photo_is_downscaled_instead_of_skipped(tmp_path: Path) -> None:
    """AI 生图产出的商品图常常 4MB+；超过阈值缩图后仍要发给模型，
    否则颜色识别/标题生成会静默退化。"""
    pytest.importorskip("PIL")
    from PIL import Image

    path = _write_big_photo(tmp_path, "big-color.png")
    assert path.stat().st_size > vision_listing.MAX_IMAGE_BYTES

    image = vision_listing._read_image(path)

    assert image is not None
    assert image.media_type == "image/jpeg"
    payload = base64.b64decode(image.data_base64)
    assert len(payload) < path.stat().st_size
    with Image.open(io.BytesIO(payload)) as shrunk:
        assert max(shrunk.size) <= vision_listing.VISION_IMAGE_EDGE


def test_oversized_photo_without_pillow_keeps_raw_bytes_up_to_the_hard_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有 Pillow 时退回原始字节（仍有硬上限），不能出现「一张图都发不出去」。"""
    path = _write_big_photo(tmp_path, "big-color.png")
    monkeypatch.setattr(vision_listing, "_downscale_image", lambda _path, **_kwargs: None)

    image = vision_listing._read_image(path)
    assert image is not None
    assert image.media_type == "image/png"

    oversized = tmp_path / "absurd.png"
    oversized.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (vision_listing.MAX_IMAGE_BYTES_HARD + 1))
    assert vision_listing._read_image(oversized) is None


def test_color_pass_uses_small_images_and_always_downscales(tmp_path: Path) -> None:
    """导入提速：颜色识别只要看清颜色，用 512px/70 的小图（请求体小一个数量级）。"""

    pytest.importorskip("PIL")
    from PIL import Image

    path = _write_big_photo(tmp_path, "big-color.png")
    assert vision_listing.IMAGE_PROFILE_COLOR["edge"] < vision_listing.IMAGE_PROFILE_TEXT["edge"]
    assert vision_listing.IMAGE_PROFILE_COLOR["always_downscale"] is True

    full = vision_listing._read_image(path, profile=vision_listing.IMAGE_PROFILE_TEXT)
    small = vision_listing._read_image(path, profile=vision_listing.IMAGE_PROFILE_COLOR)

    assert full is not None and small is not None
    full_bytes = base64.b64decode(full.data_base64)
    small_bytes = base64.b64decode(small.data_base64)
    assert len(small_bytes) < len(full_bytes), "颜色档必须更小"
    with Image.open(io.BytesIO(small_bytes)) as shrunk:
        assert max(shrunk.size) <= vision_listing.IMAGE_PROFILE_COLOR["edge"]

    # 小原图也必须缩（不然 12 张原图依旧慢）
    small_source = tmp_path / "already-small.png"
    with Image.open(path) as source:
        source.thumbnail((900, 900))
        source.save(small_source)
    shrunk_anyway = vision_listing._read_image(small_source, profile=vision_listing.IMAGE_PROFILE_COLOR)
    assert shrunk_anyway is not None
    with Image.open(io.BytesIO(base64.b64decode(shrunk_anyway.data_base64))) as shrunk:
        assert max(shrunk.size) <= vision_listing.IMAGE_PROFILE_COLOR["edge"]


def test_import_runs_text_and_color_stages_in_parallel() -> None:
    """导入提速：标题/描述阶段与颜色识别阶段并行跑（总耗时 max 而非相加）。"""

    source = (Path(__file__).resolve().parents[1] / "services" / "api" / "main.py").read_text(encoding="utf-8")

    assert "def _run_text_stage() -> None:" in source
    assert "def _run_color_stage() -> None:" in source
    assert "title_errors: list[str] = []" in source and "color_errors: list[str] = []" in source
    assert "if need_text and need_color:" in source
    assert 'ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-stage")' in source
    assert "errors.extend(title_errors)" in source and "errors.extend(color_errors)" in source
    # 两个阶段的错误不要写进同一个共享列表（并行时顺序会乱）
    import re as _re

    stage_block = source[source.index("def _run_text_stage() -> None:"): source.index("errors.extend(title_errors)")]
    assert not _re.search(r"(?<![a-zA-Z_])errors\.append\(", stage_block), "阶段内应写各自的错误列表"
    assert "title_errors.append(" in stage_block and "color_errors.append(" in stage_block


def test_numbered_color_set_with_a_main_image_is_named_by_vision(tmp_path: Path) -> None:
    """文件夹里另有主图时，1-9 这类无信息图仍要交给颜色识别命名。

    现场：改色图重命名成 1-9 + 一张主图导入，扫描器为了不把多角度商品图当颜色，
    把所有无信息图都留在主图角色，AI 颜色那一趟因此一张都看不到，颜色全是空的。
    """
    names = [f"{i}.png" for i in range(1, 10)]
    assets = []
    for name in ["主图.png", *names]:
        path = _write_image(tmp_path, name)
        assets.append({"file_name": name, "file_path": str(path), "asset_type": "main", "sku_option": ""})

    candidates = vision_listing.ordered_unnamed_variant_assets(assets)
    assert [asset["file_name"] for asset in candidates] == names
    # 显式主图不参与配色。
    assert "主图.png" not in [asset["file_name"] for asset in candidates]

    colors = ["Apricot", "Purple", "Blue", "Red", "Black", "White", "Pink", "Brown", "Green"]
    gateway = _StubGateway(
        '{"title": "", "description": "", "color_translations": '
        + json.dumps({name: color for name, color in zip(names, colors)})
        + "}"
    )
    product = {"main_image": "主图.png", "recognized_colors": [], "variant_image_map": {}}

    result = vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=False, want_description=False,
    )

    assert result.ok
    assert gateway.last_request is not None
    assert gateway.last_request.user.count("#") >= len(names)
    assert "#1 1.png" in gateway.last_request.user and "#9 9.png" in gateway.last_request.user
    assert "Fixed color palette" in gateway.last_request.user

    patch = _color_translation_patch(product, result.color_translations or {}, assets=assets)
    assert patch["recognized_colors"] == colors
    assert patch["variant_image_map"] == {color: name for color, name in zip(colors, names)}


def test_a_few_plain_main_images_stay_product_photos(tmp_path: Path) -> None:
    """主图 + 多角度商品图 + 已命名颜色图的文件夹：少量无信息图不是改色图。"""
    assets = []
    for name, asset_type, sku_option in [
        ("主图-正面.jpg", "main", ""),
        ("主图-背面.jpg", "main", ""),
        ("A白色.jpg", "variant", "White"),
        ("1.jpg", "main", ""),
        ("2.jpg", "main", ""),
        ("详情1.jpg", "detail", ""),
    ]:
        path = _write_image(tmp_path, name)
        assets.append({"file_name": name, "file_path": str(path), "asset_type": asset_type, "sku_option": sku_option})

    assert vision_listing.plain_main_color_assets(assets) == []
    assert [asset["file_name"] for asset in vision_listing.ordered_unnamed_variant_assets(assets)] == []


def test_numbered_color_set_with_one_named_color_takes_the_rest(tmp_path: Path) -> None:
    """一张已经命名的颜色图 + 1-9 时，剩下的无信息图继续由 AI 命名。"""
    names = [f"{i}.png" for i in range(1, 10)]
    assets = []
    for name in ["A白色.png", *names]:
        path = _write_image(tmp_path, name)
        asset_type = "variant" if name == "A白色.png" else "main"
        sku_option = "White" if name == "A白色.png" else ""
        assets.append({"file_name": name, "file_path": str(path), "asset_type": asset_type, "sku_option": sku_option})

    candidates = vision_listing.ordered_unnamed_variant_assets(assets)
    assert [asset["file_name"] for asset in candidates] == names

    translations = {name: color for name, color in zip(names, ["Apricot", "Purple", "Blue", "Red", "Black", "Pink", "Brown", "Green", "Apricot"])}
    patch = _color_translation_patch(
        {"recognized_colors": ["White"], "variant_image_map": {"White": "A白色.png"}},
        translations,
        assets=assets,
    )
    assert patch["recognized_colors"][0] == "White"
    assert patch["variant_image_map"]["White"] == "A白色.png"
    # 9 张图 + 已有的 White：重复的 Apricot 让位（不覆盖已有绑定），颜色数不重复。
    assert len(patch["recognized_colors"]) == len(set(color.lower() for color in patch["recognized_colors"]))


# --- 2026-11-04：颜色识别容错 -------------------------------------------------
# 现场症状：A.png / A (1).png … A (7).png + A白色.jpg 的导入里，8 张全变成
# 款式A-H，只有文件名带「白色」的那张留住了 White。三个真实原因都在这里钉住。


def test_color_translation_keys_tolerate_spacing_and_suffix_variants() -> None:
    """模型把键写成 A(1).png（少空格）/ A (1)（少后缀）也要认。"""
    names = ["A.png", "A (1).png", "A (2).png"]

    matched = vision_listing.match_color_translations(
        names,
        {"A.png": "Blue", "A(1).png": "Red", "A (2)": "Green"},
    )

    assert matched == {"A.png": "Blue", "A (1).png": "Red", "A (2).png": "Green"}


def test_color_translation_keys_accept_reversed_mapping() -> None:
    """模型把键值写反（颜色 → 文件名）时不能整批落空。"""
    matched = vision_listing.match_color_translations(
        ["A.png", "A (1).png"],
        {"Black": "A.png", "Pink": "A (1).png"},
    )

    assert matched == {"A.png": "Black", "A (1).png": "Pink"}


def test_color_translations_patch_uses_fuzzy_file_keys() -> None:
    """导入写回时也要走同一套容错，否则 fuzzy 只在识别层生效。"""
    patch = _color_translation_patch(
        {"recognized_colors": [], "variant_image_map": {}},
        {"A(1).png": "Blue", "A(2).png": "Red"},
        assets=[
            {"file_name": "A (1).png", "asset_type": "variant"},
            {"file_name": "A (2).png", "asset_type": "variant"},
        ],
    )

    assert patch == {
        "recognized_colors": ["Blue", "Red"],
        "variant_image_map": {"Blue": "A (1).png", "Red": "A (2).png"},
    }


def test_chinese_color_answers_snap_to_the_fixed_palette() -> None:
    """模型偶尔无视英文要求：中文色名先归色板，别再被 ASCII 门槛整条丢掉。"""
    assert vision_listing.snap_color_to_palette("白色") == "White"
    assert vision_listing.snap_color_to_palette("深蓝色") == "Blue"
    assert vision_listing.snap_color_to_palette("墨绿色") == "Green"
    # 色板外的颜色仍然保留原话（灰/黄/橙不硬归类）。
    assert vision_listing.snap_color_to_palette("灰色") == ""


def test_color_pass_splits_batches_over_the_request_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一张批超过请求体预算就分批发，避免供应商 32MB 上限的 413 打死整趟。"""
    assets = []
    for name in ["A.png", "A (1).png", "A (2).png"]:
        path = _write_image(tmp_path, name)
        assets.append({"file_name": name, "file_path": str(path), "asset_type": "variant"})

    texts = [
        '{"color_translations": {"A.png": "Blue"}}',
        '{"color_translations": {"A (1).png": "Red"}}',
        '{"color_translations": {"A (2).png": "Green"}}',
    ]

    class _BatchGateway:
        def __init__(self) -> None:
            self.sizes: list[int] = []

        def generate(self, connection, request):
            self.sizes.append(len(request.images))
            return _Generated(text=texts[min(len(self.sizes) - 1, len(texts) - 1)])

    gateway = _BatchGateway()
    # 单张图的 base64 就超过预算 → 每张图单独一批。
    monkeypatch.setattr(vision_listing, "MAX_COLOR_REQUEST_BYTES", 10)

    result = vision_listing.generate_listing_from_images(
        gateway=gateway,
        connection={"id": "conn", "model_id": "test-model"},
        base_product={"id": "bp", "group_id": "G", "main_image": "", "product_images": []},
        assets=assets,
        want_title=False,
        want_description=False,
        fill_attributes_into_description=False,
        color_targets=None,
    )

    assert gateway.sizes == [1, 1, 1]
    assert result.ok
    assert (result.color_translations or {}) == {"A.png": "Blue", "A (1).png": "Red", "A (2).png": "Green"}


def test_color_pass_retries_the_images_the_model_omitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模型漏掉某个文件名时，只补发漏掉的那几张，而不是让它们直接变款式。"""
    assets = []
    for name in ["A.png", "A (1).png"]:
        path = _write_image(tmp_path, name)
        assets.append({"file_name": name, "file_path": str(path), "asset_type": "variant"})

    class _OmitFirstGateway:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def generate(self, connection, request):
            self.calls.append([line.split(" ", 1)[-1] for line in request.user.splitlines() if line.startswith("#")])
            if len(self.calls) == 1:
                return _Generated(text='{"color_translations": {"A (1).png": "Red"}}')
            return _Generated(text='{"color_translations": {"A.png": "Blue"}}')

    gateway = _OmitFirstGateway()
    result = vision_listing.generate_listing_from_images(
        gateway=gateway,
        connection={"id": "conn", "model_id": "test-model"},
        base_product={"id": "bp", "group_id": "G", "main_image": "", "product_images": []},
        assets=assets,
        want_title=False,
        want_description=False,
        fill_attributes_into_description=False,
        color_targets=None,
    )

    assert gateway.calls == [["A.png", "A (1).png"], ["A.png"]]
    assert (result.color_translations or {}) == {"A (1).png": "Red", "A.png": "Blue"}


def test_color_pass_reports_unnamed_images_instead_of_silence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模型什么都配不上时必须留下说明，不能再静默给一串款式。"""
    assets = []
    for name in ["A.png", "A (1).png"]:
        path = _write_image(tmp_path, name)
        assets.append({"file_name": name, "file_path": str(path), "asset_type": "variant"})

    gateway = _StubGateway('{"color_translations": {"nonexistent.png": "Blue"}}')
    result = vision_listing.generate_listing_from_images(
        gateway=gateway,
        connection={"id": "conn", "model_id": "test-model"},
        base_product={"id": "bp", "group_id": "G", "main_image": "", "product_images": []},
        assets=assets,
        want_title=False,
        want_description=False,
        fill_attributes_into_description=False,
        color_targets=None,
    )

    assert result.ok
    assert (result.color_translations or {}).get("nonexistent.png") == "Blue"
    assert result.warnings
    assert "没拿到颜色" in "；".join(result.warnings)


def test_import_reports_leftover_style_labels_instead_of_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只剩款式占位时必须写进 ai_generation_error，操作员才知道为什么没颜色。"""
    white = _write_image(tmp_path, "A白色.jpg")
    unnamed = _write_image(tmp_path, "A.png")
    assets = [
        {"file_name": white.name, "file_path": str(white), "asset_type": "variant", "sku_option": "White"},
        {"file_name": unnamed.name, "file_path": str(unnamed), "asset_type": "variant"},
    ]
    product = {
        "id": "bp-style-warning",
        "group_id": "G-STYLE",
        "main_image": unnamed.name,
        "recognized_colors": [],
        "variant_image_map": {},
        "variant_count": 2,
    }
    # 模型答了一个对不上的键：A.png 解析不出颜色 → 落款式A。
    gateway = _StubGateway('{"color_translations": {"unrelated.png": "Blue"}}')

    monkeypatch.setattr(storage, "get_runtime_configuration", lambda: {"ai_listing_generation": {}})
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", lambda: {"id": "conn"})
    monkeypatch.setattr(storage, "assets_for_group", lambda _group_id: assets)
    monkeypatch.setattr(storage, "update_base_product", lambda _id, patch, **_kwargs: {**product, **patch})
    monkeypatch.setattr(main, "ModelGateway", lambda **_kwargs: gateway)

    result = main._enrich_with_vision_listing(product, {})

    assert any(str(color).startswith("款式") for color in result["recognized_colors"])
    assert "没认出颜色" in result["ai_generation_error"]
