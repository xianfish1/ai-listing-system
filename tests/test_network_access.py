from __future__ import annotations

import os
import subprocess

import pytest

from services.api import network_access, settings


def test_lan_switch_resolves_to_wildcard_and_off_to_loopback(monkeypatch) -> None:
    monkeypatch.setattr(settings, "API_HOST", "127.0.0.1")

    assert network_access.resolve_bind_host({"lan_enabled": True}) == "0.0.0.0"
    assert network_access.resolve_bind_host({"lan_enabled": False}) == "127.0.0.1"
    assert network_access.resolve_bind_host({}) == "127.0.0.1"


def test_explicit_non_loopback_env_host_wins(monkeypatch) -> None:
    monkeypatch.setattr(settings, "API_HOST", "192.168.1.9")

    assert network_access.env_bind_override() == "192.168.1.9"
    assert network_access.resolve_bind_host({"lan_enabled": False}) == "192.168.1.9"
    assert network_access.is_lan_bind_host("192.168.1.9") is True


def test_loopback_env_host_does_not_lock_the_switch(monkeypatch) -> None:
    monkeypatch.setattr(settings, "API_HOST", "127.0.0.1")

    assert network_access.env_bind_override() == ""
    assert network_access.resolve_bind_host({"lan_enabled": True}) == "0.0.0.0"


def test_build_network_status_marks_addresses_reachable_only_when_wildcard(monkeypatch) -> None:
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
            "rule_name": "rule",
            "port": port,
            "is_admin": True,
            "message": "",
        },
    )
    monkeypatch.setattr(network_access, "hostname_url", lambda port: "")

    status = network_access.build_network_status(
        {"lan_enabled": True}, active_bind_host="0.0.0.0", active_port=8788
    )

    assert status["enabled"] is True
    assert status["active"] is True
    assert status["addresses"][0]["reachable"] is True
    assert status["addresses"][0]["url"] == "http://192.168.1.5:8788"
    assert status["server"]["protocol"] == settings.API_HTTP_PROTOCOL
    assert status["server"]["keep_alive"] is True
    assert status["server"]["request_queue_size"] >= 128

    off = network_access.build_network_status(
        {"lan_enabled": False}, active_bind_host="127.0.0.1", active_port=8788
    )

    assert off["enabled"] is False
    assert off["active"] is False
    assert off["addresses"][0]["reachable"] is False
    assert off["restart_required"] is False


def test_build_network_status_reports_restart_when_persisted_setting_differs(monkeypatch) -> None:
    monkeypatch.setattr(network_access, "list_lan_addresses", lambda port: [])
    monkeypatch.setattr(
        network_access,
        "firewall_status",
        lambda port: {"supported": False, "rule_exists": False, "port": port},
    )
    monkeypatch.setattr(network_access, "hostname_url", lambda port: "")

    status = network_access.build_network_status(
        {"lan_enabled": True}, active_bind_host="127.0.0.1", active_port=8788
    )

    assert status["restart_required"] is True
    assert any("重启" in hint for hint in status["hints"])


def test_list_lan_addresses_excludes_loopback_and_link_local(monkeypatch) -> None:
    rows = [
        {
            "ip": "127.0.0.1",
            "interface": "Loopback",
            "description": "",
            "if_index": 1,
            "if_type": 24,
            "oper_status": 1,
        },
        {
            "ip": "169.254.10.1",
            "interface": "Ethernet",
            "description": "",
            "if_index": 2,
            "if_type": 6,
            "oper_status": 1,
        },
        {
            "ip": "192.168.1.7",
            "interface": "Ethernet",
            "description": "Realtek",
            "if_index": 3,
            "if_type": 6,
            "oper_status": 1,
        },
        {
            "ip": "10.0.0.3",
            "interface": "Wi-Fi",
            "description": "Intel",
            "if_index": 4,
            "if_type": 71,
            "oper_status": 1,
        },
        {
            "ip": "192.168.1.9",
            "interface": "Ethernet 2",
            "description": "",
            "if_index": 5,
            "if_type": 6,
            "oper_status": 2,
        },
    ]
    monkeypatch.setattr(network_access, "_windows_adapter_addresses", lambda: rows)

    addresses = network_access.list_lan_addresses(8787)

    assert {row["ip"] for row in addresses} == {"192.168.1.7", "10.0.0.3"}
    assert all(row["url"].startswith("http://") and row["url"].endswith(":8787") for row in addresses)


def test_windows_firewall_rule_name_is_port_specific() -> None:
    assert network_access.firewall_rule_name(8788) == "AI Listing System API (LAN 8788)"


@pytest.mark.skipif(os.name != "nt", reason="Windows firewall")
def test_firewall_status_detects_an_existing_rule_for_the_same_port(monkeypatch) -> None:
    network_access.invalidate_firewall_cache()
    monkeypatch.setattr(
        network_access,
        "_run_netsh",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1, "", ""),
    )
    monkeypatch.setattr(
        network_access,
        "_existing_port_rules",
        lambda port: ["iLab CONJURE WebUI LAN (8788)"],
    )
    monkeypatch.setattr(network_access, "is_windows_admin", lambda: True)

    status = network_access.firewall_status(8788)

    assert status["rule_exists"] is False
    assert status["port_open"] is True
    assert status["port_allowed_by"] == ["iLab CONJURE WebUI LAN (8788)"]
    assert "iLab CONJURE" in status["message"]
    network_access.invalidate_firewall_cache()


@pytest.mark.skipif(os.name != "nt", reason="Windows firewall")
def test_ensure_firewall_rule_does_not_duplicate_an_existing_port_rule(monkeypatch) -> None:
    monkeypatch.setattr(
        network_access,
        "firewall_status",
        lambda port: {
            "rule_exists": False,
            "port_open": True,
            "port_allowed_by": ["iLab CONJURE WebUI LAN (8788)"],
            "port": port,
        },
    )
    monkeypatch.setattr(
        network_access,
        "_run_netsh",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not add a duplicate rule")),
    )

    result = network_access.ensure_firewall_rule(8788)

    assert result["ok"] is True
    assert result["already"] is True
    assert "iLab CONJURE" in result["message"]
