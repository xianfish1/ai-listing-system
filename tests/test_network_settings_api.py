from __future__ import annotations

import http.client
import json
import threading

import pytest

from services.api import main, network_access, settings, storage
from services.api.main import ApiHandler


@pytest.fixture()
def api_port(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "storage" / "exports")
    monkeypatch.setattr(settings, "SCREENSHOT_DIR", tmp_path / "storage" / "screenshots")
    monkeypatch.setattr(settings, "LOG_DIR", tmp_path / "storage" / "logs")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")
    monkeypatch.setattr(settings, "NAS_ASSET_ROOT", tmp_path / "nas")
    monkeypatch.setattr(settings, "API_HOST", "127.0.0.1")
    monkeypatch.setattr(storage, "_STORE_CACHE", {"key": None, "data": None})
    monkeypatch.setattr(storage, "_FILE_PARSE_CACHE", {})
    monkeypatch.setattr(storage, "_MAIN_LAST_BYTES", {})
    monkeypatch.setattr(storage, "_SPLIT_LAST_BYTES", {})
    monkeypatch.setattr(storage, "_SANITIZED_FILE_STATES", {})
    storage.ensure_dirs()
    storage.load_store()

    monkeypatch.setattr(main, "_reconcile_worker_slots", lambda previous, saved: {})
    main._invalidate_get_response_cache()

    server = main.ApiServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(main, "_ACTIVE_SERVER", server, raising=False)
    monkeypatch.setattr(main, "_ACTIVE_BIND_HOST", "127.0.0.1", raising=False)
    monkeypatch.setattr(main, "_ACTIVE_BIND_PORT", server.server_port, raising=False)
    monkeypatch.setattr(
        network_access,
        "list_lan_addresses",
        lambda port: [
            {
                "ip": "192.168.1.5",
                "url": f"http://192.168.1.5:{port}",
                "interface": "Ethernet",
                "kind": "private",
                "rank": 0,
            }
        ],
    )
    monkeypatch.setattr(
        network_access,
        "firewall_status",
        lambda port: {
            "supported": True,
            "rule_exists": True,
            "rule_name": f"AI Listing System API (LAN {port})",
            "port": port,
            "is_admin": True,
            "message": "",
        },
    )
    monkeypatch.setattr(network_access, "hostname_url", lambda port: "")
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(port: int, method: str, path: str, payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        data = json.loads(raw.decode("utf-8")) if raw else {}
        return response.status, data
    finally:
        conn.close()


def test_network_status_endpoint_reports_binding_and_lan_addresses(api_port) -> None:
    status, payload = _request(api_port, "GET", "/api/settings/network?refresh=1")

    assert status == 200
    assert payload["enabled"] is False
    assert payload["active"] is False
    assert payload["active_bind_host"] == "127.0.0.1"
    assert payload["port"] == api_port
    assert payload["addresses"][0]["url"] == f"http://192.168.1.5:{api_port}"
    assert payload["addresses"][0]["reachable"] is False
    assert payload["firewall"]["rule_exists"] is True
    assert payload["server"]["protocol"] == settings.API_HTTP_PROTOCOL


def test_network_toggle_persists_setting_and_schedules_live_rebind(api_port, monkeypatch) -> None:
    scheduled: list[str] = []

    def fake_schedule(host: str) -> bool:
        scheduled.append(host)
        return True

    monkeypatch.setattr(main, "_schedule_network_rebind", fake_schedule)

    status, payload = _request(api_port, "PUT", "/api/settings/network", {"lan_enabled": True})

    assert status == 200
    assert payload["ok"] is True
    assert payload["rebind_scheduled"] is True
    assert payload["enabled"] is True
    assert scheduled == ["0.0.0.0"]
    assert any("正在切换" in hint for hint in payload["hints"])

    status, config = _request(api_port, "GET", "/api/settings")
    assert status == 200
    assert config["network"]["lan_enabled"] is True

    status, payload = _request(api_port, "GET", "/api/settings/network?refresh=1")
    assert status == 200
    assert payload["configured_enabled"] is True
    assert payload["enabled"] is True
    assert payload["active"] is False


def test_network_toggle_rejects_non_boolean_and_keeps_previous_value(api_port, monkeypatch) -> None:
    monkeypatch.setattr(main, "_schedule_network_rebind", lambda host: True)

    status, payload = _request(api_port, "PUT", "/api/settings/network", {"lan_enabled": "maybe"})

    assert status == 422
    assert "布尔" in payload["error"]

    status, config = _request(api_port, "GET", "/api/settings")
    assert status == 200
    assert config["network"]["lan_enabled"] is False


def test_firewall_endpoint_delegates_to_windows_helper(api_port, monkeypatch) -> None:
    seen: list[int] = []

    def fake_ensure(rule_port: int):
        seen.append(rule_port)
        return {"ok": True, "message": "已开放防火墙端口。", "port": rule_port}

    monkeypatch.setattr(network_access, "ensure_firewall_rule", fake_ensure)

    status, payload = _request(api_port, "POST", "/api/settings/network/firewall", {})

    assert status == 200
    assert payload["ok"] is True
    assert payload["message"] == "已开放防火墙端口。"
    assert payload["firewall"]["rule_exists"] is True
    assert seen == [api_port]


def test_get_cache_avoids_repeated_json_serialization(api_port, monkeypatch) -> None:
    real_dumps = main.json.dumps
    calls: list[int] = []

    def counting_dumps(*args, **kwargs):
        calls.append(1)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(main.json, "dumps", counting_dumps)

    status, payload = _request(api_port, "GET", "/api/health")
    assert status == 200
    assert payload["ok"] is True
    assert calls, "first response must serialize the payload"

    calls.clear()
    status, payload = _request(api_port, "GET", "/api/health")
    assert status == 200
    assert payload["ok"] is True
    assert calls == [], "second response within the TTL must reuse cached bytes"


def test_writes_invalidate_cached_get_responses(api_port) -> None:
    status, config = _request(api_port, "GET", "/api/settings")
    assert status == 200
    assert config["product_sku"]["prefix"] != "ZZ"

    status, saved = _request(api_port, "PUT", "/api/settings", {"product_sku": {"prefix": "ZZ"}})
    assert status == 200
    assert saved["product_sku"]["prefix"] == "ZZ"

    status, config = _request(api_port, "GET", "/api/settings")
    assert status == 200
    assert config["product_sku"]["prefix"] == "ZZ"
