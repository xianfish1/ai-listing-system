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


def test_placeholder_sku_title_is_treated_as_empty() -> None:
    """现场（2026-11-27）：商品没标题时旧逻辑拼出「YSC961 YSC961」，看着有值其实不是标题。

    模板配了固定标题时，这种占位标题必须按「空」处理，副本上架用固定标题。
    """
    template = _template("固定标题：Kids Tee")
    now = "2026-11-27T00:00:00+00:00"

    for base_title in ("YSC961", "YSC961 YSC961"):
        copy = storage._build_store_listing_copy(
            {"id": "base", "group_id": "YSC961", "listing_title": base_title}, template, now, {}
        )
        assert copy["listing_title"] == "固定标题：Kids Tee", base_title

    # 没有固定标题时保持老行为（拼商品名/SKU），不会因为占位判断变成空标题
    plain = _template("")
    copy = storage._build_store_listing_copy(
        {"id": "base", "group_id": "YSC961", "listing_title": "YSC961 YSC961"}, plain, now, {}
    )
    assert copy["listing_title"] == "YSC961 YSC961"

    # 建任务阶段只拿得到副本：纯 SKU 重复的标题同样算占位
    assert storage._listing_title_is_placeholder("YSC961 YSC961", {"group_id": "YSC961"}, {}) is True
    assert storage._listing_title_is_placeholder("Kids Cotton Tee", {"group_id": "YSC961"}, {}) is False


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


def test_saving_template_fixed_title_repairs_existing_placeholder_copies(tmp_path: Path) -> None:
    """保存模板的固定标题后，该模板名下标题是「SKU 占位」的副本要一起换掉。

    现场（2026-11-27）：运营在模板里填了固定标题，回头看副本列表标题还是
    「YSC961 YSC961」，以为功能没生效。
    """
    folder = tmp_path / "YSC961 童装"
    folder.mkdir()
    (folder / "main.jpg").write_bytes(b"image")
    (folder / "Black.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Kids T-Shirt"})
    template = _template("")

    created = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"]
    copy_id = created[0]["id"]
    storage.update_store_listing_copy(copy_id, {"title": "YSC961 YSC961"})

    updated = storage.update_store_template(template["id"], {"fixed_title": "Kids Cotton Tee 固 定"})
    assert updated["fixed_title"] == "Kids Cotton Tee 固 定"
    copy = next(item for item in storage.list_store_listing_copies() if item["id"] == copy_id)
    assert copy["listing_title"] == "Kids Cotton Tee 固 定"

    # 操作员手改过的真标题不受影响
    storage.update_store_listing_copy(copy_id, {"title": "Operator Real Title"})
    storage.update_store_template(template["id"], {"fixed_title": "Another Fixed Title"})
    copy = next(item for item in storage.list_store_listing_copies() if item["id"] == copy_id)
    assert copy["listing_title"] == "Operator Real Title"
