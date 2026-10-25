#!/usr/bin/env python
"""发布新版本（薄封装）：构建安装包 -> 提交版本号 -> 打 tag -> 推送 -> 创建 Release。

示例：

    python scripts/publish_release.py --bump patch --notes "修复若干问题"
    python scripts/publish_release.py --version 1.2.0 --dry-run

Token 默认取环境变量 GITHUB_UPDATE_TOKEN，仓库默认取 GITHUB_UPDATE_REPO 或运行时配置。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api import github_updates  # noqa: E402


def _runtime_updates() -> dict:
    """尽力读取运行时配置里的 updates 分组（缺存储层依赖时返回空 dict）。"""

    try:
        from services.api import storage

        config = storage.get_runtime_configuration() or {}
        updates = config.get("updates") if isinstance(config, dict) else None
        data = dict(updates) if isinstance(updates, dict) else {}
        try:
            token = str(storage.get_github_update_token() or "").strip()
        except Exception:  # noqa: BLE001 - 没配置/解密失败都按空处理
            token = ""
        if token:
            data["token"] = token
        return data
    except Exception:  # noqa: BLE001 - 独立运行时允许没有存储层
        return {}


def _read_notes(args: argparse.Namespace) -> str:
    if str(args.notes or "").strip():
        return str(args.notes).strip()
    path_text = str(args.notes_file or "").strip()
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise github_updates.UpdateError(f"读取发布说明失败：{exc}", code="notes_read_failed") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="发布 ai-listing-system 新版本到 GitHub")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--version", default="", help="新版本号，例如 1.2.0")
    group.add_argument("--bump", choices=["patch", "minor", "major"], default="", help="按当前版本递增")
    parser.add_argument("--notes", default="", help="发布说明文本")
    parser.add_argument("--notes-file", default="", help="发布说明文件（Markdown）")
    parser.add_argument("--prerelease", action="store_true", help="标记为预发布")
    parser.add_argument("--dry-run", action="store_true", help="只构建安装包，不提交/推送/发 Release")
    parser.add_argument("--no-push", action="store_true", help="不推送 git 分支与标签")
    parser.add_argument("--repo", default="", help="GitHub 仓库 owner/name 或 URL")
    parser.add_argument("--token", default="", help="GitHub Token（默认取环境变量）")
    parser.add_argument("--api-url", default="", help="GitHub API 地址")
    parser.add_argument("--remote", default="origin", help="git 远端名（默认 origin）")
    parser.add_argument("--run-tests", action="store_true", help="发布前跑一遍 pytest")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.version and not args.bump:
        parser.error("必须指定 --version 或 --bump")
    try:
        notes = _read_notes(args)
    except github_updates.UpdateError as exc:
        print(f"发布失败：{exc}", file=sys.stderr)
        return 1

    environ = dict(os.environ)
    if str(args.token or "").strip():
        environ["GITHUB_UPDATE_TOKEN"] = str(args.token).strip()
    if str(args.repo or "").strip():
        environ["GITHUB_UPDATE_REPO"] = str(args.repo).strip()
    if str(args.api_url or "").strip():
        environ["GITHUB_API_URL"] = str(args.api_url).strip()
    config = github_updates.GitHubConfig.from_runtime(
        {"updates": _runtime_updates()},
        environ=environ,
    )

    if not config.configured:
        print("提示：未解析到 GitHub 仓库（--repo / GITHUB_UPDATE_REPO / 运行时配置）", file=sys.stderr)
    updates_dir = PROJECT_ROOT / "storage" / "updates"
    print(f"当前版本：{github_updates.read_local_version(PROJECT_ROOT)}")
    print(f"仓库：{config.slug or '（未配置）'}  API：{config.api_url}")

    try:
        result = github_updates.run_release(
            project_root=PROJECT_ROOT,
            config=config,
            updates_dir=updates_dir,
            version=str(args.version or "").strip() or None,
            bump=str(args.bump or "").strip() or None,
            notes=notes,
            prerelease=bool(args.prerelease),
            dry_run=bool(args.dry_run),
            push=not bool(args.no_push),
            run_tests=bool(args.run_tests),
            remote=str(args.remote or "origin"),
            log=lambda line: print(line, flush=True),
        )
    except github_updates.UpdateError as exc:
        print(f"发布失败：{exc}", file=sys.stderr)
        return 1

    print("")
    if result.get("dry_run"):
        print(f"dry_run 完成：v{result.get('version')} -> {result.get('artifact', {}).get('path')}")
    else:
        print(f"发布成功：v{result.get('version')}  tag={result.get('tag')}")
        print(f"Release：{result.get('release_url') or '（未返回）'}")
        print(f"已推送：{result.get('pushed')}  提交：{result.get('commit')}")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
