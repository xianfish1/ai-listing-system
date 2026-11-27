"""商品文件夹里操作员自己写好的标题文件（``标题.txt`` / ``title.txt``）。

导入商品时如果商品目录里有这个文件，就直接用文件内容当商品标题，不再让 AI
另写一个。约定：

- 只认放商品目录根下、名字恰好是 ``标题.txt`` 或 ``title.txt`` 的文件，
  大小写不敏感（``标题.TXT`` 也算）；其它 ``.txt`` 一律忽略，防止把备注、
  素材清单误当标题。
- 取文件里第一条非空行（多行只认第一行，避免把候选清单拼成超长标题）。
- 编码先按 UTF-8（带不带 BOM 都行）读，失败再按 Windows 记事本默认的
  GBK/GB18030 读。
"""
from __future__ import annotations

from pathlib import Path


TITLE_FILE_NAMES = ("标题.txt", "title.txt")
_TITLE_FILE_KEYS = {name.casefold() for name in TITLE_FILE_NAMES}


def is_title_file_name(value: str) -> bool:
    """文件名的最后一段是不是标题文件（大小写不敏感）。"""
    return Path(str(value or "")).name.strip().casefold() in _TITLE_FILE_KEYS


def find_title_file(root: str | Path) -> Path | None:
    folder = Path(str(root or ""))
    if not folder.is_dir():
        return None
    try:
        entries = sorted(folder.iterdir(), key=lambda item: item.name)
    except OSError:
        return None
    for entry in entries:
        try:
            if entry.is_file() and is_title_file_name(entry.name):
                return entry
        except OSError:
            continue
    return None


def read_title_file(root: str | Path) -> str:
    """返回商品目录里标题文件的第一条非空行；没有文件或读不到内容时返回 ``""``。"""
    path = find_title_file(root)
    if path is None:
        return ""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    for line in _decode(raw).splitlines():
        title = line.strip()
        if title:
            return title
    return ""


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
