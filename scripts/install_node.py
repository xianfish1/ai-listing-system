#!/usr/bin/env python
"""部署初始化：确保系统级 Node.js 可用，缺失时自动下载并静默安装。

新机部署链路（docs/DEPLOY_NEW_WINDOWS.md / DEPLOY_NEW_MACOS.md）里的 ``npm install``
假定系统已装 Node，实际没有任何检查或安装逻辑——PATH 上没有 node 时，自动化 worker
只会在 spawn 执行器那一刻报 ``无法启动 Node：[WinError 2]``。本脚本挂在启动脚本与
部署文档里做兜底：

- 已装（PATH 或标准安装位置）→ 打印版本直接退出 0，幂等秒过；
- 缺失 → 从 Node 官方分发包下载并系统级安装：
  - Windows：MSI + ``msiexec /qn``（需要管理员权限）；
  - macOS：pkg + ``installer``（需要 sudo / root）；
  - Linux：tar.xz 解压到 /usr/local（需要 root）。
- 默认走 npmmirror 国内镜像（不走代理也快），``NODEJS_MIRROR`` 环境变量可切换
  （官方源填 ``https://nodejs.org/dist/``）。

用法：

    python scripts/install_node.py                 # 检查，缺失则安装
    python scripts/install_node.py --dry-run       # 只打印将要下载的 URL 与安装命令
    python scripts/install_node.py --version v22.21.1

版本号写死经过验证的 LTS（v22.21.1，2026-11-05 在 npmmirror 与官方源均确认存在），
避免运行时解析镜像目录列表；``--version`` 可覆盖。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api.subprocess_utils import no_console_window_kwargs  # noqa: E402

DEFAULT_NODE_VERSION = "v22.21.1"
DEFAULT_MIRROR = "https://npmmirror.com/mirrors/node"
OFFICIAL_MIRROR = "https://nodejs.org/dist"
# 与 browser_use_runner._node_fallback_candidates 同一路径集：PATH 靠不住时兜底。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_RETRIES = 2  # 总尝试次数：首次 + 重试一次


def node_mirror() -> str:
    """分发镜像基址：默认国内 npmmirror，环境变量 NODEJS_MIRROR 覆盖。"""
    return str(os.environ.get("NODEJS_MIRROR") or "").strip().rstrip("/") or DEFAULT_MIRROR


def node_distribution_filename(version: str, *, system: str | None = None, machine: str | None = None) -> str:
    """按平台拼官方分发包文件名（version 含 v 前缀）。"""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    if system == "windows":
        return f"node-{version}-x64.msi"
    if system == "darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
        return f"node-{version}-osx-{arch}.pkg"
    if system == "linux":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
        return f"node-{version}-linux-{arch}.tar.xz"
    raise SystemExit(f"[install-node] 不支持的平台：{system}（只支持 Windows / macOS / Linux）")


def normalize_dist_version(version: str) -> str:
    """允许 ``22.21.1`` 这种不带 v 的写法，统一成官方分发包要求的 ``v22.21.1``。"""
    version = version.strip()
    if version.startswith("v"):
        return version
    return f"v{version}"


def dist_urls(mirror: str, version: str, filename: str) -> tuple[str, str]:
    """返回 (分发包 URL, SHASUMS256.txt URL)。"""
    base = mirror.rstrip("/")
    return f"{base}/{version}/{filename}", f"{base}/{version}/SHASUMS256.txt"


def _node_fallback_candidates() -> list[Path]:
    """标准安装位置的 node 可执行文件，仅在 PATH 上找不到时用。"""
    if os.name == "nt":
        roots = [
            Path(os.environ.get("ProgramFiles") or r"C:\Program Files"),
            Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"),
        ]
        candidates = [root / "nodejs" / "node.exe" for root in roots]
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "nodejs" / "node.exe")
        return candidates
    return [
        Path("/usr/local/bin/node"),
        Path("/opt/homebrew/bin/node"),
    ]


def probe_node_version(node_path: str) -> str | None:
    """``node --version`` 探活：能跑且退出码 0 才算可用。"""
    try:
        result = subprocess.run(
            [node_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            **no_console_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    version = (result.stdout or result.stderr or "").strip()
    return version or None


def find_existing_node() -> tuple[str, str] | None:
    """返回 (node 绝对路径, 版本)；PATH 优先，其次标准安装位置。"""
    candidates: list[str] = []
    which = shutil.which("node")
    if which:
        candidates.append(which)
    candidates.extend(str(path) for path in _node_fallback_candidates() if path.is_file())
    for candidate in candidates:
        version = probe_node_version(candidate)
        if version:
            return candidate, version
    return None


def windows_installed_node_path() -> Path:
    """MSI 装完后 node.exe 的标准落点（当前进程 PATH 不刷新，必须用绝对路径验证）。"""
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(program_files) / "nodejs" / "node.exe"


def is_admin() -> bool:
    if os.name == "nt":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001 - 拿不到就按非管理员处理，让 msiexec 的报错兜底
            return False
    return os.geteuid() == 0


def download_file(url: str, destination: Path) -> None:
    """流式下载到 destination；浏览器 UA（Cloudflare 1010 教训）+ 失败重试一次。"""
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                with open(destination, "wb") as sink:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        sink.write(chunk)
            return
        except (urllib.error.URLError, OSError) as error:
            last_error = error
            print(f"[install-node] 下载失败（第 {attempt} 次）：{error}")
    raise SystemExit(f"[install-node] 下载失败，已重试 {DOWNLOAD_RETRIES} 次：{url}（{last_error}）")


def expected_sha256(shasums_text: str, filename: str) -> str:
    """从 SHASUMS256.txt 内容里取指定文件的哈希；取不到报错退出。"""
    for line in shasums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == filename:
            return parts[0]
    raise SystemExit(f"[install-node] SHASUMS256.txt 里找不到 {filename}，无法校验完整性")


def verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected.lower():
        raise SystemExit(
            f"[install-node] sha256 校验失败：{path.name}\n"
            f"  期望 {expected.lower()}\n  实际 {actual}"
        )


def install_command_plan(package_path: Path) -> list[str]:
    """返回安装命令（同时用于 --dry-run 打印与真实执行）。"""
    if os.name == "nt":
        return ["msiexec", "/i", str(package_path), "/qn", "/norestart"]
    if platform.system().lower() == "darwin":
        prefix = [] if is_admin() else ["sudo"]
        return prefix + ["installer", "-pkg", str(package_path), "-target", "/"]
    return ["sudo", "tar", "-xJf", str(package_path), "-C", "/usr/local", "--strip-components=1"]


def run_installer(command: list[str]) -> None:
    print(f"[install-node] 开始系统级安装：{' '.join(command)}")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
        **no_console_window_kwargs(),
    )
    if result.returncode != 0:
        raise SystemExit(
            f"[install-node] 安装命令退出码 {result.returncode}：\n{result.stdout or ''}\n{result.stderr or ''}"
        )


def ensure_node(version: str, *, dry_run: bool) -> None:
    system = platform.system().lower()
    filename = node_distribution_filename(version)
    mirror = node_mirror()
    package_url, shasums_url = dist_urls(mirror, version, filename)

    print(f"[install-node] 未检测到系统 Node，准备安装 {version}")
    if not dry_run and not is_admin():
        raise SystemExit(
            "[install-node] 安装系统级 Node 需要管理员权限：请用管理员身份重新运行本命令"
            + ("（PowerShell：右键「以管理员身份运行」）" if os.name == "nt" else "（sudo）")
        )
    print(f"[install-node] 镜像：{mirror}")
    print(f"[install-node] 下载：{package_url}")
    if dry_run:
        print(f"[install-node] 校验：{shasums_url}")
        print(f"[install-node] 安装命令：{' '.join(install_command_plan(Path(f'<tmp>/{filename}')))}")
        print("[install-node] dry-run 结束，未下载未安装。")
        return

    with tempfile.TemporaryDirectory(prefix="install-node-") as temp_dir:
        package_path = Path(temp_dir) / filename
        download_file(package_url, package_path)
        print(f"[install-node] 已下载 {package_path.stat().st_size} 字节，校验完整性…")
        shasums_path = Path(temp_dir) / "SHASUMS256.txt"
        download_file(shasums_url, shasums_path)
        verify_sha256(package_path, expected_sha256(shasums_path.read_text(encoding="utf-8"), filename))
        run_installer(install_command_plan(package_path))

    # msiexec 不改当前进程的 PATH，这里必须用安装后的标准落点验证。
    if system == "windows":
        installed = windows_installed_node_path()
        installed_version = probe_node_version(str(installed))
        if not installed_version:
            raise SystemExit(
                f"[install-node] 安装命令已成功，但 {installed} 探活失败——请手动确认后重启 API。"
            )
    else:
        found = find_existing_node()
        if not found:
            raise SystemExit("[install-node] 安装命令已成功，但仍探活不到 node——请重新打开终端后重试。")
        installed_version = found[1]
    print(f"[install-node] 安装完成：node {installed_version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="部署初始化：确保系统级 Node.js 可用")
    parser.add_argument("--version", default=DEFAULT_NODE_VERSION, help="要安装的 Node 版本（默认写死 LTS）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要下载的 URL 与安装命令")
    args = parser.parse_args(argv)

    found = find_existing_node()
    if found:
        path, version = found
        print(f"[install-node] 已有系统 Node：node {version}（{path}）")
        return 0

    ensure_node(normalize_dist_version(args.version), dry_run=bool(args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
