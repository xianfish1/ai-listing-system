"""基础商品批量「重新识别标题 / 详情 / 颜色图」的静态契约。

按钮挂在批量操作栏：勾选（含「全选当前列表」）后出现，只对勾选商品逐个重跑。
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"
STYLES = ROOT / "apps" / "web" / "styles.css"


def test_batch_bar_exposes_three_retry_buttons() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert 'id="base-product-batch-bar"' in html
    assert 'data-base-batch-action="retry-title"' in html
    assert 'data-base-batch-action="retry-description"' in html
    assert 'data-base-batch-action="retry-colors"' in html
    assert "重新识别标题" in html
    assert "重新识别详情" in html
    assert "重新识别颜色图" in html


def test_app_js_maps_actions_to_aspects_and_calls_the_endpoint() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "BASE_PRODUCT_RETRY_ASPECTS" in script
    assert '"retry-title": { aspect: "title", label: "标题" }' in script
    assert '"retry-description": { aspect: "description", label: "详情" }' in script
    assert '"retry-colors": { aspect: "colors", label: "颜色图" }' in script
    assert "function retryCheckedBaseProducts(aspect, label)" in script
    assert "regenerate-listing" in script
    assert "body: JSON.stringify(body)" in script
    # 勾选（含全选）是唯一来源；串行执行并显示进度/失败明细。
    assert "state.selectedBaseProducts" in script
    assert "products.length" in script
    assert "index + 1" in script
    assert "setBaseProductRetryBusy(true)" in script


def test_retry_title_exposes_a_skill_selector() -> None:
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert 'id="base-retry-skill"' in html
    assert "标题规则：自动匹配（默认）" in html
    assert "baseRetrySkills" in script
    assert 'api("/api/title-skills")' in script
    assert "selectedBaseRetrySkillId()" in script
    # 选了规则就强制指定，不再被商品范围推断挡掉。
    assert "skill_id: skillId" in script
    assert "force_skill: true" in script
    assert ".base-retry-skill" in styles
    assert html.count("feature=retry-title-skill-v1") == 2


def test_retry_buttons_are_disabled_while_running() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "baseProductRetryRunning" in script
    assert "button.disabled = busy" in script
    assert 'button.classList.toggle("chip--busy", busy)' in script


def test_rerecognize_styles_and_cache_version_are_wired() -> None:
    styles = STYLES.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    assert ".base-product-batch-divider" in styles
    assert ".base-product-batch-label" in styles
    assert ".chip.chip--ai" in styles
    assert ".chip.chip--busy" in styles
    # app.js / styles.css 的 ?v= 必须同步 bump，否则旧标签页会继续跑旧代码。
    assert html.count("feature=base-product-rerecognize-v1") == 2
