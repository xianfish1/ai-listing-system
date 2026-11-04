from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from services.api import github_updates, main, settings, storage
from services.api.main import ApiHandler

LATEST_RELEASE = {
    "version": "1.2.0",
    "tag": "v1.2.0",
    "name": "v1.2.0",
    "body": "修复若干问题",
    "html_url": "https://github.com/owner/repo/releases/tag/v1.2.0",
    "published_at": "2026-10-01T00:00:00Z",
    "prerelease": False,
    "draft": False,
    "assets": [
        {
            "name": "ai-listing-system-1.2.0.zip",
            "size": 1024,
            "url": "https://example.invalid/asset.zip",
            "browser_download_url": "https://example.invalid/asset.zip",
            "content_type": "application/zip",
        }
    ],
}

PENDING = {
    "version": "1.2.0",
    "tag": "v1.2.0",
    "staged_dir": "staged",
    "package_path": "package.zip",
    "package_sha256": "abc123",
    "checksum_verified": True,
    "backup_dir": "backup",
    "staged_at": "2026-10-24T00:00:00Z",
    "restart": True,
}


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
    monkeypatch.setattr(settings, "API_GET_CACHE_TTL_SECONDS", 0.0)
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
    finally:
        conn.close()
    return response.status, json.loads(raw.decode("utf-8"))


def _configure(api_port: int, *, token: str = "ghp_test_secret") -> None:
    status, _payload = _request(
        api_port,
        "PUT",
        "/api/settings",
        {"updates": {"repo": "owner/repo", "token": token, "auto_check": True}},
    )
    assert status == 200


def test_health_reports_local_version(api_port, monkeypatch) -> None:
    monkeypatch.setattr(github_updates, "read_local_version", lambda root=None: "9.9.9")
    status, payload = _request(api_port, "GET", "/api/health")

    assert status == 200
    assert payload["version"] == "9.9.9"


def test_update_status_hides_token_and_reports_configuration(api_port) -> None:
    _configure(api_port)

    status, payload = _request(api_port, "GET", "/api/updates/status")

    assert status == 200
    assert payload["configured"] is True
    assert payload["repo"] == "owner/repo"
    assert payload["token_configured"] is True
    assert "ghp_test_secret" not in json.dumps(payload)

    status, config = _request(api_port, "GET", "/api/settings")
    assert status == 200
    assert "token" not in config["updates"]
    assert config["updates"]["token_configured"] is True

    # 落库必须是密文；进程内解密后要能拿到原 Token（用于 GitHub 请求）。
    store_text = (settings.DATA_DIR / "store.json").read_text(encoding="utf-8")
    assert "ghp_test_secret" not in store_text
    assert storage.get_github_update_token() == "ghp_test_secret"


def test_status_falls_back_to_the_default_repository(api_port) -> None:
    """没填仓库时开箱即用官方仓库：面板不再提示「还没有配置 GitHub 仓库」。"""
    status, payload = _request(api_port, "GET", "/api/updates/status")

    assert status == 200
    assert payload["configured"] is True
    assert payload["repo"] == github_updates.DEFAULT_REPO


def test_status_repo_is_empty_when_default_is_blank(api_port, monkeypatch) -> None:
    monkeypatch.setattr(github_updates, "DEFAULT_REPO", "")
    status, payload = _request(api_port, "GET", "/api/updates/status")

    assert status == 200
    assert payload["configured"] is False
    assert payload["repo"] == ""


def test_status_ignores_pending_after_successful_install(api_port) -> None:
    updates_dir = main._updates_dir()
    github_updates.write_pending(
        updates_dir,
        {
            "version": "1.2.0",
            "staged_dir": str(updates_dir / "staged" / "1.2.0"),
            "backup_dir": str(updates_dir / "backups" / "20261024-000000-from-1.0.0"),
            "package_sha256": "abc",
            "checksum_verified": True,
            "staged_at": "2026-10-24T00:00:00Z",
            "restart": True,
        },
    )
    github_updates.write_update_state(
        updates_dir,
        {"status": "success", "version": "1.2.0", "applied_at": "2026-10-24T00:01:00Z"},
    )

    status, payload = _request(api_port, "GET", "/api/updates/status")

    assert status == 200
    assert payload["pending"] is None
    assert payload["last_update"]["status"] == "success"
    assert payload["restart_required"] is False


def test_check_update_caches_latest_release(api_port, monkeypatch) -> None:
    monkeypatch.setattr(github_updates, "read_local_version", lambda root=None: "1.0.0")
    calls: list[dict] = []

    def fake_check(config, current, *, include_prerelease=False, client=None):
        calls.append({"repo": config.slug, "current": current})
        return {
            "ok": True,
            "current_version": current,
            "repo": config.slug,
            "latest": LATEST_RELEASE,
            "update_available": True,
            "error": "",
            "checked_at": "2026-10-24T00:00:00Z",
        }

    monkeypatch.setattr(github_updates, "check_for_update", fake_check)
    _configure(api_port)

    status, payload = _request(api_port, "POST", "/api/updates/check", {})

    assert status == 200
    assert payload["update_available"] is True
    assert payload["latest"]["version"] == "1.2.0"
    assert calls == [{"repo": "owner/repo", "current": "1.0.0"}]

    # 第二次读状态走缓存，不再请求 GitHub。
    status, cached = _request(api_port, "GET", "/api/updates/status")
    assert status == 200
    assert cached["latest"]["version"] == "1.2.0"
    assert len(calls) == 1


def test_check_update_reports_error_without_500(api_port, monkeypatch) -> None:
    monkeypatch.setattr(github_updates, "read_local_version", lambda root=None: "1.0.0")

    def fake_check(config, current, *, include_prerelease=False, client=None):
        return {
            "ok": False,
            "current_version": current,
            "repo": config.slug,
            "latest": None,
            "update_available": False,
            "error": "GitHub 认证失败",
            "checked_at": "2026-10-24T00:00:00Z",
        }

    monkeypatch.setattr(github_updates, "check_for_update", fake_check)
    _configure(api_port)

    status, payload = _request(api_port, "POST", "/api/updates/check", {})

    assert status == 200
    assert payload["error"] == "GitHub 认证失败"
    assert payload["update_available"] is False


def test_apply_requires_configured_repository(api_port, monkeypatch) -> None:
    # 默认仓库会兜底，所以这里显式把默认清空来覆盖「没配置」这条守卫。
    monkeypatch.setattr(github_updates, "DEFAULT_REPO", "")
    status, payload = _request(api_port, "POST", "/api/updates/apply", {})

    assert status == 422
    assert "GitHub 仓库" in payload["error"]


def test_apply_stages_release_and_schedules_restart(api_port, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(github_updates, "read_local_version", lambda root=None: "1.0.0")
    _configure(api_port)
    main._write_update_check_cache(
        {"repo": "owner/repo", "latest": LATEST_RELEASE, "checked_at": "2026-10-24T00:00:00Z", "error": ""}
    )
    calls: dict[str, object] = {}

    def fake_stage(**kwargs):
        calls["stage"] = kwargs
        return dict(PENDING, staged_dir=str(tmp_path / "staged"))

    def fake_spawn(**kwargs):
        calls["spawn"] = kwargs
        return 4242

    monkeypatch.setattr(github_updates, "stage_update", fake_stage)
    monkeypatch.setattr(github_updates, "spawn_update_runner", fake_spawn)
    monkeypatch.setattr(main, "_schedule_api_exit_for_update", lambda *a, **k: calls.setdefault("exit", True))

    status, payload = _request(api_port, "POST", "/api/updates/apply", {"version": "1.2.0"})

    assert status == 202
    assert payload["status"] == "staged"
    assert payload["version"] == "1.2.0"
    assert payload["restart_scheduled"] is True
    assert payload["package_sha256"] == "abc123"
    assert calls["stage"]["release"]["version"] == "1.2.0"
    assert calls["spawn"]["api_pid"] > 0
    assert calls.get("exit") is True


def test_apply_rejects_unknown_version(api_port, monkeypatch) -> None:
    monkeypatch.setattr(github_updates, "read_local_version", lambda root=None: "1.0.0")
    _configure(api_port)
    main._write_update_check_cache(
        {"repo": "owner/repo", "latest": LATEST_RELEASE, "checked_at": "2026-10-24T00:00:00Z", "error": ""}
    )

    status, payload = _request(api_port, "POST", "/api/updates/apply", {"version": "bad-version"})

    assert status == 422
    assert "版本" in payload["error"]


def test_publish_rejects_invalid_bump(api_port) -> None:
    status, payload = _request(api_port, "POST", "/api/releases/publish", {"bump": "banana"})

    assert status == 422
    assert "补丁" in payload["error"]


def test_publish_requires_github_configuration(api_port) -> None:
    status, payload = _request(api_port, "POST", "/api/releases/publish", {"bump": "patch"})

    assert status == 422
    assert "GitHub" in payload["error"]


def test_publish_dry_run_job_reaches_success(api_port, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_release(**kwargs):
        captured.update(kwargs)
        kwargs["log"]("构建 dist/ai-listing-system-1.1.0.zip")
        return {
            "ok": True,
            "dry_run": True,
            "version": "1.1.0",
            "artifact": {"path": "dist/ai-listing-system-1.1.0.zip", "sha256": "deadbeef"},
        }

    monkeypatch.setattr(github_updates, "run_release", fake_run_release)

    status, payload = _request(
        api_port,
        "POST",
        "/api/releases/publish",
        {"bump": "minor", "dry_run": True},
    )

    assert status == 202
    job_id = payload["job_id"]
    deadline = time.time() + 5
    job = None
    while time.time() < deadline:
        status, body = _request(api_port, "GET", f"/api/releases/publish/status?job_id={job_id}")
        assert status == 200
        job = body["job"]
        if job and job.get("status") in {"success", "failed"}:
            break
        time.sleep(0.05)

    assert job is not None
    assert job["status"] == "success", job
    assert job["result"]["version"] == "1.1.0"
    assert any("dist/ai-listing-system-1.1.0.zip" in line for line in job["log"])
    assert captured["bump"] == "minor"
    assert captured["dry_run"] is True
