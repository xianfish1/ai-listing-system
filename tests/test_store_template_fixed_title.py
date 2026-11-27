"""店铺模板的「固定标题（可选）」：商品标题为空时兜底，跟随店铺模板（不同店铺各配各的）。

覆盖点：
- 模板保存/更新能存下 fixed_title；
- 商品侧完全没有标题时，建副本用模板固定标题；有标题时仍用商品标题；
- 基础商品导入的默认标题同样兜底；
- 老副本标题为空时，建任务前从所属模板补上（不同模板取各自的）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _template(fixed_title: str = "", **overrides) -> dict:
    payload = {
        "store_name": "HN_POD",
        "template_name": "Kids Tee",
        "product_line": "POD Kids T-Shirt",
        "marketplace_category_path": "Baby & Kids Fashion > Tops > T-Shirts",
        "normal_price": 100,
        "sale_price": 60,
        "sizes": ["130cm", "140cm"],
        "variation_rule": "color_size",
        "sku_template": "{main_sku}-{color}-{size}",
        "listing_mode": "site",
        "fixed_title": fixed_title,
    }
    payload.update(overrides)
    return storage.create_store_template(payload)


def test_store_template_persists_fixed_title() -> None:
    template = _template("Kids Cotton T-Shirt Boy Casual Tee")
    assert template["fixed_title"] == "Kids Cotton T-Shirt Boy Casual Tee"
    assert storage.get_item("store_templates", template["id"])["fixed_title"] == "Kids Cotton T-Shirt Boy Casual Tee"

    updated = storage.update_store_template(template["id"], {"fixed_title": "Girl Casual Tee"})
    assert updated["fixed_title"] == "Girl Casual Tee"
    # 别的字段保存（payload 没带 fixed_title）时不能被抹掉
    updated = storage.update_store_template(template["id"], {"template_name": "Kids Tee v2"})
    assert updated["fixed_title"] == "Girl Casual Tee"


def test_copy_uses_fixed_title_only_when_product_title_is_empty() -> None:
    template = _template("固定标题：Kids Tee")
    now = "2026-11-27T00:00:00+00:00"

    # 商品没有任何标题/名称 → 用模板固定标题
    copy = storage._build_store_listing_copy({"id": "base-1", "group_id": ""}, template, now, {})
    assert copy["listing_title"] == "固定标题：Kids Tee"

    # 商品自己有标题 → 固定标题不覆盖
    copy = storage._build_store_listing_copy(
        {"id": "base-2", "group_id": "", "listing_title": "Product Own Title"}, template, now, {}
    )
    assert copy["listing_title"] == "Product Own Title"


def test_default_listing_title_falls_back_to_fixed_title() -> None:
    template = {"fixed_title": "固定标题 X", "title_suffix": ""}
    assert storage._build_default_listing_title(template, "", "") == "固定标题 X"
    # 有商品名时照旧拼接，不用固定标题
    assert storage._build_default_listing_title(template, "AL0301", "AL0301") == "AL0301 AL0301"


def test_job_build_fills_empty_copy_title_from_its_own_template() -> None:
    template_a = _template("店铺A固定标题")
    template_b = _template("店铺B固定标题", store_name="Oct.3H")
    data = {"store_templates": [template_a, template_b]}
    assert storage._fixed_title_for_copy(data, {"template_id": template_a["id"]}) == "店铺A固定标题"
    assert storage._fixed_title_for_copy(data, {"template_id": template_b["id"]}) == "店铺B固定标题"
    assert storage._fixed_title_for_copy(data, {"template_id": "missing"}) == ""


def test_template_editor_exposes_and_saves_fixed_title() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (root / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="store-template-fixed-title"' in html
    assert "固定标题" in html
    # 保存时带进 payload、编辑时回显、新建时清空
    assert 'fixed_title: $("store-template-fixed-title")?.value.trim() || ""' in script
    assert 'if ($("store-template-fixed-title")) $("store-template-fixed-title").value = template.fixed_title || ""' in script
    assert '"store-template-fixed-title"' in script  # resetInputs 里
