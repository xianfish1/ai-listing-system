"""Bundle a project-local Chrome under vendor/chrome for the automation worker.

The automation executor drives Chrome through Playwright, and Playwright already
downloads a matching Chromium build. Rather than depend on a system-installed
Chrome (which may be missing or the wrong version), we copy that build into the
project so the project owns its own browser and can launch it itself.

Resolution order:
  1. Copy from an existing Playwright browser cache (fast, version-matched).
  2. Fall back to `playwright install chromium` into the project vendor dir.

Usage:
    python -m scripts.bundle_chrome            # bundle if missing
    python -m scripts.bundle_chrome --force    # re-copy even if present
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_CHROME_DIR = PROJECT_ROOT / "vendor" / "chrome"


def _existing_bundled_chrome() -> Path | None:
    if not VENDOR_CHROME_DIR.is_dir():
        return None
    for pattern in ("chrome.exe", "*/chrome.exe", "*/*/chrome.exe", "chrome", "*/chrome"):
        matches = [
            c
            for c in sorted(VENDOR_CHROME_DIR.glob(pattern))
            if c.is_file() and not (os.name != "nt" and c.suffix.lower() == ".exe")
        ]
        match = matches[0] if matches else None
        if match:
            return match
    # macOS 的 Playwright 布局藏得更深（chrome-mac/Chromium.app/Contents/MacOS/Chromium）。
    if os.name != "nt":
        for candidate in sorted(VENDOR_CHROME_DIR.rglob("*")):
            if candidate.is_file() and candidate.name in {"chrome", "Chromium"}:
                return candidate
    return None


def _playwright_cache_dirs() -> list[Path]:
    dirs: list[Path] = []
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_path:
        dirs.append(Path(env_path))
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app:
        dirs.append(Path(local_app) / "ms-playwright")
    dirs.append(Path.home() / "AppData" / "Local" / "ms-playwright")
    dirs.append(Path.home() / ".cache" / "ms-playwright")
    if sys.platform == "darwin":
        dirs.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in dirs:
        if candidate not in seen and candidate.is_dir():
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _find_cached_chromium_dir() -> Path | None:
    """Return the newest chromium-<build> directory from any Playwright cache."""

    newest: tuple[int, Path] | None = None
    for cache in _playwright_cache_dirs():
        for entry in cache.glob("chromium-*"):
            if not entry.is_dir():
                continue
            try:
                build = int(entry.name.split("-", 1)[1])
            except (IndexError, ValueError):
                continue
            if newest is None or build > newest[0]:
                newest = (build, entry)
    return newest[1] if newest else None


def _copy_from_cache(cached_dir: Path) -> Path:
    """Copy a cached chromium-<build> tree into vendor/chrome and return the exe."""

    target = VENDOR_CHROME_DIR / cached_dir.name
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cached_dir, target)
    exe = _existing_bundled_chrome()
    if not exe:
        raise RuntimeError(f"复制完成但未在 {target} 找到 chrome 可执行文件")
    return exe


def _install_via_playwright() -> Path:
    """Run `playwright install chromium` targeting the project vendor dir."""

    VENDOR_CHROME_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(VENDOR_CHROME_DIR)
    npx = shutil.which("npx") or "npx"
    print(f"未找到 Playwright 缓存，改用 {npx} playwright install chromium 下载到 {VENDOR_CHROME_DIR} ...")
    subprocess.run([npx, "playwright", "install", "chromium"], cwd=str(PROJECT_ROOT), env=env, check=True)
    exe = _existing_bundled_chrome()
    if not exe:
        raise RuntimeError("playwright install 已运行，但未找到 chrome 可执行文件")
    return exe


def bundle_chrome(*, force: bool = False) -> Path:
    existing = _existing_bundled_chrome()
    if existing and not force:
        print(f"已存在内置 Chrome：{existing}")
        return existing
    if existing and force:
        print(f"--force：清除现有内置 Chrome 目录 {VENDOR_CHROME_DIR}")
        shutil.rmtree(VENDOR_CHROME_DIR, ignore_errors=True)
    cached = _find_cached_chromium_dir()
    if cached:
        print(f"从 Playwright 缓存复制：{cached}")
        exe = _copy_from_cache(cached)
    else:
        exe = _install_via_playwright()
    print(f"内置 Chrome 就绪：{exe}")
    return exe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bundle a project-local Chrome for the automation worker")
    parser.add_argument("--force", action="store_true", help="re-copy even if a bundled Chrome already exists")
    args = parser.parse_args(argv)
    try:
        bundle_chrome(force=args.force)
    except Exception as exc:  # noqa: BLE001 - surface a friendly message to the operator
        print(f"内置 Chrome 失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
