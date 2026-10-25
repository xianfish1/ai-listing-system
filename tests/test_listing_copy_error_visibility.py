"""测试：副本错误可见性 + 浏览器上下文缺失的明确报错。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "web" / "app.js"
INDEX = ROOT / "apps" / "web" / "index.html"
STYLES = ROOT / "apps" / "web" / "styles.css"
EXECUTOR = ROOT / "scripts" / "mabang_playwright_executor.cjs"
LOGIN = ROOT / "scripts" / "mabang_auto_login.cjs"


def test_copy_editor_shows_action_errors_inside_the_copy() -> None:
    """现场反馈：点「检查」/保存出错时只弹页面顶部提示，副本里看不到。"""

    script = APP.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert 'id="listing-copy-last-run"' in html, "副本里要有「上次执行」一行"
    assert "function setListingCopyEditorStatus(message, { error = false } = {})" in script
    # 错误要写进副本状态行（operationError 是所有编辑器动作的公共出口）
    assert 'if ($("listing-copy-editor-dialog")?.open) setListingCopyEditorStatus(`错误：${message}`, { error: true });' in script
    # 保存失败也要落进副本
    assert "setListingCopyEditorStatus(`保存失败：${error?.message || error}`, { error: true });" in script
    # 点「检查」时带出上次执行结果（失败标红）
    assert "function renderListingCopyLastRun(copy)" in script
    assert "renderListingCopyLastRun(copy);" in script
    assert "listing-copy-last-run--failed" in styles


def test_browser_context_missing_reports_a_clear_retryable_error() -> None:
    """现场 CNSC 任务报过 “Cannot read properties of undefined (reading 'newPage')”：
    浏览器上下文为空时必须是明确的可重试错误，而不是 TypeError。"""

    executor = EXECUTOR.read_text(encoding="utf-8")
    login = LOGIN.read_text(encoding="utf-8")

    assert "function requireBrowserContext(browser) {" in executor
    assert "if (!context) throw new BlockedError('browser_context_missing');" in executor
    # 两处 newPage 都必须走这个 helper
    assert "requireBrowserContext(browser).newPage()" in executor
    assert "browser.contexts()[0].newPage()" not in executor
    assert "'browser_context_missing'," in executor, "应加入可重试错误集合"

    assert "if (!context) {" in login
    assert "browser_context_missing" in login
    assert "browser.contexts()[0];" not in login

    # 界面上要有中文说明
    app = APP.read_text(encoding="utf-8")
    assert re.search(r"browser_context_missing: \"[^\"]*重试", app), "错误文案表要补中文"


def test_copy_editor_warns_and_offers_fill_from_template_for_missing_attributes() -> None:
    """现场：模板里配了「型号」，老副本没继承 → 执行器没值可填、刊登上空。

    现在：编辑器里给提醒 + 「从模板补全缺失属性」按钮。
    2026-10-20 现场要求：副本列表头部的批量「补全缺失属性」入口隐藏，只保留编辑器内入口。
    """

    script = APP.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    assert "function listingCopyTemplateGaps(copy)" in script
    assert "function fillListingCopyMissingAttributes(copyId = \"\")" in script
    assert "从模板补全缺失属性" in script
    assert "templateGapMarkup" in script
    assert 'data-fill-missing-attributes=' in script
    assert 'id="fill-missing-attributes-btn"' not in html, "批量入口已按现场要求隐藏"
    assert "补全缺失属性" not in html, "副本列表头部不再显示补全入口"
    assert '/fill-missing-attributes' in script
    # 只补缺：前端判断与后端同口径（模板有值 + 副本空值）
    gaps = script[script.index("function listingCopyTemplateGaps(copy)"):]
    gaps = gaps[: gaps.index("\n}\n")]
    assert "listingCopyAttributeBlank(templateValues[fieldId])" in gaps
    assert "listingCopyAttributeBlank(current[fieldId])" in gaps
