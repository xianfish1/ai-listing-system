from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import settings
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _request_json(method: str, url: str, payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_account_crud_round_trip_over_http() -> None:
    server, base_url = _start_api()
    try:
        status, created = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "账号A", "login_account": "acct-a", "login_password": "pw-a", "cdp_port": 9223},
        )
        assert status == 201
        assert created["login_account"] == "acct-a"
        assert created["has_password"] is True
        assert "encrypted_password" not in created

        status, listed = _request_json("GET", f"{base_url}/api/mabang/accounts")
        assert status == 200
        assert len(listed["items"]) == 1
        assert listed["default_account_id"] == created["id"]
        assert "pw-a" not in json.dumps(listed)

        status, updated = _request_json(
            "PUT",
            f"{base_url}/api/mabang/accounts/{created['id']}",
            {"name": "账号A改", "cdp_port": 9224},
        )
        assert status == 200
        assert updated["name"] == "账号A改"
        assert updated["cdp_port"] == 9224
        # 空密码 = 保留原密码
        assert updated["has_password"] is True

        status, deleted = _request_json("DELETE", f"{base_url}/api/mabang/accounts/{created['id']}")
        assert status == 200
        assert deleted["login_account"] == "acct-a"

        status, listed = _request_json("GET", f"{base_url}/api/mabang/accounts")
        assert listed["items"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_store_options_are_scoped_by_account_over_http() -> None:
    server, base_url = _start_api()
    try:
        _status_a, a = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "A", "login_account": "acct-a", "cdp_port": 9223},
        )
        _status_b, b = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "B", "login_account": "acct-b", "cdp_port": 9224},
        )

        for account in (a, b):
            status, _saved = _request_json(
                "POST",
                f"{base_url}/api/mabang/store-options",
                {
                    "account_id": account["id"],
                    "platform": "shopee",
                    "country": "PH",
                    "store_name": f"店-{account['login_account']}",
                    "mabang_store_id": "shared-1",
                },
            )
            assert status == 201

        status, all_items = _request_json("GET", f"{base_url}/api/mabang/store-options")
        assert status == 200
        assert len(all_items["items"]) == 2

        status, only_a = _request_json(
            "GET", f"{base_url}/api/mabang/store-options?account_id={a['id']}"
        )
        assert status == 200
        assert [item["store_name"] for item in only_a["items"]] == ["店-acct-a"]

        status, blocked = _request_json("DELETE", f"{base_url}/api/mabang/accounts/{a['id']}")
        assert status == 409
        assert "店铺" in blocked["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_browser_launch_and_auto_login_use_the_accounts_own_session(monkeypatch) -> None:
    """每个账号用自己的端口、自己的 Profile 目录、自己的凭据登录；cookie 因此隔离。"""

    calls: dict[str, object] = {"launches": []}

    def fake_launch(cdp_url, *, profile_dir, headless=False, **kwargs):
        calls["launches"].append({"cdp_url": cdp_url, "profile_dir": str(profile_dir)})
        return {"ok": True, "status": "started", "login_required": True}

    def fake_auto_login(cdp_url, *, account, password):
        calls["login_account"] = account
        calls["login_password"] = password
        return {"ok": True, "status": "logged_in", "exit_code": 0}

    monkeypatch.setattr("services.api.main.launch_mabang_browser", fake_launch)
    monkeypatch.setattr("services.api.main.run_mabang_auto_login", fake_auto_login)

    server, base_url = _start_api()
    try:
        _status_a, a = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "A", "login_account": "acct-a", "login_password": "pw-a", "cdp_port": 9223},
        )
        _status_b, b = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "B", "login_account": "acct-b", "login_password": "pw-b", "cdp_port": 9224},
        )

        status, launched_a = _request_json(
            "POST", f"{base_url}/api/mabang-browser/start", {"account_id": a["id"]}
        )
        assert status == 200
        assert launched_a["account_id"] == a["id"]
        assert launched_a["login_account"] == "acct-a"
        assert calls["login_account"] == "acct-a"
        assert calls["login_password"] == "pw-a"
        assert calls["launches"][-1]["cdp_url"] == "http://127.0.0.1:9223"

        status, launched_b = _request_json(
            "POST", f"{base_url}/api/mabang-browser/start", {"account_id": b["id"]}
        )
        assert status == 200
        assert calls["login_account"] == "acct-b"
        assert calls["login_password"] == "pw-b"
        assert calls["launches"][-1]["cdp_url"] == "http://127.0.0.1:9224"

        profile_dirs = [item["profile_dir"] for item in calls["launches"]]
        assert len(profile_dirs) == 2
        assert profile_dirs[0] != profile_dirs[1]
    finally:
        server.shutdown()
        server.server_close()


def test_worker_status_reports_one_worker_per_account() -> None:
    """有账号时 Worker 状态按账号列出，三个账号可以并行。"""

    server, base_url = _start_api()
    try:
        _status_a, a = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "A", "login_account": "acct-a", "cdp_port": 9223},
        )
        _status_b, b = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "B", "login_account": "acct-b", "cdp_port": 9224},
        )

        status, payload = _request_json("GET", f"{base_url}/api/worker/status")
        assert status == 200
        assert [row["account_id"] for row in payload["workers"]] == [a["id"], b["id"]]

        status, listed = _request_json("GET", f"{base_url}/api/worker/workers")
        assert status == 200
        assert [row["account_id"] for row in listed["items"]] == [a["id"], b["id"]]
    finally:
        server.shutdown()
        server.server_close()
