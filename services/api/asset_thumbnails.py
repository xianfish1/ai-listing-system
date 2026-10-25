"""资产缩略图：按需生成、磁盘缓存、母版派生、后台预热。

现场素材库有 2600 多张原图、合计 3.5GB（单个最大 16MB、5120x5120），而列表/卡片里
显示的是 46~480px 的小图。浏览器直接加载原图时，一屏十几张就是几十上百 MB。

这里按长边把原图缩成 JPEG 缓存到 storage/thumbnails/ 下。为了不再为**每一个**尺寸
都重新解码原图（实测 5120x5120 的 PNG 解码要 ~370ms，占整个生成耗时的 90%），
所有小于 MASTER_EDGE 的尺寸都从一张母版派生：母版只解码一次，派生一张只要 12~20ms。
再配合后台预热（列表页请求素材时顺手把母版排队生成），第一次点开列表就不用等。
"""

from __future__ import annotations

import atexit
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from . import settings

try:  # Pillow 是既有依赖（vision_listing 也用它）
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - 依赖缺失时优雅退化
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

DEFAULT_THUMB_EDGE = 480
MIN_THUMB_EDGE = 64
MAX_THUMB_EDGE = 1024
# 母版边长：小于它的尺寸都从母版派生，不再重新解码原图。
MASTER_EDGE = 900
JPEG_QUALITY = 82
# 大图解码很吃内存（5120x5120 解出来是几百 MB），并发解码数量必须封顶。
# Pillow 解码时会释放 GIL，机器核多时适度并发能明显缩短一屏图片的等待。
MAX_CONCURRENT_GENERATIONS = 3
# 后台预热：一个进程最多预生成多少张母版（磁盘约 150KB/张），以及单次扫描排多少。
WARM_PROCESS_LIMIT = 600
WARM_BATCH_LIMIT = 120
WARM_WORKERS = 1

_SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_SAFE_KEY = re.compile(r"[^0-9A-Za-z._-]+")

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_GENERATIONS)

_WARM_GUARD = threading.Lock()
_WARM_SEEN: set[str] = set()
_WARM_SCAN_RUNNING = False
_WARM_EXECUTOR = ThreadPoolExecutor(max_workers=WARM_WORKERS, thread_name_prefix="thumb-warm")
atexit.register(lambda: _WARM_EXECUTOR.shutdown(wait=False, cancel_futures=True))


def thumbnails_dir() -> Path:
    return settings.STORAGE_DIR / "thumbnails"


def normalize_edge(value: Any) -> int:
    try:
        edge = int(value)
    except (TypeError, ValueError):
        edge = DEFAULT_THUMB_EDGE
    return max(MIN_THUMB_EDGE, min(MAX_THUMB_EDGE, edge))


def is_thumbnailable(path: Path) -> bool:
    return path.suffix.lower() in _SUPPORTED_SUFFIXES


def _safe_key(key: str, source: Path) -> str:
    raw = str(key or "").strip() or source.stem
    cleaned = _SAFE_KEY.sub("_", raw)[:120].strip("._-")
    return cleaned or "asset"


def _target_path(source: Path, edge: int, key: str) -> Path | None:
    try:
        stat = source.stat()
    except OSError:
        return None
    stamp = f"{int(stat.st_mtime_ns):x}-{int(stat.st_size):x}"
    return thumbnails_dir() / f"{_safe_key(key, source)}-{edge}-{stamp}.jpg"


def _key_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _atomic_write(image: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    image.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    os.replace(tmp, target)


def _prune_stale(target: Path, key: str, edge: int) -> None:
    """同一个资产同一个尺寸只留最新的一份，别让旧缩略图一直堆着。"""

    keep = target.name
    prefix = f"{key}-{edge}-"
    try:
        for candidate in target.parent.glob(f"{prefix}*.jpg"):
            if candidate.name != keep:
                try:
                    candidate.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _cached(target: Path | None) -> bool:
    try:
        return target is not None and target.is_file() and target.stat().st_size > 0
    except OSError:
        return False


def _generate_from_source(source: Path, edge: int, key: str) -> Path | None:
    """直接从原图解码生成（母版与大于母版的尺寸走这里）。"""

    target = _target_path(source, edge, key)
    if target is None:
        return None
    with _key_lock(f"{_safe_key(key, source)}-{edge}"):
        if _cached(target):
            return target
        acquired = _SLOTS.acquire(timeout=120)
        if not acquired:
            return None
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                # JPEG 可以在解码阶段就按 1/2、1/4、1/8 抽样，省掉大图的全量解码。
                try:
                    image.draft("RGB", (edge * 2, edge * 2))
                except Exception:
                    pass
                image.thumbnail((edge, edge))
                _atomic_write(image, target)
        except Exception:
            return None
        finally:
            _SLOTS.release()
    _prune_stale(target, _safe_key(key, source), edge)
    return target


def _derive_from_master(master: Path, source: Path, edge: int, key: str) -> Path | None:
    """从母版派生小图：只解码一张 ~150KB 的 JPEG，实测 12~20ms。"""

    target = _target_path(source, edge, key)
    if target is None:
        return None
    with _key_lock(f"{_safe_key(key, source)}-{edge}"):
        if _cached(target):
            return target
        try:
            with Image.open(master) as opened:
                opened.load()
                image = opened if opened.mode in {"RGB", "L"} else opened.convert("RGB")
                image.thumbnail((edge, edge))
                _atomic_write(image, target)
        except Exception:
            return None
    _prune_stale(target, _safe_key(key, source), edge)
    return target


def ensure_thumbnail(source: Path, edge: int, *, key: str = "") -> Path | None:
    """返回缩略图路径（磁盘缓存）；不支持或失败返回 None。"""

    if Image is None or ImageOps is None or not is_thumbnailable(source):
        return None
    target = _target_path(source, edge, key)
    if target is None:
        return None
    if _cached(target):
        return target
    if edge < MASTER_EDGE:
        master = ensure_thumbnail(source, MASTER_EDGE, key=key)
        if master is not None:
            derived = _derive_from_master(master, source, edge, key)
            if derived is not None:
                return derived
        # 母版不可用（坏图/写盘失败）：退回直接从原图生成。
    return _generate_from_source(source, edge, key)


def _identity(asset: dict[str, Any]) -> str:
    """不 stat 的稳定标识：asset id + 版本标记（file_hash/updated_at/size_bytes）。"""

    asset_id = str(asset.get("id") or "").strip()
    stamp = str(asset.get("file_hash") or asset.get("updated_at") or asset.get("size_bytes") or "")
    return f"{asset_id}:{stamp}"


def _is_local_path(path: Path) -> bool:
    raw = str(path)
    return len(raw) > 1 and raw[1] == ":"


def schedule_warm_masters(assets: Iterable[dict[str, Any]]) -> int:
    """把一批资产的母版生成排进后台队列；不阻塞调用方。

    返回 1 表示本次真的排了一次扫描，0 表示已有扫描在跑 / 达到进程上限 / 依赖不可用。

    - 单飞：同一时刻只跑一个扫描任务，避免每个页面请求都排一遍。
    - 去重：同一个 asset 版本只排一次（进程级）。
    - 限额：一个进程最多预生成 WARM_PROCESS_LIMIT 张，单批 WARM_BATCH_LIMIT 张。
    - 本地盘优先：网络盘（\\\\NAS\\...）每张都要走 SMB 读原图，排在后面。
    """

    global _WARM_SCAN_RUNNING
    if Image is None:
        return 0
    with _WARM_GUARD:
        if _WARM_SCAN_RUNNING or len(_WARM_SEEN) >= WARM_PROCESS_LIMIT:
            return 0
        _WARM_SCAN_RUNNING = True
    try:
        _WARM_EXECUTOR.submit(_warm_scan, list(assets))
    except Exception:  # pragma: no cover - 线程池关闭等
        with _WARM_GUARD:
            _WARM_SCAN_RUNNING = False
        return 0
    return 1


def _warm_scan(assets: list[dict[str, Any]]) -> None:
    global _WARM_SCAN_RUNNING
    try:
        entries: list[tuple[Path, str, str]] = []
        # 顺序由调用方决定（列表真正引用的主图在前）——这里不再自行倒序。
        for asset in assets:
            if len(_WARM_SEEN) + len(entries) >= WARM_PROCESS_LIMIT:
                break
            if not isinstance(asset, dict):
                continue
            path = Path(str(asset.get("file_path") or ""))
            asset_id = str(asset.get("id") or "").strip()
            if not asset_id or not is_thumbnailable(path):
                continue
            ident = _identity(asset)
            with _WARM_GUARD:
                if ident in _WARM_SEEN:
                    continue
                _WARM_SEEN.add(ident)
            entries.append((path, asset_id, ident))
            if len(entries) >= WARM_BATCH_LIMIT:
                break
        entries.sort(key=lambda item: 0 if _is_local_path(item[0]) else 1)
        for path, asset_id, _ident in entries:
            _WARM_EXECUTOR.submit(_warm_one, path, asset_id)
    finally:
        with _WARM_GUARD:
            _WARM_SCAN_RUNNING = False


def _warm_one(source: Path, key: str) -> None:
    try:
        ensure_thumbnail(source, MASTER_EDGE, key=key)
    except Exception:
        pass


def warm_progress() -> dict[str, int]:
    """预热进度（测试与排查用）。"""

    with _WARM_GUARD:
        return {"queued": len(_WARM_SEEN), "limit": WARM_PROCESS_LIMIT, "running": int(_WARM_SCAN_RUNNING)}
