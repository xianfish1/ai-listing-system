from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import main, settings, storage, vision_listing
from tests.test_api_boundaries import _start_api
from tests.test_vision_listing import _StubGateway


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _make_product(tmp_path: Path) -> dict:
    folder = tmp_path / "AL7777 rerecognize"
    folder.mkdir()
    for name in ["主图.jpg", "SKU-01-dress only.jpg", "SKU-02-dress only.jpg"]:
        (folder / name).write_bytes(b"image")
    product = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD Mens T-Shirt"}
    )
    assert product is not None
    return product


def _dummy_connection() -> dict:
    return {
        "id": "conn-rerecognize",
        "name": "stub",
        "protocol": "anthropic_messages",
        "base_url": "https://example.invalid",
        "model_id": "stub-model",
        "encrypted_api_key": "",
    }


def test_color_recognition_targets_include_current_map_and_variant_assets() -> None:
    names = main._color_recognition_target_names(
        {"variant_image_map": {"Blue": "1 (10).jpg"}},
        [
            {"file_name": "1 (2).jpg", "asset_type": "variant"},
            {"file_name": "1 (10).jpg", "asset_type": "variant"},
            {"file_name": "主图.jpg", "asset_type": "main"},
        ],
    )

    assert names == ["1 (2).jpg", "1 (10).jpg"]


def test_forced_color_patch_rebuilds_named_images_and_keeps_manual_colors() -> None:
    patch = main._forced_color_translation_patch(
        {
            "recognized_colors": ["White", "Navy", "Red"],
            "variant_image_map": {"White": "white.jpg", "Navy": "navy.jpg"},
        },
        {"white.jpg": "Pink", "navy.jpg": "Blue"},
        [
            {"file_name": "white.jpg", "asset_type": "variant", "sku_option": "White"},
            {"file_name": "navy.jpg", "asset_type": "variant", "sku_option": "Navy"},
            {"file_name": "green.jpg", "asset_type": "variant", "sku_option": "Green"},
        ],
    )

    assert patch["recognized_colors"] == ["Pink", "Blue", "Green", "Red"]
    assert patch["variant_image_map"] == {
        "Pink": "white.jpg",
        "Blue": "navy.jpg",
        "Green": "green.jpg",
    }


def test_forced_color_patch_keeps_existing_binding_when_ai_misses_a_file() -> None:
    patch = main._forced_color_translation_patch(
        {"recognized_colors": ["White", "Black"], "variant_image_map": {"White": "w.jpg", "Black": "b.jpg"}},
        {},
        [
            {"file_name": "w.jpg", "asset_type": "variant", "sku_option": "White"},
            {"file_name": "b.jpg", "asset_type": "variant", "sku_option": "Black"},
        ],
    )

    assert patch["recognized_colors"] == ["White", "Black"]
    assert patch["variant_image_map"] == {"White": "w.jpg", "Black": "b.jpg"}


def test_forced_color_targets_drive_the_vision_request(tmp_path: Path) -> None:
    names = ["1 (1).jpg", "1 (2).jpg"]
    assets = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"image")
        assets.append({"file_name": name, "file_path": str(path), "asset_type": "variant", "sku_option": "White"})

    gateway = _StubGateway(
        json.dumps({"title": "", "description": "", "color_translations": {"1 (1).jpg": "Pink", "1 (2).jpg": "Blue"}})
    )
    result = vision_listing.generate_listing_from_images(
        gateway=gateway,
        connection={"id": "conn"},
        base_product={"main_image": "", "recognized_colors": ["White"], "variant_image_map": {"White": "1 (1).jpg"}},
        assets=assets,
        want_title=False,
        want_description=False,
        color_targets=names,
    )

    assert result.ok
    assert gateway.last_request is not None
    assert len(gateway.last_request.images) == 2
    # 已命名的颜色图也要重看；提示里不能再出现上一次的颜色名。
    assert "#1 1 (1).jpg" in gateway.last_request.user
    assert "#2 1 (2).jpg" in gateway.last_request.user
    assert "Detected color variants" not in gateway.last_request.user


def test_title_aspect_only_rewrites_the_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    product = _make_product(tmp_path)
    storage.update_base_product(
        product["id"],
        {
            "listing_title": "OLD TITLE",
            "description": "OLD DESCRIPTION",
            "recognized_attributes": {"Material": "Cotton"},
        },
    )
    product = storage.get_item("base_products", product["id"])
    calls: dict = {}

    def fake_generate(**kwargs):
        calls.update(kwargs)
        return vision_listing.VisionListingResult(
            title="NEW TITLE",
            description="SHOULD NOT APPLY",
            model_id="stub-model",
            input_image_count=1,
            attributes={"Material": "Polyester"},
        )

    monkeypatch.setattr(vision_listing, "generate_listing_from_images", fake_generate)
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", _dummy_connection)

    updated = main._enrich_with_vision_listing(product, {"generate_description": True}, mark_edited=True, aspect="title")

    assert updated["listing_title"] == "NEW TITLE"
    assert updated["description"] == "OLD DESCRIPTION"
    assert updated["recognized_attributes"] == {"Material": "Cotton"}
    assert calls["want_title"] is True
    assert calls["want_description"] is False


def test_title_regeneration_honors_an_explicitly_selected_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「重新识别标题」可以手动指定规则：即使商品范围推断出来是男装，也按选中的童装规则生成。"""
    from services.api import title_skill_presets

    product = _make_product(tmp_path)
    storage.update_base_product(
        product["id"], {"recognized_attributes": {"Item Type": "T-Shirt", "Gender": "Men"}}
    )
    product = storage.get_item("base_products", product["id"])
    preset_rule = title_skill_presets.list_presets()[0]["rule"]
    skill = storage.create_title_skill({**preset_rule, "name": "童装规则", "audience": "kids"})
    calls: dict = {}

    def fake_generate(**kwargs):
        calls.update(kwargs)
        return vision_listing.VisionListingResult(
            title="KIDS TEE", description="", model_id="stub-model", input_image_count=1
        )

    monkeypatch.setattr(vision_listing, "generate_listing_from_images", fake_generate)
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", _dummy_connection)

    updated = main._enrich_with_vision_listing(
        product,
        {"aspect": "title", "skill_id": skill["id"], "force_skill": True},
        mark_edited=True,
        aspect="title",
    )

    assert updated["listing_title"] == "KIDS TEE"
    assert calls["title_skill"]["id"] == skill["id"]
    assert calls["title_context"]["scope"]["audience"] == "kids"


def test_description_aspect_only_rewrites_the_description(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    product = _make_product(tmp_path)
    storage.update_base_product(
        product["id"],
        {"listing_title": "OLD TITLE", "description": "OLD DESCRIPTION"},
    )
    product = storage.get_item("base_products", product["id"])
    calls: dict = {}

    def fake_generate(**kwargs):
        calls.update(kwargs)
        return vision_listing.VisionListingResult(
            title="SHOULD NOT APPLY",
            description="NEW DESCRIPTION",
            model_id="stub-model",
            input_image_count=1,
        )

    monkeypatch.setattr(vision_listing, "generate_listing_from_images", fake_generate)
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", _dummy_connection)

    updated = main._enrich_with_vision_listing(product, {"generate_title": True}, mark_edited=True, aspect="description")

    assert updated["listing_title"] == "OLD TITLE"
    assert updated["description"] == "NEW DESCRIPTION"
    assert calls["want_title"] is False
    assert calls["want_description"] is True


def test_colors_aspect_rerecognizes_already_named_variant_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    product = _make_product(tmp_path)
    storage.update_base_product(
        product["id"],
        {"recognized_colors": ["White"], "variant_image_map": {"White": "SKU-01-dress only.jpg"}},
    )
    product = storage.get_item("base_products", product["id"])
    calls: dict = {}

    def fake_generate(**kwargs):
        calls.update(kwargs)
        return vision_listing.VisionListingResult(
            title="SHOULD NOT APPLY",
            description="SHOULD NOT APPLY",
            model_id="stub-model",
            input_image_count=2,
            color_translations={"SKU-01-dress only.jpg": "Pink", "SKU-02-dress only.jpg": "Blue"},
        )

    monkeypatch.setattr(vision_listing, "generate_listing_from_images", fake_generate)
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", _dummy_connection)

    updated = main._enrich_with_vision_listing(product, {}, mark_edited=True, aspect="colors")

    assert calls["color_targets"] == ["SKU-01-dress only.jpg", "SKU-02-dress only.jpg"]
    assert calls["want_title"] is False
    assert calls["want_description"] is False
    assert updated["recognized_colors"] == ["Pink", "Blue"]
    assert updated["variant_image_map"] == {
        "Pink": "SKU-01-dress only.jpg",
        "Blue": "SKU-02-dress only.jpg",
    }


def test_colors_aspect_can_use_the_color_only_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有默认连接、只配了「颜色识别使用的 AI」时，纯颜色重识别也要能跑。"""
    product = _make_product(tmp_path)
    storage.update_base_product(
        product["id"],
        {"recognized_colors": ["White"], "variant_image_map": {"White": "SKU-01-dress only.jpg"}},
    )
    product = storage.get_item("base_products", product["id"])
    color_connection = {**_dummy_connection(), "id": "color-connection"}

    def fake_generate(**kwargs):
        return vision_listing.VisionListingResult(
            title="",
            description="",
            model_id="stub-model",
            input_image_count=2,
            color_translations={"SKU-01-dress only.jpg": "Pink", "SKU-02-dress only.jpg": "Blue"},
        )

    monkeypatch.setattr(vision_listing, "generate_listing_from_images", fake_generate)
    monkeypatch.setattr(storage, "get_default_ai_model_connection_private", lambda: None)
    monkeypatch.setattr(storage, "get_ai_connection_for_purpose", lambda purpose: color_connection)

    updated = main._enrich_with_vision_listing(product, {}, mark_edited=True, aspect="colors")

    assert updated["recognized_colors"] == ["Pink", "Blue"]
    assert not updated.get("ai_generation_error")


def test_colors_aspect_reports_an_error_when_there_is_no_variant_image(tmp_path: Path) -> None:
    folder = tmp_path / "AL7778 no variant"
    folder.mkdir()
    (folder / "主图.jpg").write_bytes(b"image")
    product = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD Mens T-Shirt"}
    )

    updated = main._enrich_with_vision_listing(product, {}, aspect="colors")

    assert "没有可重新识别的颜色图" in updated["ai_generation_error"]


def _post_json(url: str, payload: dict) -> dict:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_regenerate_endpoint_routes_the_title_aspect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    product = _make_product(tmp_path)
    storage.update_base_product(
        product["id"],
        {"listing_title": "OLD TITLE", "description": "OLD DESCRIPTION"},
    )
    storage.create_ai_model_connection(
        {
            "name": "stub",
            "protocol": "anthropic_messages",
            "base_url": "https://example.invalid",
            "model_id": "stub-model",
            "api_key": "test-key",
            "is_default": True,
        }
    )
    calls: dict = {}

    def fake_generate(**kwargs):
        calls.update(kwargs)
        return vision_listing.VisionListingResult(
            title="NEW TITLE",
            description="SHOULD NOT APPLY",
            model_id="stub-model",
            input_image_count=1,
        )

    monkeypatch.setattr(vision_listing, "generate_listing_from_images", fake_generate)
    server, base_url = _start_api()
    try:
        result = _post_json(
            f"{base_url}/api/base-products/{product['id']}/regenerate-listing",
            {"aspect": "title"},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["listing_title"] == "NEW TITLE"
    assert result["description"] == "OLD DESCRIPTION"
    assert calls["want_title"] is True
    assert calls["want_description"] is False


def test_regenerate_endpoint_rejects_an_unknown_aspect(tmp_path: Path) -> None:
    product = _make_product(tmp_path)
    server, base_url = _start_api()
    try:
        req = request.Request(
            f"{base_url}/api/base-products/{product['id']}/regenerate-listing",
            data=json.dumps({"aspect": "everything"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(error.HTTPError) as excinfo:
            request.urlopen(req, timeout=10)
    finally:
        server.shutdown()
        server.server_close()

    assert excinfo.value.code == 422
    assert "aspect" in excinfo.value.read().decode("utf-8")


def test_color_recognition_targets_include_numbered_images_typed_as_main() -> None:
    """图片命名成 1-9、文件夹里另有主图的老商品：重新识别颜色图也要看到它们。

    扫描器把这类无信息图保留成主图角色（避免把多角度商品图当颜色），只按 variant
    取目标会让「重新识别颜色图」对这些商品什么都不做。"""
    assets = [
        {"file_name": "主图.png", "asset_type": "main", "sku_option": ""},
        *[
            {"file_name": f"{index}.png", "asset_type": "main", "sku_option": ""}
            for index in range(1, 10)
        ],
    ]

    names = main._color_recognition_target_names({"variant_image_map": {}}, assets)

    assert names == [f"{index}.png" for index in range(1, 10)]


def test_a_few_plain_main_images_are_not_color_targets() -> None:
    """主图 + 多角度图 + 颜色图：少量无信息主图不是颜色图，不进重识别目标。"""
    assets = [
        {"file_name": "主图-正面.jpg", "asset_type": "main", "sku_option": ""},
        {"file_name": "主图-背面.jpg", "asset_type": "main", "sku_option": ""},
        {"file_name": "A白色.jpg", "asset_type": "variant", "sku_option": "White"},
        {"file_name": "1.jpg", "asset_type": "main", "sku_option": ""},
        {"file_name": "2.jpg", "asset_type": "main", "sku_option": ""},
        {"file_name": "详情1.jpg", "asset_type": "detail", "sku_option": ""},
    ]

    assert main._color_recognition_target_names({"variant_image_map": {}}, assets) == ["A白色.jpg"]
