"""GitHub 版本发布与在线更新引擎（标准库实现）。

本模块只依赖标准库与 services.api.settings（可选，导入失败时自行推导项目根），
不得 import main / storage，避免循环依赖。API 层与
scripts/apply_update.py、scripts/publish_release.py 直接 import 这里的
公开常量/类/函数。

安全要点：
- 安装包（zip）路径校验：拒绝绝对路径、..、反斜杠、符号链接与清单外文件；
- 只允许写入 RELEASE_ROOTS / RELEASE_FILES 白名单内的路径，运行数据目录
  （data/、storage/、.env 等）永远不会被更新覆盖；
- 整包 sha256 + 逐文件 sha256/size 校验，然后再解压、暂存、安装；
- GitHub Token 只出现在 HTTP 请求头与 git 的 GIT_CONFIG_* 环境变量里，绝不写入
  argv、日志或错误信息。
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import posixpath
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # 可选依赖：settings 只用来取 PROJECT_ROOT，导入失败时退回文件位置推导
    from services.api import settings as _settings

    PROJECT_ROOT = Path(_settings.PROJECT_ROOT).resolve()
except Exception:  # noqa: BLE001 - 缺少第三方依赖时仍要能用
    PROJECT_ROOT = Path(__file__).resolve().parents[2]

try:  # 运行期子进程统一不弹控制台窗口（Windows GUI 宿主下必须）
    from services.api.subprocess_utils import no_console_window_kwargs
except Exception:  # noqa: BLE001 - 独立运行时退回空实现
    def no_console_window_kwargs() -> dict[str, Any]:  # type: ignore[misc]
        return {}

# 安装包大小上限（下载与校验共用）。
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
# 更新时允许写入的目录白名单。
RELEASE_ROOTS = ("apps", "services", "scripts", "workers", "browser_extension", "docs")
# 更新时允许写入的顶层文件白名单。
RELEASE_FILES = (
    "VERSION",
    "requirements.txt",
    "README.md",
    "AGENTS.md",
    "start-api.bat",
    "start-api-silent.vbs",
    "run_api_silent.pyw",
    "preview_launch.py",
)

DEFAULT_API_URL = "https://api.github.com"
# 开箱即用的版本通道：没填仓库时默认用官方仓库，操作员不用先配置也能检查更新/发布。
# 想指向别的仓库照旧在 设置 → 版本更新 里改（或 GITHUB_UPDATE_REPO 环境变量）。
DEFAULT_REPO = "xianfish1/ai-listing-system"
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = "ai-listing-system-updater"

PENDING_FILE = "pending.json"
STATE_FILE = "state.json"
BACKUP_FILE = "backup.json"
MANIFEST_FILE = "manifest.json"


class UpdateError(RuntimeError):
    """更新/发布流程的公开异常。

    code 供调用方分类（如 checksum_mismatch），status 是 GitHub HTTP 状态码
    （非 HTTP 错误时为 None）。
    """

    def __init__(self, message: str, *, code: str = "update_error", status: int | None = None) -> None:
        super().__init__(str(message))
        self.message = str(message)
        self.code = str(code or "update_error")
        self.status = status


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if not callable(log):
        return
    try:
        log(str(message))
    except Exception:  # noqa: BLE001 - 日志失败不能影响发布/更新流程
        pass


def _notify(progress: Callable[[str], None] | None, message: str) -> None:
    if not callable(progress):
        return
    try:
        progress(str(message))
    except Exception:  # noqa: BLE001 - 进度回调失败不影响流程
        pass


# ---------------------------------------------------------------------------
# 版本号
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(
    r"^v?(\d{1,9})(?:\.(\d{1,9}))?(?:\.(\d{1,9}))?(?:-([0-9A-Za-z.\-]+))?(?:\+([0-9A-Za-z.\-]+))?$",
    re.IGNORECASE,
)


def normalize_version(value: Any) -> str:
    """把 v1.2.3 / 1.2 / 1.2.3-beta.1 归一化成可比较的版本号。

    build metadata（+abc）会被丢弃（语义上不参与比较）；非法版本号抛
    UpdateError。
    """

    text = _text(value)
    if not text:
        raise UpdateError("版本号不能为空", code="invalid_version")
    match = _VERSION_RE.match(text)
    if match is None:
        raise UpdateError(f"版本号格式不正确：{text}（示例 1.2.3）", code="invalid_version")
    major, minor, patch = (int(part or 0) for part in match.group(1, 2, 3))
    prerelease = _text(match.group(4)).strip(".")
    normalized = f"{major}.{minor}.{patch}"
    if prerelease:
        normalized = f"{normalized}-{prerelease}"
    return normalized


def version_key(value: Any) -> tuple[Any, ...]:
    """返回可直接比较的版本 key（实现 semver 优先级规则）。

    1.2.3-beta.1 < 1.2.3；数字段按数值比较，数字标识符低于字母标识符。
    """

    normalized = normalize_version(value)
    core, _sep, prerelease = normalized.partition("-")
    parts = [int(part) for part in core.split(".")]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[0], parts[1], parts[2]
    if not prerelease:
        return (major, minor, patch, 1, ())
    identifiers: list[tuple[int, int, str]] = []
    for raw in prerelease.split("."):
        item = _text(raw)
        if not item:
            continue
        if item.isdigit():
            identifiers.append((0, int(item), ""))
        else:
            identifiers.append((1, 0, item))
    return (major, minor, patch, 0, tuple(identifiers))


def compare_versions(a: Any, b: Any) -> int:
    """比较两个版本号，返回 -1 / 0 / 1。"""

    key_a = version_key(a)
    key_b = version_key(b)
    if key_a > key_b:
        return 1
    if key_a < key_b:
        return -1
    return 0


def is_newer(candidate: Any, current: Any) -> bool:
    """candidate 是否严格新于 current。"""

    return compare_versions(candidate, current) > 0


def bump_version(current: Any, part: str) -> str:
    """按 patch / minor / major 递增版本号（预发布后缀会被丢弃）。"""

    normalized = normalize_version(current)
    core = normalized.split("-", 1)[0]
    major, minor, patch = (int(item) for item in core.split("."))
    kind = _text(part).lower()
    if kind == "patch":
        patch += 1
    elif kind == "minor":
        minor += 1
        patch = 0
    elif kind == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise UpdateError("版本递增方式必须是 patch、minor 或 major", code="invalid_bump")
    return f"{major}.{minor}.{patch}"


def read_local_version(root: Path | None = None) -> str:
    """读取项目根 VERSION 文件；文件不存在时返回 0.0.0。"""

    base = Path(root) if root is not None else PROJECT_ROOT
    path = base / "VERSION"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "0.0.0"
    except OSError as exc:
        raise UpdateError(f"读取 VERSION 失败：{exc}", code="version_read_failed") from exc
    for line in raw.splitlines():
        text = line.strip()
        if text:
            return normalize_version(text)
    return "0.0.0"


def _read_version_text(root: Path) -> str | None:
    try:
        return (Path(root) / "VERSION").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _write_version_text(root: Path, value: str) -> None:
    path = Path(root) / "VERSION"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="utf-8")


# ---------------------------------------------------------------------------
# GitHub 配置
# ---------------------------------------------------------------------------


def parse_repo_slug(value: Any) -> tuple[str, str]:
    """解析 owner/name 或 GitHub URL / SSH 地址，返回 (owner, name)。

    无法解析时返回两个空字符串（让 configured 为 False），不抛异常。
    """

    text = _text(value)
    if not text:
        return ("", "")
    if text.startswith("git@") and ":" in text:
        text = text.split(":", 1)[1]
    elif "://" in text:
        text = urllib.parse.urlparse(text).path
    text = text.strip().strip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    parts = [part for part in text.split("/") if part]
    if len(parts) < 2:
        return ("", "")
    owner, name = parts[-2], parts[-1]
    pattern = re.compile(r"^[A-Za-z0-9._-]+$")
    if not pattern.match(owner) or not pattern.match(name):
        return ("", "")
    return (owner, name)


def _normalize_api_url(value: Any) -> str:
    text = _text(value) or DEFAULT_API_URL
    return text.rstrip("/")


@dataclass(frozen=True)
class GitHubConfig:
    """GitHub 仓库配置；token 不参与 repr，避免日志/调试输出泄漏。"""

    owner: str
    repo: str
    token: str = field(default="", repr=False)
    api_url: str = DEFAULT_API_URL

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def configured(self) -> bool:
        return bool(self.owner and self.repo)

    @classmethod
    def from_runtime(
        cls,
        runtime: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "GitHubConfig":
        """按 环境变量 > 运行时配置 的优先级构造配置。"""

        env = os.environ if environ is None else environ
        data = runtime if isinstance(runtime, Mapping) else {}
        nested = data.get("updates")
        updates: Mapping[str, Any] = nested if isinstance(nested, Mapping) else data

        repo_raw = (
            _text(env.get("GITHUB_UPDATE_REPO"))
            or _text(updates.get("repo"))
            or DEFAULT_REPO
        )
        token_raw = _text(env.get("GITHUB_UPDATE_TOKEN")) or _text(updates.get("token"))
        api_raw = (
            _text(env.get("GITHUB_API_URL"))
            or _text(updates.get("api_url"))
            or DEFAULT_API_URL
        )
        owner, name = parse_repo_slug(repo_raw)
        return cls(owner=owner, repo=name, token=token_raw, api_url=_normalize_api_url(api_raw))


# ---------------------------------------------------------------------------
# Release 数据
# ---------------------------------------------------------------------------


@dataclass
class ReleaseInfo:
    version: str = ""
    tag: str = ""
    name: str = ""
    body: str = ""
    html_url: str = ""
    published_at: str = ""
    prerelease: bool = False
    draft: bool = False
    assets: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """给 API/前端用的扁平结构（不含 raw，避免把整包 JSON 回显）。"""

        return {
            "version": self.version,
            "tag": self.tag,
            "name": self.name,
            "body": self.body,
            "html_url": self.html_url,
            "published_at": self.published_at,
            "prerelease": bool(self.prerelease),
            "draft": bool(self.draft),
            "assets": [dict(asset) for asset in self.assets if isinstance(asset, Mapping)],
        }


def release_from_payload(payload: Any) -> ReleaseInfo:
    """把 GitHub Release JSON（或 ReleaseInfo.to_dict 的结果）转成对象。"""

    if isinstance(payload, ReleaseInfo):
        return payload
    data: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    tag = _text(data.get("tag") or data.get("tag_name"))
    version_raw = _text(data.get("version")) or tag
    try:
        version = normalize_version(version_raw)
    except UpdateError:
        version = _text(version_raw)
    assets_raw = data.get("assets")
    assets = (
        [dict(item) for item in assets_raw if isinstance(item, Mapping)]
        if isinstance(assets_raw, list)
        else []
    )
    raw_value = data.get("raw")
    raw = dict(raw_value) if isinstance(raw_value, Mapping) else dict(data)
    return ReleaseInfo(
        version=version,
        tag=tag or (f"v{version}" if version else ""),
        name=_text(data.get("name")),
        body=str(data.get("body") or ""),
        html_url=_text(data.get("html_url")),
        published_at=_text(data.get("published_at")),
        prerelease=bool(data.get("prerelease")),
        draft=bool(data.get("draft")),
        assets=assets,
        raw=dict(raw) if isinstance(raw, Mapping) else {},
    )


def _release_assets(release: Any) -> list[dict[str, Any]]:
    if isinstance(release, ReleaseInfo):
        return [dict(asset) for asset in release.assets if isinstance(asset, Mapping)]
    if isinstance(release, Mapping):
        assets = release.get("assets")
        if isinstance(assets, list):
            return [dict(asset) for asset in assets if isinstance(asset, Mapping)]
    return []


def select_release_asset(release: Any) -> dict[str, Any] | None:
    """从 Release 资产里挑安装包：优先 ai-listing-system 的 zip，否则第一个 zip。"""

    zips = [
        asset
        for asset in _release_assets(release)
        if _text(asset.get("name")).lower().endswith(".zip")
    ]
    if not zips:
        return None
    for asset in zips:
        if "ai-listing-system" in _text(asset.get("name")).lower():
            return asset
    return zips[0]


# ---------------------------------------------------------------------------
# GitHub HTTP 客户端
# ---------------------------------------------------------------------------


class _CrossHostAuthStripRedirectHandler(urllib.request.HTTPRedirectHandler):
    """GitHub Release 资产会 302 到对象存储；跨主机跳转时去掉 Authorization。

    urllib 默认会把请求头原样带到重定向目标，而预签名 URL 再叠加 Authorization
    会被存储端以 400 拒绝（私有仓库下载失败）。同主机跳转仍保留认证头。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is None:
            return None
        try:
            old_host = urllib.parse.urlsplit(req.full_url).netloc
            new_host = urllib.parse.urlsplit(newurl).netloc
        except ValueError:
            return new_request
        if old_host and new_host and old_host != new_host:
            for target in (new_request.headers, getattr(new_request, "unredirected_hdrs", {})):
                for key in list(target):
                    if key.lower() == "authorization":
                        del target[key]
        return new_request


_HTTP_OPENER: Any = None


def _github_proxy_settings() -> tuple[str, tuple[str, ...]]:
    """读 GITHUB_UPDATE_PROXY / GITHUB_UPDATE_DIRECT_HOSTS，返回 (代理, 直连主机)。

    典型分流场景（见 docs/GITHUB_UPDATES.md 1.3）：api.github.com 直连可用、
    走共享代理反而被 GitHub 按 IP 拒（403）；而 github.com / Release 资产的
    对象存储直连被墙、必须走本地代理。proxy_url 为空时维持系统默认行为。
    """

    proxy_url = (os.getenv("GITHUB_UPDATE_PROXY") or "").strip()
    if not proxy_url:
        # 没显式配代理时沿用系统/环境代理（Windows 的 Clash 之类会写进 IE 设置），
        # 但 direct_hosts 仍然直连：api.github.com 走代理会被按出口 IP 拒（403），
        # 代理做 TLS 中间解密时 Python 又不认它的证书（现场报
        # SSL CERTIFICATE_VERIFY_FAILED，而 curl 用 Windows 证书库能通）。
        proxies = urllib.request.getproxies()
        proxy_url = (proxies.get("https") or proxies.get("http") or "").strip()
    raw_hosts = (os.getenv("GITHUB_UPDATE_DIRECT_HOSTS") or "api.github.com").strip()
    direct_hosts = tuple(host.strip().lower() for host in raw_hosts.split(",") if host.strip())
    return proxy_url, direct_hosts


class _SplitProxyHandler(urllib.request.ProxyHandler):
    """http/https 统一走指定代理；direct_hosts 里的主机放回直连。

    ProxyHandler 的机制是 proxy_open 里 set_proxy 把请求改写到代理地址再放回
    处理器链；返回 None 且不改写即等于直连（与 proxy_bypass 的短路一致）。
    """

    def __init__(self, proxy_url: str, direct_hosts: tuple[str, ...]) -> None:
        super().__init__({"http": proxy_url, "https": proxy_url})
        self._direct_hosts = {host.lower() for host in direct_hosts}

    def proxy_open(self, req, proxy, type):  # noqa: ANN001, A002 - urllib 回调签名
        try:
            host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
        except ValueError:
            host = ""
        if host and host in self._direct_hosts:
            return None
        return super().proxy_open(req, proxy, type)


def _http_opener() -> Any:
    global _HTTP_OPENER
    if _HTTP_OPENER is None:
        proxy_url, direct_hosts = _github_proxy_settings()
        if proxy_url:
            _HTTP_OPENER = urllib.request.build_opener(
                _SplitProxyHandler(proxy_url, direct_hosts),
                _CrossHostAuthStripRedirectHandler(),
            )
        else:
            _HTTP_OPENER = urllib.request.build_opener(_CrossHostAuthStripRedirectHandler())
    return _HTTP_OPENER


def _http_open(request: urllib.request.Request, timeout: float) -> Any:
    """可被测试 monkeypatch 的统一 HTTP 入口。"""

    return _http_opener().open(request, timeout=timeout)


def _map_http_error(exc: urllib.error.HTTPError) -> UpdateError:
    status = int(getattr(exc, "code", 0) or 0)
    detail = ""
    try:
        raw = exc.read()
        if raw:
            try:
                detail = _text(json.loads(raw.decode("utf-8", "replace")).get("message"))
            except (ValueError, AttributeError, TypeError):
                detail = ""
    except Exception:  # noqa: BLE001 - 读不到响应体就用状态码
        detail = ""
    if status in (401, 403):
        return UpdateError(
            "GitHub 认证失败/权限不足（Token 或限流）", code="auth_failed", status=status
        )
    if status == 404:
        return UpdateError("仓库或 Release 不存在", code="not_found", status=status)
    message = f"GitHub 请求失败（HTTP {status}）"
    if detail:
        message = f"{message}：{detail[:200]}"
    return UpdateError(message, code="http_error", status=status)


def _map_network_error(exc: BaseException) -> UpdateError:
    reason = getattr(exc, "reason", None)
    # 报错里带上主机名：分不清是 api.github.com 直连挂了、还是走代理的那台挂了，
    # 现场只能靠猜（SSL/403 两种故障的处理方式完全相反）。
    host = ""
    try:
        raw_url = str(getattr(exc, "filename", "") or getattr(exc, "url", "") or "")
        host = urllib.parse.urlsplit(raw_url).hostname or ""
    except ValueError:
        host = ""
    where = f"（{host}）" if host else ""
    if isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(
        reason, (TimeoutError, socket.timeout)
    ):
        return UpdateError(f"连接 GitHub 超时{where}，请检查网络或代理设置", code="network_timeout")
    detail = _text(reason) or _text(exc) or exc.__class__.__name__
    return UpdateError(f"连接 GitHub 失败{where}：{detail}，请检查网络或代理设置", code="network_error")


class GitHubClient:
    """极简 GitHub REST 客户端（只覆盖更新/发布需要的接口）。"""

    def __init__(self, config: GitHubConfig, *, timeout: float = 20.0) -> None:
        self.config = config
        self.timeout = float(timeout)

    # -- 基础 -----------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.config.api_url}{path}"

    def _headers(self, accept: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": accept or "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        content_type: str = "",
        accept: str | None = None,
    ) -> Any:
        payload: bytes | None = None
        if body is not None:
            if isinstance(body, (bytes, bytearray)):
                payload = bytes(body)
            else:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method=method)
        for key, value in self._headers(accept).items():
            request.add_header(key, value)
        if payload is not None:
            request.add_header("Content-Type", content_type or "application/json")
        try:
            with _http_open(request, self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _map_http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise _map_network_error(exc) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise _map_network_error(exc) from exc
        except OSError as exc:
            raise _map_network_error(exc) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise UpdateError("GitHub 返回了无法解析的数据", code="bad_response") from exc

    # -- 业务 -----------------------------------------------------------------

    def latest_release(self) -> ReleaseInfo:
        payload = self._request("GET", self._url(f"/repos/{self.config.slug}/releases/latest"))
        if not isinstance(payload, Mapping):
            raise UpdateError("GitHub 返回的 Release 数据格式不正确", code="bad_response")
        return release_from_payload(payload)

    def get_release(self, tag: str) -> ReleaseInfo:
        clean = urllib.parse.quote(_text(tag), safe="")
        if not clean:
            raise UpdateError("Release 标签不能为空", code="invalid_tag")
        payload = self._request(
            "GET", self._url(f"/repos/{self.config.slug}/releases/tags/{clean}")
        )
        if not isinstance(payload, Mapping):
            raise UpdateError("GitHub 返回的 Release 数据格式不正确", code="bad_response")
        return release_from_payload(payload)

    def list_releases(self, limit: int = 10) -> list[ReleaseInfo]:
        count = max(1, min(int(limit), 100))
        payload = self._request(
            "GET", self._url(f"/repos/{self.config.slug}/releases?per_page={count}")
        )
        if not isinstance(payload, list):
            raise UpdateError("GitHub 返回的 Release 列表格式不正确", code="bad_response")
        return [release_from_payload(item) for item in payload if isinstance(item, Mapping)]

    def create_release(
        self,
        *,
        tag: str,
        name: str,
        body: str = "",
        target_commitish: str = "",
        prerelease: bool = False,
        draft: bool = False,
    ) -> ReleaseInfo:
        payload: dict[str, Any] = {
            "tag_name": _text(tag),
            "name": _text(name) or _text(tag),
            "body": str(body or ""),
            "prerelease": bool(prerelease),
            "draft": bool(draft),
        }
        if _text(target_commitish):
            payload["target_commitish"] = _text(target_commitish)
        result = self._request(
            "POST", self._url(f"/repos/{self.config.slug}/releases"), body=payload
        )
        if not isinstance(result, Mapping):
            raise UpdateError("GitHub 创建 Release 的返回格式不正确", code="bad_response")
        return release_from_payload(result)

    def upload_asset(
        self,
        upload_url: str,
        filename: str,
        payload: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        base = _text(upload_url).split("{", 1)[0]
        if not base:
            raise UpdateError("Release 没有可用的上传地址", code="bad_upload_url")
        separator = "&" if "?" in base else "?"
        url = f"{base}{separator}name={urllib.parse.quote(_text(filename), safe='')}"
        result = self._request(
            "POST",
            url,
            body=bytes(payload),
            content_type=content_type or "application/octet-stream",
        )
        return dict(result) if isinstance(result, Mapping) else {}

    @contextlib.contextmanager
    def open_asset(
        self,
        asset: Mapping[str, Any],
        *,
        accept: str = "application/octet-stream",
    ) -> Iterator[Any]:
        """打开资产下载流（私有仓库走 API 地址 + Token）。"""

        if not isinstance(asset, Mapping):
            raise UpdateError("安装包信息不正确", code="bad_asset")
        api_url = _text(asset.get("url"))
        browser_url = _text(asset.get("browser_download_url"))
        url = api_url if (self.config.token and api_url) else (browser_url or api_url)
        if not url:
            raise UpdateError("安装包没有下载地址", code="bad_asset")
        request = urllib.request.Request(url, method="GET")
        for key, value in self._headers(accept).items():
            request.add_header(key, value)
        try:
            response = _http_open(request, self.timeout)
        except urllib.error.HTTPError as exc:
            raise _map_http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise _map_network_error(exc) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise _map_network_error(exc) from exc
        except OSError as exc:
            raise _map_network_error(exc) from exc
        try:
            yield response
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 检查更新
# ---------------------------------------------------------------------------


def _pick_release(client: Any, *, include_prerelease: bool) -> ReleaseInfo | None:
    if not include_prerelease:
        try:
            release = client.latest_release()
        except UpdateError as exc:
            if exc.status == 404:
                return None  # 仓库还没有发布任何正式版本
            raise
        if isinstance(release, Mapping):
            release = release_from_payload(release)
        return None if getattr(release, "draft", False) else release

    releases = client.list_releases(30) or []
    best: tuple[tuple[Any, ...], ReleaseInfo] | None = None
    for release in releases:
        if isinstance(release, Mapping):
            release = release_from_payload(release)
        if getattr(release, "draft", False):
            continue
        try:
            key = version_key(release.version)
        except UpdateError:
            continue
        if best is None or key > best[0]:
            best = (key, release)
    return best[1] if best is not None else None


def check_for_update(
    config: GitHubConfig,
    current_version: str,
    *,
    include_prerelease: bool = False,
    client: Any = None,
) -> dict[str, Any]:
    """检查是否有新版本。任何失败都通过 error 字段返回，不抛异常。"""

    checked_at = _iso_now()
    result: dict[str, Any] = {
        "ok": False,
        "current_version": _text(current_version),
        "repo": config.slug if isinstance(config, GitHubConfig) else "",
        "latest": None,
        "update_available": False,
        "error": "",
        "checked_at": checked_at,
    }
    try:
        current = normalize_version(current_version)
    except UpdateError as exc:
        result["error"] = str(exc)
        return result
    result["current_version"] = current

    if not isinstance(config, GitHubConfig) or not config.configured:
        result["error"] = "尚未配置 GitHub 仓库（owner/name）"
        return result

    try:
        active = client if client is not None else GitHubClient(config)
        release = _pick_release(active, include_prerelease=include_prerelease)
        if release is None:
            result["ok"] = True
            return result
        result["latest"] = release.to_dict()
        result["ok"] = True
        try:
            result["update_available"] = is_newer(release.version, current)
        except UpdateError:
            result["update_available"] = False
        return result
    except UpdateError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001 - 检查更新不允许把异常抛给 API
        result["error"] = f"检查更新失败：{exc}"
        return result


# ---------------------------------------------------------------------------
# 安装包：下载、校验、解压
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """计算文件 sha256（流式读取，不整包进内存）。"""

    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _asset_stream(client: Any, asset: Mapping[str, Any]) -> Any:
    opener = getattr(client, "open_asset", None)
    if callable(opener):
        return opener(asset)
    raise UpdateError("下载客户端不支持资产下载", code="bad_client")


def download_asset(
    asset: Mapping[str, Any],
    dest: Path,
    *,
    client: Any,
    max_bytes: int = MAX_PACKAGE_BYTES,
) -> dict[str, Any]:
    """流式下载资产到 dest，返回 {path, bytes, sha256}。"""

    target = Path(dest)
    if not isinstance(asset, Mapping):
        raise UpdateError("安装包信息不正确", code="bad_asset")
    limit = int(max_bytes or MAX_PACKAGE_BYTES)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")
    digest = hashlib.sha256()
    total = 0
    try:
        with _asset_stream(client, asset) as response:
            with open(temp, "wb") as stream:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise UpdateError(
                            f"安装包超过大小上限（{limit // (1024 * 1024)} MB）",
                            code="package_too_large",
                        )
                    digest.update(chunk)
                    stream.write(chunk)
        if total <= 0:
            raise UpdateError("下载到的安装包是空文件", code="empty_package")
        os.replace(temp, target)
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError(f"下载安装包失败：{exc}", code="download_failed") from exc
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return {"path": str(target), "bytes": total, "sha256": digest.hexdigest()}


def _safe_relative(value: Any) -> str:
    """校验 zip 内/清单里的相对路径，返回 POSIX 形式；不安全直接抛错。"""

    text = _text(value)
    if not text:
        raise UpdateError("安装包内存在空路径", code="unsafe_path")
    if "\x00" in text:
        raise UpdateError("安装包内路径包含非法字符", code="unsafe_path")
    if "\\" in text:
        raise UpdateError(f"安装包内路径不能包含反斜杠：{text}", code="unsafe_path")
    if text.startswith("/"):
        raise UpdateError(f"安装包内路径不能是绝对路径：{text}", code="unsafe_path")
    if re.match(r"^[A-Za-z]:", text):
        raise UpdateError(f"安装包内路径不能是绝对路径：{text}", code="unsafe_path")
    if ":" in text:
        raise UpdateError(f"安装包内路径包含非法字符：{text}", code="unsafe_path")
    parts = text.split("/")
    if any(part == ".." for part in parts):
        raise UpdateError(f"安装包内路径不能包含 .. ：{text}", code="unsafe_path")
    normalized = posixpath.normpath(text)
    if normalized in ("", ".", "/"):
        raise UpdateError(f"安装包内路径无效：{text}", code="unsafe_path")
    if normalized.startswith("../") or normalized == "..":
        raise UpdateError(f"安装包内路径不能包含 .. ：{text}", code="unsafe_path")
    return normalized


def _allowed_release_path(rel: str) -> bool:
    if rel in RELEASE_FILES:
        return True
    head = rel.split("/", 1)[0]
    return head in RELEASE_ROOTS


def _join_within(base: Path, rel: str) -> Path:
    """把 rel 拼到 base 下，并确保结果仍在 base 内。"""

    base_resolved = Path(base).resolve()
    target = (base_resolved / Path(*str(rel).split("/"))).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise UpdateError(f"路径越界：{rel}", code="unsafe_path") from exc
    return target


def _parse_manifest(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("manifest.json 不是有效的 JSON", code="bad_manifest") from exc
    if not isinstance(data, Mapping):
        raise UpdateError("manifest.json 必须是 JSON 对象", code="bad_manifest")
    try:
        fmt = int(data.get("format") or 0)
    except (TypeError, ValueError):
        fmt = 0
    if fmt != 1:
        raise UpdateError(f"不支持的 manifest 格式：{data.get('format')!r}", code="bad_manifest")
    try:
        version = normalize_version(data.get("version"))
    except UpdateError as exc:
        raise UpdateError(f"manifest.json 版本号无效：{exc}", code="bad_manifest") from exc
    files_raw = data.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise UpdateError("manifest.json 缺少文件清单", code="bad_manifest")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in files_raw:
        if not isinstance(item, Mapping):
            raise UpdateError("manifest.json 文件项格式不正确", code="bad_manifest")
        rel = _safe_relative(item.get("path"))
        if rel == MANIFEST_FILE:
            raise UpdateError("manifest.json 不能把自身列进文件清单", code="bad_manifest")
        if not _allowed_release_path(rel):
            raise UpdateError(f"manifest.json 里有不允许写入的路径：{rel}", code="unsafe_path")
        digest = _text(item.get("sha256")).lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise UpdateError(f"manifest.json 中 {rel} 的 sha256 无效", code="bad_manifest")
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError) as exc:
            raise UpdateError(f"manifest.json 中 {rel} 的 size 无效", code="bad_manifest") from exc
        if size < 0:
            raise UpdateError(f"manifest.json 中 {rel} 的 size 无效", code="bad_manifest")
        if rel in seen:
            raise UpdateError(f"manifest.json 中存在重复文件：{rel}", code="bad_manifest")
        seen.add(rel)
        files.append({"path": rel, "sha256": digest, "size": size})
    deletions_raw = data.get("delete") or []
    if not isinstance(deletions_raw, list):
        raise UpdateError("manifest.json 的 delete 必须是数组", code="bad_manifest")
    deletions = [_safe_relative(item) for item in deletions_raw]
    return {"format": 1, "version": version, "files": files, "delete": deletions}


def _zip_member_digest(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(info, "r") as stream:
        while True:
            chunk = stream.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def verify_package(
    zip_path: Path,
    *,
    expected_sha256: str = "",
    expected_version: str = "",
) -> dict[str, Any]:
    """校验安装包（路径白名单 / 无符号链接 / manifest / 逐文件 sha256 与 size）。

    校验通过返回 manifest（已归一化），失败抛 UpdateError（中文）。
    """

    path = Path(zip_path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UpdateError(f"读取安装包失败：{exc}", code="package_missing") from exc
    if size <= 0:
        raise UpdateError("安装包是空文件", code="empty_package")
    if size > MAX_PACKAGE_BYTES:
        raise UpdateError(
            f"安装包超过大小上限（{MAX_PACKAGE_BYTES // (1024 * 1024)} MB）",
            code="package_too_large",
        )

    expected_digest = _text(expected_sha256).lower()
    if expected_digest:
        actual = sha256_file(path)
        if actual.lower() != expected_digest:
            raise UpdateError(
                f"安装包 sha256 校验失败（期望 {expected_digest[:12]}…，实际 {actual[:12]}…）",
                code="checksum_mismatch",
            )

    if not zipfile.is_zipfile(path):
        raise UpdateError("不是有效的 ZIP 安装包", code="bad_package")

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos:
                raise UpdateError("安装包是空的 ZIP 文件", code="bad_package")
            manifest_info: zipfile.ZipInfo | None = None
            members: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                rel = _safe_relative(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise UpdateError(f"安装包内不允许符号链接：{rel}", code="unsafe_symlink")
                if mode and mode not in (0o100000, 0o040000):
                    raise UpdateError(f"安装包内不支持的文件类型：{rel}", code="unsafe_path")
                if rel == MANIFEST_FILE:
                    if manifest_info is not None:
                        raise UpdateError("安装包内有重复的 manifest.json", code="bad_manifest")
                    manifest_info = info
                    continue
                if not _allowed_release_path(rel):
                    raise UpdateError(f"安装包包含不允许的路径：{rel}", code="unsafe_path")
                if rel in members:
                    raise UpdateError(f"安装包内存在重复路径：{rel}", code="duplicate_path")
                members[rel] = info
            if manifest_info is None:
                raise UpdateError("安装包缺少 manifest.json", code="missing_manifest")
            manifest = _parse_manifest(archive.read(manifest_info))
            if _text(expected_version):
                expected_norm = normalize_version(expected_version)
                if manifest["version"] != expected_norm:
                    raise UpdateError(
                        f"安装包版本 {manifest['version']} 与期望版本 {expected_norm} 不一致",
                        code="version_mismatch",
                    )
            listed = {item["path"]: item for item in manifest["files"]}
            for rel in members:
                if rel not in listed:
                    raise UpdateError(f"安装包包含清单外的文件：{rel}", code="unlisted_file")
            for rel, item in listed.items():
                info = members.get(rel)
                if info is None:
                    raise UpdateError(f"安装包缺少清单中的文件：{rel}", code="missing_file")
                if info.is_dir():
                    raise UpdateError(f"清单中的路径不是文件：{rel}", code="bad_manifest")
                digest, member_size = _zip_member_digest(archive, info)
                if digest != item["sha256"]:
                    raise UpdateError(f"文件 sha256 校验失败：{rel}", code="checksum_mismatch")
                if member_size != item["size"]:
                    raise UpdateError(f"文件大小校验失败：{rel}", code="size_mismatch")
            return manifest
    except zipfile.BadZipFile as exc:
        raise UpdateError("安装包 ZIP 已损坏", code="bad_package") from exc
    except OSError as exc:
        raise UpdateError(f"读取安装包失败：{exc}", code="package_read_failed") from exc


def extract_package(
    zip_path: Path,
    dest_dir: Path,
    *,
    manifest: Mapping[str, Any] | None = None,
) -> Path:
    """安全解压安装包到 dest_dir（先清空目录），返回 dest_dir。

    只解压 manifest 里列出的文件；调用方通常先调用 verify_package。
    """

    zip_file = Path(zip_path)
    dest = Path(dest_dir)
    if not zip_file.is_file():
        raise UpdateError("安装包不存在", code="package_missing")
    if manifest is None:
        manifest = verify_package(zip_file)
    if not isinstance(manifest, Mapping):
        raise UpdateError("安装包清单格式不正确", code="bad_manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise UpdateError("安装包清单没有文件", code="bad_manifest")

    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    dest.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_file) as archive:
            available: dict[str, zipfile.ZipInfo] = {}
            for info in archive.infolist():
                if info.is_dir():
                    continue
                available[_safe_relative(info.filename)] = info
            for item in files:
                if not isinstance(item, Mapping):
                    raise UpdateError("安装包清单文件项格式不正确", code="bad_manifest")
                rel = _safe_relative(item.get("path"))
                info = available.get(rel)
                if info is None:
                    raise UpdateError(f"安装包缺少文件：{rel}", code="missing_file")
                target = _join_within(dest, rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output, 256 * 1024)
    except zipfile.BadZipFile as exc:
        raise UpdateError("安装包 ZIP 已损坏", code="bad_package") from exc
    except OSError as exc:
        raise UpdateError(f"解压安装包失败：{exc}", code="extract_failed") from exc
    # 把校验过的清单落到暂存目录根，供 apply_staged_update 直接读取。
    try:
        (dest / MANIFEST_FILE).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise UpdateError(f"写入暂存清单失败：{exc}", code="extract_failed") from exc
    return dest


# ---------------------------------------------------------------------------
# 打包发布
# ---------------------------------------------------------------------------

_SKIP_DIR_NAMES = {
    "__pycache__",
    "node_modules",
    ".git",
    "data",
    "storage",
    "dist",
    "vendor",
    "tmp_upload",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__MACOSX",
}
_SKIP_FILE_SUFFIXES = (".pyc", ".pyo", ".log", ".tmp")
_SKIP_FILE_NAMES = {"thumbs.db", ".ds_store"}


def _skip_dir_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in _SKIP_DIR_NAMES:
        return True
    return lowered.startswith(".venv")


def _skip_file_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in _SKIP_FILE_NAMES:
        return True
    if lowered.startswith("~"):
        return True
    return lowered.endswith(_SKIP_FILE_SUFFIXES)


def _relative_request(value: Any) -> str:
    text = _text(value).replace("\\", "/")
    if not text:
        raise UpdateError("include/exclude 里的路径不能为空", code="bad_include")
    return _safe_relative(text)


def _collect_release_files(root: Path) -> list[Path]:
    collected: list[Path] = []
    seen: set[str] = set()
    for dir_name in RELEASE_ROOTS:
        base = root / dir_name
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(name for name in dirnames if not _skip_dir_name(name))
            for name in sorted(filenames):
                if _skip_file_name(name):
                    continue
                item = Path(dirpath) / name
                if item.is_symlink() or not item.is_file():
                    continue
                rel = item.relative_to(root).as_posix()
                if rel not in seen:
                    seen.add(rel)
                    collected.append(item)
    for name in RELEASE_FILES:
        item = root / name
        if item.is_file() and not item.is_symlink():
            rel = item.relative_to(root).as_posix()
            if rel not in seen:
                seen.add(rel)
                collected.append(item)
    return collected


def _hash_file_stream(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def build_release_archive(
    project_root: Path,
    output_path: Path,
    *,
    version: str | None = None,
    include: Iterable[Any] | None = None,
    exclude: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """把项目打成发布 zip（含 manifest.json 与 VERSION），返回构建信息。"""

    root = Path(project_root).resolve()
    output = Path(output_path)
    target_version = normalize_version(version) if version is not None else read_local_version(root)

    rels: dict[str, Path] = {}
    for item in _collect_release_files(root):
        rels[item.relative_to(root).as_posix()] = item

    for raw in include or ():
        rel = _relative_request(raw)
        if not _allowed_release_path(rel):
            raise UpdateError(f"include 路径不在允许发布的范围内：{rel}", code="bad_include")
        target = _join_within(root, rel)
        if target.is_dir():
            for child in sorted(target.rglob("*")):
                if child.is_symlink() or not child.is_file():
                    continue
                if any(_skip_dir_name(part) for part in child.relative_to(root).parts[:-1]):
                    continue
                if _skip_file_name(child.name):
                    continue
                rels.setdefault(child.relative_to(root).as_posix(), child)
        elif target.is_file():
            rels[rel] = target
        else:
            raise UpdateError(f"include 指定的路径不存在：{rel}", code="include_missing")

    for raw in exclude or ():
        rel = _relative_request(raw).rstrip("/")
        for key in list(rels):
            if key == rel or key.startswith(f"{rel}/"):
                rels.pop(key, None)

    output_resolved = output.resolve()
    rels.pop("VERSION", None)
    rels = {
        rel: item
        for rel, item in rels.items()
        if item.resolve() != output_resolved and _allowed_release_path(rel)
    }
    if not rels:
        raise UpdateError("没有可打包的文件", code="empty_release")

    version_bytes = f"{target_version}\n".encode("utf-8")
    manifest_files: list[dict[str, Any]] = [
        {
            "path": "VERSION",
            "sha256": hashlib.sha256(version_bytes).hexdigest(),
            "size": len(version_bytes),
        }
    ]
    for rel in sorted(rels):
        digest, size = _hash_file_stream(rels[rel])
        manifest_files.append({"path": rel, "sha256": digest, "size": size})
    manifest = {"format": 1, "version": target_version, "files": manifest_files, "delete": []}
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f"{output.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(MANIFEST_FILE, manifest_bytes)
            archive.writestr("VERSION", version_bytes)
            for item in manifest_files:
                rel = item["path"]
                if rel == "VERSION":
                    continue
                with open(rels[rel], "rb") as source, archive.open(rel, "w") as member:
                    shutil.copyfileobj(source, member, 1024 * 1024)
        os.replace(temp, output)
    except OSError as exc:
        raise UpdateError(f"打包安装包失败：{exc}", code="build_failed") from exc
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return {
        "path": str(output),
        "version": target_version,
        "sha256": sha256_file(output),
        "size": output.stat().st_size,
        "files": len(manifest_files) + 1,
    }


# ---------------------------------------------------------------------------
# 更新状态 JSON（原子写）
# ---------------------------------------------------------------------------


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, target)
    except OSError as exc:
        raise UpdateError(f"写入更新状态失败：{exc}", code="state_write_failed") from exc
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return target


def _read_json_safe(path: Path) -> dict[str, Any] | None:
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        raise
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return dict(value) if isinstance(value, Mapping) else None


def write_pending(updates_dir: Path, pending: Mapping[str, Any]) -> Path:
    return _write_json_atomic(Path(updates_dir) / PENDING_FILE, pending)


def read_pending(updates_dir: Path) -> dict[str, Any] | None:
    return _read_json_safe(Path(updates_dir) / PENDING_FILE)


def write_update_state(updates_dir: Path, state: Mapping[str, Any]) -> Path:
    return _write_json_atomic(Path(updates_dir) / STATE_FILE, state)


def read_update_state(updates_dir: Path) -> dict[str, Any] | None:
    return _read_json_safe(Path(updates_dir) / STATE_FILE)


# ---------------------------------------------------------------------------
# 暂存、安装、回滚
# ---------------------------------------------------------------------------


def _safe_package_name(asset: Mapping[str, Any], version: str) -> str:
    raw = Path(_text(asset.get("name"))).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    if not cleaned.lower().endswith(".zip"):
        cleaned = f"ai-listing-system-{version}.zip"
    return cleaned


def _find_checksum_asset(release: Any, package_name: str) -> dict[str, Any] | None:
    wanted = f"{package_name}.sha256".lower()
    checksums = [
        asset
        for asset in _release_assets(release)
        if _text(asset.get("name")).lower().endswith(".sha256")
    ]
    for asset in checksums:
        if _text(asset.get("name")).lower() == wanted:
            return asset
    return checksums[0] if checksums else None


def _parse_checksum_file(path: Path) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UpdateError(f"读取校验文件失败：{exc}", code="checksum_read_failed") from exc
    for token in text.split():
        candidate = token.strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", candidate):
            return candidate
    raise UpdateError("校验文件内容不是有效的 sha256", code="bad_checksum")


def stage_update(
    *,
    config: GitHubConfig,
    release: Any,
    project_root: Path,
    updates_dir: Path,
    client: Any = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """下载并校验安装包，解压到暂存目录，写 pending.json。"""

    root = Path(project_root)
    updates = Path(updates_dir)
    updates.mkdir(parents=True, exist_ok=True)
    if not isinstance(config, GitHubConfig):
        raise UpdateError("GitHub 配置不正确", code="bad_config")
    release_info = release_from_payload(release)
    version = normalize_version(release_info.version)
    active = client if client is not None else GitHubClient(config)

    asset = select_release_asset(release_info)
    if asset is None:
        raise UpdateError("该 Release 没有可用的 ZIP 安装包", code="no_package")
    package_name = _safe_package_name(asset, version)

    downloads = updates / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    package_path = downloads / package_name
    _notify(progress, f"开始下载安装包：{package_name}")
    downloaded = download_asset(asset, package_path, client=active)
    _notify(progress, f"下载完成（{downloaded['bytes']} 字节），正在校验")

    checksum_verified = False
    expected_sha = ""
    checksum_asset = _find_checksum_asset(release_info, package_name)
    if checksum_asset is not None:
        checksum_path = downloads / f"{package_name}.sha256"
        download_asset(checksum_asset, checksum_path, client=active)
        expected_sha = _parse_checksum_file(checksum_path)
        if expected_sha != str(downloaded["sha256"]).lower():
            raise UpdateError("安装包 sha256 与 Release 校验文件不一致", code="checksum_mismatch")
        checksum_verified = True
        _notify(progress, "sha256 校验通过")

    manifest = verify_package(
        package_path,
        expected_sha256=expected_sha,
        expected_version=version,
    )
    staged_dir = updates / "staged" / version
    extract_package(package_path, staged_dir, manifest=manifest)
    _notify(progress, f"安装包已解压到暂存目录（{len(manifest['files'])} 个文件）")

    try:
        current = read_local_version(root)
    except UpdateError:
        current = "0.0.0"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = updates / "backups" / f"{stamp}-from-{current}"
    pending = {
        "version": version,
        "tag": release_info.tag or f"v{version}",
        "staged_dir": str(staged_dir),
        "package_path": str(package_path),
        "package_sha256": str(downloaded["sha256"]),
        "checksum_verified": checksum_verified,
        "backup_dir": str(backup_dir),
        "staged_at": _iso_now(),
        "restart": True,
    }
    write_pending(updates, pending)
    _notify(progress, f"更新 v{version} 已准备就绪")
    return pending


def _updates_dir_for_pending(pending: Mapping[str, Any]) -> Path:
    for key, marker in (("staged_dir", "staged"), ("backup_dir", "backups")):
        raw = _text(pending.get(key))
        if not raw:
            continue
        path = Path(raw)
        if path.parent.name == marker:
            return path.parent.parent
    return PROJECT_ROOT / "storage" / "updates"


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex[:10]}.updating"
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, target)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _restore_entry(root: Path, backup_dir: Path, rel: str, existed: bool) -> None:
    target = _join_within(root, rel)
    if existed:
        source = _join_within(backup_dir, rel)
        if not source.is_file():
            raise UpdateError(f"备份文件缺失：{rel}", code="backup_missing")
        _atomic_copy(source, target)
    elif target.is_file() or target.is_symlink():
        target.unlink()


def apply_staged_update(
    *,
    project_root: Path,
    pending: Mapping[str, Any],
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """把暂存文件安装到项目根（先备份、逐文件原子替换，失败尽量回滚）。"""

    root = Path(project_root).resolve()
    if not isinstance(pending, Mapping):
        raise UpdateError("更新状态文件格式不正确", code="bad_pending")
    version = _text(pending.get("version"))
    staged_raw = _text(pending.get("staged_dir"))
    backup_raw = _text(pending.get("backup_dir"))
    if not staged_raw or not backup_raw:
        raise UpdateError("更新状态缺少暂存目录或备份目录", code="bad_pending")
    staged_dir = Path(staged_raw)
    backup_dir = Path(backup_raw)
    if not staged_dir.is_dir():
        raise UpdateError("暂存目录不存在，请重新下载更新", code="staged_missing")
    manifest_path = staged_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        raise UpdateError("暂存目录缺少 manifest.json", code="missing_manifest")
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"读取暂存清单失败：{exc}", code="bad_manifest") from exc
    if not isinstance(raw_manifest, Mapping):
        raise UpdateError("暂存清单格式不正确", code="bad_manifest")
    manifest = _parse_manifest(json.dumps(raw_manifest, ensure_ascii=False).encode("utf-8"))

    planned: list[tuple[str, Path, Path]] = []
    for item in manifest["files"]:
        rel = item["path"]
        source = _join_within(staged_dir, rel)
        if not source.is_file():
            raise UpdateError(f"暂存目录缺少文件：{rel}", code="staged_missing")
        digest, size = _hash_file_stream(source)
        if digest != item["sha256"] or size != item["size"]:
            raise UpdateError(f"暂存文件校验失败：{rel}", code="checksum_mismatch")
        planned.append((rel, source, _join_within(root, rel)))
    if not planned:
        raise UpdateError("暂存清单没有文件，无法安装", code="bad_manifest")

    backup_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    existed_map: dict[str, bool] = {}
    for rel, _source, target in planned:
        existed = target.is_file() or target.is_symlink()
        existed_map[rel] = existed
        if existed:
            backup_target = _join_within(backup_dir, rel)
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_target)
        records.append({"path": rel, "backup_path": rel, "existed": existed})
    _write_json_atomic(
        backup_dir / BACKUP_FILE,
        {"version": version, "files": records, "created_at": _iso_now()},
    )
    _log(log, f"已备份 {len(records)} 个文件到 {backup_dir}")

    updates_dir = _updates_dir_for_pending(pending)
    applied: list[str] = []
    try:
        for rel, source, target in planned:
            _atomic_copy(source, target)
            applied.append(rel)
        _log(log, f"已替换 {len(applied)} 个文件")
    except Exception as exc:  # noqa: BLE001 - 失败要回滚并把错误写进 state
        rollback_errors: list[str] = []
        for rel in reversed(applied):
            try:
                _restore_entry(root, backup_dir, rel, existed_map.get(rel, False))
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_errors.append(f"{rel}: {rollback_exc}")
        message = f"安装更新失败：{exc}"
        if rollback_errors:
            message = f"{message}（部分文件回滚失败：{'；'.join(rollback_errors[:3])}）"
        try:
            write_update_state(
                updates_dir,
                {
                    "status": "failed",
                    "version": version,
                    "error": message,
                    "applied_at": _iso_now(),
                    "backup_dir": str(backup_dir),
                },
            )
        except UpdateError:
            pass
        # 失败已回滚，清掉 pending 让操作员可以重新下载/重试，而不是被卡在「待安装」。
        try:
            pending_file = updates_dir / PENDING_FILE
            if pending_file.is_file():
                pending_file.unlink()
        except OSError:
            pass
        raise UpdateError(message, code="apply_failed") from exc

    state = {
        "status": "success",
        "version": version,
        "applied_at": _iso_now(),
        "backup_dir": str(backup_dir),
        "files": len(applied),
    }
    try:
        write_update_state(updates_dir, state)
    except UpdateError:
        pass
    # 清掉 pending：安装已经完成，状态页若继续看到 pending 会误显示「待安装」，
    # 并让设置页的「检查更新 / 立即更新」一直禁用。
    try:
        pending_file = updates_dir / PENDING_FILE
        if pending_file.is_file():
            pending_file.unlink()
    except OSError:
        pass
    _log(log, f"更新完成：v{version}（{len(applied)} 个文件）")
    return dict(state)


def rollback_update(
    *,
    project_root: Path,
    backup_dir: Path,
    version: str = "",
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """按 backup.json 恢复上一次安装前的文件。"""

    root = Path(project_root).resolve()
    backup = Path(backup_dir)
    manifest_path = backup / BACKUP_FILE
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise UpdateError("找不到更新备份，无法回滚", code="backup_missing") from exc
    except OSError as exc:
        raise UpdateError(f"读取备份清单失败：{exc}", code="backup_read_failed") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise UpdateError("备份清单不是有效的 JSON", code="bad_backup") from exc
    if not isinstance(payload, Mapping):
        raise UpdateError("备份清单格式不正确", code="bad_backup")
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise UpdateError("备份清单缺少文件列表", code="bad_backup")

    resolved_version = _text(version) or _text(payload.get("version"))
    errors: list[str] = []
    restored = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        rel = _safe_relative(entry.get("path"))
        existed = entry.get("existed") is True
        try:
            _restore_entry(root, backup, rel, existed)
            restored += 1
        except Exception as exc:  # noqa: BLE001 - 收集后统一报错
            errors.append(f"{rel}: {exc}")
    if errors:
        raise UpdateError("回滚部分文件失败：" + "；".join(errors[:5]), code="rollback_failed")
    if backup.parent.name == "backups":
        try:
            write_update_state(
                backup.parent.parent,
                {
                    "status": "rolled_back",
                    "version": resolved_version,
                    "applied_at": _iso_now(),
                    "backup_dir": str(backup),
                    "files": restored,
                },
            )
        except UpdateError:
            pass
    _log(log, f"已回滚 {restored} 个文件（版本 {resolved_version or '未知'}）")
    return {"status": "rolled_back", "version": resolved_version, "files": restored}


def find_latest_backup(updates_dir: Path) -> Path | None:
    """返回 updates_dir/backups 下最新的、含 backup.json 的备份目录。"""

    root = Path(updates_dir) / "backups"
    try:
        candidates = [item for item in root.iterdir() if item.is_dir()]
    except FileNotFoundError:
        return None
    except OSError:
        return None
    candidates.sort(key=lambda item: item.name, reverse=True)
    for item in candidates:
        if (item / BACKUP_FILE).is_file():
            return item
    return None


# ---------------------------------------------------------------------------
# 进程管理
# ---------------------------------------------------------------------------


def _detached_kwargs() -> dict[str, Any]:
    """detached 子进程参数：不继承控制台、不弹黑框、不受父进程退出影响。"""

    kwargs: dict[str, Any] = dict(no_console_window_kwargs())
    if os.name == "nt":
        # DETACHED_PROCESS 让子进程完全没有控制台；CREATE_NEW_PROCESS_GROUP 让它
        # 不受父进程 Ctrl+C/关闭窗口影响。覆盖 no_console_window_kwargs 的
        # CREATE_NO_WINDOW（只隐藏窗口，仍会分配控制台）。
        flags = int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        kwargs["creationflags"] = flags
        return kwargs
    return {"start_new_session": True}


def spawn_update_runner(
    *,
    project_root: Path,
    pending_path: Path,
    log_dir: Path,
    api_pid: int | None = None,
) -> int:
    """detached 启动 scripts/apply_update.py，返回子进程 pid。"""

    root = Path(project_root).resolve()
    script = root / "scripts" / "apply_update.py"
    if not script.is_file():
        raise UpdateError("找不到更新脚本 scripts/apply_update.py", code="runner_missing")
    pending = Path(pending_path)
    if not pending.is_file():
        raise UpdateError("找不到待安装的更新文件 pending.json", code="pending_missing")
    logs = Path(log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"apply-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    command = [sys.executable, str(script), "--pending", str(pending), "--root", str(root)]
    if api_pid:
        command.extend(["--wait-pid", str(int(api_pid))])
    with open(log_path, "ab") as stream:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            close_fds=True,
            # no_console_window_kwargs 由 _detached_kwargs 合并（Windows 下换成
            # DETACHED_PROCESS，效果是子进程连控制台都没有，不会闪黑框）。
            **_detached_kwargs(),
        )
    return int(process.pid)


def restart_api(
    *,
    project_root: Path,
    log_dir: Path,
    env: Mapping[str, str] | None = None,
    python_executable: str | None = None,
) -> int:
    """detached 重启 API（python -m services.api.main），返回子进程 pid。"""

    root = Path(project_root).resolve()
    logs = Path(log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    executable = _text(python_executable) or sys.executable
    command = [executable, "-m", "services.api.main"]
    environment = dict(os.environ)
    if env:
        environment.update({str(key): str(value) for key, value in env.items()})
    environment.setdefault("PYTHONUNBUFFERED", "1")
    out_path = logs / "api.out.log"
    err_path = logs / "api.err.log"
    with open(out_path, "ab") as out_stream, open(err_path, "ab") as err_stream:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=out_stream,
            stderr=err_stream,
            env=environment,
            close_fds=True,
            # no_console_window_kwargs 由 _detached_kwargs 合并（Windows 下换成
            # DETACHED_PROCESS，效果是子进程连控制台都没有，不会闪黑框）。
            **_detached_kwargs(),
        )
    return int(process.pid)


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------


def _run_git(
    args: Iterable[Any],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess:
    argv = [str(item) for item in args]
    try:
        result = subprocess.run(
            ["git", *argv],
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_console_window_kwargs(),
        )
    except FileNotFoundError as exc:
        raise UpdateError("未找到 git 命令，请先安装 Git", code="git_missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("git 命令执行超时", code="git_timeout") from exc
    except OSError as exc:
        raise UpdateError(f"执行 git 失败：{exc}", code="git_failed") from exc
    if check and result.returncode != 0:
        detail = _text(result.stderr) or _text(result.stdout) or f"退出码 {result.returncode}"
        raise UpdateError(f"git {' '.join(argv)} 失败：{detail}", code="git_failed")
    return result


def _git_output(
    args: Iterable[Any],
    cwd: Path,
    *,
    env: Mapping[str, str] | None = None,
    check: bool = False,
) -> str:
    result = _run_git(args, cwd=cwd, env=env, check=check)
    return _text(result.stdout)


def _is_git_repository(root: Path) -> bool:
    try:
        result = _run_git(["rev-parse", "--show-toplevel"], cwd=root, check=False)
    except UpdateError:
        return False
    return result.returncode == 0 and bool(_text(result.stdout))


def _is_local_remote(url: str) -> bool:
    text = _text(url)
    if not text:
        return True
    lowered = text.lower()
    if lowered.startswith("file://"):
        return True
    if re.match(r"^[a-z]:[\\/]", text):
        return True
    if text.startswith(("\\\\", "/", "./", "../")):
        return True
    if "://" in text or text.startswith("git@"):
        return False
    return True


def _push_with_token(
    *,
    project_root: Path,
    config: GitHubConfig,
    remote: str,
    branch: str,
    tag: str,
    log: Callable[[str], None] | None = None,
) -> None:
    """用 Token 推送分支与 tag；Token 只出现在环境变量里。"""

    root = Path(project_root)
    remote_url = _git_output(["remote", "get-url", remote], root)
    token = _text(getattr(config, "token", ""))
    if not token and not _is_local_remote(remote_url):
        raise UpdateError("未配置 GitHub Token，无法推送到远程仓库", code="token_missing")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        credential = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: basic {credential}"
    # github.com 直连可能被墙：与 HTTP 客户端共用 GITHUB_UPDATE_PROXY 的分流约定，
    # 推送也走同一代理（-c 只作用于本次命令，不污染仓库配置）。
    proxy_url = (os.getenv("GITHUB_UPDATE_PROXY") or "").strip()
    proxy_args: list[Any] = ["-c", f"http.proxy={proxy_url}"] if proxy_url else []
    _log(log, f"推送分支 {branch} 到 {remote}")
    _run_git([*proxy_args, "push", remote, branch], cwd=root, env=env, timeout=300.0)
    if tag:
        _log(log, f"推送标签 {tag} 到 {remote}")
        _run_git([*proxy_args, "push", remote, tag], cwd=root, env=env, timeout=300.0)


def _run_release_tests(root: Path, *, log: Callable[[str], None] | None = None) -> None:
    _log(log, "运行测试套件（--run-tests）")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=str(root),
            capture_output=True,
            **no_console_window_kwargs(),
            text=True,
            timeout=1800,
        )
    except FileNotFoundError as exc:
        raise UpdateError("未找到 pytest，无法运行测试", code="tests_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("测试运行超时，已中止发布", code="tests_timeout") from exc
    if result.returncode != 0:
        _log(log, _text(result.stdout)[-800:])
        raise UpdateError("测试未通过，已中止发布", code="tests_failed")
    _log(log, "测试通过")


def _resolve_release_version(root: Path, version: Any, bump: Any) -> str:
    if _text(version):
        return normalize_version(version)
    kind = _text(bump).lower()
    if kind:
        return bump_version(read_local_version(root), kind)
    raise UpdateError("请填写版本号（version）或递增方式（bump）", code="version_required")


def _resolve_release_notes(root: Path, version: str, notes: str) -> str:
    text = str(notes or "").strip()
    if text:
        return text
    candidate = Path(root) / "docs" / "releases" / f"v{version}.md"
    try:
        if candidate.is_file():
            file_text = candidate.read_text(encoding="utf-8").strip()
            if file_text:
                return file_text
    except OSError:
        pass
    return f"发布 v{version}"


def run_release(
    *,
    project_root: Path,
    config: GitHubConfig,
    updates_dir: Path,
    version: str | None = None,
    bump: str | None = None,
    notes: str = "",
    prerelease: bool = False,
    dry_run: bool = False,
    push: bool = True,
    run_tests: bool = False,
    remote: str = "origin",
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """构建安装包 -> 提交版本号 -> 打 tag -> 推送 -> 创建 GitHub Release。"""

    root = Path(project_root).resolve()
    Path(updates_dir).mkdir(parents=True, exist_ok=True)
    target_version = _resolve_release_version(root, version, bump)
    tag = f"v{target_version}"
    _log(log, f"开始发布 v{target_version}")

    in_git = _is_git_repository(root)
    if not in_git and not dry_run:
        raise UpdateError("当前目录不是 Git 仓库（或未安装 Git），无法发布版本", code="not_git_repo")

    body = _resolve_release_notes(root, target_version, notes)
    dist_dir = root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = dist_dir / f"ai-listing-system-{target_version}.zip"
    artifact = build_release_archive(root, artifact_path, version=target_version)
    checksum_path = artifact_path.with_name(f"{artifact_path.name}.sha256")
    checksum_text = f"{artifact['sha256']}  {artifact_path.name}\n"
    checksum_path.write_text(checksum_text, encoding="utf-8")
    artifact["checksum_path"] = str(checksum_path)
    _log(
        log,
        f"安装包已生成：{artifact_path.name}（{artifact['size']} 字节，{artifact['files']} 个文件）",
    )

    if dry_run:
        _log(log, "dry_run：跳过 git 提交与 GitHub 发布")
        return {"ok": True, "dry_run": True, "version": target_version, "artifact": artifact}

    if run_tests:
        _run_release_tests(root, log=log)

    old_version_text = _read_version_text(root)
    version_written = False
    tag_created = False
    pushed = False
    commit_sha = ""
    release: ReleaseInfo | None = None
    step = "写入版本号"
    try:
        step = "写入版本号"
        _write_version_text(root, f"{target_version}\n")
        version_written = True
        _run_git(["add", "--", "VERSION"], cwd=root)
        staged = _run_git(["diff", "--cached", "--quiet", "--", "VERSION"], cwd=root, check=False)
        if staged.returncode != 0:
            step = "提交版本号"
            _run_git(["commit", "-m", f"chore(release): v{target_version}"], cwd=root)
            _log(log, f"已提交版本号变更：v{target_version}")

        step = "创建标签"
        existing = _run_git(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"], cwd=root, check=False)
        if existing.returncode == 0:
            raise UpdateError(f"标签 {tag} 已存在，不能重复发布", code="tag_exists")
        _run_git(["tag", "-a", tag, "-m", tag], cwd=root)
        tag_created = True
        commit_sha = _git_output(["rev-parse", "HEAD"], root)
        branch = _git_output(["rev-parse", "--abbrev-ref", "HEAD"], root) or "main"

        step = "推送代码"
        if push:
            _push_with_token(
                project_root=root,
                config=config,
                remote=remote,
                branch=branch,
                tag=tag,
                log=log,
            )
            pushed = True

        step = "创建 Release"
        if not isinstance(config, GitHubConfig) or not config.configured:
            raise UpdateError("未配置 GitHub 仓库，无法创建 Release", code="not_configured")
        client = GitHubClient(config)
        release = client.create_release(
            tag=tag,
            name=tag,
            body=body,
            target_commitish=branch,
            prerelease=bool(prerelease),
        )
        upload_url = _text(release.raw.get("upload_url")) if isinstance(release.raw, Mapping) else ""
        if not upload_url:
            release_id = release.raw.get("id") if isinstance(release.raw, Mapping) else None
            if release_id:
                upload_url = (
                    f"https://uploads.github.com/repos/{config.slug}"
                    f"/releases/{release_id}/assets"
                )
        if not upload_url:
            raise UpdateError("GitHub 返回的 Release 缺少上传地址", code="bad_response")

        step = "上传安装包"
        client.upload_asset(
            upload_url,
            artifact_path.name,
            artifact_path.read_bytes(),
            content_type="application/zip",
        )
        client.upload_asset(
            upload_url,
            checksum_path.name,
            checksum_text.encode("utf-8"),
            content_type="text/plain",
        )
        _log(log, f"Release 已创建：{release.html_url or tag}")
    except Exception as exc:  # noqa: BLE001 - 失败要尽量恢复本地状态
        if tag_created:
            try:
                _run_git(["tag", "-d", tag], cwd=root, check=False)
            except UpdateError:
                pass
        if version_written:
            try:
                if old_version_text is None:
                    (root / "VERSION").unlink(missing_ok=True)
                else:
                    _write_version_text(root, old_version_text)
            except OSError:
                pass
        message = f"发布失败（{step}）：{exc}"
        if isinstance(exc, UpdateError):
            raise UpdateError(message, code=exc.code, status=exc.status) from exc
        raise UpdateError(message, code="release_failed") from exc

    release_url = release.html_url if release is not None else ""
    return {
        "ok": True,
        "version": target_version,
        "tag": tag,
        "release_url": release_url,
        "artifact": artifact,
        "pushed": pushed,
        "commit": commit_sha,
    }
