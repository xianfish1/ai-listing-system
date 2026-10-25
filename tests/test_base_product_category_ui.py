"""Static UI contract for the base-product product-category picker and quick templates.

These are text-level contracts (like test_store_template_workflow_ui.py): the
editor HTML must expose the controls and app.js must wire them to the merged
category API and the quick-template endpoints.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"
STYLES = ROOT / "apps" / "web" / "styles.css"


def test_base_product_maintenance_has_category_picker_controls():
    html = INDEX.read_text(encoding="utf-8")
    for control in [
        "base-maintenance-category-path",
        "base-maintenance-category-id",
        "base-maintenance-category-store",
        "open-base-category-dialog",
        "clear-base-category-btn",
        "base-category-dialog",
        "base-category-search",
        "base-category-empty",
        "base-category-picker-status",
        "category-cascader-cols",
        "base-category-breadcrumbs",
    ]:
        assert f'id="{control}"' in html
    # Three-column cascading layout: three col containers each with a list.
    assert html.count('data-cascader-depth="') == 3
    assert html.count("category-cascader-col-head") == 3
    assert html.count('data-cascader-list="') == 3
    # The legacy flat results container is gone.
    assert 'id="base-category-results"' not in html


def test_base_product_maintenance_has_quick_category_template_toolbar():
    html = INDEX.read_text(encoding="utf-8")
    for control in [
        "base-category-template-select",
        "base-category-template-save",
        "base-category-template-delete",
        "base-category-template-status",
    ]:
        assert f'id="{control}"' in html


def test_base_product_category_wiring_in_script():
    script = APP.read_text(encoding="utf-8")
    for symbol in [
        "renderBaseMaintenanceCategoryInput",
        "baseProductCategorySelection",
        "openBaseCategoryDialog",
        "ensureBaseCategoryIndex",
        "renderCascaderColumn",
        "childrenOfBase",
        "onCascaderItemClick",
        "onBaseCategoryItemActivated",
        "selectBaseCascaderLeaf",
        "renderCascaderBreadcrumbs",
        "goCascaderCrumb",
        "searchBaseCategories",
        "clearBaseProductCategory",
        "loadBaseCategoryQuickTemplates",
        "saveCurrentBaseCategoryAsQuickTemplate",
        "deleteBaseCategoryQuickTemplate",
        "applyBaseCategoryQuickTemplate",
    ]:
        assert symbol in script
    # Category tree is loaded from a local static JSON, no per-click API calls.
    assert "BASE_CATEGORY_DATA_URL" in script
    assert "/data/marketplace-categories.json" in script
    assert "api(`/api/marketplace-categories/children?" not in script
    # Legacy async loader removed.
    assert "loadCascaderColumn" not in script
    assert 'api("/api/category-quick-templates"' in script
    assert "api(`/api/category-quick-templates/${encodeURIComponent(templateId)}`" in script
    # 「存为模板」带上当前类目属性值；套模板时回填它们（只存类目=旧行为，会丢已填值）。
    assert "attribute_values: attributeValues" in script
    assert "template.attribute_values" in script
    assert "state.editingBaseProductAttributeValues = { ...templateValues }" in script
    # 老模板（只存类目）会提示用同名「存为模板」补存属性值。
    assert "该模板未保存属性值" in script
    assert "用同名「存为模板」即可补上" in script
    # Old flat picker helpers removed.
    assert "loadMergedBaseCategories" not in script
    assert "selectMergedBaseCategory" not in script


def test_base_product_save_payload_includes_product_category():
    script = APP.read_text(encoding="utf-8")
    assert "product_category: state.editingBaseProductCategory || null," in script


def test_base_product_card_renders_category_line():
    script = APP.read_text(encoding="utf-8")
    assert "product_category.path || product_category.path_zh" in script or "product_category &&" in script
    assert "类目：" in script


def test_base_product_category_dialog_has_empty_state_guidance():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    assert "没有找到匹配的马帮类目" in html
    assert "跨店铺合并去重" in html
    # Cascader hint copy: guided search + per-column status lines.
    assert "跟马帮一样逐级选择" in html
    assert "搜索类目名称或编号" in html
    assert "category-cascader-item" in script


def test_quick_template_toolbar_style_present():
    css = STYLES.read_text(encoding="utf-8")
    assert ".category-quick-toolbar" in css
    assert ".marketplace-category-picker.inline-picker" in css
    assert ".category-cascader-cols" in css
    assert ".category-cascader-item" in css
    assert ".category-cascader-breadcrumbs" in css


def test_copy_flow_reports_category_source():
    script = APP.read_text(encoding="utf-8")
    # Rule: base product's category wins when filled, template category is the
    # default when the product has none — the copy result tells the operator which.
    assert "category_source" in script
    assert "按商品所选类目" in script
    assert "按店铺模板默认类目" in script
    # The old "mismatch vs template" hint is gone (product category now overrides).
    assert "以店铺模板类目为准" not in script


def test_category_block_lives_inside_price_collapsible_not_meta():
    html = INDEX.read_text(encoding="utf-8")
    meta_end = html.find('id="maintenance-section-images"')
    meta_form = html[html.find('id="maintenance-section-meta"'):meta_end]
    # 产品类目不应再放在 META 商品信息表单里。
    assert "产品类目" not in meta_form
    collapsible = html.find('id="base-price-collapsible"')
    category_path = html.find('id="base-maintenance-category-path"')
    toolbar = html.find('id="base-category-template-select"')
    # 类目区在站点价格可收起容器内部，因此会随「收起/展开站点价格区」一起收起。
    assert collapsible != -1 and category_path > collapsible and toolbar > collapsible
    # 类目区需出现在价格表格之前（紧邻价格录入）。
    price_table = html.find('id="base-price-color-table"')
    assert category_path < price_table and toolbar < price_table


def test_local_category_data_file_present():
    import json
    data_file = ROOT / "apps" / "web" / "data" / "marketplace-categories.json"
    assert data_file.is_file(), "exported category tree JSON missing — re-run the export"
    payload = json.loads(data_file.read_text(encoding="utf-8"))
    assert payload["count"] > 2000
    assert len(payload["nodes"]) == payload["count"]
    sample = payload["nodes"][0]
    for key in ("id", "pid", "zh", "en", "leaf"):
        assert key in sample


def test_root_column_primary_categories_with_show_all():
    script = APP.read_text(encoding="utf-8")
    # Root column collapses to a few primary categories with a show-all toggle.
    assert "BASE_CASCADER_PRIMARY_NAMES" in script
    assert '"男装"' in script and '"女装"' in script and '"婴儿和儿童时尚"' in script
    assert "data-cascader-show-all" in script or "cascaderShowAll" in script
    assert "baseCategoryShowAll" in script
    assert "显示全部" in script
    css = STYLES.read_text(encoding="utf-8")
    assert ".category-cascader-more" in css
