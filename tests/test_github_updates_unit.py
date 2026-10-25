"""github_updates 单元测试（完全离线，不访问 GitHub、不依赖本机 git 仓库状态）。"""

from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import types
import urllib.error
import zipfile
from pathlib import Path

import pytest

from services.api import github_updates as gu


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


class _FakeResponse:
    """替代 urllib 的响应对象（支持 with/read/close）。"""

    def __init__(self, payload: bytes = b"") -> None:
        self.payload = payload

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self.payload
        return self.payload[:size]

    def close(self) -> None:
        return None

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _FakeDownloadResponse(_FakeResponse):
    """模拟网络流：read(size) 按块返回。"""

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self._stream = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class _FakeClient:
    """假 GitHubClient：通过资产名提供下载字节。"""

    def __init__(self, *, releases=None, assets=None, error=None, latest_error=None) -> None:
        self.releases = list(releases or [])
        self.assets = dict(assets or {})
        self.error = error
        self.latest_error = latest_error
        self.downloads: list[str] = []

    def latest_release(self):
        if self.latest_error is not None:
            raise self.latest_error
        if self.error is not None:
            raise self.error
        if not self.releases:
            raise gu.UpdateError("仓库或 Release 不存在", code="not_found", status=404)
        return self.releases[0]

    def list_releases(self, limit: int = 10):
        if self.error is not None:
            raise self.error
        return list(self.releases)

    @contextlib.contextmanager
    def open_asset(self, asset):
        name = str(asset.get("name") or "")
        self.downloads.append(name)
        if name not in self.assets:
            raise gu.UpdateError(f"缺少测试资产：{name}", code="missing_asset")
        yield _FakeDownloadResponse(self.assets[name])


def _asset(name: str, *, url: str = "https://example.invalid/asset") -> dict:
    return {"name": name, "url": url, "browser_download_url": url}


def _release(version: str, *, assets=None, draft: bool = False, prerelease: bool = False) -> dict:
    return {
        "version": version,
        "tag": f"v{version}",
        "name": f"v{version}",
        "body": "notes",
        "html_url": f"https://github.com/o/r/releases/tag/v{version}",
        "published_at": "2026-01-01T00:00:00Z",
        "prerelease": prerelease,
        "draft": draft,
        "assets": list(assets or []),
    }


def _manifest_for(files: dict[str, bytes], version: str = "1.1.0") -> dict:
    return {
        "format": 1,
        "version": version,
        "files": [
            {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for rel, data in files.items()
        ],
        "delete": [],
    }


def _package_bytes(
    files: dict[str, bytes],
    *,
    version: str = "1.1.0",
    manifest: dict | None = None,
) -> bytes:
    payload = manifest if manifest is not None else _manifest_for(files, version)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(payload).encode("utf-8"))
        for rel, data in files.items():
            archive.writestr(rel, data)
    return buffer.getvalue()


def _write_zip(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _make_project(root: Path, *, version: str = "1.0.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "services" / "api").mkdir(parents=True, exist_ok=True)
    (root / "services" / "api" / "main.py").write_text("print('v1')\n", encoding="utf-8")
    (root / "apps" / "web").mkdir(parents=True, exist_ok=True)
    (root / "apps" / "web" / "index.html").write_text("<html>v1</html>\n", encoding="utf-8")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "note.md").write_text("# note\n", encoding="utf-8")
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    return root


def _header(headers: dict, name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


# ---------------------------------------------------------------------------
# 版本号
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2.3", "1.2.3"),
        ("v1.2.3", "1.2.3"),
        ("V2.0.0", "2.0.0"),
        (" 1.2 ", "1.2.0"),
        ("1", "1.0.0"),
        ("1.2.3-beta.1", "1.2.3-beta.1"),
        ("1.2.3+build.9", "1.2.3"),
        ("v1.2.3-rc.1+build", "1.2.3-rc.1"),
    ],
)
def test_normalize_version_accepts_common_forms(raw: str, expected: str) -> None:
    assert gu.normalize_version(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "abc", "1.2.3.4", "v", "1..2", "1.2.3-", "1.2.3 beta"])
def test_normalize_version_rejects_invalid(raw: str) -> None:
    with pytest.raises(gu.UpdateError):
        gu.normalize_version(raw)


def test_version_key_orders_prerelease_below_release() -> None:
    assert gu.version_key("1.2.3-beta.1") < gu.version_key("1.2.3")
    assert gu.version_key("1.2.3-alpha") < gu.version_key("1.2.3-beta")
    assert gu.version_key("1.2.3-alpha.9") < gu.version_key("1.2.3-alpha.10")
    assert gu.version_key("1.2.3-alpha") < gu.version_key("1.2.3-alpha.1")
    assert gu.version_key("1.10.0") > gu.version_key("1.9.9")


def test_compare_versions_and_is_newer() -> None:
    assert gu.compare_versions("1.2.3", "1.2.3") == 0
    assert gu.compare_versions("1.2.4", "1.2.3") == 1
    assert gu.compare_versions("1.2.3", "1.2.4") == -1
    assert gu.compare_versions("v1.0.0", "1.0.0") == 0
    assert gu.is_newer("1.2.0", "1.1.9") is True
    assert gu.is_newer("1.1.0", "1.1.0") is False
    assert gu.is_newer("1.1.0", "1.2.0") is False
    with pytest.raises(gu.UpdateError):
        gu.is_newer("oops", "1.0.0")


def test_bump_version_all_parts() -> None:
    assert gu.bump_version("1.2.3", "patch") == "1.2.4"
    assert gu.bump_version("1.2.3", "minor") == "1.3.0"
    assert gu.bump_version("1.2.3", "major") == "2.0.0"
    assert gu.bump_version("1.2.3-beta.1", "patch") == "1.2.4"
    with pytest.raises(gu.UpdateError):
        gu.bump_version("1.2.3", "huge")


def test_read_local_version(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _make_project(root, version="1.4.2")
    assert gu.read_local_version(root) == "1.4.2"
    (root / "VERSION").write_text("\n\nv2.0.1\n", encoding="utf-8")
    assert gu.read_local_version(root) == "2.0.1"


def test_read_local_version_missing_file_returns_zero(tmp_path: Path) -> None:
    assert gu.read_local_version(tmp_path) == "0.0.0"


# ---------------------------------------------------------------------------
# GitHubConfig / ReleaseInfo / 资产选择
# ---------------------------------------------------------------------------


def test_from_runtime_prefers_environment_over_runtime_config() -> None:
    env = {
        "GITHUB_UPDATE_REPO": "env-owner/env-repo",
        "GITHUB_UPDATE_TOKEN": "env-token",
        "GITHUB_API_URL": "https://api.example.com/",
    }
    config = gu.GitHubConfig.from_runtime(
        {"updates": {"repo": "rt/rt", "token": "rt-token", "api_url": "https://rt.example"}},
        environ=env,
    )
    assert config.slug == "env-owner/env-repo"
    assert config.token == "env-token"
    assert config.api_url == "https://api.example.com"
    assert config.configured is True


def test_from_runtime_reads_nested_updates_group() -> None:
    config = gu.GitHubConfig.from_runtime(
        {"updates": {"repo": "owner/name", "api_url": "https://ghe.example/api/v3"}},
        environ={},
    )
    assert (config.owner, config.repo) == ("owner", "name")
    assert config.api_url == "https://ghe.example/api/v3"
    assert config.token == ""


def test_from_runtime_accepts_updates_dict_directly() -> None:
    config = gu.GitHubConfig.from_runtime({"repo": "owner/name"}, environ={})
    assert config.slug == "owner/name"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("owner/name", ("owner", "name")),
        ("https://github.com/owner/name", ("owner", "name")),
        ("https://github.com/owner/name.git", ("owner", "name")),
        ("git@github.com:owner/name.git", ("owner", "name")),
        ("https://github.com/owner/name/", ("owner", "name")),
        ("", ("", "")),
        ("name-only", ("", "")),
        ("https://github.com/owner/bad name", ("", "")),
    ],
)
def test_parse_repo_slug(raw: str, expected: tuple[str, str]) -> None:
    assert gu.parse_repo_slug(raw) == expected


def test_config_repr_hides_token() -> None:
    config = gu.GitHubConfig(owner="o", repo="r", token="SECRET-TOKEN")
    assert "SECRET-TOKEN" not in repr(config)
    assert "SECRET-TOKEN" not in str(config)


def test_release_from_payload_and_to_dict() -> None:
    payload = {
        "tag_name": "v1.2.3",
        "name": "v1.2.3",
        "body": "说明",
        "html_url": "https://github.com/o/r/releases/tag/v1.2.3",
        "published_at": "2026-01-01T00:00:00Z",
        "prerelease": True,
        "draft": False,
        "assets": [{"name": "a.zip", "size": 10}],
        "upload_url": "https://uploads.github.com/x{?name,label}",
    }
    release = gu.release_from_payload(payload)
    assert release.version == "1.2.3"
    assert release.tag == "v1.2.3"
    assert release.prerelease is True
    assert release.assets[0]["name"] == "a.zip"
    flat = release.to_dict()
    assert flat["version"] == "1.2.3"
    assert "raw" not in flat
    assert flat["assets"] == [{"name": "a.zip", "size": 10}]


def test_select_release_asset_prefers_named_zip() -> None:
    release = _release(
        "1.1.0",
        assets=[_asset("other.zip"), _asset("ai-listing-system-1.1.0.zip"), _asset("x.sha256")],
    )
    assert gu.select_release_asset(release)["name"] == "ai-listing-system-1.1.0.zip"


def test_select_release_asset_falls_back_to_first_zip() -> None:
    release = _release("1.1.0", assets=[_asset("notes.txt"), _asset("bundle.zip")])
    assert gu.select_release_asset(release)["name"] == "bundle.zip"


def test_select_release_asset_returns_none_without_zip() -> None:
    assert gu.select_release_asset(_release("1.1.0", assets=[_asset("a.sha256")])) is None
    assert gu.select_release_asset({}) is None


# ---------------------------------------------------------------------------
# GitHubClient（HTTP 全部打桩）
# ---------------------------------------------------------------------------


def test_client_sends_expected_headers_and_parses_release(monkeypatch) -> None:
    captured: dict = {}

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps({"tag_name": "v1.2.3", "assets": []}).encode("utf-8"))

    monkeypatch.setattr(gu, "_http_open", fake_open)
    config = gu.GitHubConfig(owner="o", repo="r", token="TOKEN")
    release = gu.GitHubClient(config, timeout=7.5).latest_release()

    assert release.version == "1.2.3"
    assert captured["url"] == "https://api.github.com/repos/o/r/releases/latest"
    assert captured["timeout"] == 7.5
    assert _header(captured["headers"], "Accept") == "application/vnd.github+json"
    assert _header(captured["headers"], "X-GitHub-Api-Version") == "2022-11-28"
    assert _header(captured["headers"], "User-Agent") == gu.USER_AGENT
    assert _header(captured["headers"], "Authorization") == "Bearer TOKEN"


@pytest.mark.parametrize(("code", "fragment"), [(401, "认证失败"), (403, "权限不足"), (404, "不存在")])
def test_client_maps_http_errors_to_chinese(monkeypatch, code: int, fragment: str) -> None:
    def fake_open(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, code, "err", None, io.BytesIO(b'{"message":"x"}')
        )

    monkeypatch.setattr(gu, "_http_open", fake_open)
    client = gu.GitHubClient(gu.GitHubConfig(owner="o", repo="r"))
    with pytest.raises(gu.UpdateError) as excinfo:
        client.latest_release()
    assert fragment in str(excinfo.value)
    assert excinfo.value.status == code
    assert "TOKEN" not in str(excinfo.value)


def test_client_maps_other_http_and_network_errors(monkeypatch) -> None:
    def http_500(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", None, io.BytesIO(b"{}"))

    monkeypatch.setattr(gu, "_http_open", http_500)
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.GitHubClient(gu.GitHubConfig(owner="o", repo="r")).latest_release()
    assert "500" in str(excinfo.value)

    def network_down(request, timeout):
        raise urllib.error.URLError("dns error")

    monkeypatch.setattr(gu, "_http_open", network_down)
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.GitHubClient(gu.GitHubConfig(owner="o", repo="r")).latest_release()
    assert "连接 GitHub 失败" in str(excinfo.value)


def test_redirect_handler_strips_authorization_only_on_cross_host() -> None:
    handler = gu._CrossHostAuthStripRedirectHandler()

    cross_host = urllib.request.Request("https://api.github.com/repos/o/r/releases/assets/1")
    cross_host.add_header("Authorization", "Bearer SECRET")
    redirected = handler.redirect_request(
        cross_host,
        None,
        302,
        "Found",
        {},
        "https://objects.githubusercontent.com/signed?token=abc",
    )
    assert redirected is not None
    assert _header(dict(redirected.headers), "Authorization") == ""
    assert _header(dict(redirected.headers), "Accept") == ""

    same_host = urllib.request.Request("https://api.github.com/repos/o/r/releases")
    same_host.add_header("Authorization", "Bearer SECRET")
    kept = handler.redirect_request(
        same_host,
        None,
        302,
        "Found",
        {},
        "https://api.github.com/repos/o/r/releases?page=2",
    )
    assert kept is not None
    assert _header(dict(kept.headers), "Authorization") == "Bearer SECRET"


def test_client_create_release_posts_expected_payload(monkeypatch) -> None:
    captured: dict = {}

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.header_items())
        return _FakeResponse(
            json.dumps(
                {
                    "tag_name": "v1.2.0",
                    "html_url": "https://github.com/o/r/releases/tag/v1.2.0",
                    "upload_url": "https://uploads.github.com/repos/o/r/releases/1/assets{?name,label}",
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr(gu, "_http_open", fake_open)
    client = gu.GitHubClient(gu.GitHubConfig(owner="o", repo="r"))
    release = client.create_release(
        tag="v1.2.0",
        name="v1.2.0",
        body="说明",
        target_commitish="main",
        prerelease=True,
    )
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/repos/o/r/releases")
    assert captured["body"]["tag_name"] == "v1.2.0"
    assert captured["body"]["target_commitish"] == "main"
    assert captured["body"]["prerelease"] is True
    assert captured["body"]["draft"] is False
    assert _header(captured["headers"], "Content-Type") == "application/json"
    assert release.raw["upload_url"].endswith("{?name,label}")


def test_client_upload_asset_builds_name_query(monkeypatch) -> None:
    captured: dict = {}

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["headers"] = dict(request.header_items())
        return _FakeResponse(b'{"name":"a.zip"}')

    monkeypatch.setattr(gu, "_http_open", fake_open)
    client = gu.GitHubClient(gu.GitHubConfig(owner="o", repo="r"))
    result = client.upload_asset(
        "https://uploads.github.com/repos/o/r/releases/1/assets{?name,label}",
        "ai listing.zip",
        b"payload",
        content_type="application/zip",
    )
    assert result == {"name": "a.zip"}
    assert "{?" not in captured["url"]
    assert captured["url"].endswith("?name=ai%20listing.zip")
    assert captured["body"] == b"payload"
    assert _header(captured["headers"], "Content-Type") == "application/zip"


def test_client_list_releases_returns_release_list(monkeypatch) -> None:
    def fake_open(request, timeout):
        assert "per_page=5" in request.full_url
        return _FakeResponse(
            json.dumps(
                [{"tag_name": "v1.2.0"}, {"tag_name": "v1.1.0", "prerelease": True}]
            ).encode("utf-8")
        )

    monkeypatch.setattr(gu, "_http_open", fake_open)
    releases = gu.GitHubClient(gu.GitHubConfig(owner="o", repo="r")).list_releases(limit=5)
    assert [item.version for item in releases] == ["1.2.0", "1.1.0"]
    assert releases[1].prerelease is True


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------


def test_check_for_update_reports_newer_release() -> None:
    client = _FakeClient(releases=[_release("1.2.0")])
    result = gu.check_for_update(
        gu.GitHubConfig(owner="o", repo="r"), "1.1.0", client=client
    )
    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["current_version"] == "1.1.0"
    assert result["repo"] == "o/r"
    assert result["latest"]["version"] == "1.2.0"
    assert result["error"] == ""
    assert result["checked_at"]


def test_check_for_update_no_update_when_same_version() -> None:
    client = _FakeClient(releases=[_release("1.1.0")])
    result = gu.check_for_update(
        gu.GitHubConfig(owner="o", repo="r"), "v1.1.0", client=client
    )
    assert result["ok"] is True
    assert result["update_available"] is False


def test_check_for_update_not_configured_returns_error() -> None:
    result = gu.check_for_update(gu.GitHubConfig(owner="", repo=""), "1.0.0")
    assert result["ok"] is False
    assert result["update_available"] is False
    assert "尚未配置" in result["error"]


def test_check_for_update_invalid_current_version_returns_error() -> None:
    result = gu.check_for_update(
        gu.GitHubConfig(owner="o", repo="r"), "not-a-version", client=_FakeClient()
    )
    assert result["ok"] is False
    assert "版本号" in result["error"]


def test_check_for_update_returns_error_instead_of_raising() -> None:
    client = _FakeClient(error=gu.UpdateError("GitHub 认证失败/权限不足（Token 或限流）", status=401))
    result = gu.check_for_update(gu.GitHubConfig(owner="o", repo="r"), "1.0.0", client=client)
    assert result["ok"] is False
    assert result["latest"] is None
    assert "认证失败" in result["error"]


def test_check_for_update_missing_release_is_not_an_error() -> None:
    client = _FakeClient(latest_error=gu.UpdateError("仓库或 Release 不存在", status=404))
    result = gu.check_for_update(gu.GitHubConfig(owner="o", repo="r"), "1.0.0", client=client)
    assert result["ok"] is True
    assert result["latest"] is None
    assert result["update_available"] is False


def test_check_for_update_include_prerelease_picks_highest() -> None:
    releases = [
        _release("1.1.0"),
        _release("1.2.0-beta.1", prerelease=True),
        _release("1.3.0-beta.2", prerelease=True),
        _release("9.9.9", draft=True),
    ]
    result = gu.check_for_update(
        gu.GitHubConfig(owner="o", repo="r"),
        "1.0.0",
        include_prerelease=True,
        client=_FakeClient(releases=releases),
    )
    assert result["ok"] is True
    assert result["latest"]["version"] == "1.3.0-beta.2"
    assert result["update_available"] is True


# ---------------------------------------------------------------------------
# 下载与文件哈希
# ---------------------------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(b"hello world")
    assert gu.sha256_file(path) == hashlib.sha256(b"hello world").hexdigest()


def test_download_asset_streams_and_hashes(tmp_path: Path) -> None:
    payload = b"x" * 5000
    client = _FakeClient(assets={"pkg.zip": payload})
    result = gu.download_asset(_asset("pkg.zip"), tmp_path / "out" / "pkg.zip", client=client)
    assert result["bytes"] == 5000
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert Path(result["path"]).read_bytes() == payload
    assert client.downloads == ["pkg.zip"]
    assert list((tmp_path / "out").glob("*.part")) == []


def test_download_asset_enforces_size_limit(tmp_path: Path) -> None:
    client = _FakeClient(assets={"pkg.zip": b"x" * 100})
    dest = tmp_path / "pkg.zip"
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.download_asset(_asset("pkg.zip"), dest, client=client, max_bytes=10)
    assert excinfo.value.code == "package_too_large"
    assert not dest.exists()


# ---------------------------------------------------------------------------
# verify_package / extract_package
# ---------------------------------------------------------------------------


def test_verify_package_accepts_valid_archive(tmp_path: Path) -> None:
    files = {"services/api/main.py": b"print(1)\n", "VERSION": b"1.1.0\n"}
    zip_path = _write_zip(tmp_path / "pkg.zip", _package_bytes(files))
    manifest = gu.verify_package(zip_path, expected_version="1.1.0")
    assert manifest["version"] == "1.1.0"
    assert {item["path"] for item in manifest["files"]} == set(files)


def test_verify_package_checks_external_sha256(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path / "pkg.zip", _package_bytes({"services/a.py": b"a"}))
    digest = gu.sha256_file(zip_path)
    gu.verify_package(zip_path, expected_sha256=digest)
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path, expected_sha256="0" * 64)
    assert excinfo.value.code == "checksum_mismatch"


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ("../evil.py", ".."),
        ("/etc/passwd", "绝对路径"),
        ("C:/Windows/evil.py", "绝对路径"),
    ],
)
def test_verify_package_rejects_unsafe_paths(tmp_path: Path, entry: str, fragment: str) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(_manifest_for({})).encode("utf-8"))
        archive.writestr(entry, b"x")
    zip_path = _write_zip(tmp_path / "bad.zip", buffer.getvalue())
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path)
    assert fragment in str(excinfo.value)


@pytest.mark.parametrize(
    "raw",
    [
        "../evil.py",
        "apps/../../evil.py",
        "/etc/passwd",
        "C:/Windows/evil.py",
        "c:\\Windows\\evil.py",
        "apps\\web\\x.py",
        "apps/web/x.py:stream",
        "",
        "   ",
        "..",
    ],
)
def test_safe_relative_rejects_unsafe_paths(raw: str) -> None:
    with pytest.raises(gu.UpdateError):
        gu._safe_relative(raw)


def test_safe_relative_normalizes_dot_segments() -> None:
    assert gu._safe_relative("apps/./web/index.html") == "apps/web/index.html"
    assert gu._safe_relative("services/api/main.py") == "services/api/main.py"


def test_verify_package_rejects_symlink_entry(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(_manifest_for({})).encode("utf-8"))
        info = zipfile.ZipInfo("apps/link.py")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"target")
    zip_path = _write_zip(tmp_path / "link.zip", buffer.getvalue())
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path)
    assert excinfo.value.code == "unsafe_symlink"


@pytest.mark.parametrize("entry", ["data/store.json", "storage/db.sqlite3", ".env", "dist/out.zip"])
def test_verify_package_rejects_paths_outside_allowlist(tmp_path: Path, entry: str) -> None:
    files = {entry: b"x"}
    zip_path = _write_zip(tmp_path / "pkg.zip", _package_bytes(files))
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path)
    assert excinfo.value.code == "unsafe_path"


def test_verify_package_requires_manifest(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("services/a.py", b"a")
    zip_path = _write_zip(tmp_path / "no-manifest.zip", buffer.getvalue())
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path)
    assert excinfo.value.code == "missing_manifest"


@pytest.mark.parametrize("fmt", [2, 0, "x"])
def test_verify_package_rejects_unknown_manifest_format(tmp_path: Path, fmt) -> None:
    manifest = _manifest_for({"services/a.py": b"a"})
    manifest["format"] = fmt
    zip_path = _write_zip(tmp_path / "pkg.zip", _package_bytes({"services/a.py": b"a"}, manifest=manifest))
    with pytest.raises(gu.UpdateError):
        gu.verify_package(zip_path)


def test_verify_package_detects_file_sha_mismatch(tmp_path: Path) -> None:
    manifest = _manifest_for({"services/a.py": b"good"})
    zip_path = _write_zip(
        tmp_path / "pkg.zip", _package_bytes({"services/a.py": b"evil"}, manifest=manifest)
    )
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path)
    assert excinfo.value.code == "checksum_mismatch"


def test_verify_package_detects_size_mismatch(tmp_path: Path) -> None:
    files = {"services/a.py": b"good"}
    manifest = _manifest_for(files)
    manifest["files"][0]["size"] = len(files["services/a.py"]) + 1
    zip_path = _write_zip(tmp_path / "pkg.zip", _package_bytes(files, manifest=manifest))
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path)
    assert excinfo.value.code == "size_mismatch"


def test_verify_package_rejects_unlisted_and_missing_files(tmp_path: Path) -> None:
    files = {"services/a.py": b"a", "services/b.py": b"b"}
    manifest = _manifest_for({"services/a.py": b"a"})
    zip_path = _write_zip(tmp_path / "extra.zip", _package_bytes(files, manifest=manifest))
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path)
    assert excinfo.value.code == "unlisted_file"

    files = {"services/a.py": b"a"}
    manifest = _manifest_for({"services/a.py": b"a", "services/b.py": b"b"})
    zip_path = _write_zip(tmp_path / "missing.zip", _package_bytes(files, manifest=manifest))
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path)
    assert excinfo.value.code == "missing_file"


def test_verify_package_rejects_version_mismatch(tmp_path: Path) -> None:
    zip_path = _write_zip(
        tmp_path / "pkg.zip", _package_bytes({"services/a.py": b"a"}, version="1.2.0")
    )
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.verify_package(zip_path, expected_version="1.1.0")
    assert excinfo.value.code == "version_mismatch"


def test_verify_package_rejects_non_zip(tmp_path: Path) -> None:
    path = tmp_path / "nope.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(gu.UpdateError):
        gu.verify_package(path)
    with pytest.raises(gu.UpdateError):
        gu.verify_package(tmp_path / "missing.zip")


def test_extract_package_writes_files_and_clears_destination(tmp_path: Path) -> None:
    files = {
        "services/api/main.py": b"new-main",
        "apps/web/index.html": b"<html/>",
    }
    zip_path = _write_zip(tmp_path / "pkg.zip", _package_bytes(files))
    manifest = gu.verify_package(zip_path)
    dest = tmp_path / "staged"
    dest.mkdir()
    (dest / "stale.txt").write_text("old", encoding="utf-8")
    result = gu.extract_package(zip_path, dest, manifest=manifest)
    assert result == dest
    assert (dest / "services" / "api" / "main.py").read_bytes() == b"new-main"
    assert (dest / "apps" / "web" / "index.html").read_bytes() == b"<html/>"
    assert not (dest / "stale.txt").exists()


# ---------------------------------------------------------------------------
# build_release_archive
# ---------------------------------------------------------------------------


def _tree_with_noise(root: Path) -> Path:
    _make_project(root)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "tool.py").write_text("print('tool')\n", encoding="utf-8")
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "store.json").write_text("{}", encoding="utf-8")
    (root / "storage" / "logs").mkdir(parents=True, exist_ok=True)
    (root / "storage" / "logs" / "x.log").write_text("log", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    (root / ".venv" / "lib").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "lib" / "site.py").write_text("x", encoding="utf-8")
    (root / "services" / "api" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (root / "services" / "api" / "__pycache__" / "main.cpython.pyc").write_bytes(b"pyc")
    (root / "services" / "api" / "debug.log").write_text("log", encoding="utf-8")
    (root / "README.md").write_text("# readme\n", encoding="utf-8")
    (root / "dist").mkdir(parents=True, exist_ok=True)
    (root / "dist" / "old.zip").write_bytes(b"old")
    return root


def test_build_release_archive_includes_manifest_and_version(tmp_path: Path) -> None:
    root = _tree_with_noise(tmp_path / "proj")
    output = root / "dist" / "ai-listing-system-1.2.0.zip"
    result = gu.build_release_archive(root, output, version="1.2.0")

    assert result["version"] == "1.2.0"
    assert Path(result["path"]) == output
    assert result["sha256"] == gu.sha256_file(output)
    assert result["size"] == output.stat().st_size

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert archive.read("VERSION").decode("utf-8").strip() == "1.2.0"
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    assert manifest["format"] == 1
    assert manifest["version"] == "1.2.0"
    listed = {item["path"] for item in manifest["files"]}
    assert "VERSION" in listed
    assert "services/api/main.py" in names
    assert "scripts/tool.py" in names
    assert "README.md" in names
    assert "apps/web/index.html" in names
    for excluded in (
        "data/store.json",
        "storage/logs/x.log",
        "node_modules/pkg/index.js",
        ".venv/lib/site.py",
        "services/api/__pycache__/main.cpython.pyc",
        "services/api/debug.log",
        "dist/old.zip",
    ):
        assert excluded not in names
    assert result["files"] == len(names)
    # 产物自己能通过安装包校验
    assert gu.verify_package(output, expected_version="1.2.0")["version"] == "1.2.0"


def test_build_release_archive_supports_include_and_exclude(tmp_path: Path) -> None:
    root = _tree_with_noise(tmp_path / "proj")
    extra = root / "docs" / "extra.md"
    extra.write_text("# extra\n", encoding="utf-8")
    output = tmp_path / "out" / "pkg.zip"
    gu.build_release_archive(root, output, version="1.1.0", include=["docs/extra.md"], exclude=["apps"])
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "docs/extra.md" in names
    assert "docs/note.md" in names
    assert not any(name.startswith("apps/") for name in names)


def test_build_release_archive_rejects_outside_include(tmp_path: Path) -> None:
    root = _tree_with_noise(tmp_path / "proj")
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.build_release_archive(root, tmp_path / "pkg.zip", include=["data/store.json"])
    assert excinfo.value.code == "bad_include"


def test_build_release_archive_uses_local_version_by_default(tmp_path: Path) -> None:
    root = _tree_with_noise(tmp_path / "proj")
    result = gu.build_release_archive(root, tmp_path / "pkg.zip")
    assert result["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# pending / state 读写
# ---------------------------------------------------------------------------


def test_pending_and_state_roundtrip(tmp_path: Path) -> None:
    updates = tmp_path / "updates"
    pending = {"version": "1.1.0", "restart": True}
    assert gu.read_pending(updates) is None
    gu.write_pending(updates, pending)
    assert gu.read_pending(updates) == pending
    assert (updates / "pending.json").is_file()

    gu.write_update_state(updates, {"status": "success", "files": 2})
    assert gu.read_update_state(updates)["status"] == "success"
    assert list(updates.glob("*.tmp")) == []
    assert list(updates.glob(".*.tmp")) == []


def test_corrupt_pending_is_treated_as_absent(tmp_path: Path) -> None:
    updates = tmp_path / "updates"
    updates.mkdir()
    (updates / "pending.json").write_text("{not json", encoding="utf-8")
    assert gu.read_pending(updates) is None


# ---------------------------------------------------------------------------
# stage_update
# ---------------------------------------------------------------------------


def _stage_fixture(tmp_path: Path, *, version="1.1.0", with_checksum=True, package_version=None):
    root = _make_project(tmp_path / "proj", version="1.0.0")
    updates = root / "storage" / "updates"
    files = {"services/api/main.py": b"print('v2')\n", "apps/web/index.html": b"<html>v2</html>\n"}
    package = _package_bytes(files, version=package_version or version)
    package_name = f"ai-listing-system-{version}.zip"
    assets: dict[str, bytes] = {package_name: package}
    asset_list = [_asset(package_name)]
    if with_checksum:
        digest = hashlib.sha256(package).hexdigest()
        checksum_name = f"{package_name}.sha256"
        assets[checksum_name] = f"{digest}  {package_name}\n".encode("utf-8")
        asset_list.append(_asset(checksum_name))
    client = _FakeClient(assets=assets)
    release = _release(version, assets=asset_list)
    return root, updates, client, release


def test_stage_update_downloads_verifies_and_writes_pending(tmp_path: Path) -> None:
    root, updates, client, release = _stage_fixture(tmp_path)
    progress: list[str] = []
    pending = gu.stage_update(
        config=gu.GitHubConfig(owner="o", repo="r"),
        release=release,
        project_root=root,
        updates_dir=updates,
        client=client,
        progress=progress.append,
    )
    assert pending["version"] == "1.1.0"
    assert pending["tag"] == "v1.1.0"
    assert pending["checksum_verified"] is True
    assert pending["restart"] is True
    assert Path(pending["staged_dir"]).is_dir()
    assert (Path(pending["staged_dir"]) / "manifest.json").is_file()
    assert (Path(pending["staged_dir"]) / "services" / "api" / "main.py").read_bytes() == b"print('v2')\n"
    assert str(updates / "backups") in pending["backup_dir"]
    assert gu.read_pending(updates) == pending
    assert any("sha256 校验通过" in line for line in progress)


def test_stage_update_without_checksum_asset_still_works(tmp_path: Path) -> None:
    root, updates, client, release = _stage_fixture(tmp_path, with_checksum=False)
    pending = gu.stage_update(
        config=gu.GitHubConfig(owner="o", repo="r"),
        release=release,
        project_root=root,
        updates_dir=updates,
        client=client,
    )
    assert pending["checksum_verified"] is False
    assert len(pending["package_sha256"]) == 64


def test_stage_update_rejects_wrong_checksum(tmp_path: Path) -> None:
    root, updates, _client, release = _stage_fixture(tmp_path)
    package_name = "ai-listing-system-1.1.0.zip"
    package = _package_bytes(
        {"services/api/main.py": b"print('v2')\n", "apps/web/index.html": b"<html>v2</html>\n"}
    )
    client = _FakeClient(
        assets={package_name: package, f"{package_name}.sha256": b"0" * 64 + b"\n"}
    )
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.stage_update(
            config=gu.GitHubConfig(owner="o", repo="r"),
            release=release,
            project_root=root,
            updates_dir=updates,
            client=client,
        )
    assert excinfo.value.code == "checksum_mismatch"


def test_stage_update_rejects_version_mismatch(tmp_path: Path) -> None:
    root, updates, client, release = _stage_fixture(tmp_path, package_version="9.9.9")
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.stage_update(
            config=gu.GitHubConfig(owner="o", repo="r"),
            release=release,
            project_root=root,
            updates_dir=updates,
            client=client,
        )
    assert excinfo.value.code == "version_mismatch"


def test_stage_update_requires_zip_asset(tmp_path: Path) -> None:
    root = _make_project(tmp_path / "proj")
    updates = root / "storage" / "updates"
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.stage_update(
            config=gu.GitHubConfig(owner="o", repo="r"),
            release=_release("1.1.0", assets=[_asset("notes.txt")]),
            project_root=root,
            updates_dir=updates,
            client=_FakeClient(),
        )
    assert excinfo.value.code == "no_package"


# ---------------------------------------------------------------------------
# apply_staged_update / rollback_update
# ---------------------------------------------------------------------------


def _apply_fixture(tmp_path: Path):
    root = _make_project(tmp_path / "proj", version="1.0.0")
    (root / "docs" / "keep.md").write_text("keep\n", encoding="utf-8")
    updates = root / "storage" / "updates"
    version = "1.1.0"
    files = {
        "services/api/main.py": b"print('v2')\n",
        "apps/web/index.html": b"<html>v2</html>\n",
    }
    staged = updates / "staged" / version
    for rel, data in files.items():
        target = staged / Path(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (staged / "manifest.json").write_text(
        json.dumps(_manifest_for(files, version)), encoding="utf-8"
    )
    pending = {
        "version": version,
        "tag": f"v{version}",
        "staged_dir": str(staged),
        "package_path": str(updates / "downloads" / "pkg.zip"),
        "package_sha256": "0" * 64,
        "checksum_verified": True,
        "backup_dir": str(updates / "backups" / "20260101-000000-from-1.0.0"),
        "staged_at": "2026-01-01T00:00:00+00:00",
        "restart": True,
    }
    return root, updates, pending


def test_apply_staged_update_replaces_files_and_records_backup(tmp_path: Path) -> None:
    root, updates, pending = _apply_fixture(tmp_path)
    gu.write_pending(updates, pending)
    logs: list[str] = []
    state = gu.apply_staged_update(project_root=root, pending=pending, log=logs.append)

    assert state["status"] == "success"
    assert state["version"] == "1.1.0"
    assert state["files"] == 2
    assert (root / "services" / "api" / "main.py").read_text(encoding="utf-8") == "print('v2')\n"
    assert (root / "apps" / "web" / "index.html").read_text(encoding="utf-8") == "<html>v2</html>\n"
    # 未在清单里的文件不能被删
    assert (root / "docs" / "keep.md").read_text(encoding="utf-8") == "keep\n"

    backup_dir = Path(pending["backup_dir"])
    backup = json.loads((backup_dir / "backup.json").read_text(encoding="utf-8"))
    assert backup["version"] == "1.1.0"
    by_path = {item["path"]: item for item in backup["files"]}
    assert by_path["services/api/main.py"]["existed"] is True
    assert (backup_dir / "services" / "api" / "main.py").read_text(encoding="utf-8") == "print('v1')\n"

    state_on_disk = gu.read_update_state(updates)
    assert state_on_disk["status"] == "success"
    assert state_on_disk["files"] == 2
    assert list(root.rglob("*.updating")) == []
    assert any("更新完成" in line for line in logs)
    # 安装完成后必须清掉 pending，否则设置页会一直显示「待安装」并禁用更新按钮。
    assert gu.read_pending(updates) is None


def test_apply_staged_update_restores_when_replace_fails(tmp_path: Path, monkeypatch) -> None:
    root, updates, pending = _apply_fixture(tmp_path)
    gu.write_pending(updates, pending)
    original = gu._atomic_copy
    calls = {"count": 0}

    def flaky(source, target):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("模拟磁盘错误")
        return original(source, target)

    monkeypatch.setattr(gu, "_atomic_copy", flaky)
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.apply_staged_update(project_root=root, pending=pending)
    assert "安装更新失败" in str(excinfo.value)
    # 第一个文件已回滚，第二个文件没被动过
    assert (root / "services" / "api" / "main.py").read_text(encoding="utf-8") == "print('v1')\n"
    assert (root / "apps" / "web" / "index.html").read_text(encoding="utf-8") == "<html>v1</html>\n"
    state = gu.read_update_state(updates)
    assert state["status"] == "failed"
    assert state["version"] == "1.1.0"
    assert state["error"]
    # 失败已回滚，pending 也要清掉，操作员才能重新下载重试。
    assert gu.read_pending(updates) is None


def test_apply_staged_update_rejects_missing_staged_dir(tmp_path: Path) -> None:
    root, _updates, pending = _apply_fixture(tmp_path)
    pending["staged_dir"] = str(tmp_path / "nope")
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.apply_staged_update(project_root=root, pending=pending)
    assert excinfo.value.code == "staged_missing"


def test_rollback_update_restores_original_files(tmp_path: Path) -> None:
    root, updates, pending = _apply_fixture(tmp_path)
    gu.apply_staged_update(project_root=root, pending=pending)
    # 新增文件在安装前不存在；回滚后应被删除
    (root / "apps" / "web" / "index.html").unlink()
    (root / "apps" / "web" / "index.html").write_text("<html>v2</html>\n", encoding="utf-8")

    result = gu.rollback_update(
        project_root=root,
        backup_dir=Path(pending["backup_dir"]),
        version="1.1.0",
    )
    assert result["status"] == "rolled_back"
    assert result["version"] == "1.1.0"
    assert result["files"] == 2
    assert (root / "services" / "api" / "main.py").read_text(encoding="utf-8") == "print('v1')\n"
    assert (root / "apps" / "web" / "index.html").read_text(encoding="utf-8") == "<html>v1</html>\n"
    assert gu.read_update_state(updates)["status"] == "rolled_back"


def test_rollback_removes_files_that_did_not_exist_before(tmp_path: Path) -> None:
    root = _make_project(tmp_path / "proj")
    updates = root / "storage" / "updates"
    backup_dir = updates / "backups" / "20260101-000000-from-1.0.0"
    backup_dir.mkdir(parents=True)
    new_file = root / "docs" / "brand-new.md"
    new_file.write_text("new\n", encoding="utf-8")
    (backup_dir / "backup.json").write_text(
        json.dumps(
            {
                "version": "1.1.0",
                "files": [{"path": "docs/brand-new.md", "backup_path": "docs/brand-new.md", "existed": False}],
            }
        ),
        encoding="utf-8",
    )
    result = gu.rollback_update(project_root=root, backup_dir=backup_dir)
    assert result["files"] == 1
    assert not new_file.exists()


def test_rollback_update_requires_backup_manifest(tmp_path: Path) -> None:
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.rollback_update(project_root=tmp_path, backup_dir=tmp_path / "nope")
    assert excinfo.value.code == "backup_missing"


def test_find_latest_backup(tmp_path: Path) -> None:
    updates = tmp_path / "updates"
    assert gu.find_latest_backup(updates) is None
    older = updates / "backups" / "20260101-000000-from-1.0.0"
    newer = updates / "backups" / "20260102-000000-from-1.1.0"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "backup.json").write_text("{}", encoding="utf-8")
    assert gu.find_latest_backup(updates) == older
    (newer / "backup.json").write_text("{}", encoding="utf-8")
    assert gu.find_latest_backup(updates) == newer


# ---------------------------------------------------------------------------
# 进程管理
# ---------------------------------------------------------------------------


def test_spawn_update_runner_is_detached(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "apply_update.py").write_text("# runner\n", encoding="utf-8")
    pending = tmp_path / "pending.json"
    pending.write_text("{}", encoding="utf-8")
    captured: dict = {}

    class _Proc:
        pid = 4321

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(gu.subprocess, "Popen", fake_popen)
    pid = gu.spawn_update_runner(
        project_root=root,
        pending_path=pending,
        log_dir=tmp_path / "logs",
        api_pid=777,
    )
    assert pid == 4321
    assert captured["command"][1].endswith("apply_update.py")
    assert "--pending" in captured["command"]
    assert "--wait-pid" in captured["command"]
    assert "777" in captured["command"]
    assert captured["kwargs"]["cwd"] == str(root)
    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL
    assert list((tmp_path / "logs").glob("apply-*.log"))
    if os.name == "nt":
        assert captured["kwargs"]["creationflags"] & subprocess.DETACHED_PROCESS
        assert captured["kwargs"]["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        # 不弹黑框：detached 进程也带 SW_HIDE 的 startupinfo 兜底。
        assert captured["kwargs"].get("startupinfo") is not None
    else:
        assert captured["kwargs"]["start_new_session"] is True


def test_spawn_update_runner_requires_script(tmp_path: Path) -> None:
    pending = tmp_path / "pending.json"
    pending.write_text("{}", encoding="utf-8")
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.spawn_update_runner(
            project_root=tmp_path, pending_path=pending, log_dir=tmp_path / "logs"
        )
    assert excinfo.value.code == "runner_missing"


def test_restart_api_spawns_module_with_logs(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    class _Proc:
        pid = 2468

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(gu.subprocess, "Popen", fake_popen)
    log_dir = tmp_path / "logs"
    pid = gu.restart_api(
        project_root=tmp_path,
        log_dir=log_dir,
        env={"API_PORT": "9999"},
        python_executable="python-test",
    )
    assert pid == 2468
    assert captured["command"] == ["python-test", "-m", "services.api.main"]
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["API_PORT"] == "9999"
    assert captured["kwargs"]["env"]["PYTHONUNBUFFERED"] == "1"
    assert (log_dir / "api.out.log").is_file()
    assert (log_dir / "api.err.log").is_file()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/o/r.git", False),
        ("git@github.com:o/r.git", False),
        ("file:///tmp/repo.git", True),
        ("C:\\repo", True),
        ("\\\\nas\\share\\repo", True),
        ("/mnt/repo", True),
        ("", True),
    ],
)
def test_is_local_remote(url: str, expected: bool) -> None:
    assert gu._is_local_remote(url) is expected


# ---------------------------------------------------------------------------
# run_release
# ---------------------------------------------------------------------------


class _GitStub:
    def __init__(self, *, tag_exists: bool = False, remote: str = "https://github.com/o/r.git") -> None:
        self.calls: list[tuple[list[str], dict | None]] = []
        self.tag_exists = tag_exists
        self.remote = remote
        self.head = "abc1234"

    def __call__(self, args, *, cwd, env=None, check=True, timeout=120.0):
        argv = [str(item) for item in args]
        self.calls.append((argv, dict(env) if env is not None else None))
        stdout = ""
        code = 0
        if argv[:2] == ["rev-parse", "--show-toplevel"]:
            stdout = str(cwd)
        elif argv[:3] == ["rev-parse", "-q", "--verify"]:
            code = 0 if self.tag_exists else 1
            stdout = "refs/tags/x" if self.tag_exists else ""
        elif argv[:2] == ["diff", "--cached"]:
            code = 1
        elif argv[:2] == ["rev-parse", "HEAD"]:
            stdout = self.head
        elif argv[:2] == ["rev-parse", "--abbrev-ref"]:
            stdout = "main"
        elif argv[:2] == ["remote", "get-url"]:
            stdout = self.remote
        return subprocess.CompletedProcess(["git", *argv], code, stdout, "")


class _FakeReleaseClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.created: list[dict] = []
        self.uploads: list[tuple[str, int, str]] = []

    def create_release(self, **kwargs):
        self.created.append(kwargs)
        if self.fail:
            raise gu.UpdateError("模拟 GitHub 故障", code="http_error", status=500)
        return gu.ReleaseInfo(
            version=kwargs["tag"].lstrip("v"),
            tag=kwargs["tag"],
            html_url=f"https://github.com/o/r/releases/tag/{kwargs['tag']}",
            raw={"upload_url": "https://uploads.github.com/repos/o/r/releases/9/assets{?name,label}"},
        )

    def upload_asset(self, upload_url, filename, payload, content_type="application/octet-stream"):
        self.uploads.append((filename, len(payload), content_type))
        return {}


def _release_project(tmp_path: Path) -> Path:
    root = _tree_with_noise(tmp_path / "proj")
    (root / "dist" / "old.zip").unlink()
    return root


def test_run_release_dry_run_builds_artifact_only(tmp_path: Path, monkeypatch) -> None:
    root = _release_project(tmp_path)
    monkeypatch.setattr(gu, "_is_git_repository", lambda _root: False)
    result = gu.run_release(
        project_root=root,
        config=gu.GitHubConfig(owner="o", repo="r"),
        updates_dir=root / "storage" / "updates",
        version="1.1.0",
        dry_run=True,
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["version"] == "1.1.0"
    artifact = Path(result["artifact"]["path"])
    assert artifact.is_file()
    assert artifact.name == "ai-listing-system-1.1.0.zip"
    assert gu.verify_package(artifact, expected_version="1.1.0")["version"] == "1.1.0"
    checksum = artifact.with_name(artifact.name + ".sha256")
    assert gu.sha256_file(artifact) in checksum.read_text(encoding="utf-8")
    # dry_run 不写 VERSION
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0"


def test_run_release_requires_version_or_bump(tmp_path: Path) -> None:
    root = _release_project(tmp_path)
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.run_release(
            project_root=root,
            config=gu.GitHubConfig(owner="o", repo="r"),
            updates_dir=tmp_path / "updates",
        )
    assert excinfo.value.code == "version_required"


def test_run_release_requires_git_repository(tmp_path: Path, monkeypatch) -> None:
    root = _release_project(tmp_path)
    monkeypatch.setattr(gu, "_is_git_repository", lambda _root: False)
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.run_release(
            project_root=root,
            config=gu.GitHubConfig(owner="o", repo="r"),
            updates_dir=tmp_path / "updates",
            version="1.1.0",
        )
    assert excinfo.value.code == "not_git_repo"


def test_run_release_success_path(tmp_path: Path, monkeypatch) -> None:
    root = _release_project(tmp_path)
    stub = _GitStub()
    client = _FakeReleaseClient()
    monkeypatch.setattr(gu, "_run_git", stub)
    monkeypatch.setattr(gu, "GitHubClient", lambda config, **_kwargs: client)

    result = gu.run_release(
        project_root=root,
        config=gu.GitHubConfig(owner="o", repo="r", token="SECRET"),
        updates_dir=tmp_path / "updates",
        version="1.1.0",
        notes="修复若干问题",
    )
    assert result["ok"] is True
    assert result["version"] == "1.1.0"
    assert result["tag"] == "v1.1.0"
    assert result["pushed"] is True
    assert result["commit"] == "abc1234"
    assert result["release_url"].endswith("/v1.1.0")
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.1.0"

    assert client.created[0]["tag"] == "v1.1.0"
    assert client.created[0]["body"] == "修复若干问题"
    # 断言 token 只出现在环境变量里，argv 中绝不出现
    assert stub.calls
    for argv, _env in stub.calls:
        assert "SECRET" not in " ".join(argv)
    push_calls = [(argv, env) for argv, env in stub.calls if argv and argv[0] == "push"]
    assert push_calls, "应当推送分支与标签"
    for _argv, env in push_calls:
        assert env is not None
        expected = base64.b64encode(b"x-access-token:SECRET").decode("ascii")
        assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: basic {expected}"
        assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
        assert env["GIT_CONFIG_COUNT"] == "1"
    assert [item[0] for item in client.uploads] == [
        "ai-listing-system-1.1.0.zip",
        "ai-listing-system-1.1.0.zip.sha256",
    ]


def test_run_release_uses_release_notes_file(tmp_path: Path, monkeypatch) -> None:
    root = _release_project(tmp_path)
    notes_file = root / "docs" / "releases" / "v1.1.0.md"
    notes_file.parent.mkdir(parents=True, exist_ok=True)
    notes_file.write_text("# 发布说明\n\n- 修复 A\n", encoding="utf-8")
    stub = _GitStub()
    client = _FakeReleaseClient()
    monkeypatch.setattr(gu, "_run_git", stub)
    monkeypatch.setattr(gu, "GitHubClient", lambda config, **_kwargs: client)
    result = gu.run_release(
        project_root=root,
        config=gu.GitHubConfig(owner="o", repo="r", token="T"),
        updates_dir=tmp_path / "updates",
        version="1.1.0",
    )
    assert result["ok"] is True
    assert "发布说明" in client.created[0]["body"]


def test_run_release_restores_version_and_tag_when_release_creation_fails(
    tmp_path: Path, monkeypatch
) -> None:
    root = _release_project(tmp_path)
    stub = _GitStub()
    client = _FakeReleaseClient(fail=True)
    monkeypatch.setattr(gu, "_run_git", stub)
    monkeypatch.setattr(gu, "GitHubClient", lambda config, **_kwargs: client)

    with pytest.raises(gu.UpdateError) as excinfo:
        gu.run_release(
            project_root=root,
            config=gu.GitHubConfig(owner="o", repo="r", token="T"),
            updates_dir=tmp_path / "updates",
            version="1.1.0",
        )
    assert "创建 Release" in str(excinfo.value)
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0"
    assert any(argv[:2] == ["tag", "-d"] for argv, _env in stub.calls)


def test_run_release_rejects_existing_tag_and_restores_version(tmp_path: Path, monkeypatch) -> None:
    root = _release_project(tmp_path)
    stub = _GitStub(tag_exists=True)
    monkeypatch.setattr(gu, "_run_git", stub)
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.run_release(
            project_root=root,
            config=gu.GitHubConfig(owner="o", repo="r", token="T"),
            updates_dir=tmp_path / "updates",
            version="1.1.0",
        )
    assert "已存在" in str(excinfo.value)
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0"


def test_run_release_requires_token_for_remote_push(tmp_path: Path, monkeypatch) -> None:
    root = _release_project(tmp_path)
    stub = _GitStub()
    monkeypatch.setattr(gu, "_run_git", stub)
    with pytest.raises(gu.UpdateError) as excinfo:
        gu.run_release(
            project_root=root,
            config=gu.GitHubConfig(owner="o", repo="r", token=""),
            updates_dir=tmp_path / "updates",
            version="1.1.0",
        )
    assert excinfo.value.code == "token_missing"
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0"


# ---------------------------------------------------------------------------
# 模块边界
# ---------------------------------------------------------------------------


def test_module_does_not_import_main_or_storage() -> None:
    source = Path(gu.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    plain_imports: set[str] = set()
    from_imports: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            plain_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            from_imports.extend((node.module, alias.name) for alias in node.names)
    assert plain_imports & {"main", "storage"} == set()
    assert not any(
        module in {"services.api.main", "services.api.storage"}
        for module, _name in from_imports
    )
    assert not any(
        module == "services.api" and name in {"main", "storage"}
        for module, name in from_imports
    )
    # 唯一的 services.api 依赖是可选导入的 settings
    assert ("services.api", "settings") in from_imports

# ---------------------------------------------------------------------------
# scripts/apply_update.py 与 scripts/publish_release.py（导入执行，不启动进程）
# ---------------------------------------------------------------------------


def _load_script(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_unit_script_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _script_root(tmp_path: Path) -> Path:
    root = _make_project(tmp_path / "proj", version="1.0.0")
    updates = root / "storage" / "updates"
    staged = updates / "staged" / "1.1.0"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "manifest.json").write_text(
        json.dumps(_manifest_for({"services/api/main.py": b"print('v2')\n"}, "1.1.0")),
        encoding="utf-8",
    )
    (updates / "pending.json").write_text(
        json.dumps(
            {
                "version": "1.1.0",
                "tag": "v1.1.0",
                "staged_dir": str(staged),
                "package_path": str(updates / "downloads" / "pkg.zip"),
                "package_sha256": "0" * 64,
                "checksum_verified": True,
                "backup_dir": str(updates / "backups" / "20260101-000000-from-1.0.0"),
                "staged_at": "2026-01-01T00:00:00+00:00",
                "restart": True,
            }
        ),
        encoding="utf-8",
    )
    return root


def _script_argv(root: Path, *extra: str) -> list[str]:
    argv = ["--pending", str(root / "storage" / "updates" / "pending.json"), "--root", str(root)]
    argv.extend(extra)
    return argv


def test_apply_script_windows_process_alive_sets_ctypes_signatures(monkeypatch) -> None:
    if os.name != "nt":
        pytest.skip("Windows 专用：ctypes kernel32 分支")
    script = _load_script("apply_update")
    import ctypes

    class _FakeKernel32:
        def __init__(self, handle=4242, exit_code=259) -> None:
            self.handle = handle
            self.exit_code = exit_code
            self.calls: list[tuple] = []

            def OpenProcess(access, inherit, pid):
                self.calls.append(("OpenProcess", access, inherit, pid))
                return self.handle

            def GetExitCodeProcess(handle_arg, code_ref):
                self.calls.append(("GetExitCodeProcess", handle_arg))
                code_ref._obj.value = self.exit_code
                return 1

            def CloseHandle(handle_arg):
                self.calls.append(("CloseHandle", handle_arg))
                return 1

            self.OpenProcess = OpenProcess
            self.GetExitCodeProcess = GetExitCodeProcess
            self.CloseHandle = CloseHandle

    fake = _FakeKernel32(exit_code=259)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=fake))

    assert script._process_alive(4242) is True
    assert fake.OpenProcess.argtypes == [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    assert fake.OpenProcess.restype is ctypes.c_void_p
    assert fake.GetExitCodeProcess.argtypes == [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    assert fake.GetExitCodeProcess.restype is ctypes.c_int
    assert fake.CloseHandle.argtypes == [ctypes.c_void_p]
    assert fake.CloseHandle.restype is ctypes.c_int
    assert fake.calls[0] == ("OpenProcess", 0x1000, False, 4242)
    assert ("CloseHandle", 4242) in fake.calls

    finished = _FakeKernel32(exit_code=0)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=finished))
    assert script._process_alive(4242) is False

    missing = _FakeKernel32(handle=0)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=missing))
    assert script._process_alive(4242) is False
    assert script._process_alive(0) is False


def test_apply_script_aborts_when_api_exit_times_out(tmp_path: Path, monkeypatch) -> None:
    script = _load_script("apply_update")
    root = _script_root(tmp_path)
    calls = {"apply": 0, "restart": 0}

    monkeypatch.setattr(script, "_wait_for_pid_exit", lambda pid, timeout, log_path: False)
    monkeypatch.setattr(script, "_process_alive", lambda pid: True)
    monkeypatch.setattr(
        script,
        "_restart_api",
        lambda root_arg, log_dir, log_path: calls.__setitem__("restart", calls["restart"] + 1),
    )
    monkeypatch.setattr(
        script.github_updates,
        "apply_staged_update",
        lambda **kwargs: calls.__setitem__("apply", calls["apply"] + 1),
    )

    code = script.main(_script_argv(root, "--wait-pid", "1234", "--timeout", "5"))
    assert code == 1
    assert calls["apply"] == 0, "超时后绝不能替换文件"
    assert calls["restart"] == 0, "API 还活着时不应该再起一个"
    state = json.loads((root / "storage" / "updates" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "超时" in state["error"]
    assert "未安装" in state["error"]
    assert (root / "services" / "api" / "main.py").read_text(encoding="utf-8") == "print('v1')\n"
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0"


def test_apply_script_aborts_when_port_never_frees(tmp_path: Path, monkeypatch) -> None:
    script = _load_script("apply_update")
    root = _script_root(tmp_path)
    calls = {"apply": 0, "restart": 0}

    monkeypatch.setattr(script, "_wait_for_port_release", lambda port, timeout, log_path: False)
    monkeypatch.setattr(script, "_port_free", lambda port: False)
    monkeypatch.setattr(
        script,
        "_restart_api",
        lambda root_arg, log_dir, log_path: calls.__setitem__("restart", calls["restart"] + 1),
    )
    monkeypatch.setattr(
        script.github_updates,
        "apply_staged_update",
        lambda **kwargs: calls.__setitem__("apply", calls["apply"] + 1),
    )

    code = script.main(_script_argv(root))
    assert code == 1
    assert calls["apply"] == 0
    assert calls["restart"] == 0
    state = json.loads((root / "storage" / "updates" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "端口" in state["error"]


def test_apply_script_applies_and_restarts_when_wait_succeeds(tmp_path: Path, monkeypatch) -> None:
    script = _load_script("apply_update")
    root = _script_root(tmp_path)
    calls = {"versions": [], "restart": 0}

    monkeypatch.setattr(script, "_wait_for_pid_exit", lambda pid, timeout, log_path: True)
    monkeypatch.setattr(script, "_wait_for_port_release", lambda port, timeout, log_path: True)

    def fake_apply(**kwargs):
        calls["versions"].append(kwargs["pending"]["version"])
        return {"status": "success", "version": "1.1.0", "files": 1}

    def fake_restart(root_arg, log_dir, log_path):
        calls["restart"] += 1
        return 999

    monkeypatch.setattr(script.github_updates, "apply_staged_update", fake_apply)
    monkeypatch.setattr(script, "_restart_api", fake_restart)

    code = script.main(_script_argv(root, "--wait-pid", "4321"))
    assert code == 0
    assert calls["versions"] == ["1.1.0"]
    assert calls["restart"] == 1


def test_apply_script_rolls_back_and_restarts_on_apply_failure(tmp_path: Path, monkeypatch) -> None:
    script = _load_script("apply_update")
    root = _script_root(tmp_path)
    calls = {"rolled": [], "restart": 0}

    monkeypatch.setattr(script, "_wait_for_pid_exit", lambda pid, timeout, log_path: True)
    monkeypatch.setattr(script, "_wait_for_port_release", lambda port, timeout, log_path: True)

    def boom(**kwargs):
        raise gu.UpdateError("模拟安装失败", code="apply_failed")

    def fake_rollback(**kwargs):
        calls["rolled"].append(kwargs["version"])
        return {"status": "rolled_back", "version": kwargs["version"], "files": 1}

    def fake_restart(root_arg, log_dir, log_path):
        calls["restart"] += 1
        return 999

    monkeypatch.setattr(script.github_updates, "apply_staged_update", boom)
    monkeypatch.setattr(script.github_updates, "rollback_update", fake_rollback)
    monkeypatch.setattr(script, "_restart_api", fake_restart)

    code = script.main(_script_argv(root, "--wait-pid", "4321"))
    assert code == 1
    assert calls["rolled"] == ["1.1.0"]
    assert calls["restart"] == 1
    state = json.loads((root / "storage" / "updates" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "模拟安装失败" in state["error"]


def test_apply_script_rejects_broken_pending_json(tmp_path: Path) -> None:
    script = _load_script("apply_update")
    root = _make_project(tmp_path / "proj")
    updates = root / "storage" / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    broken = updates / "pending.json"
    broken.write_text("{broken", encoding="utf-8")
    code = script.main(["--pending", str(broken), "--root", str(root)])
    assert code == 2


def test_publish_script_requires_version_or_bump() -> None:
    script = _load_script("publish_release")
    with pytest.raises(SystemExit) as excinfo:
        script.main([])
    assert excinfo.value.code == 2


def test_publish_script_reads_notes_file(tmp_path: Path) -> None:
    script = _load_script("publish_release")
    notes = tmp_path / "notes.md"
    notes.write_text("# 发布说明\n\n- 修复 A\n", encoding="utf-8")
    parser = script._build_parser()
    args = parser.parse_args(["--version", "1.2.0", "--notes-file", str(notes)])
    assert script._read_notes(args) == "# 发布说明\n\n- 修复 A"
    args2 = parser.parse_args(["--version", "1.2.0", "--notes", "直接说明"])
    assert script._read_notes(args2) == "直接说明"


def test_publish_script_wires_config_and_run_release(tmp_path: Path, monkeypatch, capsys) -> None:
    script = _load_script("publish_release")
    root = _make_project(tmp_path / "proj")
    monkeypatch.setattr(script, "PROJECT_ROOT", root)
    monkeypatch.setattr(script, "_runtime_updates", lambda: {})
    captured: dict = {}

    def fake_run_release(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "dry_run": True,
            "version": "1.2.0",
            "artifact": {"path": str(root / "dist" / "ai-listing-system-1.2.0.zip")},
        }

    monkeypatch.setattr(script.github_updates, "run_release", fake_run_release)
    code = script.main(["--version", "1.2.0", "--dry-run", "--repo", "owner/name", "--token", "T"])
    assert code == 0
    assert captured["config"].slug == "owner/name"
    assert captured["config"].token == "T"
    assert captured["dry_run"] is True
    assert captured["version"] == "1.2.0"
    assert captured["push"] is True
    out = capsys.readouterr().out
    assert "dry_run 完成" in out
    assert '"version": "1.2.0"' in out


# ---------------------------------------------------------------------------
# 分流代理：GITHUB_UPDATE_PROXY / GITHUB_UPDATE_DIRECT_HOSTS
# ---------------------------------------------------------------------------


def test_proxy_settings_default_direct_hosts(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_UPDATE_PROXY", raising=False)
    monkeypatch.delenv("GITHUB_UPDATE_DIRECT_HOSTS", raising=False)
    proxy, direct = gu._github_proxy_settings()
    assert proxy == ""
    assert direct == ("api.github.com",)


def test_proxy_settings_custom(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_UPDATE_PROXY", " http://127.0.0.1:7897 ")
    monkeypatch.setenv("GITHUB_UPDATE_DIRECT_HOSTS", "api.github.com, GHE.example.com,")
    proxy, direct = gu._github_proxy_settings()
    assert proxy == "http://127.0.0.1:7897"
    assert direct == ("api.github.com", "ghe.example.com")


def test_split_proxy_handler_direct_host_stays_direct(monkeypatch) -> None:
    import urllib.request

    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False, raising=False)
    handler = gu._SplitProxyHandler("http://127.0.0.1:7897", ("api.github.com",))
    request = urllib.request.Request("https://api.github.com/repos/owner/name/releases")
    assert handler.proxy_open(request, "http://127.0.0.1:7897", "https") is None
    assert request.host == "api.github.com"  # 未被改写到代理 = 直连


def test_split_proxy_handler_other_host_goes_via_proxy(monkeypatch) -> None:
    import urllib.request

    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False, raising=False)
    handler = gu._SplitProxyHandler("http://127.0.0.1:7897", ("api.github.com",))
    request = urllib.request.Request("https://objects.githubusercontent.com/pkg.zip")
    assert handler.proxy_open(request, "http://127.0.0.1:7897", "https") is None
    assert "127.0.0.1:7897" in request.host  # set_proxy 已把请求改写到代理


def test_http_opener_uses_split_proxy_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_UPDATE_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setattr(gu, "_HTTP_OPENER", None)
    opener = gu._http_opener()
    assert any(isinstance(handler, gu._SplitProxyHandler) for handler in opener.handlers)
    monkeypatch.setattr(gu, "_HTTP_OPENER", None)


def test_http_opener_default_without_proxy_env(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_UPDATE_PROXY", raising=False)
    monkeypatch.setattr(gu, "_HTTP_OPENER", None)
    opener = gu._http_opener()
    assert not any(isinstance(handler, gu._SplitProxyHandler) for handler in opener.handlers)
    monkeypatch.setattr(gu, "_HTTP_OPENER", None)
