from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "web" / "app.js"
INDEX = ROOT / "apps" / "web" / "index.html"
STYLES = ROOT / "apps" / "web" / "styles.css"


def test_settings_page_has_lan_access_group_and_panel() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert 'data-settings-group="network"' in html
    assert ">局域网访问</button>" in html
    assert 'data-settings-panel="network"' in html
    for element in (
        'id="settings-network-lan-enabled"',
        'id="settings-network-port"',
        'id="settings-network-bind"',
        'id="settings-network-urls"',
        'id="settings-network-refresh-btn"',
        'id="settings-network-firewall-btn"',
        'id="settings-network-apply-btn"',
        'id="settings-network-state"',
    ):
        assert element in html


def test_lan_panel_has_render_and_apply_functions() -> None:
    script = APP.read_text(encoding="utf-8")

    for function in (
        "async function loadNetworkSettings",
        "function renderNetworkSettings",
        "async function applyNetworkSettings",
        "async function openNetworkFirewall",
        "function copyTextToClipboard",
        "function renderNetworkUnavailable",
    ):
        assert function in script

    assert '"/api/settings/network"' in script
    assert '`/api/settings/network${suffix}`' in script
    assert 'method: "PUT"' in script
    assert '/api/settings/network/firewall' in script
    assert "loadNetworkSettings({ refresh: true })" in script
    assert "重启一次 API" in script
    assert "port_open" in script
    assert "port_allowed_by" in script
    # 局域网开关只走专用接口，普通“保存设置”不会触发重绑。
    save_body = script[script.index("async function saveRuntimeSettings"):]
    save_body = save_body[: save_body.index("\n}\n")]
    assert "network:" not in save_body


def test_settings_view_loader_includes_network_status() -> None:
    script = APP.read_text(encoding="utf-8")

    assert '"settings": () => [loadRuntimeSettings(), loadNetworkSettings()]' in script


def test_lan_panel_styles_exist() -> None:
    styles = STYLES.read_text(encoding="utf-8")

    assert ".lan-address-row" in styles
    assert ".lan-address-url" in styles
    assert ".lan-address-actions" in styles
    assert ".lan-hint-list" in styles
