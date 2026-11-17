"""install_node.py 的纯逻辑单测：不联网、不真装 node。

覆盖：版本归一化、分发包文件名/URL 拼接（镜像切换）、node 探活与兜底路径、
SHASUMS 校验、安装命令按平台分支、--dry-run 全链路输出。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from scripts import install_node


def test_normalize_dist_version_accepts_bare_and_prefixed():
    assert install_node.normalize_dist_version("v22.21.1") == "v22.21.1"
    assert install_node.normalize_dist_version("22.21.1") == "v22.21.1"


def test_default_version_is_pinned_lts():
    # 写死版本是部署可重复性的前提：改动这里必须先在镜像上确认存在。
    assert install_node.DEFAULT_NODE_VERSION == "v22.21.1"


def test_distribution_filename_per_platform():
    v = "v22.21.1"
    assert install_node.node_distribution_filename(v, system="Windows", machine="AMD64") == f"node-{v}-x64.msi"
    assert install_node.node_distribution_filename(v, system="Darwin", machine="arm64") == f"node-{v}-osx-arm64.pkg"
    assert install_node.node_distribution_filename(v, system="Darwin", machine="x86_64") == f"node-{v}-osx-x64.pkg"
    assert install_node.node_distribution_filename(v, system="Linux", machine="aarch64") == f"node-{v}-linux-arm64.tar.xz"
    assert install_node.node_distribution_filename(v, system="Linux", machine="x86_64") == f"node-{v}-linux-x64.tar.xz"


def test_dist_urls_default_mirror_and_env_override(monkeypatch):
    url, shasums = install_node.dist_urls(install_node.node_mirror(), "v22.21.1", "node-v22.21.1-x64.msi")
    assert url == "https://npmmirror.com/mirrors/node/v22.21.1/node-v22.21.1-x64.msi"
    assert shasums == "https://npmmirror.com/mirrors/node/v22.21.1/SHASUMS256.txt"

    monkeypatch.setenv("NODEJS_MIRROR", "https://nodejs.org/dist/")
    url, _ = install_node.dist_urls(install_node.node_mirror(), "v22.21.1", "node-v22.21.1-x64.msi")
    assert url == "https://nodejs.org/dist/v22.21.1/node-v22.21.1-x64.msi"


def test_node_mirror_tolerates_trailing_slash(monkeypatch):
    monkeypatch.setenv("NODEJS_MIRROR", "https://nodejs.org/dist/")
    assert install_node.node_mirror() == "https://nodejs.org/dist"
    monkeypatch.delenv("NODEJS_MIRROR", raising=False)
    assert install_node.node_mirror() == install_node.DEFAULT_MIRROR


def test_find_existing_node_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(install_node.shutil, "which", lambda name: None)
    monkeypatch.setattr(install_node, "_node_fallback_candidates", lambda: [])
    assert install_node.find_existing_node() is None


def test_find_existing_node_prefers_path_then_fallback(monkeypatch):
    calls: list[str] = []

    def fake_probe(node_path: str) -> str | None:
        calls.append(node_path)
        if node_path.endswith("from-path"):
            return "v22.21.1"
        return None

    monkeypatch.setattr(install_node.shutil, "which", lambda name: "C:/fake/from-path")
    monkeypatch.setattr(install_node, "_node_fallback_candidates", lambda: [Path("C:/fake/standard")])
    monkeypatch.setattr(install_node, "probe_node_version", fake_probe)

    found = install_node.find_existing_node()
    assert found == ("C:/fake/from-path", "v22.21.1")
    assert calls == ["C:/fake/from-path"]  # PATH 命中就不再探兜底


def test_find_existing_node_skips_broken_fallback(monkeypatch):
    # PATH 没命中、兜底路径探活失败（坏 node）→ 不能当作已装。
    monkeypatch.setattr(install_node.shutil, "which", lambda name: None)
    monkeypatch.setattr(install_node, "_node_fallback_candidates", lambda: [Path("C:/fake/broken")])
    monkeypatch.setattr(install_node, "probe_node_version", lambda path: None)
    assert install_node.find_existing_node() is None


def test_expected_sha256_and_verify(tmp_path):
    shasums = "\n".join(
        [
            "abc123  node-v22.21.1-linux-x64.tar.xz",
            "fd7a63fec3a54a665851e2d3d93e07cfead2ffb4521675ffdbceb1bb5ac009bb  node-v22.21.1-x64.msi",
        ]
    )
    assert (
        install_node.expected_sha256(shasums, "node-v22.21.1-x64.msi")
        == "fd7a63fec3a54a665851e2d3d93e07cfead2ffb4521675ffdbceb1bb5ac009bb"
    )
    with pytest.raises(SystemExit, match="SHASUMS256.txt 里找不到"):
        install_node.expected_sha256(shasums, "not-in-list.msi")

    payload = tmp_path / "pkg"
    payload.write_bytes(b"node installer bytes")
    good = hashlib.sha256(b"node installer bytes").hexdigest()
    install_node.verify_sha256(payload, good)
    with pytest.raises(SystemExit, match="sha256 校验失败"):
        install_node.verify_sha256(payload, "0" * 64)


def test_install_command_plan_windows(monkeypatch):
    monkeypatch.setattr(install_node.os, "name", "nt")
    command = install_node.install_command_plan(Path(r"C:\tmp\node-v22.21.1-x64.msi"))
    assert command[:2] == ["msiexec", "/i"]
    assert "/qn" in command and "/norestart" in command


def test_install_command_plan_darwin(monkeypatch):
    monkeypatch.setattr(install_node.os, "name", "posix")
    monkeypatch.setattr(install_node.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(install_node, "is_admin", lambda: False)
    command = install_node.install_command_plan(Path("/tmp/node-v22.21.1-osx-arm64.pkg"))
    assert command[0] == "sudo"  # 非 root 时走 sudo installer
    assert "installer" in command and "-target" in command
    monkeypatch.setattr(install_node, "is_admin", lambda: True)
    command = install_node.install_command_plan(Path("/tmp/node-v22.21.1-osx-arm64.pkg"))
    assert command[0] == "installer"  # root 时直接 installer


def test_ensure_node_dry_run_prints_plan_without_download(monkeypatch, capsys):
    # 缺 node + dry-run：只打印 URL 与安装命令，不下载不装（也不要求管理员）。
    monkeypatch.setattr(install_node, "find_existing_node", lambda: None)
    monkeypatch.setattr(install_node, "is_admin", lambda: False)

    install_node.ensure_node("v22.21.1", dry_run=True)
    out = capsys.readouterr().out
    assert "https://npmmirror.com/mirrors/node/v22.21.1/node-" in out
    assert "SHASUMS256.txt" in out
    assert "dry-run 结束" in out


def test_probe_node_version_rejects_broken_node(monkeypatch):
    # 探活失败的 node（坏 PATH 命中）不算可用：find_existing_node 靠它过滤。
    def fake_run(*args, **kwargs):
        raise OSError("bad exe")

    monkeypatch.setattr(install_node.subprocess, "run", fake_run)
    assert install_node.probe_node_version("C:/fake/node.exe") is None
