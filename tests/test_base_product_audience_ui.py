"""商品维护：卡片人群标记（男童 / 女童 / 男装 / 女装）+ 分类行的人群快速选择。

和 test_base_product_lifecycle_ui.py 一样是文本级契约：人群只看标题关键词，
卡片上挂在主 SKU 行最右侧，分类行里作为二级分类放在「未分类」前面；
样式和静态资源版本必须同步到位（改完 app.js/styles.css 不 bump ?v=，旧标签页会继续跑旧代码）。
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"
STYLES = ROOT / "apps" / "web" / "styles.css"


def test_app_js_declares_the_four_audience_buckets() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "BASE_PRODUCT_AUDIENCE_LABELS" in script
    assert 'boys: "男童"' in script
    assert 'girls: "女童"' in script
    assert 'men: "男装"' in script
    assert 'women: "女装"' in script
    assert 'BASE_PRODUCT_AUDIENCE_ORDER = ["boys", "girls", "men", "women"]' in script
    # 分类行只放男童/女童：男装/女装已经在二级分类里由「上架期望类目」占用，
    # 同名不同数会让人分不清点哪个（卡片徽标仍是四个人群）。
    assert 'BASE_PRODUCT_AUDIENCE_CHIP_ORDER = ["boys", "girls"]' in script


def test_audience_comes_from_the_title_not_from_attributes() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "function baseProductAudienceKey(product)" in script
    # 只看标题（标题 → 生成标题 → 商品名顺序回退），不读 recognized_attributes。
    assert "product?.listing_title || product?.generated_title" in script
    assert "BASE_PRODUCT_AUDIENCE_PATTERNS" in script
    # 两个关键词都出现时取最先出现的那个。
    assert "match.index < hitIndex" in script


def test_audience_is_a_second_level_filter_chip() -> None:
    script = APP.read_text(encoding="utf-8")

    # 和阶段（stage:）、商品类目共用同一个单选筛选状态，人群 key 带 audience: 前缀。
    assert '`audience:${audience}`' in script
    assert 'value.startsWith("audience:")' in script
    # 二级分类第一组渲染在商品类目 chip 前面。
    assert "chip--audience chip--audience-${audience}" in script
    assert "function baseProductAudienceLabel(audience)" in script


def test_card_shows_the_badge_at_the_right_of_the_sku_line() -> None:
    script = APP.read_text(encoding="utf-8")

    assert 'class="product-card-sku"' in script
    assert "audience-badge audience-badge--${audienceKey}" in script
    # 阶段徽标不受影响。
    assert "stage-badge stage-badge--${stageKey}" in script


def test_audience_styles_and_asset_versions_exist() -> None:
    styles = STYLES.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    for audience in ("boys", "girls", "men", "women"):
        assert f".chip--audience-{audience}" in styles
        assert f".audience-badge--{audience}" in styles
    assert ".chip--audience.chip--active" in styles
    assert ".product-card-body .product-card-sku" in styles
    assert ".product-card-body .audience-badge" in styles

    # 静态资源版本：styles.css + app.js 各一处，旧标签页才会拉到新代码。
    assert html.count("base-product-audience-v1") == 2
