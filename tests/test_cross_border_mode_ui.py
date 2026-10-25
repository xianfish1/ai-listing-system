"""跨境模式界面开关的静态契约：设置页入口、列表/选择器/编辑器的过滤与隐藏、保存链路。

只做源码契约断言（与仓库其余 UI 测试同风格），不启动浏览器。
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "web" / "app.js"
INDEX = ROOT / "apps" / "web" / "index.html"


def _function_source(source: str, header: str) -> str:
    start = source.index(header)
    return source[start: source.index("\n}\n", start)]


def test_settings_page_has_cross_border_toggle() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert 'data-settings-group="display"' in html, "设置导航新增「界面显示」分组"
    assert "界面显示" in html
    assert 'id="settings-cross-border-mode"' in html, "设置页有跨境模式开关"
    assert "跨境模式（CNSC 全球商品）" in html
    # app.js 静态直出，改动必须带新的缓存参数，否则操作员浏览器仍加载旧脚本。
    assert "feature=cross-border-mode-v1" in html


def test_app_has_cross_border_helpers() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "function crossBorderModeEnabled()" in source
    assert "state.runtimeConfiguration?.display?.cross_border_enabled" in source
    assert "function applyCrossBorderUi()" in source


def test_template_table_and_cards_filter_to_local_only() -> None:
    source = APP.read_text(encoding="utf-8")

    table = source[source.index("function renderStoreTemplateTable()"): source.index("function renderStoreTemplateCards()")]
    assert "crossBorderModeEnabled()" in table, "模板表格按跨境模式过滤"
    assert "!isCNSCListingCopy(item)" in table, "关闭时只渲染本土模板"
    assert "没有本土店铺模板。" in table, "关闭后的空态文案点明本土-only"

    cards = _function_source(source, "function renderStoreTemplateCards()")
    assert "crossBorderModeEnabled()" in cards, "模板卡片按跨境模式过滤"
    assert "没有本土店铺模板。" in cards


def test_listing_copies_and_select_all_respect_mode() -> None:
    source = APP.read_text(encoding="utf-8")

    selectable = _function_source(source, "function selectableListingCopies()")
    assert "isCNSCListingCopy(copy)" in selectable, "全选新建只覆盖可见的本土副本"

    copies = source[source.index("function renderStoreListingCopies()"): source.index("function toggleSelectAllListingCopies()")]
    assert "crossBorderModeEnabled()" in copies, "副本列表按跨境模式过滤"
    assert "暂无本土店铺刊登副本。" in copies


def test_copy_picker_forces_local_mode_when_disabled() -> None:
    source = APP.read_text(encoding="utf-8")

    setter = _function_source(source, "function setCopyTemplatePickerMode(mode)")
    assert "if (mode === \"cnsc\" && !crossBorderModeEnabled())" in setter, "关闭时不允许切到跨境卡片"

    picker = _function_source(source, "function renderCopyTemplatePicker(query")
    assert 'toggleAttribute("hidden", !crossBorderModeEnabled())' in picker, "跨境卡片随模式隐藏"
    assert "copyTemplatePickerMode() : \"site\"" in picker, "关闭时强制本土模式"


def test_template_editor_hides_cnsc_listing_mode_option() -> None:
    source = APP.read_text(encoding="utf-8")

    fn = _function_source(source, "function updateCnscTemplateVisibility()")
    assert 'option[value="cnsc_global"]' in fn, "刊登模式下拉的 CNSC 选项用 hidden 控制（不删除，保护已打开模板）"


def test_base_maintenance_hides_cnsc_chrome() -> None:
    source = APP.read_text(encoding="utf-8")

    # 「站点价格」区（产品类目 / 尺码表 / 站点价格表）是跨境专属：关闭时整节隐藏。
    # applyCrossBorderUi 与 renderCnscSiteOrderDisplays（维护弹窗每次打开都会走）
    # 两处都要切，另一标签页改了开关也能在下一次打开弹窗时生效。
    assert '$("maintenance-section-prices")?.toggleAttribute("hidden", !enabled)' in source
    assert '$("maintenance-section-prices")?.toggleAttribute("hidden", !cbEnabled)' in source
    assert 'document.querySelector(".maintenance-note--cnsc")?.toggleAttribute("hidden", !cbEnabled)' in source
    assert '$("base-price-cnsc-site-order")?.toggleAttribute("hidden", !cbEnabled)' in source
    assert '$("base-price-cnsc-site-order-display")?.toggleAttribute("hidden", !cbEnabled)' in source

    # 设置页的灰字说明与实际行为一致：整节隐藏，而不是只藏说明和按钮。
    html = INDEX.read_text(encoding="utf-8")
    assert "「站点价格」区整个隐藏" in html


def test_settings_save_and_render_wire_display_group() -> None:
    source = APP.read_text(encoding="utf-8")

    assert 'cross_border_enabled: $("settings-cross-border-mode")?.checked !== false' in source, "保存设置携带 display.cross_border_enabled"
    assert 'const crossBorderToggle = $("settings-cross-border-mode");' in source, "设置页回显跨境模式开关"
    assert "applyCrossBorderUi();" in source
