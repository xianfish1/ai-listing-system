"""商品维护「分类」行四段阶段分类（首次导入 / 未复制 / 待执行 / 已完成）静态契约。

和 test_base_product_category_ui.py 一样是文本级契约：HTML 必须挂上筛选行，
app.js 必须按服务端 lifecycle_stage 渲染阶段 chip 和卡片徽标，样式和静态资源版本
必须同步到位（改完 app.js/styles.css 不 bump ?v=，旧标签页会继续跑旧代码）。
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"
STYLES = ROOT / "apps" / "web" / "styles.css"
STORAGE = ROOT / "services" / "api" / "storage.py"
MAIN = ROOT / "services" / "api" / "main.py"


def test_app_js_declares_the_four_lifecycle_stages() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "BASE_PRODUCT_STAGE_LABELS" in script
    assert 'imported: "首次导入"' in script
    assert 'uncopied: "未复制"' in script
    assert 'pending: "待执行"' in script
    assert 'completed: "已完成"' in script
    assert 'BASE_PRODUCT_STAGE_ORDER = ["imported", "uncopied", "pending", "completed"]' in script


def test_app_js_reads_server_stage_and_keeps_a_fallback() -> None:
    script = APP.read_text(encoding="utf-8")

    # 服务端 lifecycle_stage 优先，旧后端（没这个字段）时按 status + 副本数降级。
    assert "function baseProductStage(product)" in script
    assert "product.lifecycle_stage" in script
    assert "product.listing_copy_count" in script
    assert "product.last_edited_at" in script


def test_app_js_renders_stage_chips_in_the_category_row() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "function baseProductStageKey(stage)" in script
    assert "function baseProductFilterKey(product)" in script
    assert "function baseProductFilterLabel(key)" in script
    # 阶段 chip 带 stage: 前缀，和商品类目 chip 共用同一个单选筛选状态。
    assert "`stage:${stage}`" in script
    assert "data-base-category-filter=\"${escapeHtml(key)}\"" in script
    assert "chip chip--stage chip--stage-${stage}" in script
    # 两个维度之间有分隔符，卡片上还有一枚阶段徽标。
    assert "chip-divider" in script
    assert "stage-badge stage-badge--${stageKey}" in script
    # 空列表提示要说清是哪个分类（含阶段）没有商品。
    assert "baseProductFilterLabel(state.baseProductCategoryFilter)" in script


def test_filter_row_and_asset_versions_are_wired_in_html() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert 'id="base-product-filter-category"' in html
    assert "阶段 + 商品类目" in html
    assert "base-product-stage-v1" in html
    assert html.count("base-product-stage-v1") == 2  # styles.css + app.js


def test_stage_styles_exist() -> None:
    styles = STYLES.read_text(encoding="utf-8")

    assert ".chip--stage-imported" in styles
    assert ".chip--stage-uncopied" in styles
    assert ".chip--stage-pending" in styles
    assert ".chip--stage-completed" in styles
    assert ".chip--stage.chip--active" in styles
    assert ".chip-divider" in styles
    assert ".product-card-body .stage-badge" in styles


def test_storage_derives_the_stage_and_marks_operator_edits() -> None:
    storage_source = STORAGE.read_text(encoding="utf-8")
    main_source = MAIN.read_text(encoding="utf-8")

    assert "def _derive_lifecycle_stage(" in storage_source
    assert 'product["lifecycle_stage"] = _derive_lifecycle_stage(product, product_copies)' in storage_source
    assert 'base["last_edited_at"] = now' in storage_source
    # 操作员保存路径标记编辑；导入时的 AI 识别不标。
    assert "mark_edited=True" in main_source
    assert "mark_edited=mark_edited" in main_source
