from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "web" / "app.js"
INDEX = ROOT / "apps" / "web" / "index.html"
STYLES = ROOT / "apps" / "web" / "styles.css"


def _function_body(script: str, signature: str) -> str:
    body = script[script.index(signature):]
    return body[: body.index("\n}\n")]


def test_settings_page_has_updates_group_and_panel() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert 'data-settings-group="updates"' in html
    assert ">版本更新</button>" in html
    assert 'data-settings-panel="updates"' in html
    for element in (
        'id="settings-updates-current-version"',
        'id="settings-updates-repo"',
        'id="settings-updates-token"',
        'id="settings-updates-include-prerelease"',
        'id="settings-updates-auto-check"',
        'id="settings-updates-save-btn"',
        'id="settings-updates-check-btn"',
        'id="settings-updates-apply-btn"',
        'id="settings-updates-state"',
        'id="settings-updates-result"',
        'id="settings-updates-latest"',
        'id="settings-updates-notes"',
        'id="settings-release-version"',
        'id="settings-release-bump"',
        'id="settings-release-notes"',
        'id="settings-release-prerelease"',
        'id="settings-release-dry-run"',
        'id="settings-release-publish-btn"',
        'id="settings-release-build-btn"',
        'id="settings-release-status"',
        'id="settings-release-log"',
    ):
        assert element in html


def test_updates_group_is_placed_before_safety() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert html.index('data-settings-group="updates"') < html.index('data-settings-group="safety"')
    assert html.index('data-settings-panel="updates"') < html.index('data-settings-panel="safety"')


def test_updates_panel_has_required_functions() -> None:
    script = APP.read_text(encoding="utf-8")

    for function in (
        "async function loadUpdateStatus",
        "function renderUpdateStatus",
        "async function saveUpdateSettings",
        "async function checkForUpdates",
        "async function applyUpdate",
        "async function publishRelease",
        "async function buildRelease",
        "function renderReleaseJob",
        "async function pollReleaseJob",
        "function suggestNextVersion",
    ):
        assert function in script


def test_updates_panel_uses_documented_endpoints() -> None:
    script = APP.read_text(encoding="utf-8")

    assert 'api("/api/updates/status")' in script
    assert 'api("/api/updates/check"' in script
    assert 'api("/api/updates/apply"' in script
    assert 'api("/api/releases/publish"' in script
    assert 'api("/api/releases/publish/status" + suffix)' in script
    assert 'api("/api/health")' in script


def test_save_update_settings_puts_updates_group_and_blanks_token() -> None:
    script = APP.read_text(encoding="utf-8")
    body = _function_body(script, "async function saveUpdateSettings")

    assert 'api("/api/settings"' in body
    assert 'method: "PUT"' in body
    assert "updates: {" in body
    # token 留空时传空串，后端保留已存的加密值。
    assert 'const token = $("settings-updates-token")?.value || "";' in body
    assert "await loadUpdateStatus()" in body


def test_check_button_disabled_when_repo_not_configured() -> None:
    script = APP.read_text(encoding="utf-8")
    body = _function_body(script, "function renderUpdateStatus")

    assert "checkButton.disabled = !configured" in body
    assert "publishButton.disabled = !configured" in body
    assert "applyButton.disabled = pending ||" in body
    assert "update_available === true" in body


def test_apply_update_confirms_polls_health_then_reloads() -> None:
    script = APP.read_text(encoding="utf-8")
    body = _function_body(script, "async function applyUpdate")

    assert "window.confirm" in body
    assert "waitForUpdateRestart()" in body
    assert "window.location.reload()" in body
    assert "120 秒" in body
    # 重启等待必须观察到 API 先失败再成功，避免命中还没退出的旧进程。
    wait_body = _function_body(script, "async function waitForUpdateRestart")
    assert "downObserved" in wait_body
    assert "UPDATE_HEALTH_POLL_MS" in wait_body


def test_release_publish_and_dry_run_build_flow() -> None:
    script = APP.read_text(encoding="utf-8")

    publish = _function_body(script, "async function publishRelease")
    assert "window.confirm" in publish
    assert "dry_run: dryRun" in publish
    assert "push: true" in publish

    # 次要按钮只构建安装包：dry_run=true 且不推送。
    build = _function_body(script, "async function buildRelease")
    assert "dry_run: true" in build
    assert "push: false" in build

    poll = _function_body(script, "async function pollReleaseJob")
    assert "RELEASE_JOB_POLL_MS" in poll
    assert "renderReleaseJob(job)" in poll
    assert '"failed"' in poll

    render = _function_body(script, "function renderReleaseJob")
    assert "RELEASE_JOB_LOG_TAIL" in render
    assert "logEl.scrollTop = logEl.scrollHeight" in render


def test_bump_select_suggests_next_version() -> None:
    script = APP.read_text(encoding="utf-8")

    assert 'target.id === "settings-release-bump"' in script
    assert "suggestNextVersion" in script
    body = _function_body(script, "function suggestNextVersion")
    for part in ('"major"', '"minor"'):
        assert part in body


def test_settings_view_and_nav_loader_refresh_update_status() -> None:
    script = APP.read_text(encoding="utf-8")

    settings_line = next(line for line in script.splitlines() if line.strip().startswith('"settings": () =>'))
    assert "loadUpdateStatus()" in settings_line
    assert 'settingsNav.dataset.settingsGroup === "updates"' in script


def test_updates_panel_styles_exist() -> None:
    styles = STYLES.read_text(encoding="utf-8")

    for rule in (
        ".updates-version-card",
        ".updates-version",
        ".updates-latest",
        ".updates-latest[hidden]",
        ".updates-latest-title",
        ".updates-notes",
    ):
        assert rule in styles


def test_update_panel_assets_are_cache_bumped() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert "feature=github-updates-v1" in html
    assert html.count("feature=github-updates-v1") >= 2
    assert "app.js?v=" in html


def test_topbar_has_global_update_reminder() -> None:
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # 提醒按钮挂在 topbar（所有视图共用），而不是只在设置页里有。
    topbar = html[html.index('<header class="topbar">'):]
    topbar = topbar[: topbar.index("</header>")]
    assert 'id="update-reminder-btn"' in topbar
    assert 'id="update-reminder-text"' in topbar
    assert " hidden>" in topbar

    for function in (
        "function renderUpdateReminder",
        "async function refreshUpdateReminder",
        "async function handleUpdateReminderClick",
    ):
        assert function in script
    # 缓存过期才补查，避免每次打开页面都打 GitHub。
    assert "UPDATE_REMINDER_STALE_MS" in script
    assert 'target.id === "update-reminder-btn"' in script
    # 设置页状态渲染后要同步顶栏提醒。
    assert "renderUpdateReminder(current)" in _function_body(script, "function renderUpdateStatus")
    # 本工程没有全局 [hidden] 规则，必须显式写。
    assert ".update-reminder[hidden]" in styles
