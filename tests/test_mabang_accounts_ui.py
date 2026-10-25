from __future__ import annotations

from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "apps" / "web"
INDEX = WEB / "index.html"
APP = WEB / "app.js"


def test_mabang_account_list_is_present_and_wired():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    # 账号列表容器与入口
    assert 'id="settings-mabang-account-list"' in html
    assert 'id="add-mabang-account-btn"' in html
    # 新增/编辑对话框
    for control in [
        "mabang-account-dialog",
        "mabang-account-name",
        "mabang-account-login",
        "mabang-account-password",
        "mabang-account-enabled",
        "save-mabang-account-btn",
    ]:
        assert f'id="{control}"' in html, control
    # 端口与数据目录由系统分配，界面上不提供配置入口。
    assert 'id="mabang-account-cdp-port"' not in html
    assert 'id="mabang-account-profile-dir"' not in html
    assert "由系统按账号自动分配" in html
    # 说明文字用 callout 放在字段网格之外；塞进 form-grid 会被当成一格挤成一条。
    assert 'class="muted small dialog-note"' in html
    dialog = html[html.index('id="mabang-account-dialog"'):html.index('id="description-profile-dialog"')]
    grid = dialog[dialog.index('class="form-grid compact-form"'):]
    assert "dialog-note" not in grid[: grid.index("</div>")]
    # 同一分组里的第二块内容要有分隔线，标题用页面既有的 h2 尺寸（h3 会掉到浏览器默认字号）。
    assert "settings-subsection" in html
    assert "<h2>马帮账号</h2>" in html

    styles = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".settings-subsection" in styles
    assert "#mabang-account-dialog .form-grid.compact-form" in styles
    assert ".dialog-note" in styles
    assert 'id="mabang-account-password" type="password"' in html
    assert 'autocomplete="new-password"' in html

    # 脚本接线
    for fn in [
        "function renderMabangAccounts",
        "async function loadMabangAccounts",
        "function openMabangAccountDialog",
        "function openMabangAccountEditor",
        "async function saveMabangAccount",
    ]:
        assert fn in script, fn
    assert "/api/mabang/accounts" in script
    assert "data-edit-mabang-account" in script and "data-delete-mabang-account" in script
    assert "state.mabangAccounts" in script


def test_mabang_account_form_keeps_the_legacy_single_account_binding():
    """新增账号列表是叠加的：单账号时代的默认绑定表单必须仍在，登录门禁还靠它。"""

    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    for control in ["settings-mabang-account", "settings-mabang-password", "settings-mabang-login-status"]:
        assert f'id="{control}"' in html
    assert "自动登录账号" in html and "自动登录密码" in html

    render = script[script.index("function renderRuntimeSettings"):script.index("function formatStorageBytes")]
    assert "renderMabangAccounts();" in render
    assert "config.mabang?.login_account" in render


def test_page_import_picks_an_account_and_shows_the_window():
    """页面导入必须选账号（决定用哪套登录态），并在这一步临时显示窗口。"""

    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="mabang-page-import-account"' in html
    assert "mabang-import-account-field" in html
    # 创建的导入 run 要带上所选账号
    assert 'account_id: $("mabang-page-import-account")' in script
    assert "syncMabangPageImportAccountOptions" in script
    # 换账号要重建 run（换账号 = 换一套登录态）
    assert "startMabangPageImportRun" in script
    # 打开浏览器这一步强制显示窗口；采集完成后服务端恢复无头
    assert "headless: false" in script
    assert "恢复无头" in script


def test_only_one_module_level_state_binding_exists():
    """模块级 state 只能声明一次。

    局部变量一旦也叫 state，就会遮蔽它；如果同作用域里在声明前读过 state，
    整个「设置与 Worker 状态」都会以 "Cannot access 'state' before initialization" 失败。
    """

    script = APP.read_text(encoding="utf-8")
    assert script.count("const state = ") == 1


def test_web_assets_cache_busting_includes_the_account_feature():
    html = INDEX.read_text(encoding="utf-8")
    assert html.count("feature=mabang-account-list-v1") == 2
