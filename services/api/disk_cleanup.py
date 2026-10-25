from __future__ import annotations

import datetime
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.api import asset_compaction
from services.api import settings


# 商品素材两个类目（见 asset_compaction 与 2026-10-19 设计规格）：
# - product_assets：已上架完成且超期的商品素材，自动+手动；
# - product_assets_legacy：没有任何基础商品归属的遗留素材（商品已删/重复导入残留），
#   **只允许手动**——不可逆且牵涉已删商品，绝不进每日自动集合。
CATEGORY_IDS = (
    "browser_cache",
    "temporary_files",
    "screenshots",
    "exports",
    "logs",
    asset_compaction.CATEGORY_ID,
    asset_compaction.LEGACY_CATEGORY_ID,
)
ASSET_CATEGORIES = (asset_compaction.CATEGORY_ID, asset_compaction.LEGACY_CATEGORY_ID)
AUTOMATIC_CATEGORIES = (
    "temporary_files",
    "screenshots",
    "exports",
    "logs",
    asset_compaction.CATEGORY_ID,
)
ACTIVE_JOB_STATUSES = {
    "ready", "claimed", "running", "needs_confirmation", "needs_manual", "needs_regeneration",
}
AUTO_INTERVAL_SECONDS = 24 * 60 * 60

_BROWSER_PROFILE_NAMES = ("browser-use-profile", "mabang-cdp-profile")
_BROWSER_ROOT_CACHE_DIRS = {
    "OptGuideOnDeviceModel",
    "OptGuideOnDeviceClassifierModel",
    "OnDeviceHeadSuggestModel",
    "optimization_guide_model_store",
    "component_crx_cache",
    "screen_ai",
    "WasmTtsEngine",
}
_BROWSER_NESTED_CACHE_DIRS = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GrShaderCache",
    "ShaderCache",
    "AutofillAiModelCache",
    "optimization_guide_hint_cache_store",
}
_CATEGORY_LABELS = {
    "browser_cache": "浏览器缓存",
    "temporary_files": "自动化临时文件",
    "screenshots": "截图",
    "exports": "导出文件",
    "logs": "日志",
    asset_compaction.CATEGORY_ID: "商品素材",
    asset_compaction.LEGACY_CATEGORY_ID: "已删商品遗留素材",
}
_PROTECTED_NOTES = {
    "browser_cache": "保留 Cookie、本地存储、会话、扩展和马帮登录状态；仅手动清理。",
    "temporary_files": "保留已完成商品素材和活动自动化任务的中间文件。",
    "screenshots": "仅清理超过保留期的截图。",
    "exports": "仅清理超过保留期的本地导出副本。",
    "logs": "仅清理超过保留期的历史日志。",
    asset_compaction.CATEGORY_ID: "只删已上架完成且超过素材保留期的本地素材；未完成、回滚过、有在途任务或素材在 NAS 的商品一律跳过。清理后该商品需要重新导入素材。",
    asset_compaction.LEGACY_CATEGORY_ID: "删的是商品已被删除后遗留的本地素材（仍被素材库或历史执行记录引用）；只在手动勾选时清理，不会自动执行。",
}
_CLEANUP_LOCK = threading.Lock()
# 压缩会连写几十个商品、单次好几分钟；手动按钮与每日自动任务必须互斥，
# 否则两轮同名映射可能交叉改写引用。
_COMPACTION_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()


class BrowserInUseError(RuntimeError):
    pass


class CleanupInProgressError(RuntimeError):
    pass


def active_automation_job_ids(data: dict[str, Any]) -> set[str]:
    return {
        str(job.get("id") or "")
        for job in data.get("automation_jobs") or []
        if str(job.get("status") or "") in ACTIVE_JOB_STATUSES and str(job.get("id") or "")
    }


def cdp_is_available(cdp_url: str) -> bool:
    base = str(cdp_url or "").strip().rstrip("/")
    if not base:
        return False
    try:
        with urlopen(Request(f"{base}/json/version", method="GET"), timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return isinstance(payload, dict) and bool(payload.get("Browser"))
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def scan_usage(
    config: dict[str, Any],
    *,
    active_job_ids: set[str],
    store: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    current = time.time() if now is None else float(now)
    categories: dict[str, dict[str, Any]] = {}
    for category in CATEGORY_IDS:
        if category in ASSET_CATEGORIES:
            total_files, candidates = _asset_category_files(
                category, config, store=store, active_job_ids=active_job_ids, now=current
            )[:2]
        else:
            total_files = list(_total_files(category, active_job_ids=active_job_ids))
            candidates = list(
                dict.fromkeys(_cleanup_files(category, config, active_job_ids=active_job_ids, now=current))
            )
        categories[category] = {
            "id": category,
            "label": _CATEGORY_LABELS[category],
            "total_bytes": sum(_file_size(path) for path in total_files),
            "reclaimable_bytes": sum(_file_size(path) for path in candidates),
            "file_count": len(total_files),
            "reclaimable_file_count": len(candidates),
            "automatic": category in AUTOMATIC_CATEGORIES,
            "protected_note": _PROTECTED_NOTES[category],
        }
    return {
        "total_bytes": sum(item["total_bytes"] for item in categories.values()),
        "reclaimable_bytes": sum(item["reclaimable_bytes"] for item in categories.values()),
        "categories": categories,
        "last_cleanup": read_cleanup_state(),
    }


def cleanup_categories(
    categories: Iterable[str],
    config: dict[str, Any],
    *,
    active_job_ids: set[str],
    cdp_available: Callable[[], bool],
    store: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    selected = list(dict.fromkeys(str(item or "").strip() for item in categories))
    unknown = [item for item in selected if item not in CATEGORY_IDS]
    if unknown:
        raise ValueError(f"未知清理类别：{unknown[0]}")
    if not selected:
        raise ValueError("至少选择一个清理类别")
    if "browser_cache" in selected and cdp_available():
        raise BrowserInUseError("自动化浏览器正在运行，请关闭后再清理浏览器缓存")
    if not _CLEANUP_LOCK.acquire(blocking=False):
        raise CleanupInProgressError("已有清理任务正在执行")

    started = time.time()
    current = started if now is None else float(now)
    try:
        details: dict[str, dict[str, Any]] = {}
        total_freed = 0
        total_deleted = 0
        all_skipped: list[str] = []
        all_errors: list[str] = []
        for category in selected:
            asset_dirs: list[Path] = []
            if category in ASSET_CATEGORIES:
                _total, files, asset_dirs = _asset_category_files(
                    category, config, store=store, active_job_ids=active_job_ids, now=current
                )
            else:
                files = list(
                    dict.fromkeys(_cleanup_files(category, config, active_job_ids=active_job_ids, now=current))
                )
            deleted_files = 0
            freed_bytes = 0
            skipped: list[str] = []
            errors: list[str] = []
            freed_by_dir: dict[str, int] = {}
            for path in files:
                size = _file_size(path)
                try:
                    if _is_link_path(path) or not _within_roots(path, _category_roots(category)):
                        skipped.append(str(path))
                        continue
                    path.unlink(missing_ok=True)
                    deleted_files += 1
                    freed_bytes += size
                    freed_by_dir[str(path.parent)] = freed_by_dir.get(str(path.parent), 0) + size
                    _prune_empty_parents(path.parent, _category_roots(category))
                except OSError as exc:
                    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {32, 33}:
                        skipped.append(str(path))
                    else:
                        errors.append(f"{path}: {exc}")
            cleaned_products: list[str] = []
            if category in ASSET_CATEGORIES and asset_dirs:
                # 目录真的被删空了才留痕，避免「部分失败却标成已清理」。
                # 遗留素材目录没有归属商品，不需要留痕。
                cleaned_products = _mark_cleaned_products(
                    category, asset_dirs, store=store, freed_by_dir=freed_by_dir, now=current
                )
            details[category] = {
                "label": _CATEGORY_LABELS[category],
                "freed_bytes": freed_bytes,
                "deleted_files": deleted_files,
                "skipped": skipped,
                "errors": errors,
            }
            if cleaned_products:
                details[category]["cleaned_products"] = cleaned_products
            total_freed += freed_bytes
            total_deleted += deleted_files
            all_skipped.extend(skipped)
            all_errors.extend(errors)
        # 清理后可能留下指向已删目录的资产记录（同组同名的旧会话记录先被解析命中）：
        # 能就地改指到现存同名文件的都改掉，避免在售商品解析到死链。
        repaired = 0
        if store is not None and any(category in ASSET_CATEGORIES for category in selected):
            try:
                from services.api import storage as storage_module

                updates = asset_compaction.plan_asset_pointer_repair(
                    store, storage_dir=settings.STORAGE_DIR
                )
                repaired = storage_module.apply_asset_pointer_repair(updates) if updates else 0
            except Exception:  # noqa: BLE001 - 修复失败不能影响清理结果
                repaired = 0
        return {
            "ok": not all_errors,
            "categories": details,
            "freed_bytes": total_freed,
            "deleted_files": total_deleted,
            "repaired_asset_pointers": repaired,
            "skipped": all_skipped,
            "errors": all_errors,
            "started_at": started,
            "finished_at": time.time(),
        }
    finally:
        _CLEANUP_LOCK.release()


def record_cleanup_result(
    result: dict[str, Any], *, automatic: bool = False, recorded_at: float | None = None,
) -> None:
    timestamp = float(recorded_at or result.get("finished_at") or time.time())
    with _STATE_LOCK:
        state = read_cleanup_state()
        state["last_cleanup_at"] = timestamp
        state["last_cleanup"] = {
            "automatic": bool(automatic),
            "ok": bool(result.get("ok")),
            "freed_bytes": int(result.get("freed_bytes") or 0),
            "deleted_files": int(result.get("deleted_files") or 0),
            "error_count": len(result.get("errors") or []),
        }
        if automatic:
            state["last_auto_cleanup_at"] = timestamp
        _write_cleanup_state(state)


def run_automatic_cleanup_if_due(
    config: dict[str, Any],
    *,
    active_job_ids: set[str],
    store: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    if config.get("enabled") is not True:
        return None
    current = time.time() if now is None else float(now)
    state = read_cleanup_state()
    last_run = float(state.get("last_auto_cleanup_at") or 0)
    if last_run and current - last_run < AUTO_INTERVAL_SECONDS:
        return None
    # 先压缩再清理：压缩是低风险（同名换扩展名 + 改写引用），清理是不可逆删除。
    # 压缩结果单独记账，即使它失败也不能挡住清理。
    compaction = run_asset_compaction(
        config, store=store, active_job_ids=active_job_ids, now=current
    )
    if compaction is not None:
        record_compaction_result(compaction, recorded_at=current)
    result = cleanup_categories(
        AUTOMATIC_CATEGORIES,
        config,
        active_job_ids=active_job_ids,
        cdp_available=lambda: False,
        store=store,
        now=current,
    )
    record_cleanup_result(result, automatic=True, recorded_at=current)
    return result


def read_cleanup_state() -> dict[str, Any]:
    path = settings.DATA_DIR / "storage_cleanup_state.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cleanup_state(value: dict[str, Any]) -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.DATA_DIR / "storage_cleanup_state.json"
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(target)


def _asset_dir_sets(
    category: str,
    config: dict[str, Any],
    *,
    store: dict[str, Any] | None,
    active_job_ids: set[str],
    now: float,
) -> tuple[list[Path], list[Path]]:
    """商品素材类目的 ``(全部目录, 可清理目录)``。

    没有 store 快照时返回空：宁可显示 0，也不在没有依据的情况下给出可删清单。
    """

    if store is None:
        return [], []
    storage_dir = settings.STORAGE_DIR
    retention = _retention_days(category, config)
    moment = datetime.datetime.fromtimestamp(float(now), tz=datetime.timezone.utc)
    if category == asset_compaction.CATEGORY_ID:
        all_dirs = [
            root
            for root in (
                asset_compaction.local_asset_root(base, storage_dir)
                for base in store.get("base_products") or []
            )
            if root is not None and root.is_dir()
        ]
        candidates, _legacy = asset_compaction.cleanup_candidate_dirs(
            store,
            storage_dir=storage_dir,
            retention_days=retention,
            active_job_ids=active_job_ids,
            now=moment,
        )
        return all_dirs, candidates
    all_dirs = asset_compaction.unowned_asset_dirs(
        store, storage_dir=storage_dir, older_than_days=0, now=moment
    )
    candidates = asset_compaction.unowned_asset_dirs(
        store, storage_dir=storage_dir, older_than_days=retention, now=moment
    )
    return all_dirs, candidates


def _asset_category_files(
    category: str,
    config: dict[str, Any],
    *,
    store: dict[str, Any] | None,
    active_job_ids: set[str],
    now: float,
) -> tuple[list[Path], list[Path], list[Path]]:
    all_dirs, candidate_dirs = _asset_dir_sets(
        category, config, store=store, active_job_ids=active_job_ids, now=now
    )
    total_files = [path for directory in all_dirs for path in _safe_files(directory)]
    candidate_files = [path for directory in candidate_dirs for path in _safe_files(directory)]
    return total_files, list(dict.fromkeys(candidate_files)), candidate_dirs


def _mark_cleaned_products(
    category: str,
    candidate_dirs: list[Path],
    *,
    store: dict[str, Any] | None,
    freed_by_dir: dict[str, int],
    now: float,
) -> list[str]:
    """给「素材目录真的被删空」的商品写清理留痕。

    只对 ``product_assets`` 生效（遗留素材目录没有归属商品）；目录没删空就不留痕，
    避免「部分失败却标成已清理」。
    """

    if category != asset_compaction.CATEGORY_ID or store is None:
        return []
    from services.api import storage as storage_module  # 延迟导入：避免与 storage 形成导入环

    candidate_keys = {
        os.path.normcase(os.path.normpath(str(item))) for item in candidate_dirs
    }
    stamp = datetime.datetime.fromtimestamp(float(now), tz=datetime.timezone.utc).isoformat()
    cleaned: list[str] = []
    for base in store.get("base_products") or []:
        root = asset_compaction.local_asset_root(base, settings.STORAGE_DIR)
        if root is None or root.exists():
            continue
        key = os.path.normcase(os.path.normpath(str(root)))
        if key not in candidate_keys:
            continue
        base_id = str(base.get("id") or "")
        if not base_id:
            continue
        deleted = sum(size for path, size in freed_by_dir.items() if path.lower().startswith(str(root).lower()))
        try:
            result = storage_module.mark_assets_cleaned(base_id, deleted, now=stamp)
        except Exception:  # noqa: BLE001 - 留痕失败不能影响清理结果
            result = None
        if result:
            cleaned.append(base_id)
    return cleaned


def run_asset_compaction(
    config: dict[str, Any],
    *,
    store: dict[str, Any] | None,
    active_job_ids: set[str],
    now: float,
) -> dict[str, Any] | None:
    """按配置压缩符合条件的本地素材（写文件 + 一次事务内改写引用）。"""

    compaction_settings = asset_compaction.CompactionSettings.from_config(config)
    if not compaction_settings.enabled:
        return {"ok": True, "skipped_reason": "disabled", "products": 0, "files": 0, "saved_bytes": 0, "errors": []}
    if store is None:
        return None
    if not _COMPACTION_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "busy": True,
            "skipped_reason": "busy",
            "products": 0,
            "files": 0,
            "saved_bytes": 0,
            "errors": [],
        }
    moment = datetime.datetime.fromtimestamp(float(now), tz=datetime.timezone.utc)
    try:
        return asset_compaction.run_compaction(
            settings=compaction_settings,
            storage_dir=settings.STORAGE_DIR,
            now=moment,
            store=store,
            active_job_ids=active_job_ids,
        )
    except Exception as exc:  # noqa: BLE001 - 压缩失败不能拖垮清理或 API
        return {
            "ok": False,
            "products": 0,
            "files": 0,
            "saved_bytes": 0,
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    finally:
        _COMPACTION_LOCK.release()


def record_compaction_result(result: dict[str, Any], *, recorded_at: float | None = None) -> None:
    timestamp = float(recorded_at or time.time())
    with _STATE_LOCK:
        state = read_cleanup_state()
        state["last_compaction_at"] = timestamp
        state["last_compaction"] = {
            "ok": bool(result.get("ok")),
            "products": int(result.get("products") or 0),
            "files": int(result.get("files") or 0),
            "saved_bytes": int(result.get("saved_bytes") or 0),
            "error_count": len(result.get("errors") or []),
            "skipped_reason": str(result.get("skipped_reason") or ""),
        }
        _write_cleanup_state(state)


_DEFAULT_RETENTION_DAYS = {
    "temporary_files": 7,
    "screenshots": 30,
    "exports": 90,
    "logs": 30,
    asset_compaction.CATEGORY_ID: 30,
    asset_compaction.LEGACY_CATEGORY_ID: 30,
}


def _retention_days(category: str, config: dict[str, Any]) -> int:
    field = {
        "temporary_files": "temp_retention_days",
        "screenshots": "screenshot_retention_days",
        "exports": "export_retention_days",
        "logs": "log_retention_days",
        asset_compaction.CATEGORY_ID: "assets_retention_days",
        asset_compaction.LEGACY_CATEGORY_ID: "assets_retention_days",
    }[category]
    # 配置缺键（老库 / 局部配置）时退回默认值：usage 接口不能因为这个 500。
    fallback = _DEFAULT_RETENTION_DAYS[category]
    try:
        value = int((config or {}).get(field, fallback))
    except (TypeError, ValueError):
        return fallback
    return max(1, value)
    return value


def _category_roots(category: str) -> tuple[Path, ...]:
    if category == "browser_cache":
        return tuple(settings.STORAGE_DIR / name for name in _BROWSER_PROFILE_NAMES)
    if category in ASSET_CATEGORIES:
        # 显式挂在 base-products 下：_prune_empty_parents 到这一层就停，不会删到 uploads/。
        return (settings.STORAGE_DIR / "uploads" / "base-products",)
    if category == "temporary_files":
        return (
            settings.STORAGE_DIR / "tmp",
            settings.PROJECT_ROOT / "tmp_upload",
            settings.STORAGE_DIR / "uploads" / "base-products",
        )
    if category == "screenshots":
        return (settings.SCREENSHOT_DIR,)
    if category == "exports":
        return (settings.EXPORT_DIR,)
    return (settings.LOG_DIR,)


def _total_files(category: str, *, active_job_ids: set[str]) -> Iterable[Path]:
    if category == "temporary_files":
        yield from _safe_files(settings.STORAGE_DIR / "tmp")
        yield from _safe_files(settings.PROJECT_ROOT / "tmp_upload")
        upload_root = settings.STORAGE_DIR / "uploads" / "base-products"
        for session in _upload_sessions(upload_root):
            yield from _safe_files(session)
        return
    for root in _category_roots(category):
        yield from _safe_files(root)


def _cleanup_files(
    category: str,
    config: dict[str, Any],
    *,
    active_job_ids: set[str],
    now: float,
) -> Iterable[Path]:
    if category == "browser_cache":
        yield from _browser_cache_files()
        return
    cutoff = now - _retention_days(category, config) * 86400
    if category == "temporary_files":
        yield from _older_files(settings.STORAGE_DIR / "tmp", cutoff)
        automation_root = settings.PROJECT_ROOT / "tmp_upload" / "automation-jobs"
        for child in _safe_children(automation_root):
            if child.is_dir() and child.name in active_job_ids:
                continue
            yield from _older_files(child, cutoff)
        for child in _safe_children(settings.PROJECT_ROOT / "tmp_upload"):
            if child == automation_root:
                continue
            yield from _older_files(child, cutoff)
        upload_root = settings.STORAGE_DIR / "uploads" / "base-products"
        for session in _upload_sessions(upload_root):
            marker = session / "session.json"
            if _mtime(marker) < cutoff:
                yield from _safe_files(session)
        return
    for root in _category_roots(category):
        yield from _older_files(root, cutoff)


def _browser_cache_files() -> Iterable[Path]:
    for profile_root in _category_roots("browser_cache"):
        if not profile_root.is_dir() or _is_link_path(profile_root):
            continue
        for child in _safe_children(profile_root):
            if child.is_dir() and child.name in _BROWSER_ROOT_CACHE_DIRS:
                yield from _safe_files(child)
        for current, dirs, _files in os.walk(profile_root, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = [name for name in dirs if not _is_link_path(current_path / name)]
            for name in list(dirs):
                if name in _BROWSER_NESTED_CACHE_DIRS and _known_browser_cache_parent(profile_root, current_path):
                    target = current_path / name
                    yield from _safe_files(target)
                    dirs.remove(name)


def _upload_sessions(root: Path) -> Iterable[Path]:
    for child in _safe_children(root):
        if child.is_dir() and not _is_link_path(child) and (child / "session.json").is_file():
            yield child


def _older_files(root: Path, cutoff: float) -> Iterable[Path]:
    for path in _safe_files(root):
        if _mtime(path) < cutoff:
            yield path


def _safe_children(root: Path) -> list[Path]:
    if not root.is_dir() or _is_link_path(root):
        return []
    try:
        return list(root.iterdir())
    except OSError:
        return []


def _safe_files(root: Path) -> Iterable[Path]:
    if not root.exists() or _is_link_path(root):
        return
    if root.is_file():
        if _within_allowed_root(root):
            yield root
        return
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if not _is_link_path(current_path / name)]
        for name in files:
            path = current_path / name
            if not _is_link_path(path) and _within_allowed_root(path):
                yield path


# 允许删除的两棵根（storage 与 tmp_upload）的规范形式缓存。
# 原来每个文件都算一次 Path.resolve()（Windows 上要逐级走父目录，实测约 0.2ms/文件）：
# 3500 个文件的浏览器 Profile 遍历光这一项就要 ~750ms，而 storage/usage 接口要遍历两遍。
# os.walk 给出的路径一定绝对且不含 ".."，符号链接在遍历时已经跳过，所以用
# normcase + normpath 做前缀比较既安全又快（同一份遍历 493ms → 55ms）。
_ALLOWED_ROOT_CACHE: dict[tuple[str, str], tuple[str, ...]] = {}


def _allowed_roots_normalized() -> tuple[str, ...]:
    key = (str(settings.STORAGE_DIR), str(settings.PROJECT_ROOT))
    cached = _ALLOWED_ROOT_CACHE.get(key)
    if cached is None:
        cached = (
            os.path.normcase(os.path.normpath(str(settings.STORAGE_DIR))),
            os.path.normcase(os.path.normpath(str(settings.PROJECT_ROOT / "tmp_upload"))),
        )
        _ALLOWED_ROOT_CACHE[key] = cached
    return cached


def _within_allowed_root(path: Path) -> bool:
    candidate = os.path.normcase(os.path.normpath(str(path)))
    for root in _allowed_roots_normalized():
        if candidate == root or candidate.startswith(root + os.sep):
            return True
    return False


def _within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    try:
        candidate = path.resolve(strict=False)
        resolved_roots = tuple(root.resolve(strict=False) for root in roots)
    except OSError:
        return False
    return any(candidate == root or root in candidate.parents for root in resolved_roots)


def _known_browser_cache_parent(profile_root: Path, parent: Path) -> bool:
    if parent == profile_root:
        return True
    try:
        relative = parent.relative_to(profile_root)
    except ValueError:
        return False
    return len(relative.parts) == 1 and (
        relative.name == "Default"
        or relative.name in {"Guest Profile", "System Profile"}
        or relative.name.startswith("Profile ")
    )


def _prune_empty_parents(directory: Path, roots: tuple[Path, ...]) -> None:
    resolved_roots = {root.resolve(strict=False) for root in roots}
    current = directory
    while current.resolve(strict=False) not in resolved_roots:
        if not _within_allowed_root(current) or _is_link_path(current):
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _is_link_path(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return float("inf")
