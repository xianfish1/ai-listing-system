"""启动器日志轮转：`api.out.log` / `api.err.log` 必须有上界。

现场（2026-10-13）：API 的 stdout 直接 append 到 `data/api-runtime/api.out.log`，
界面每秒轮询 `/api/worker/status`、`/api/automation/jobs`，12:36 重启到 14:17 就
49.6 MB，而且启动器中间从不看大小。这里锁住三件事：

1. 超阈值才轮转，并按份数把旧的 `.1 → .2 …` 后移、最旧的丢掉；
2. 运行中边写边轮转，写入内容不丢；
3. 轮转失败（Windows 上句柄占着文件、改名报 WinError 32）不许影响写入。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.api import log_files, settings


def _oversized(path: Path, *, body: bytes = b"x" * 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _lines(count: int, *, prefix: str = "line") -> list[str]:
    """每行 20 字节（19 个字符 + 换行），方便按大小推算轮转时机。"""

    return [f"{prefix}{index:04d}".ljust(19) + "\n" for index in range(1, count + 1)]


def test_rotation_only_touches_oversized_files(tmp_path: Path) -> None:
    path = tmp_path / "api.out.log"
    _oversized(path, body=b"x" * 10)

    assert log_files.rotate_log(path, max_bytes=64, backups=2) is False
    assert path.read_bytes() == b"x" * 10
    assert not log_files.archived_path(path, 1).exists()


def test_rotation_shifts_archives_and_drops_the_oldest(tmp_path: Path) -> None:
    path = tmp_path / "api.out.log"
    _oversized(path, body=b"live")
    _oversized(log_files.archived_path(path, 1), body=b"one")
    _oversized(log_files.archived_path(path, 2), body=b"two")

    assert log_files.rotate_log(path, max_bytes=1, backups=2) is True

    assert not path.exists()
    assert log_files.archived_path(path, 1).read_bytes() == b"live"
    assert log_files.archived_path(path, 2).read_bytes() == b"one"
    # backups=2 ⇒ 原来的 .2（"two"）是第三份，被丢掉；不会出现 .3
    assert not log_files.archived_path(path, 3).exists()


def test_zero_backups_deletes_the_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "api.err.log"
    _oversized(path, body=b"y" * 32)

    assert log_files.rotate_log(path, max_bytes=8, backups=0) is True
    assert not path.exists()
    assert not log_files.archived_path(path, 1).exists()


def test_open_rotating_log_rotates_the_existing_oversized_file(tmp_path: Path) -> None:
    """上次留下的 49 MB 文件不该接着写：开句柄之前先挪走。"""

    path = tmp_path / "api.out.log"
    _oversized(path, body=b"old" * 20)

    stream = log_files.open_rotating_log(path, max_bytes=16, backups=1)
    try:
        assert stream.path.stat().st_size == 0
        assert log_files.archived_path(path, 1).read_bytes() == b"old" * 20
        stream.write("fresh\n")
        stream.flush()
        assert path.read_text(encoding="utf-8") == "fresh\n"
    finally:
        stream.close()


def test_stream_rotates_while_running_and_keeps_every_recent_line(tmp_path: Path) -> None:
    path = tmp_path / "api.out.log"
    stream = log_files.open_rotating_log(path, max_bytes=100, backups=2)
    try:
        for line in _lines(20, prefix="l"):
            stream.write(line)
        stream.flush()
    finally:
        stream.close()

    live = path.read_text(encoding="utf-8")
    first = log_files.archived_path(path, 1).read_text(encoding="utf-8")
    second = log_files.archived_path(path, 2).read_text(encoding="utf-8")

    assert path.stat().st_size <= 100, "活文件不能超过阈值"
    # 每 5 行一轮（5 × 20 字节 = 阈值）：活文件是最后 5 行，往前各归一份归档
    assert live == "".join(_lines(20, prefix="l")[15:])
    assert first == "".join(_lines(20, prefix="l")[10:15])
    assert second == "".join(_lines(20, prefix="l")[5:10])
    # 最老的 5 行在第三次轮转时被丢掉，但没有任何一行是被“写坏”的：
    # 按时间顺序拼回去 = 第二旧归档 → 第一旧归档 → 活文件。
    assert second + first + live == "".join(_lines(20, prefix="l")[5:])


def test_rename_failure_never_blocks_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows：句柄占着文件时改名会 WinError 32。写日志不能因此中断或丢内容。"""

    path = tmp_path / "api.out.log"

    def _boom(*args: object, **kwargs: object) -> None:
        raise PermissionError(32, "另一个程序正在使用此文件，进程无法访问。")

    monkeypatch.setattr(log_files.os, "replace", _boom)

    stream = log_files.open_rotating_log(path, max_bytes=40, backups=1)
    try:
        for line in _lines(6, prefix="k"):
            stream.write(line)
        stream.flush()
    finally:
        stream.close()

    content = path.read_text(encoding="utf-8")
    assert content == "".join(_lines(6, prefix="k")), "轮转失败也要把每行都写下去"
    assert path.stat().st_size > 40
    assert not log_files.archived_path(path, 1).exists()


def test_rename_failure_is_not_retried_on_every_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """改名失败后要隔一整段阈值再试，不能每行都去动文件系统。"""

    path = tmp_path / "api.out.log"
    attempts: list[int] = []

    def _boom(*args: object, **kwargs: object) -> None:
        attempts.append(1)
        raise PermissionError(32, "busy")

    monkeypatch.setattr(log_files.os, "replace", _boom)

    stream = log_files.open_rotating_log(path, max_bytes=40, backups=1)
    try:
        for line in _lines(10, prefix="r"):
            stream.write(line)
        stream.flush()
    finally:
        stream.close()

    # 10 行 = 200 字节 = 5 个阈值段：每段只试一次（实测 4 次），
    # 绝不是每行一次（那样会是 10 次）。
    assert 1 <= len(attempts) <= 5, f"改名尝试次数应当很少，实测 {len(attempts)}"


def test_stream_can_stand_in_for_sys_stdout(tmp_path: Path) -> None:
    path = tmp_path / "api.out.log"
    stream = log_files.open_rotating_log(path, max_bytes=1024, backups=1)
    try:
        print("127.0.0.1 - GET /api/health 200", file=stream)
        stream.flush()

        assert stream.writable() is True
        assert stream.readable() is False
        assert stream.seekable() is False
        assert stream.isatty() is False
        assert stream.encoding == "utf-8"
        assert stream.name == str(path)
        assert stream.closed is False
        assert isinstance(stream.fileno(), int)
        assert path.read_text(encoding="utf-8") == "127.0.0.1 - GET /api/health 200\n"

        # `print()` 之外，traceback / http.server 也会拿它当 stderr 用：属性要能转发下去
        assert stream.mode == "a"
        assert stream.line_buffering is True
        stream.close()
        assert stream.closed is True
        with pytest.raises(ValueError):
            stream.write("after close\n")
    finally:
        stream.close()


def test_threshold_and_archive_count_come_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(log_files.ENV_MAX_BYTES, "2048")
    monkeypatch.setenv(log_files.ENV_BACKUPS, "5")
    assert log_files.max_log_bytes() == 2048
    assert log_files.log_backups() == 5

    stream = log_files.open_rotating_log(tmp_path / "api.out.log")
    try:
        assert stream.max_bytes == 2048
        assert stream.backups == 5
    finally:
        stream.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", log_files.DEFAULT_MAX_LOG_BYTES),
        ("0", log_files.DEFAULT_MAX_LOG_BYTES),
        ("脏值", log_files.DEFAULT_MAX_LOG_BYTES),
    ],
)
def test_dirty_env_falls_back_to_the_default(
    value: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动器不该被一个手抖的环境变量搞得起不来。"""

    monkeypatch.setenv(log_files.ENV_MAX_BYTES, value)
    assert log_files.max_log_bytes() == expected


def test_launcher_writes_its_logs_through_the_rotating_stream() -> None:
    """`run_api_silent.pyw` 是操作员双击 VBS 走的那条路：它必须用轮转流。"""

    source = (settings.PROJECT_ROOT / "run_api_silent.pyw").read_text(encoding="utf-8")

    assert "open_rotating_log" in source
    assert "api.out.log" in source and "api.err.log" in source
    assert 'buffering=1' not in source, "旧的裸 append 句柄不该再出现"
