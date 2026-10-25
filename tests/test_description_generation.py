from __future__ import annotations

from services.api.description_generation import DescriptionGenerationService
from services.api.model_gateway import GenerationResult
from services.api import settings, storage

import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


class FakeGateway:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.error: Exception | None = None
        self.requests = []

    def generate(self, connection, request):
        self.requests.append((connection, request))
        if self.error:
            raise self.error
        return GenerationResult(
            text=self.text,
            model_id="model-a",
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 12},
        )


def _connection() -> dict:
    return {"id": "conn-1", "name": "主模型", "model_id": "model-a"}


def _combined_profile() -> dict:
    return {
        "id": "dpv-1",
        "mode": "combined",
        "opening": "Welcome to Fly_Show",
        "after_sales": "Contact us first.",
        "protected_facts": {"shipping_time": "7-12 days"},
        "max_length": 3000,
    }


def _full_profile() -> dict:
    return {
        "id": "dpv-2",
        "mode": "full",
        "protected_facts": {
            "shipping_time": "Ship from China and usually 7-12 days to arrive.",
            "after_sales": "For problems after the transaction, contact us first.",
        },
        "max_length": 3000,
    }


def test_combined_generation_only_replaces_product_section() -> None:
    gateway = FakeGateway("Soft Hoodie for daily wear.")

    result = DescriptionGenerationService(gateway).generate(
        profile=_combined_profile(),
        product={"product_name": "Hoodie", "package_quantity": 1, "package_item": "Hoodie"},
        connection=_connection(),
    )

    assert result.status == "ready"
    assert result.description == "Welcome to Fly_Show\n\nSoft Hoodie for daily wear.\n\nContact us first."


def test_combined_generation_rejects_model_shipping_or_package_promises() -> None:
    gateway = FakeGateway("Usually 3 days to arrive. What's in the package:2*Hoodie")
    result = DescriptionGenerationService(gateway).generate(
        profile=_combined_profile(), product={"product_name": "Hoodie"}, connection=_connection()
    )
    assert result.status == "pending"
    assert "受保护事实" in result.error


def test_full_generation_injects_each_protected_fact_once() -> None:
    gateway = FakeGateway(
        "Intro {{SHIPPING_TIME}} Product {{PACKAGE_CONTENTS}} Service {{AFTER_SALES}}"
    )

    result = DescriptionGenerationService(gateway).generate(
        profile=_full_profile(),
        product={"product_name": "Hoodie", "package_quantity": 1, "package_item": "Hoodie"},
        connection=_connection(),
    )

    assert result.status == "ready"
    assert result.description.count("7-12 days") == 1
    assert result.description.count("1*Hoodie") == 1
    assert result.description.count("For problems after the transaction") == 1


def test_full_generation_rejects_missing_placeholder() -> None:
    gateway = FakeGateway("Intro {{SHIPPING_TIME}} Product without protected blocks")

    result = DescriptionGenerationService(gateway).generate(
        profile=_full_profile(),
        product={"product_name": "Hoodie", "package_quantity": 1, "package_item": "Hoodie"},
        connection=_connection(),
    )

    assert result.status == "pending"
    assert result.description == ""
    assert "占位符" in result.error


def test_generation_failure_creates_pending_result_without_secret() -> None:
    gateway = FakeGateway()
    gateway.error = RuntimeError("HTTP 503 secret-key")

    result = DescriptionGenerationService(gateway).generate(
        profile=_combined_profile(),
        product={"product_name": "Hoodie"},
        connection=_connection(),
    )

    assert result.status == "pending"
    assert result.description == ""
    assert result.error == "AI 描述生成失败"
    assert "secret-key" not in result.error


def test_description_results_are_appended_as_history() -> None:
    first = storage.record_listing_description_generation(
        "copy-1",
        {
            "status": "ready",
            "description": "First description",
            "profile_version_id": "dpv-1",
            "connection_id": "conn-1",
            "model_id": "model-a",
        },
    )
    second = storage.record_listing_description_generation(
        "copy-1",
        {
            "status": "ready",
            "description": "Second description",
            "profile_version_id": "dpv-2",
            "connection_id": "conn-2",
            "model_id": "model-b",
        },
    )

    history = storage.list_ai_outputs_for_product("copy-1", "listing_description")
    assert [item["description"] for item in history] == ["Second description", "First description"]
    assert first["id"] != second["id"]


def test_generation_records_profile_version_snapshot_id() -> None:
    profile = _combined_profile() | {"id": "profile-1", "current_version_id": "profile-version-3"}
    result = DescriptionGenerationService(FakeGateway("Soft Hoodie for daily wear.")).generate(
        profile=profile,
        product={"product_name": "Hoodie"},
        connection=_connection(),
    )

    assert result.profile_version_id == "profile-version-3"


# ---------------------------------------------------------------------------
# Sectioned mode
# ---------------------------------------------------------------------------

from services.api.description_generation import default_sectioned_profile  # noqa: E402


def _el0008_product() -> dict:
    return {
        "listing_title": "Premium Anime Print Summer Cotton T-Shirt",
        "marketplace_category_path": "Men Clothes>Tops>T-Shirts",
        "recognized_colors": ["black"],
        "variation": {
            "type": "variant",
            "axes": [
                "{'name': 'color', 'values': ['black']}",
                "{'name': 'size', 'values': ['S', 'M', 'L', 'XL', '2XL']}",
            ],
        },
    }


def _ai_slot_text(**slots: str) -> str:
    return "\n".join(f"[[SLOT:{name}]]{value}[[/SLOT]]" for name, value in slots.items())


def test_sectioned_assembles_fixed_field_and_ai_blocks() -> None:
    gateway = FakeGateway(
        _ai_slot_text(
            store_intro="Level up your look with fire_leaf anime tees!",
            product_name="Premium Anime Print Summer Cotton T-Shirt",
            theme="Classic Anime & Pop Culture Print",
            age_range="Adults / Teens",
            highlights="Soft breathable cotton\nSharp long-lasting print\nVivid fade-resistant colors",
        )
    )
    result = DescriptionGenerationService(gateway).generate(
        profile=default_sectioned_profile(),
        product=_el0008_product(),
        connection=_connection(),
    )
    assert result.status == "ready", result.error
    desc = result.description
    # fixed section verbatim
    assert "🚀 SHIPPING NOTICE" in desc
    assert "100% Local Stock" in desc
    # ai slot placed under its section
    assert "🌿 WELCOME TO FIRE_LEAF" in desc
    assert "Level up your look with fire_leaf" in desc
    # field rows mapped from real data
    assert "📁 Category: Men Clothes>Tops>T-Shirts" in desc
    assert "🎨 Color: Black" in desc
    assert "📏 Size: S / M / L / XL / 2XL" in desc
    # store-fixed rows (混合 decision)
    assert "🧵 Material: 100% High-Quality Cotton (Knit Fabric)" in desc
    assert "👥 Target Users: Women / Kids (Middle Age / Youth Fashion)" in desc
    # no HTML, plain text only
    assert "<" not in desc


REQUIRED_SPEC_LABELS = (
    "🧵 Material:",
    "📐 Size Guide:",
    "📏 Size Tolerance:",
    "🧒 Age Range:",
    "📏 Size:",
    "📦 Stock Availability:",
)


def test_sectioned_renders_required_spec_rows() -> None:
    """必加行：Material / Size Guide / Size Tolerance / Age Range / Size /
    Stock Availability 必须出现在生成的描述里（操作员要求）。"""
    gateway = FakeGateway(
        _ai_slot_text(
            store_intro="Fresh anime styles from fire_leaf.",
            product_name="Anime Tee",
            theme="Anime Print",
            age_range="Kids 2-8 years",
            highlights="Comfortable\nDurable",
        )
    )
    result = DescriptionGenerationService(gateway).generate(
        profile=default_sectioned_profile(),
        product=_el0008_product(),
        connection=_connection(),
    )
    assert result.status == "ready", result.error
    for label in REQUIRED_SPEC_LABELS:
        assert label in result.description, label


def test_sectioned_fixed_spec_rows_survive_missing_product_data() -> None:
    """商品缺颜色/尺码数据时必加行仍要在：字段行会因空值被跳过，固定值行不会。"""
    product = {"listing_title": "Plain Kids Tee", "recognized_attributes": {}}
    gateway = FakeGateway(
        _ai_slot_text(
            store_intro="Fresh anime styles from fire_leaf.",
            product_name="Plain Kids Tee",
            theme="Plain Print",
            age_range="Kids 2-8 years",
            highlights="Comfortable",
        )
    )
    result = DescriptionGenerationService(gateway).generate(
        profile=default_sectioned_profile(),
        product=product,
        connection=_connection(),
    )
    assert result.status == "ready", result.error
    desc = result.description
    for label in ("🧵 Material:", "📐 Size Guide:", "📏 Size Tolerance:", "🧒 Age Range:", "📦 Stock Availability:"):
        assert label in desc, label
    assert "📏 Size:" not in desc  # 没有尺码数据 → 字段行照旧不输出空行


def test_sectioned_skips_gateway_when_no_ai_slots() -> None:
    gateway = FakeGateway("should-not-be-used")
    profile = {
        "name": "no-ai",
        "mode": "sectioned",
        "sections": [
            {"id": "s", "type": "fixed", "emoji": "🚀", "title": "SHIPPING", "body": "Local stock."},
            {"id": "c", "type": "fields", "emoji": "👕", "title": "INFO",
             "rows": [{"emoji": "📁", "label": "Category", "source": "field", "field": "category"}]},
        ],
    }
    result = DescriptionGenerationService(gateway).generate(
        profile=profile, product=_el0008_product(), connection=_connection()
    )
    assert result.status == "ready", result.error
    assert gateway.requests == []  # gateway never called
    assert "📁 Category: Men Clothes>Tops>T-Shirts" in result.description


def test_sectioned_blocks_protected_claims_in_ai_slot() -> None:
    gateway = FakeGateway(
        _ai_slot_text(
            store_intro="We ship worldwide and arrive in 3-5 days!",
            product_name="Tee",
            theme="Anime",
            age_range="Adults",
            highlights="Nice",
        )
    )
    result = DescriptionGenerationService(gateway).generate(
        profile=default_sectioned_profile(),
        product=_el0008_product(),
        connection=_connection(),
    )
    assert result.status == "pending"
    assert result.description == ""
    assert "受保护事实" in result.error


def test_sectioned_drops_missing_optional_slot() -> None:
    # An OPTIONAL slot missing from AI output → that row is omitted, rest renders.
    profile = default_sectioned_profile()
    for section in profile["sections"]:
        for row in section.get("rows") or []:
            if row.get("slot") == "theme":
                row["optional"] = True
    gateway = FakeGateway(
        _ai_slot_text(
            store_intro="Fresh anime styles from fire_leaf.",
            product_name="Anime Tee",
            age_range="Adults",
            highlights="Comfortable\nDurable print",
        )
    )
    result = DescriptionGenerationService(gateway).generate(
        profile=profile, product=_el0008_product(), connection=_connection()
    )
    assert result.status == "ready", result.error
    assert "🎬 Character/Theme:" not in result.description  # dropped, no empty row
    assert "🏷️ Product Name: Anime Tee" in result.description


def test_sectioned_fails_loudly_when_required_slot_missing() -> None:
    # theme is required by default; if the model never returns it, retries are
    # exhausted and we fail with a clear message rather than shipping a gap.
    gateway = FakeGateway(
        _ai_slot_text(
            store_intro="Fresh anime styles.",
            product_name="Anime Tee",
            age_range="Adults",
            highlights="Comfortable",
        )
    )
    result = DescriptionGenerationService(gateway).generate(
        profile=default_sectioned_profile() | {"max_attempts": 2},
        product=_el0008_product(),
        connection=_connection(),
    )
    assert result.status == "pending"
    assert result.description == ""
    assert "theme" in result.error and "必填槽位" in result.error


def test_sectioned_parses_slots_when_close_tag_omitted() -> None:
    # Observed real-model behavior: it omits [[/SLOT]] and uses the next
    # [[SLOT:...]] as the delimiter. Each slot must still parse cleanly without
    # swallowing the following slots or leaking raw markers.
    raw = (
        "[[SLOT:store_intro]]Welcome to fire_leaf — grab an easy everyday tee."
        "[[SLOT:product_name]]Fire Leaf Everyday Graphic Tee"
        "[[SLOT:theme]]Bold Urban Street Graphic Print"
        "[[SLOT:age_range]]Adults / Teens"
        "[[SLOT:highlights]]Ultra-soft breathable fabric\nDurable print that stays sharp"
    )
    result = DescriptionGenerationService(FakeGateway(raw)).generate(
        profile=default_sectioned_profile(),
        product=_el0008_product(),
        connection=_connection(),
    )
    assert result.status == "ready", result.error
    desc = result.description
    assert "[[SLOT:" not in desc and "[[/SLOT]]" not in desc  # no leaked markers
    assert "🌿 WELCOME TO FIRE_LEAF\nWelcome to fire_leaf — grab an easy everyday tee." in desc
    assert "🏷️ Product Name: Fire Leaf Everyday Graphic Tee" in desc
    assert "🎬 Character/Theme: Bold Urban Street Graphic Print" in desc
    assert "Ultra-soft breathable fabric" in desc
    assert "Durable print that stays sharp" in desc


def test_sectioned_retries_on_transient_empty_content() -> None:
    # First call raises (empty content), retry succeeds → ready.
    good = _ai_slot_text(
        store_intro="Fresh anime styles from fire_leaf.",
        product_name="Anime Tee",
        theme="Anime Print",
        age_range="Adults",
        highlights="Comfortable\nDurable",
    )

    class FlakyGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__(good)
            self.calls = 0

        def generate(self, connection, request):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("AI 服务没有返回文本内容")
            return super().generate(connection, request)

    gateway = FlakyGateway()
    result = DescriptionGenerationService(gateway).generate(
        profile=default_sectioned_profile(),
        product=_el0008_product(),
        connection=_connection(),
    )
    assert result.status == "ready", result.error
    assert gateway.calls == 2  # retried once
    assert "🎬 Character/Theme: Anime Print" in result.description


def test_sectioned_real_sample_render(capsys) -> None:
    """Render a real EL0008 description and print it for visual inspection."""
    gateway = FakeGateway(
        _ai_slot_text(
            store_intro="Get the best anime styles right here at fire_leaf! Premium locally stocked apparel — shop with confidence and level up your daily look.",
            product_name="Premium Anime Print Summer Cotton T-Shirt",
            theme="Classic Anime & Pop Culture Print",
            age_range="Adults / Teens (Unisex)",
            highlights="Soft, breathable 100% cotton for all-day comfort\nSharp DTF print that stays vivid wash after wash\nEasy to style — pairs with jeans, shorts, or joggers",
        )
    )
    result = DescriptionGenerationService(gateway).generate(
        profile=default_sectioned_profile(),
        product=_el0008_product(),
        connection=_connection(),
    )
    assert result.status == "ready", result.error
    with capsys.disabled():
        print("\n===== EL0008 SECTIONED DESCRIPTION =====\n" + result.description + "\n===== END =====")


def test_sectioned_base_detail_row_dumps_full_attribute_list() -> None:
    """字段行 source=base_detail 时，把基础商品识别属性整组直出为键值列表（不经 AI）。"""
    from services.api.description_generation import _sectioned_description

    sections = [
        {
            "id": "product_info",
            "type": "fields",
            "emoji": "👕",
            "title": "PRODUCT INFORMATION",
            "rows": [
                {"emoji": "🏷️", "label": "Product Name", "source": "field", "field": "product_name"},
                {"emoji": "🧷", "label": "Base Detail", "source": "base_detail"},
            ],
        }
    ]
    product = {
        "listing_title": "Anime Hair Clip",
        "recognized_attributes": {
            "Item Type": "Hair Ornament",
            "Material": "Zinc Alloy",
            "Style": "Anime",
            "Theme": "Anime Character",
            "Pattern Type": "Flower",
            "Applicable Scene": "Cosplay",
            "Applicable Season": "Four Seasons",
            "Gender": "Women",
        },
    }
    out = _sectioned_description(sections, product, slot_values={})
    assert "Product Name: Anime Hair Clip" in out
    # 整组键值按原始格式直出（分隔符带空格冒号）
    assert "Item Type : Hair Ornament" in out
    assert "Material : Zinc Alloy" in out
    assert "Pattern Type : Flower" in out
    assert "Gender : Women" in out


def test_sectioned_base_detail_row_falls_back_to_description_text() -> None:
    """基础商品没有识别属性表时，base_detail 回退到商品 description 原文。"""
    from services.api.description_generation import _sectioned_description

    sections = [
        {
            "id": "product_info",
            "type": "fields",
            "emoji": "👕",
            "title": "PRODUCT INFORMATION",
            "rows": [
                {"emoji": "🧷", "label": "Base Detail", "source": "base_detail"},
            ],
        }
    ]
    product = {
        "listing_title": "Plain Tee",
        "recognized_attributes": {},
        "description": "This men's t-shirt is a versatile basic top.",
    }
    out = _sectioned_description(sections, product, slot_values={})
    assert "This men's t-shirt is a versatile basic top." in out
