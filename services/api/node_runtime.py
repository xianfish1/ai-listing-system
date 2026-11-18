"""定位 Node.js 解释器，并在需要时把它注入 PATH（本地补丁，官方包无此文件）。

背景（2026-11-05 现场）：这台机器没有独立安装 Node.js（``C:\\Program Files\\nodejs``
不存在），唯一的 ``node.exe`` 在

    %USERPROFILE%\\.workbuddy\\binaries\\node\\versions\\<版本>\\node.exe

而这个目录只会被注入到 WorkBuddy 工具会话的 PATH 里。看护（supervisor）或 explorer
拉起的 API 进程 PATH 里没有它 → ``shutil.which("node")`` 返回 None → 自动登录/采集
子进程 ``[WinError 2] 系统找不到指定的文件`` → 界面只显示
「马帮登录未确认：failed - 无法启动 Node」。

解析顺序固定为：
    1. PATH 上的 node（正常安装的机器走这条，行为与官方一致）
    2. ``MABANG_NODE`` 环境变量（可指向 node.exe 或它所在目录）
    3. 标准安装位置（Program Files\\nodejs、LOCALAPPDATA\\Programs\\nodejs）
    4. WorkBuddy 自带：``versions/current`` 标记 → 版本号最大的那一份

WorkBuddy 升级换了版本目录也不用改代码；要临时换用别的 node，设 ``MABANG_NODE`` 即可。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


def _version_key(path: Path) -> tuple[int, ...]:
    """把版本目录名（如 ``22.22.2-6``）拆成数字元组，用于挑最新的一份。"""

    return tuple(int(part) for part in re.findall(r"\d+", path.name))


def _as_executable(raw: str) -> Path | None:
    """把 MABANG_NODE 的写法（node.exe 路径或目录）归一成 node.exe 路径。"""

    value = str(raw or "").strip().strip('"')
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_dir():
        candidate = path / "node.exe"
        return candidate if candidate.is_file() else None
    return path if path.is_file() else None


def workbuddy_node_paths() -> list[Path]:
    """WorkBuddy 自带 node 的候选路径（current 标记优先，其余按版本号倒序）。"""

    user_profile = str(os.environ.get("USERPROFILE") or "").strip()
    home = Path(user_profile) if user_profile else Path.home()
    versions_dir = home / ".workbuddy" / "binaries" / "node" / "versions"
    if not versions_dir.is_dir():
        return []
    ordered: list[Path] = []
    try:
        current = (versions_dir / "current").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        current = ""
    if current:
        ordered.append(versions_dir / current / "node.exe")
    try:
        installed = [entry for entry in versions_dir.iterdir() if (entry / "node.exe").is_file()]
    except OSError:
        installed = []
    installed.sort(key=_version_key, reverse=True)
    ordered.extend(entry / "node.exe" for entry in installed)
    return [path for path in dict.fromkeys(ordered) if path.is_file()]


def node_candidates() -> list[str]:
    """按优先级返回所有可用的 node 可执行文件绝对路径（去重、只保留真实存在的）。"""

    candidates: list[Path] = []
    found = shutil.which("node")
    if found:
        candidates.append(Path(found))
    explicit = _as_executable(os.environ.get("MABANG_NODE", ""))
    if explicit is not None:
        candidates.append(explicit)
    for root in (
        os.environ.get("ProgramFiles") or r"C:\Program Files",
        os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
    ):
        candidates.append(Path(root) / "nodejs" / "node.exe")
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "nodejs" / "node.exe")
    candidates.extend(workbuddy_node_paths())
    unique = list(dict.fromkeys(candidates))
    return [str(path) for path in unique if path.is_file()]


def which_node() -> str | None:
    """PATH 优先，其次见模块注释里的兜底顺序；都没有返回 None。"""

    candidates = node_candidates()
    return candidates[0] if candidates else None


def ensure_node_on_path() -> str | None:
    """把找到的 node 目录放到 PATH 最前面，返回该目录；找不到返回 None。

    放最前面是为了压过残留的坏 node（例如被卸载的 RPA 工具留在 PATH 里的条目），
    同时让本进程拉起的 node 子进程（含 CJS 内部再 spawn 的）都能直接找到 node。
    """

    candidate = which_node()
    if not candidate:
        return None
    node_dir = str(Path(candidate).parent)
    parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    if parts and parts[0] == node_dir:
        return node_dir
    os.environ["PATH"] = os.pathsep.join([node_dir, *[p for p in parts if p != node_dir]])
    return node_dir
