"""基础商品维护：标题字符提醒、AI 标题入口隐藏、左侧副本列表布局。"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "web" / "app.js"
INDEX = ROOT / "apps" / "web" / "index.html"
STYLES = ROOT / "apps" / "web" / "styles.css"


def test_base_title_hint_and_hidden_ai_entry() -> None:
    """基础商品没有站点，标题下方按最严站点给字符提醒；AI 标题暂时隐藏。"""

    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="base-maintenance-title-hint"' in html
    # AI 标题入口保留节点但默认隐藏，恢复时去掉 hidden 即可。
    assert 'id="base-maintenance-audience"' in html and 'hidden>' in html
    assert 'id="base-maintenance-ai-title"' in html and 'class="secondary-button" hidden' in html
    assert "AI 标题暂时停用" in html

    assert "function updateBaseTitleLengthHint()" in script
    assert 'const used = String(input.value || "").length;' in script
    # 打开弹窗、AI 识别回填、手动输入三处都要刷新。
    assert script.count("updateBaseTitleLengthHint();") >= 3
    assert 'target.id === "base-maintenance-title-input"' in script
    # 之前容易误解的兜底说明已移除。
    assert "标题为空才自动拼" not in script


def test_base_copy_sidebar_uses_stable_two_line_layout() -> None:
    """窄侧栏里店名和状态不再互相挤压，状态文字不会被拆成两行。"""

    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert "maintenance-summary-copy-body" in script
    assert "maintenance-summary-copy-name" in script
    assert "maintenance-summary-copy-status" in script
    # 旧的三段式 flex 行（店名/· 状态）已移除。
    assert '<span class="muted">· ${escapeHtml(label)}</span>' not in script

    assert ".maintenance-summary-copies {\n  display: grid;\n  gap: 6px;\n  padding: 0;" in styles
    assert ".maintenance-summary-copy-body" in styles
    assert ".maintenance-summary-copy-status {\n  white-space: nowrap;" in styles
    assert "align-items: flex-start;" in styles
