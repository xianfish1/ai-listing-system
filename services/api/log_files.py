"""启动器日志：边写边按大小轮转，避免 `api.out.log` / `api.err.log` 无限长大。

`run_api_silent.pyw` 是操作员双击 VBS 走的那条启动路径，它把 API 的 stdout/stderr
直接 append 到 `data/api-runtime/api.out.log` / `api.err.log`。界面每秒轮询
`/api/worker/status`、`/api/automation/jobs`，访问日志涨得很快：2026-10-13 现场实测
**49.6 MB**（12:36 重启、14:17 就已经这么大）。老实现只在启动那一刻开一次句柄，
中间从不看大小，所以文件只能一直长。

这里给启动器一个「写之前先看大小」的流：超过阈值就把当前文件挪成 `.1`，旧的
`.1 → .2`……最多留 `backups` 份，总占用因此有上界（默认 8 MB × (1 + 3)）。

Windows 细节：**正在写的文件不能直接改名**——句柄没带 `FILE_SHARE_DELETE`，
`os.replace` 会以 WinError 32 失败。所以轮转时先 flush + close，改名之后再以 append
打开。改名失败（比如有别的进程正拿着这个文件）也只当这次不轮转：写入照旧，绝不把
API 拖挂；下一次要等到又长了 `max_bytes` 才再试，避免每行都试一次。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULT_MAX_LOG_BYTES = 8 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 3

ENV_MAX_BYTES = "API_LOG_MAX_BYTES"
ENV_BACKUPS = "API_LOG_BACKUPS"


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """读一个整数环境变量；缺失或脏值一律退回默认值（启动器不该因此起不来）。"""

    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def max_log_bytes() -> int:
    """单个日志文件的轮转阈值（字节）。`API_LOG_MAX_BYTES` 可覆盖。"""

    return _env_int(ENV_MAX_BYTES, DEFAULT_MAX_LOG_BYTES, minimum=1)


def log_backups() -> int:
    """保留几份归档（`.1`、`.2`……）。`API_LOG_BACKUPS` 可覆盖，0 = 不留归档。"""

    return _env_int(ENV_BACKUPS, DEFAULT_LOG_BACKUPS, minimum=0)


def archived_path(path: Path, index: int) -> Path:
    """第 `index` 份归档：`api.out.log.1`、`api.out.log.2`……"""

    path = Path(path)
    return path.with_name(f"{path.name}.{index}")


def rotate_log(path: Path, *, max_bytes: int, backups: int) -> bool:
    """把超过阈值的日志挪成 `.1`，返回是否真的动过。

    只做文件改名，不碰任何打开的句柄——调用方（`RotatingLogStream`）自己负责先关后开。
    任何 `OSError` 都吞掉并返回 False：轮转是尽力而为的卫生工作，不该让启动或写入失败。
    """

    path = Path(path)
    threshold = max(1, int(max_bytes))
    try:
        if not path.is_file() or path.stat().st_size < threshold:
            return False
    except OSError:
        return False

    keep = max(0, int(backups))
    try:
        if keep <= 0:
            path.unlink()
            return True
        oldest = archived_path(path, keep)
        if oldest.exists():
            oldest.unlink()
        for index in range(keep - 1, 0, -1):
            source = archived_path(path, index)
            if source.exists():
                os.replace(source, archived_path(path, index + 1))
        os.replace(path, archived_path(path, 1))
        return True
    except OSError:
        return False


class RotatingLogStream:
    """append-only 文本流：每次写入前看大小，超阈值就轮转。

    行为上尽量像普通文件对象（`write` / `flush` / `close` / `writable` / `encoding` /
    `fileno` + 其余属性转发给底下的文件），这样它可以直接顶替 `sys.stdout` /
    `sys.stderr`——`print()`、`http.server` 的访问日志、traceback 都照旧能写。
    """

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int | None = None,
        backups: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max(1, int(max_log_bytes() if max_bytes is None else max_bytes))
        self.backups = max(0, int(log_backups() if backups is None else backups))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 上次留下的超大文件在开句柄之前先挪走，别接着往 49 MB 的尾巴上写。
        rotate_log(self.path, max_bytes=self.max_bytes, backups=self.backups)
        self._file = self._open()
        self._size = self._current_size()
        self._closed = False
        # 轮转失败后的静默期：等到又长了 max_bytes 再试，不要每行都去改名。
        self._next_rotate_at = 0

    # ---- 内部 ---------------------------------------------------------------

    def _open(self) -> Any:
        # newline="\n"：Windows 文本模式默认把 \n 翻成 \r\n（每行多 1 字节），
        # _size 的记账就会恒偏小，活文件悄悄越过 max_bytes 上界。
        return open(self.path, "a", encoding="utf-8", buffering=1, newline="\n")

    def _current_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def _rotate(self) -> bool:
        """关句柄 → 改名 → 重新 append 打开；返回改名是否成功。"""

        try:
            self._file.flush()
            self._file.close()
        except (OSError, ValueError):
            pass
        rotated = rotate_log(self.path, max_bytes=self.max_bytes, backups=self.backups)
        self._file = self._open()
        self._size = self._current_size()
        return rotated

    # ---- 文件对象接口 -------------------------------------------------------

    def write(self, data: Any) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed log stream")
        text = data if isinstance(data, str) else str(data)
        if not text:
            return 0
        pending = len(text.encode("utf-8", "replace"))
        if (
            self._size > 0
            and self._size + pending > self.max_bytes
            and self._size >= self._next_rotate_at
        ):
            if self._rotate():
                self._next_rotate_at = 0
            else:
                self._next_rotate_at = self._size + self.max_bytes
        self._file.write(text)
        self._size += pending
        if "\n" in text:
            self._file.flush()
        return len(text)

    def flush(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed log stream")
        self._file.flush()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._file.flush()
        finally:
            self._file.close()
            self._closed = True

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._file.fileno()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def encoding(self) -> str:
        return "utf-8"

    @property
    def errors(self) -> str:
        return "strict"

    @property
    def name(self) -> str:
        return str(self.path)

    @property
    def mode(self) -> str:
        return "a"

    @property
    def line_buffering(self) -> bool:
        return True

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        handle = self.__dict__.get("_file")
        if handle is None:
            raise AttributeError(item)
        return getattr(handle, item)


def open_rotating_log(
    path: Path,
    *,
    max_bytes: int | None = None,
    backups: int | None = None,
) -> RotatingLogStream:
    """给启动器用的入口：`data/api-runtime/xxx.log` 的有界 append 流。"""

    return RotatingLogStream(path, max_bytes=max_bytes, backups=backups)
