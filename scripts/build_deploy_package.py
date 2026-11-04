#!/usr/bin/env python
"""构建「新机部署包」：标准发布内容 + 运行时大件（自带 Chrome / 导出模板 / .env.example / package.json）。

与 `scripts/publish_release.py`（走 GitHub 更新通道的**代码**包）的区别：

- 更新通道的安装包刻意排除 vendor/storage（几百 MB 的 Chrome 不该跟着每次更新下载），
  且安装时会按 `_allowed_release_path` 白名单拒绝白名单外的路径；
- 本脚本产出的是**新 Windows 机器首次部署**用的完整包：手动解压 + 装依赖即可跑，
  manifest.json 仅供人核对，不喂给应用内更新器（里面的 vendor 路径不在更新白名单）。

用法：

    python scripts/build_deploy_package.py               # 用 VERSION 文件里的版本号
    python scripts/build_deploy_package.py --version 1.0.2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api import github_updates  # noqa: E402

# 额外打进部署包的顶层文件（更新通道白名单之外、但新机开箱即用所必需）。
DEPLOY_EXTRA_FILES = (
    ".env.example",
    "package.json",
)
# 额外打包的目录（整目录收集，沿用发布器同一套 skip 规则）。
DEPLOY_EXTRA_ROOTS = (
    "vendor",          # 便携版 Chrome（马帮采集浏览器自动发现，新机不用装 Chrome）
    "storage/samples",  # 马帮导入模板的本地兜底（NAS 不可达时导出仍可用）
)

OUTPUT_NAME = "ai-listing-system-{version}-deploy-full.zip"


def _collect_extra_files(root: Path) -> list[Path]:
    collected: list[Path] = []
    seen: set[str] = set()
    for dir_name in DEPLOY_EXTRA_ROOTS:
        base = root / dir_name
        if not base.is_dir():
            print(f"[warn] 额外目录不存在，跳过：{dir_name}")
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(name for name in dirnames if not github_updates._skip_dir_name(name))
            for name in sorted(filenames):
                if github_updates._skip_file_name(name):
                    continue
                item = Path(dirpath) / name
                if item.is_symlink() or not item.is_file():
                    continue
                rel = item.relative_to(root).as_posix()
                if rel not in seen:
                    seen.add(rel)
                    collected.append(item)
    for name in DEPLOY_EXTRA_FILES:
        item = root / name
        if item.is_file() and not item.is_symlink():
            rel = item.relative_to(root).as_posix()
            if rel not in seen:
                seen.add(rel)
                collected.append(item)
    return collected


def build_deploy_package(project_root: Path, output_path: Path, *, version: str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    output = Path(output_path)
    target_version = github_updates.normalize_version(version)

    files: dict[str, Path] = {}
    for item in github_updates._collect_release_files(root):
        files[item.relative_to(root).as_posix()] = item
    for item in _collect_extra_files(root):
        files[item.relative_to(root).as_posix()] = item

    # 绝不把真实 .env（马帮/云端凭据）或数据/日志打进部署包。
    for guarded in (".env",):
        files.pop(guarded, None)
    output_resolved = output.resolve()
    files = {
        rel: item
        for rel, item in files.items()
        if item.resolve() != output_resolved
        and not rel.startswith(("dist/", "data/", ".git/"))
    }
    if not files:
        raise github_updates.UpdateError("没有可打包的文件", code="empty_release")

    version_bytes = f"{target_version}\n".encode("utf-8")
    manifest_files: list[dict[str, Any]] = [
        {"path": "VERSION", "sha256": hashlib.sha256(version_bytes).hexdigest(), "size": len(version_bytes)}
    ]
    for rel in sorted(files):
        digest, size = github_updates._hash_file_stream(files[rel])
        manifest_files.append({"path": rel, "sha256": digest, "size": size})
    manifest = {"format": 1, "version": target_version, "files": manifest_files, "delete": []}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f"{output.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(github_updates.MANIFEST_FILE, manifest_bytes)
            archive.writestr("VERSION", version_bytes)
            for item in manifest_files:
                rel = item["path"]
                if rel == "VERSION":
                    continue
                with open(files[rel], "rb") as source, archive.open(rel, "w") as member:
                    github_updates.shutil.copyfileobj(source, member, 1024 * 1024)
        os.replace(temp, output)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)

    return {
        "path": str(output),
        "version": target_version,
        "sha256": github_updates.sha256_file(output),
        "size": output.stat().st_size,
        "files": len(manifest_files),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建新 Windows 部署包（含自带 Chrome 与模板）")
    parser.add_argument("--version", default="", help="版本号（默认读 VERSION 文件）")
    parser.add_argument("--output", default="", help="输出 zip 路径（默认 dist/ 下）")
    args = parser.parse_args(argv)

    version = str(args.version or "").strip() or github_updates.read_local_version(PROJECT_ROOT)
    output = (
        Path(args.output).expanduser()
        if str(args.output or "").strip()
        else PROJECT_ROOT / "dist" / OUTPUT_NAME.format(version=version)
    )
    result = build_deploy_package(PROJECT_ROOT, output, version=version)
    sidecar = Path(str(result["path"]) + ".sha256")
    sidecar.write_text(f"{result['sha256']}  {output.name}\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"sha256 sidecar: {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
