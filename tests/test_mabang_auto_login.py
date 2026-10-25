from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib import request

import pytest

from services.api import settings, storage
from services.api.mabang_auto_login import run_mabang_auto_login
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _post(base_url: str, path: str) -> dict:
    req = request.Request(
        f"{base_url}{path}",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_run_mabang_auto_login_passes_credentials_via_env_only() -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["env"] = kwargs["env"]
        captured["creationflags"] = kwargs.get("creationflags")
        return _FakeCompleted('MABANG_LOGIN_RESULT {"ok": true, "status": "logged_in"}\n', 0)

    result = run_mabang_auto_login(
        "http://127.0.0.1:9223",
        account="ops@example.com",
        password="sup3r-secret",
        node_executable="node-fake",
        popen_fn=fake_run,
    )

    assert result == {"ok": True, "status": "logged_in", "exit_code": 0}
    assert captured["env"]["MABANG_ACCOUNT"] == "ops@example.com"
    assert captured["env"]["MABANG_PASSWORD"] == "sup3r-secret"
    # 密码只允许通过环境变量传递，绝不出现在命令行参数中。
    assert "sup3r-secret" not in captured["command"]
    assert captured["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", None)


def test_run_mabang_auto_login_reports_missing_structured_result() -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted("node crashed without a marker", 1)

    result = run_mabang_auto_login(
        "http://127.0.0.1:9223",
        account="ops",
        password="pw",
        node_executable="node-fake",
        popen_fn=fake_run,
    )

    assert result["status"] == "failed"
    assert result["error"] == "自动登录脚本未返回结构化结果"
    assert result["exit_code"] == 1


def test_run_mabang_auto_login_requires_credentials() -> None:
    assert run_mabang_auto_login("http://127.0.0.1:9223", account="", password="pw")["status"] == "no_credentials"
    assert run_mabang_auto_login("http://127.0.0.1:9223", account="ops", password="")["status"] == "no_credentials"


def test_start_mabang_browser_runs_auto_login_with_bound_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_launch(cdp_url: str, *, profile_dir: Path, headless: bool = False, **kwargs) -> dict:
        calls["cdp_url"] = cdp_url
        return {"ok": True, "status": "started", "login_required": True}

    def fake_auto_login(cdp_url: str, *, account: str, password: str) -> dict:
        calls["login_account"] = account
        calls["login_password"] = password
        return {"ok": True, "status": "logged_in", "exit_code": 0}

    monkeypatch.setattr("services.api.main.launch_mabang_browser", fake_launch)
    monkeypatch.setattr("services.api.main.run_mabang_auto_login", fake_auto_login)
    storage.save_runtime_configuration(
        {"mabang": {"login_account": "ops@example.com", "login_password": "sup3r-secret"}}
    )

    server, base_url = _start_api()
    try:
        payload = _post(base_url, "/api/mabang-browser/start")

        assert payload["login_required"] is False
        assert payload["auto_login"] == {"status": "logged_in", "error": ""}
        assert calls["cdp_url"] == "http://127.0.0.1:9223"
        assert calls["login_account"] == "ops@example.com"
        assert calls["login_password"] == "sup3r-secret"
    finally:
        server.shutdown()
        server.server_close()


def test_auto_login_endpoint_reports_captcha_without_clearing_login_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.api.main.launch_mabang_browser",
        lambda cdp_url, *, profile_dir, headless=False, **kwargs: {"ok": True, "status": "started", "login_required": True},
    )
    monkeypatch.setattr(
        "services.api.main.run_mabang_auto_login",
        lambda cdp_url, *, account, password: {
            "ok": False,
            "status": "captcha_required",
            "error": "mabang_captcha_or_verification",
        },
    )
    storage.save_runtime_configuration({"mabang": {"login_account": "ops", "login_password": "pw"}})

    server, base_url = _start_api()
    try:
        payload = _post(base_url, "/api/mabang-browser/auto-login")

        assert payload["auto_login"]["status"] == "captcha_required"
        assert payload["login_required"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_start_mabang_browser_reports_no_credentials_without_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.api.main.launch_mabang_browser",
        lambda cdp_url, *, profile_dir, headless=False, **kwargs: {"ok": True, "status": "started", "login_required": True},
    )

    server, base_url = _start_api()
    try:
        payload = _post(base_url, "/api/mabang-browser/start")

        assert payload["auto_login"] == {"status": "no_credentials", "error": ""}
        assert payload["login_required"] is True
    finally:
        server.shutdown()
        server.server_close()
