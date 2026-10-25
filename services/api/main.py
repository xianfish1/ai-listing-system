from __future__ import annotations

import io
import json
import gzip
import mimetypes
import os
import re
import zipfile
import subprocess
import sys
import threading
import time
import traceback
import copy
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from services.api import asset_thumbnails, disk_cleanup, github_updates, network_access, project_plan, settings, storage
from services.api.runtime_configuration import (
    worker_idle_shutdown_seconds,
    worker_slots_per_account,
    worker_start_on_demand,
)
from services.api.cnsc_price_matrix import parse_price_matrix
from services.api.operations_dashboard import build_dashboard
from services.api.category_profile_service import CategoryProfileConflict, CategoryProfileNotFound
from services.api.mabang_export import (
    build_mabang_preview_payload,
    collect_mabang_bundles,
    export_mabang_products,
    _resolve_template_path,
)
from services.api.marketplace_category_catalog import (
    MarketplaceCategoryCatalogConflict,
    MarketplaceCategoryCatalogNotFound,
)
from services.api.mabang_page_import import MabangPageImportError
from services.api.mabang_browser import (
    MabangBrowserError,
    find_bundled_chrome,
    launch_mabang_browser,
    stop_mabang_browser,
)
from services.api.mabang_auto_login import run_mabang_auto_login
from services.api.size_chart_uploads import (
    SizeChartUploadError,
    resolve_image_upload,
    resolve_size_chart,
    save_image_upload,
    save_size_chart,
)
from services.api.anthropic_messages_adapter import AnthropicMessagesAdapter
from services.api.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter
from services.api.credential_crypto import CredentialCrypto
from services.api.description_generation import DescriptionGenerationService
from services.api.model_gateway import GenerationRequest, ModelGateway
from services.api.store_collection_service import run_store_collection
from services.api.worker_supervisor import MultiWorkerSupervisor, WorkerSupervisor, normalize_worker_target
from scripts.browser_use_runner import collect_mabang_page_import_via_playwright as run_mabang_page_import_collect
from scripts.browser_use_runner import collect_store_watermarks_via_playwright as run_store_watermark_collect
from workers.asset_scanner.scanner import scan_asset_root
from workers.executor.deepseek_adapter import DeepSeekAdapter
from services.api.local_folder_uploads import LocalFolderUploadError, LocalFolderUploads
from services.api.cnsc_price_matrix import parse_price_matrix


MAX_JSON_BODY_BYTES = 1_048_576
MAX_CATEGORY_CATALOG_BODY_BYTES = 10_485_760
MAX_VIDEO_BODY_BYTES = 42_000_000
# 图片上传走 base64 JSON（膨胀 4/3），商品图单张上限 10 MiB → 预留到 16 MiB。
# 用默认的 1 MiB JSON 上限会让 1 MB 以上的图直接 413，浏览器只报 Failed to fetch。
MAX_IMAGE_UPLOAD_BODY_BYTES = 16 * 1024 * 1024
MAX_LOCAL_UPLOAD_FILE_BYTES = 512 * 1024 * 1024
_WORKER_SUPERVISOR: WorkerSupervisor | None = None
_WORKER_RUNTIME_DIR: Path | None = None
# 并行部署：每个马帮账号一个 Worker 进程，各自独立的 runtime 目录/锁/日志。
_MULTI_WORKER_SUPERVISOR: MultiWorkerSupervisor | None = None
_MULTI_WORKER_RUNTIME_DIR: Path | None = None

# NAS folder browser cache. Listing a directory on a UNC share is slow because
# every child needs an extra scan to learn `has_subdirs`; operators browse back
# and forth while queueing product folders, so cache each directory's listing
# briefly instead of re-scanning the network on every click. Entries are keyed
# by "{root}|{path}" and dropped when the configured root changes.
#
# The cache is also persisted to data/nas_browse_cache.json so an API restart
# (or next-day start) still serves recently scanned directories instantly
# instead of re-walking the network share. In-memory entries stay fresh for
# `_NAS_BROWSE_CACHE_FRESH_SECONDS`; a persisted entry is only trusted to warm the
# memory cache if it is younger than `_NAS_BROWSE_CACHE_DISK_TTL_SECONDS` (the
# NAS tree changes often enough that older listings would be misleading).
#
# Serving is stale-while-revalidate: any cached entry younger than the disk TTL
# is returned immediately (`from_cache=True`), and when it is older than the
# fresh threshold the responder re-scans it in the background so the operator is
# never blocked on a slow UNC share for a second visit. Only the explicit
# refresh button (`refresh=1`) forces a synchronous re-scan of the real disk.
_NAS_BROWSE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_NAS_BROWSE_CACHE_LOCK = threading.Lock()
# Serializes disk writes (see _nas_browse_cache_save): request threads and
# background workers all persist, and concurrent atomic replaces collide on
# Windows when two threads replace the same destination at once.
_NAS_BROWSE_CACHE_WRITE_LOCK = threading.Lock()
_NAS_BROWSE_CACHE_FRESH_SECONDS = 300.0
_NAS_BROWSE_CACHE_DISK_TTL_SECONDS = 7 * 24 * 60 * 60
_NAS_BROWSE_CACHE_MAX_ENTRIES = 512
_NAS_BROWSE_CACHE_FILE_NAME = "nas_browse_cache.json"
_NAS_BROWSE_CACHE_DISK_WARMED = False
# Full (leaf-probing) listings launched in the background after a shallow open.
# Keyed by directory path so a series folder is only re-probed once at a time.
# The same set also deduplicates background re-scans of stale cache entries
# (keys prefixed `rescan|`) — no two background workers may ever scan the same
# directory at the same time. Child prefetches keep their own set below.
_NAS_BROWSE_PROBE_INFLIGHT: set[str] = set()
_NAS_BROWSE_PROBE_LOCK = threading.Lock()
_NAS_BROWSE_PREFETCH_INFLIGHT: set[str] = set()
# Background re-scans of stale cached directories (stale-while-revalidate) and
# one-level prefetches of the current directory's children. Separate executors
# from the NAS import pool so prefetching never competes with an active import.
_NAS_BROWSE_RESCAN_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nas-browse-rescan")
_NAS_BROWSE_PREFETCH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nas-browse-prefetch")
# Cap how many children a directory prefetches in one go, and how many levels
# deep the background warm-up drills before giving up on the first visit.
_NAS_BROWSE_PREFETCH_MAX_CHILDREN = 200

# NAS imports are deliberately asynchronous. Scanning a UNC folder and the
# optional vision enrichment can each take seconds, so the request only creates
# a task and the bounded executor does the expensive work in the background.
_NAS_IMPORT_TASKS: dict[str, dict[str, Any]] = {}
_NAS_IMPORT_TASKS_LOCK = threading.Lock()
_NAS_IMPORT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nas-import")


def _nas_import_task_snapshot(task_id: str) -> dict[str, Any] | None:
    with _NAS_IMPORT_TASKS_LOCK:
        task = _NAS_IMPORT_TASKS.get(task_id)
        return copy.deepcopy(task) if task is not None else None


def _update_nas_import_task(task_id: str, **changes: Any) -> None:
    with _NAS_IMPORT_TASKS_LOCK:
        task = _NAS_IMPORT_TASKS.get(task_id)
        if task is not None:
            item_update = changes.pop("item_update", None)
            if isinstance(item_update, dict):
                index = item_update.get("index")
                if isinstance(index, int) and 0 <= index < len(task.get("items") or []):
                    task["items"][index].update(
                        {key: value for key, value in item_update.items() if key != "index"}
                    )
            task.update(changes)


def _run_nas_import_item(task_id: str, index: int, path: str, payload: dict[str, Any]) -> None:
    try:
        _update_nas_import_task(
            task_id,
            item_update={"index": index, "status": "running", "path": path, "stage": "scanning"},
        )
        item_payload = {**payload, "root_path": path}
        product = storage.create_base_product_from_folder(item_payload)
        if not product:
            raise ValueError("No assets found for folder")
        _update_nas_import_task(
            task_id,
            item_update={"index": index, "status": "running", "path": path, "stage": "ai"},
        )
        product = _enrich_with_vision_listing(product, item_payload)
        ai_error = str(product.get("ai_generation_error") or "").strip()
        with _NAS_IMPORT_TASKS_LOCK:
            task = _NAS_IMPORT_TASKS.get(task_id)
            if task is None:
                return
            item = task["items"][index]
            item.update({
                "status": "succeeded",
                "stage": "done",
                "product": product,
                "ai_error": ai_error,
            })
            task["completed"] += 1
            task["success"] += 1
    except Exception as exc:  # noqa: BLE001 - one bad folder must not cancel a batch
        with _NAS_IMPORT_TASKS_LOCK:
            task = _NAS_IMPORT_TASKS.get(task_id)
            if task is None:
                return
            task["items"][index].update({"status": "failed", "stage": "failed", "error": str(exc)})
            task["completed"] += 1
            task["failed"] += 1


def _run_nas_import_task(task_id: str, payload: dict[str, Any]) -> None:
    _update_nas_import_task(task_id, status="running")
    task = _nas_import_task_snapshot(task_id)
    if task is None:
        return
    futures = [
        _NAS_IMPORT_EXECUTOR.submit(_run_nas_import_item, task_id, index, item["path"], payload)
        for index, item in enumerate(task["items"])
    ]
    for future in as_completed(futures):
        try:
            future.result()
        except Exception:
            # _run_nas_import_item records per-item failures; this is only a
            # defensive guard for executor-level errors.
            pass
    with _NAS_IMPORT_TASKS_LOCK:
        task = _NAS_IMPORT_TASKS.get(task_id)
        if task is not None:
            task["status"] = "completed"


def _create_nas_import_task(payload: dict[str, Any]) -> dict[str, Any]:
    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, list):
        raise ApiRequestError("paths must be a list", status=422)
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths[:100]:
        path = str(raw_path or "").strip()
        key = os.path.normcase(os.path.normpath(path))
        if path and key not in seen:
            seen.add(key)
            paths.append(path)
    if not paths:
        raise ApiRequestError("至少选择一个 NAS 商品文件夹", status=422)
    task_id = str(uuid.uuid4())
    task = {
        "id": task_id,
        "status": "queued",
        "total": len(paths),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "items": [{"path": path, "status": "queued", "stage": "queued"} for path in paths],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _NAS_IMPORT_TASKS_LOCK:
        _NAS_IMPORT_TASKS[task_id] = task
    worker_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"paths", "root_path"}
    }
    threading.Thread(
        target=_run_nas_import_task,
        args=(task_id, worker_payload),
        name=f"nas-import-task-{task_id[:8]}",
        daemon=True,
    ).start()
    return copy.deepcopy(task)


def _nas_browse_cache_path() -> Path:
    return settings.DATA_DIR / _NAS_BROWSE_CACHE_FILE_NAME


def _nas_browse_cache_load(root: str, now: float) -> None:
    """Warm the in-memory cache from disk after a restart.

    Only entries younger than the disk TTL and matching the current NAS root are
    loaded; anything else is ignored so a stale listing can never be served.
    """
    path = _nas_browse_cache_path()
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return
    if str(raw.get("root") or "") != str(root):
        return  # configured NAS root changed — the old listing is meaningless
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return
    loaded = 0
    with _NAS_BROWSE_CACHE_LOCK:
        for cache_key, record in entries.items():
            if not isinstance(record, dict):
                continue
            # Key format is "{root}|s|{path}" / "{root}|f|{path}". Older entries
            # without the s/f flag (pre-shallow builds) never match a request, so
            # drop them instead of warming useless rows.
            if "|s|" not in cache_key and "|f|" not in cache_key:
                continue
            try:
                cached_at = float(record.get("cached_at") or 0)
            except (TypeError, ValueError):
                continue
            if now - cached_at > _NAS_BROWSE_CACHE_DISK_TTL_SECONDS:
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
                continue
            _NAS_BROWSE_CACHE[str(cache_key)] = (cached_at, payload)
            loaded += 1
    if loaded:
        print(f"NAS browse cache: warmed {loaded} directories from disk")


def _nas_browse_cache_save(root: str, now: float) -> None:
    """Persist the in-memory cache to disk (best-effort, atomic replace).

    Called from request threads and background workers (probe / rescan /
    prefetch / warm-up) that can overlap, so writes are serialized with a
    dedicated lock — concurrent atomic replaces on Windows collide otherwise.
    """
    try:
        path = _nas_browse_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _NAS_BROWSE_CACHE_LOCK:
            records = {
                cache_key: {"cached_at": cached_at, "payload": payload}
                for cache_key, (cached_at, payload) in _NAS_BROWSE_CACHE.items()
            }
        payload = {"version": 1, "root": str(root), "saved_at": now, "entries": records}
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with _NAS_BROWSE_CACHE_WRITE_LOCK:
            os.replace(tmp, path)
        # Clean up tmp files left by a previous crashed write.
        try:
            for leftover in path.parent.glob(f"{path.name}.*.tmp"):
                leftover.unlink(missing_ok=True)
        except OSError:
            pass
    except OSError:
        # A cache file is an optimization — never let persistence break
        # browsing. The main file is left alone: on a concurrent-write failure
        # it may hold a just-written valid snapshot from another thread.
        pass


def _nas_browse_cache_invalidate_root(root: str) -> None:
    """Drop entries whose cached root differs from the configured NAS root."""
    prefix = f"{root}|"
    with _NAS_BROWSE_CACHE_LOCK:
        stale = [key for key in _NAS_BROWSE_CACHE if not key.startswith(prefix)]
        for key in stale:
            _NAS_BROWSE_CACHE.pop(key, None)
    # Also clear the disk file when the root changed so a future restart cannot
    # warm from a different root's listing.
    try:
        path = _nas_browse_cache_path()
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and str(raw.get("root") or "") != str(root):
                path.unlink(missing_ok=True)
    except (OSError, json.JSONDecodeError):
        pass


def _nas_browse_resolve_root() -> Path | None:
    """Resolve the configured NAS root exactly the way browse requests do.

    Every cache key is prefixed with `str(root_path)`, so request threads and
    background writers (probe / rescan / prefetch / warm) must agree on this
    spelling or cache hits would silently miss. Returns None when the root is
    unusable (e.g. an invalid path) so callers can skip gracefully."""
    try:
        runtime = storage.get_runtime_configuration()
        raw_root = str((runtime.get("nas") or {}).get("asset_root") or "").strip()
        root_str = raw_root or str(settings.NAS_ASSET_ROOT)
        return Path(root_str).resolve(strict=False)
    except OSError:
        return None


def _scan_nas_directory(target: Path, *, shallow: bool) -> list[dict[str, Any]]:
    """List the direct child directories of `target` as browse entries.

    Shared by the request path and the background leaf-probe thread. When
    `shallow` is True, entries carry `has_subdirs: None` (unknown) and no extra
    scan is performed per child — this is what makes opening a big directory fast.
    When False, each child is probed with one nested scandir so the UI knows which
    rows are leaf product folders. Raises ApiRequestError on permission/IO errors.
    """
    entries: list[dict[str, Any]] = []
    try:
        with os.scandir(target) as scanner:
            for entry in scanner:
                if entry.name.startswith("."):
                    continue  # hide dotfiles / dot-directories
                try:
                    if entry.is_dir(follow_symlinks=False):
                        # mtime lets the UI put recently generated product folders
                        # on top; scandir already carries stat data on Windows so
                        # this costs no extra network round-trip on a UNC share.
                        try:
                            mtime = entry.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            mtime = 0.0
                        if shallow:
                            entries.append(
                                {"name": entry.name, "is_dir": True, "has_subdirs": None, "mtime": mtime}
                            )
                            continue
                        # `has_subdirs` lets the UI treat a directory without
                        # child directories as a product-folder leaf that can be
                        # selected directly (no need to drill into an empty page).
                        has_subdirs = False
                        try:
                            with os.scandir(entry.path) as children:
                                for child in children:
                                    if not child.name.startswith(".") and child.is_dir(follow_symlinks=False):
                                        has_subdirs = True
                                        break
                        except OSError:
                            pass
                        entries.append({"name": entry.name, "is_dir": True, "has_subdirs": has_subdirs, "mtime": mtime})
                except OSError:
                    continue
    except PermissionError as exc:
        raise ApiRequestError(f"无权访问：{exc}", status=403) from exc
    except OSError as exc:
        raise ApiRequestError(f"读取目录失败：{exc}", status=500) from exc
    # Newest first (folder content last changed at top); same timestamp falls
    # back to name order so bulk-generated folders stay grouped predictably.
    entries.sort(key=lambda item: (-item.get("mtime", 0.0), item["name"].casefold()))
    return entries


def _model_adapters() -> dict[str, Any]:
    # `chat_completions` is OpenAI-style for providers that hand out the full
    # /chat/completions endpoint; it routes through the same adapter.
    return {
        "anthropic_messages": AnthropicMessagesAdapter(),
        "openai_chat_completions": OpenAIChatCompletionsAdapter(),
        "chat_completions": OpenAIChatCompletionsAdapter(),
    }


def _persist_ai_generation_error(product: dict[str, Any], error: str) -> dict[str, Any]:
    """Keep the AI failure on the base product record so it survives a page refresh;
    the create flow itself still succeeds (best-effort enrichment)."""
    product_id = str(product.get("id") or "")
    updated = storage.update_base_product(product_id, {"ai_generation_error": error}) if product_id else None
    return updated or {**product, "ai_generation_error": error}


def _enrich_with_vision_listing(
    product: dict[str, Any],
    payload: dict[str, Any],
    *,
    mark_edited: bool = False,
    aspect: str = "",
) -> dict[str, Any]:
    """After a base product is created from a NAS folder, optionally ask a vision-capable
    AI model to write an English title/description or name unlabeled color images.

    Split into two independent stages so the frontend can report them separately:
    stage 1 generates title/description; stage 2 runs only when the deterministic
    filename scanner left variant images unnamed (numeric SKU files) — colors the
    scanner already recognized are kept as-is and never renamed by the AI.

    Controlled by runtime_configuration.ai_listing_generation toggles. All failures are
    swallowed — the base product is returned unchanged with `ai_generation_error` set so
    the frontend can surface a hint without breaking the create flow.

    ``mark_edited`` is set by the maintenance modal's 「重试 AI 识别」: that run rewrites
    the title/description, so it counts as an edit (import-time enrichment does not).

    ``aspect`` is set by the base product batch retry buttons (标题 / 详情 / 颜色图).
    It narrows the run to exactly that field: title-only never rewrites the description
    or attributes, description-only never rewrites the title, and colors re-inspects
    every current color image — including ones the filename scanner already named —
    instead of only the unnamed numeric SKU files.
    """
    from services.api import vision_listing

    runtime_config = storage.get_runtime_configuration()
    want_title, want_description = vision_listing.selected_flags(runtime_config)
    fill_attributes = vision_listing.fill_attributes_flag(runtime_config)
    # Explicit override from the client (e.g. a per-import checkbox) wins over settings.
    if isinstance(payload.get("generate_title"), bool):
        want_title = bool(payload["generate_title"])
    if isinstance(payload.get("generate_description"), bool):
        want_description = bool(payload["generate_description"])
    if isinstance(payload.get("fill_attributes_into_description"), bool):
        fill_attributes = bool(payload["fill_attributes_into_description"])

    force_colors = False
    run_color_stage: bool | None = None  # None = 导入时的旧行为：只补未命名 SKU 图
    persist_attributes = payload.get("persist_attributes") is not False
    if aspect == "title":
        want_title, want_description = True, False
        fill_attributes = False
        persist_attributes = False
        run_color_stage = False
    elif aspect == "description":
        want_title, want_description = False, True
        run_color_stage = False
    elif aspect == "colors":
        want_title, want_description = False, False
        force_colors = True
        run_color_stage = True

    # Numeric SKU filenames (for example ``SKU-01-dress only.png``) are
    # deliberately not treated as colors by the deterministic scanner. Only those
    # files need a color-only vision pass; scanner-recognized colors stay as-is.
    # 素材只取本次导入目录：同一个 SKU 重新导入后，上一批素材还在素材库里，
    # 按分组取会把两次导入的图片混进同一次颜色识别（也会写回旧文件名）。
    assets = storage.assets_for_base_product(product)
    unnamed_variant_files = [
        str(asset.get("file_name") or "").strip()
        for asset in assets
        if str(asset.get("asset_type") or "").lower() == "variant"
        and str(asset.get("file_name") or "").strip()
        and not str(asset.get("sku_option") or "").strip()
    ]
    color_targets: list[str] = []
    if run_color_stage is not False:
        color_targets = (
            # 与视觉请求的图片上限保持一致：超过上限的图这次没发出去，不能因为
            # 没拿到颜色就给它造 款式A/B/C；已有颜色由补丁的「手工颜色」分支保留。
            _color_recognition_target_names(product, assets)[: vision_listing.MAX_COLOR_IMAGES]
            if force_colors
            else list(unnamed_variant_files)
        )
    stages = {"title": "skipped", "colors": "skipped"}
    if force_colors and not color_targets:
        return {**_persist_ai_generation_error(product, "没有可重新识别的颜色图"), "ai_stages": stages}
    if not (want_title or want_description or color_targets):
        return {**product, "ai_stages": stages}

    # 纯颜色重识别允许只用「颜色识别使用的 AI」：没配默认连接但有专用的也能跑。
    if force_colors and not (want_title or want_description):
        connection = storage.get_ai_connection_for_purpose("colors")
    else:
        connection = storage.get_default_ai_model_connection_private()
    if not connection:
        return {**_persist_ai_generation_error(product, "未配置默认 AI 模型连接"), "ai_stages": stages}

    hints = payload.get("attribute_hints") if isinstance(payload.get("attribute_hints"), dict) else {}
    hints = {str(k): str(v) for k, v in hints.items() if str(v or "").strip()}

    # 标题 Skill（Skills 管理页维护）：默认 Skill 自动生效，只影响标题部分；
    # 热词按 Skill 的策略注入，策略为「不使用」或词库为空时都不占 prompt。
    title_skill = storage.get_active_title_skill() if want_title else None
    hot_words_for_title: list[str] = []
    if title_skill and str(title_skill.get("hot_word_policy") or "").lower() != "off":
        hot_words_for_title = storage.list_enabled_hot_words()

    try:
        gateway = ModelGateway(
            crypto=CredentialCrypto(settings.get_ai_credentials_master_key()),
            adapters=_model_adapters(),
        )
    except Exception as exc:  # noqa: BLE001 - vision must never break create
        return {**_persist_ai_generation_error(product, f"AI 生成过程异常: {exc}"), "ai_stages": stages}

    patch: dict[str, Any] = {}
    errors: list[str] = []

    title_errors: list[str] = []
    color_errors: list[str] = []

    def _run_text_stage() -> None:
        # Stage 1 — title / description / attributes.
        if want_title or want_description:
            stages["title"] = "done"
            try:
                result = vision_listing.generate_listing_from_images(
                    gateway=gateway,
                    connection=connection,
                    base_product=product,
                    assets=assets,
                    attribute_hints=hints,
                    want_title=want_title,
                    want_description=want_description,
                    fill_attributes_into_description=fill_attributes,
                    title_skill=title_skill,
                    hot_words=hot_words_for_title,
                )
            except Exception as exc:  # noqa: BLE001 - vision must never break create
                result = None
                title_errors.append(f"AI 生成过程异常: {exc}")
            if result is not None:
                if not result.ok:
                    title_errors.append(result.error)
                else:
                    # 只写这次点名要重识别的字段：定向重试时模型即使多给了另一个
                    # 字段，也不能把操作员没要求改的内容覆盖掉。
                    if result.title and want_title:
                        patch["listing_title"] = result.title
                    if result.description and want_description:
                        patch["description"] = result.description
                    if result.attributes and (want_title or want_description) and persist_attributes:
                        patch["recognized_attributes"] = dict(result.attributes)
                    if not any(key in patch for key in ("listing_title", "description")):
                        title_errors.append("AI 生成结果为空")


    target_asset_by_name = {
        str(asset.get("file_name") or "").strip().lower(): asset
        for asset in assets
        if str(asset.get("file_name") or "").strip()
    }
    color_target_assets = [
        target_asset_by_name.get(name.lower()) or {"file_name": name, "asset_type": "variant"}
        for name in color_targets
    ]

    def _run_color_stage() -> None:
        # Stage 2 — name the SKU images the scanner could not. On a forced re-run
        # (重新识别颜色图) the targets also include images whose colors came from
        # their filenames, because the operator explicitly asked to look again.
        # Even when the AI call fails, unnamed variants get deterministic 款式A/B/C
        # fallback labels so the product is never left without variant names.
        if color_targets:
            stages["colors"] = "done"
            color_patch: dict[str, Any] = {}
            # 颜色识别允许用单独的 AI 连接（设置里的「颜色识别使用的 AI」）：
            # 与标题/描述解耦，可以单独换成更便宜/更稳的视觉模型。
            color_connection = storage.get_ai_connection_for_purpose("colors") or connection
            color_gateway = gateway
            if str(color_connection.get("id") or "") != str((connection or {}).get("id") or ""):
                try:
                    color_gateway = ModelGateway(
                        crypto=CredentialCrypto(settings.get_ai_credentials_master_key()),
                        adapters=_model_adapters(),
                    )
                except Exception as exc:  # noqa: BLE001 - 视觉失败不能阻断创建
                    color_gateway = gateway
                    color_errors.append(f"颜色识别专用 AI 连接不可用: {exc}")
            try:
                color_result = vision_listing.generate_listing_from_images(
                    gateway=color_gateway,
                    connection=color_connection,
                    base_product=product,
                    assets=assets,
                    attribute_hints={},
                    want_title=False,
                    want_description=False,
                    fill_attributes_into_description=False,
                    color_targets=color_targets if force_colors else None,
                )
            except Exception as exc:  # noqa: BLE001 - vision must never break create
                color_result = None
                color_errors.append(f"颜色识别失败: AI 调用异常: {exc}")
            if color_result is not None and color_result.ok and color_result.color_translations:
                if force_colors:
                    color_patch = _forced_color_translation_patch(
                        product, color_result.color_translations, color_target_assets
                    )
                else:
                    color_patch = _color_translation_patch(product, color_result.color_translations, assets=assets)
            elif color_result is not None and not color_result.ok:
                stages["colors"] = "failed"
                fallback_hint = "" if force_colors else "（已用款式A/B/C 兜底命名）"
                color_errors.append(f"颜色识别失败: {color_result.error}{fallback_hint}")
            elif force_colors:
                stages["colors"] = "failed"
                color_errors.append("颜色识别失败: AI 未返回颜色名称")
            if not color_patch and not force_colors:
                color_patch = _color_translation_patch(product, {}, assets=assets)
            if color_patch:
                patch.update(color_patch)


    need_text = bool(want_title or want_description)
    need_color = bool(color_targets)
    if need_text and need_color:
        # 两个阶段互不依赖（一个写标题/描述，一个写颜色映射），并行跑：
        # 导入总耗时从「标题 + 颜色」变成 max(标题, 颜色)。
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-stage") as pool:
            futures = [pool.submit(_run_text_stage), pool.submit(_run_color_stage)]
            for future in futures:
                future.result()
    elif need_text:
        _run_text_stage()
    elif need_color:
        _run_color_stage()
    errors.extend(title_errors)
    errors.extend(color_errors)

    if errors and not patch:
        return {**_persist_ai_generation_error(product, "；".join(errors)), "ai_stages": stages}
    if errors:
        patch["ai_generation_error"] = "；".join(errors)
    else:
        patch["ai_generation_error"] = ""
    patch["ai_stages"] = stages
    updated = storage.update_base_product(str(product.get("id") or ""), patch, mark_edited=mark_edited)
    return {**(updated or {**product, **patch}), "ai_stages": stages}


def _is_ascii_english(value: str) -> bool:
    return bool(value) and all(char.isascii() and (char.isalpha() or not char.isalnum()) for char in value)


def _color_recognition_target_names(
    product: dict[str, Any],
    assets: list[dict[str, Any]] | None,
) -> list[str]:
    """「重新识别颜色图」要重看的文件：现有颜色图 + 全部变体图，按自然序。

    不能只取未命名 SKU 图：操作员点这个按钮，往往就是因为上次 AI 把已经命名的
    颜色图也认错了（或扫描器按文件名认错了），重跑时这些图必须一起看。"""
    names: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        name = str(value or "").strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)

    image_map = product.get("variant_image_map") or {}
    if isinstance(image_map, dict):
        for value in image_map.values():
            add(value)
    for asset in assets or []:
        if str(asset.get("asset_type") or "").lower() == "variant":
            add(asset.get("file_name"))
    return sorted(names, key=storage.natural_name_key)


def _color_translation_patch(
    product: dict[str, Any],
    translations: dict[str, str],
    *,
    assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = {
        str(key).strip().lower(): " ".join(str(value).split())
        for key, value in translations.items()
        if str(key).strip() and str(value).strip()
    }
    from services.api import vision_listing

    # 顺序必须与发给模型的图片顺序一致（自然序：1 (2) 在 1 (10) 前），否则颜色会
    # 整体错位一格；列表里带上文件名是为了判断哪个数字段在各文件间共用。
    unnamed_assets = vision_listing.ordered_unnamed_variant_assets(assets)
    # 未命名 SKU 图的颜色由这一次识别决定：商品上留着的上一次绑定（可能是错的）不能
    # 再带进来，否则重试只会把错配原样保留下来。
    unnamed_files = {str(asset.get("file_name") or "").strip().lower() for asset in unnamed_assets}

    colors = [str(item).strip() for item in product.get("recognized_colors") or [] if str(item).strip()]
    image_map = {str(key).strip().lower(): str(value).strip() for key, value in (product.get("variant_image_map") or {}).items()}
    translated_colors: list[str] = []
    seen: set[str] = set()
    translated_map: dict[str, str] = {}
    for color in colors:
        translated = normalized.get(color.lower()) or color
        # Scanner-recognized English colors are authoritative — keep the original
        # SKU name (the "items are identical, trust the filename" rule) and only let
        # the AI rename Chinese colors into English or name unnamed files.
        if translated != color and _is_ascii_english(color):
            translated = color
        # Do not replace a Chinese source with another non-English model value.
        if translated != color and not any(char.isascii() and char.isalpha() for char in translated):
            translated = color
        file_name = image_map.get(color.lower())
        if file_name and file_name.lower() in unnamed_files:
            # 属于未命名 SKU 图的颜色，交给下面这趟重新识别（含兜底标签）。
            continue
        key = translated.lower()
        if key in seen:
            continue
        seen.add(key)
        translated_colors.append(translated)
        if file_name:
            translated_map[translated] = file_name

    # Numeric SKU files have no color key until the vision model names the color.
    # The prompt asks the model to key these translations by exact filename.
    # Files the AI could not name fall back to deterministic 款式A/B/C labels so the
    # import is never left with unnamed variants blocking the flow (the operator can
    # rename them later in the maintenance dialog).
    fallback_index = 0

    def _next_fallback_label() -> str:
        nonlocal fallback_index
        while True:
            index = fallback_index
            fallback_index += 1
            if index < 26:
                label = f"款式{chr(ord('A') + index)}"
            else:
                label = f"款式A{index - 25}"
            if label.lower() not in seen:
                return label

    # "1 (3).png" 这类文件名里，前导的 "1" 每个文件都有；拿它去匹配模型返回的裸数字
    # 键会把一堆文件配到同一个颜色上（其余全被去重丢掉）。只有能区分文件的数字段才
    # 当作候选键，公用的那一段直接跳过。
    shared_tokens: set[str] = set()
    token_sets: list[set[str]] = []
    for asset in unnamed_assets:
        stem = Path(str(asset.get("file_name") or "")).stem.lower()
        tokens = set(re.findall(r"(?<![a-z0-9])\d+(?![a-z0-9])", stem))
        token_sets.append(tokens)
    for index, tokens in enumerate(token_sets):
        for token in tokens:
            if any(token in other for other in token_sets[:index] + token_sets[index + 1:]):
                shared_tokens.add(token)

    for position, (asset, tokens) in enumerate(zip(unnamed_assets, token_sets), start=1):
        file_name = str(asset.get("file_name") or "").strip()
        stem = Path(file_name).stem.lower()
        # 匹配优先级：整文件名 → 主干 → 该文件独有的数字段 → 提示里的图片编号 #N。
        lookup_keys = [
            file_name.lower(),
            stem,
            *[token for token in re.findall(r"(?<![a-z0-9])\d+(?![a-z0-9])", stem) if token not in shared_tokens],
            str(position),
            f"#{position}",
        ]
        translated = next((normalized.get(key) for key in lookup_keys if normalized.get(key)), "")
        if not translated or not any(char.isascii() and char.isalpha() for char in translated):
            translated = _next_fallback_label()
        # 颜色列表和颜色图映射用同一个规范写法，避免 "Light Purple" 归一化成
        # "LightPurple" 之后两边对不上、映射被整条丢掉。
        translated = storage.normalize_color_label(translated)
        key = translated.lower()
        if key in seen and any(existing.lower() == key for existing in translated_map):
            # 这个颜色已经绑在别的文件上（例如扫描器按文件名认出的颜色）：让位。
            continue
        if key not in seen:
            seen.add(key)
            translated_colors.append(translated)
        # 走到这里说明要么是全新颜色，要么是上一次留下的「有颜色名、没有图」的半截数据
        # ——那正是重试识别要补上的绑定。
        translated_map[translated] = file_name
    if not translated_colors:
        return {}
    translated_colors, translated_map = _order_variant_set_labels(translated_colors, translated_map)
    return {"recognized_colors": translated_colors, "variant_image_map": translated_map}


def _forced_color_translation_patch(
    product: dict[str, Any],
    translations: dict[str, str],
    target_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """「重新识别颜色图」：以现有颜色图 + 全部变体图为准重建颜色列表与映射。

    和导入时的 _color_translation_patch 有两点不同：
    1. 已经有颜色名的 SKU 图也要重看——文件名/旧颜色只在 AI 没给结果时兜底；
    2. AI 没给结果时不造新的 款式A/B/C 变体，避免把操作员已有的颜色冲掉。

    颜色顺序优先沿用现有颜色列表，再按文件名自然序补上新出现的图，最后保留
    没有对应图片的手工颜色（例如操作员手加、还没绑图的颜色）。"""
    normalized = {
        str(key).strip().lower(): " ".join(str(value).split())
        for key, value in translations.items()
        if str(key).strip() and str(value).strip()
    }
    targets = [
        asset
        for asset in target_assets
        if str(asset.get("file_name") or "").strip()
    ]
    if not targets:
        return {}

    # 自然序与发给模型的图片顺序一致；数字段匹配规则沿用导入那条路径：
    # 文件名里各图共用的数字段（如 1 (1).png 的 1）不能当作颜色键。
    target_names = [str(asset.get("file_name") or "").strip() for asset in targets]
    positions = {name.lower(): index for index, name in enumerate(target_names, start=1)}
    shared_tokens: set[str] = set()
    token_sets: list[set[str]] = []
    for name in target_names:
        tokens = set(re.findall(r"(?<![a-z0-9])\d+(?![a-z0-9])", Path(name).stem.lower()))
        token_sets.append(tokens)
    for index, tokens in enumerate(token_sets):
        for token in tokens:
            if any(token in other for other in token_sets[:index] + token_sets[index + 1:]):
                shared_tokens.add(token)

    def _lookup(file_name: str, position: int) -> str:
        stem = Path(file_name).stem.lower()
        keys = [
            file_name.lower(),
            stem,
            *[
                token
                for token in re.findall(r"(?<![a-z0-9])\d+(?![a-z0-9])", stem)
                if token not in shared_tokens
            ],
            str(position),
            f"#{position}",
        ]
        return next((normalized.get(key) for key in keys if normalized.get(key)), "")

    colors = [str(item).strip() for item in product.get("recognized_colors") or [] if str(item).strip()]
    image_map = {
        str(key).strip().lower(): str(value).strip()
        for key, value in (product.get("variant_image_map") or {}).items()
    }
    file_to_color: dict[str, str] = {}
    for color in colors:
        file_name = image_map.get(color.lower())
        if file_name:
            file_to_color.setdefault(file_name.lower(), color)

    result_colors: list[str] = []
    result_map: dict[str, str] = {}
    seen: set[str] = set()
    handled: set[str] = set()
    fallback_index = 0

    def _next_fallback_label() -> str:
        nonlocal fallback_index
        while True:
            index = fallback_index
            fallback_index += 1
            label = f"款式{chr(ord('A') + index)}" if index < 26 else f"款式A{index - 25}"
            if label.lower() not in seen:
                return label

    def _assign(file_name: str, color: Any) -> bool:
        label = storage.normalize_color_label(str(color or ""))
        if not label:
            return False
        key = label.lower()
        if key in seen:
            return False
        seen.add(key)
        result_colors.append(label)
        result_map[label] = file_name
        return True

    # ① 现有颜色顺序：同一张图换了个颜色名也留在原位置。
    for color in colors:
        file_name = image_map.get(color.lower())
        if not file_name or file_name.lower() not in positions:
            continue
        key = file_name.lower()
        if key in handled:
            continue
        handled.add(key)
        # 模型有时会按「上一次的颜色名」而不是文件名回答；旧颜色键也认一次。
        translated = _lookup(file_name, positions[key]) or normalized.get(color.lower(), "")
        _assign(file_name, translated or color)

    # ② 其余变体图：按自然序补进来；AI 没结果时先沿用旧绑定/文件名颜色。
    for position, asset in enumerate(targets, start=1):
        file_name = str(asset.get("file_name") or "").strip()
        key = file_name.lower()
        if key in handled:
            continue
        handled.add(key)
        translated = _lookup(file_name, position)
        if translated:
            _assign(file_name, translated)
            continue
        reused = False
        for candidate in (file_to_color.get(key), asset.get("sku_option")):
            if not str(candidate or "").strip():
                continue
            # 候选颜色已绑在别的图上（同一颜色的第二张图）：让位，不再造新变体。
            _assign(file_name, candidate)
            reused = True
            break
        if reused:
            continue
        while True:
            label = _next_fallback_label()
            if _assign(file_name, label):
                break

    # ③ 没有对应图片的手工颜色原样保留，别因为重识别把操作员加的项删掉。
    for color in colors:
        label = storage.normalize_color_label(color)
        if not label or label.lower() in seen:
            continue
        file_name = image_map.get(color.lower())
        if file_name and file_name.lower() in positions:
            continue
        seen.add(label.lower())
        result_colors.append(label)
        if file_name:
            result_map[label] = file_name

    if not result_colors:
        return {}
    result_colors, result_map = _order_variant_set_labels(result_colors, result_map)
    return {"recognized_colors": result_colors, "variant_image_map": result_map}


# When SKU images describe package contents (costume / wig / full set) rather than
# colors, variants are shown in this fixed order; labels not present are simply absent.
_VARIANT_SET_ORDER = ("costume", "wig", "costume + wig", "costume+wig")


def _variant_set_rank(label: str) -> int | None:
    value = " ".join(str(label or "").strip().lower().split())
    if value == "costume" or value == "costumes":
        return 0
    if value == "wig":
        return 1
    if value.replace(" ", "") in ("costume+wig", "costumes+wig"):
        return 2
    return None


def _order_variant_set_labels(
    colors: list[str], image_map: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    ranked = [( _variant_set_rank(color), color) for color in colors]
    if all(rank is not None for rank, _ in ranked):
        ordered = [color for _, color in sorted(ranked, key=lambda item: item[0] or 0)]
        ordered_map = {color: image_map[color] for color in ordered if color in image_map}
        return ordered, ordered_map
    return colors, image_map


def _enabled_mabang_accounts() -> list[dict[str, Any]]:
    return [item for item in storage.list_mabang_accounts() if item.get("enabled", True)]


def _worker_slots_per_account(config: dict[str, Any] | None = None) -> int:
    """每个马帮账号开几个并行槽位（设置里的 worker.slots_per_account）。"""

    merged = config if isinstance(config, dict) else storage.get_runtime_configuration()
    return worker_slots_per_account(merged)


def _account_has_stores(account_id: str) -> bool:
    """这个马帮账号名下有没有店铺（没店铺就没有上架任务，不必为它多开浏览器）。

    读不出店铺目录时按「有」处理（fail-open）：宁可多开一只浏览器，也不要因为一次
    读取抖动把并行度悄悄降回 1。
    """

    try:
        return bool(storage.list_mabang_store_options(account_id=account_id))
    except Exception:  # noqa: BLE001 - 目录读不出来不该让 Worker 少开
        return True


def _worker_targets(
    accounts: list[dict[str, Any]] | None = None,
    *,
    slots: int | None = None,
) -> list[dict[str, Any]]:
    """把「启用的马帮账号 × 并行槽位」展开成 Worker 目标列表。

    目标是 [{"account_id", "slot"}]：slot 0 是账号自己的那只浏览器（老行为），
    slot 1 起是同一个账号的额外槽位（独立 CDP 端口 + 独立 Profile），
    这样同一个账号也能同时跑多单上架。

    额外槽位只开给「名下有店铺」的账号：没有店铺的账号根本不会产生上架任务，
    多开一只浏览器纯属浪费内存与登录会话。它那条 slot 0 的 Worker 照旧保留。
    """

    wanted_accounts = _enabled_mabang_accounts() if accounts is None else list(accounts)
    wanted_slots = _worker_slots_per_account() if slots is None else max(1, int(slots))
    targets: list[dict[str, Any]] = []
    for account in wanted_accounts:
        account_id = str(account.get("id") or "").strip()
        if not account_id:
            continue
        account_slots = wanted_slots if wanted_slots <= 1 or _account_has_stores(account_id) else 1
        for slot in range(account_slots):
            targets.append({"account_id": account_id, "slot": slot})
    return targets


def _worker_target_runtime_dir(accounts_root: Path, target: dict[str, Any]) -> Path:
    """目标的 runtime 目录：1 号槽位保持 `accounts/<账号>`（老目录不搬家）。"""

    account_dir = accounts_root / str(target.get("account_id") or "")
    slot = int(target.get("slot") or 0)
    return account_dir if slot <= 0 else account_dir / f"slot-{slot + 1}"


def _worker_target_id(target: dict[str, Any]) -> str:
    account_id = str(target.get("account_id") or "")
    slot = int(target.get("slot") or 0)
    return f"mabang-{account_id[:8]}" + (f"-s{slot + 1}" if slot > 0 else "")


def _worker_pending_completion_path(target: dict[str, Any]) -> Path:
    """每个 Worker 一份「保存成功但没上报完」的续期文件。

    这个文件以前只有全局一个（`tmp_upload/automation-jobs/pending-completion.json`）；
    同账号多槽位后两个 Worker 会互相覆盖对方的待上报记录，所以 2 号槽位起各写各的，
    1 号槽位保持原路径不变（在途的旧文件还能被认出来）。
    """

    base = settings.PROJECT_ROOT / "tmp_upload" / "automation-jobs"
    slot = int(target.get("slot") or 0)
    if slot <= 0:
        return base / "pending-completion.json"
    return base / f"pending-completion-slot{slot + 1}.json"


def _payload_slot(payload: dict[str, Any], default: int = 0) -> int:
    """请求体里的槽位号；缺失/脏值按 default（1 号槽位）处理。"""

    raw = payload.get("slot") if isinstance(payload, dict) else None
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return int(default)


def _worker_supervisors() -> MultiWorkerSupervisor | None:
    """启用账号存在时返回「按账号 × 槽位分区」的多 Worker supervisor；否则 None（单 Worker 旧路径）。

    每个账号一个进程、一套 runtime 目录与日志、一个 CDP 端口；同一个账号的每个槽位
    再各有一套（独立端口与 Profile），因此 13 个店铺同属一个账号时也能 2~3 单并行；
    账号之间、槽位之间互不影响：停一个不会停别的，一个登录失败也不阻止其他目标启动。
    """

    global _MULTI_WORKER_SUPERVISOR, _MULTI_WORKER_RUNTIME_DIR
    config = storage.get_runtime_configuration()
    targets = _worker_targets(slots=_worker_slots_per_account(config))
    # 只有一个目标时不分区：单 Worker 会按任务切换账号，行为与并行版一致，
    # 却少一套进程/日志/状态；只有真正需要并行（≥2 个目标）才拆进程。
    if len(targets) < 2:
        return None
    worker_config = config["worker"]
    runtime_dir = settings.DATA_DIR / "worker-runtime"
    if _MULTI_WORKER_SUPERVISOR is None or _MULTI_WORKER_RUNTIME_DIR != runtime_dir:
        accounts_root = runtime_dir / "accounts"

        def build(target: dict[str, Any]) -> WorkerSupervisor:
            account_id = str(target.get("account_id") or "")
            slot = int(target.get("slot") or 0)
            target_dir = _worker_target_runtime_dir(accounts_root, target)
            account_runtime = storage.resolve_mabang_account_runtime(account_id, slot=slot)
            return WorkerSupervisor(
                target_dir,
                command=[
                    sys.executable, "-m", "scripts.automation_worker",
                    "--api-url", _local_api_url(),
                    "--cdp-url", str(account_runtime.get("cdp_url") or worker_config["cdp_url"]),
                    "--account-id", account_id,
                    "--worker-slot", str(slot),
                    "--worker-id", _worker_target_id(target),
                    "--pending-completion-file", str(_worker_pending_completion_path(target)),
                    "--poll-seconds", str(worker_config["poll_seconds"]),
                    "--lease-seconds", str(worker_config["lease_seconds"]),
                    "--lock-file", str(target_dir / "automation-worker.lock"),
                ],
                worker_pid_path=target_dir / "automation-worker.lock",
                working_directory=settings.PROJECT_ROOT,
            )

        _MULTI_WORKER_SUPERVISOR = MultiWorkerSupervisor(accounts_root, build=build)
        _MULTI_WORKER_RUNTIME_DIR = runtime_dir
    _MULTI_WORKER_SUPERVISOR.configure(targets)
    return _MULTI_WORKER_SUPERVISOR


def _attach_account_worker_activities(
    multi: "MultiWorkerSupervisor", status: dict[str, Any]
) -> dict[str, Any]:
    """把每个 Worker「正在做什么」并进 /api/worker/status。

    多目标模式下 status 只有进程存活信息（running/pid），Worker 面板需要 activity
    才能显示「页面导入中 · 已让路」这类状态。顶层 activity 取最近的一条，沿用单账号
    时代的形状（worker.activity / activity_error / activity_at），界面代码不用分叉。
    """

    activities: list[dict[str, Any]] = []
    for row in status.get("workers") or []:
        if not isinstance(row, dict):
            continue
        supervisor = multi.get(str(row.get("key") or row.get("account_id") or ""))
        if supervisor is None:
            continue
        activity = _latest_worker_activity(supervisor)
        if not activity:
            continue
        row.update(activity)
        activities.append(activity)
    if activities:
        # 顶层活动取「最值得看的」而不是「最新的」：一个账号在等浏览器/给导入让路，
        # 另一个账号刚报了 idle，界面不该被 idle 盖过去。
        status.update(
            max(
                activities,
                key=lambda item: (
                    str(item.get("activity") or "") not in {"", "idle"},
                    str(item.get("activity_at") or ""),
                ),
            )
        )
    return status


def _retire_legacy_worker() -> None:
    """多账号模式下停掉旧的全局单 Worker。

    它没有 `--account-id`：领取时不做账号过滤（能把 B 账号的任务跑进 A 账号的登录态），
    而且它的浏览器恢复会跟某个账号的 Worker 抢同一只浏览器（现场就是 9223 被反复
    杀/起，把操作员的页面导入窗口收走）。账号 Worker 已经覆盖它的全部职责。
    """

    try:
        supervisor = _worker_supervisor()
        status = supervisor.status()
    except Exception as exc:  # noqa: BLE001 - 收尾动作绝不能拖垮启动
        print(f"Legacy global worker retire skipped: {exc}", file=sys.stderr)
        return
    if status.get("status") not in {"running", "paused"} and not status.get("pid"):
        return
    try:
        supervisor.stop()
        print("Legacy global worker stopped: multi-account workers own every collection browser")
    except Exception as exc:  # noqa: BLE001
        print(f"Legacy global worker stop failed: {exc}", file=sys.stderr)


def _target_worker_is_running(multi: "MultiWorkerSupervisor", key: str) -> bool:
    """这个目标的 Worker 进程是否在跑（读不到状态时按「不在跑」处理，照旧准备浏览器）。"""

    supervisor = multi.get(key)
    if supervisor is None:
        return False
    try:
        return str(dict(supervisor.status()).get("status") or "") == "running"
    except Exception:  # noqa: BLE001 - 状态读不出来不该挡住启动
        return False


def _start_workers() -> dict[str, Any]:
    """启动 Worker：有账号时每个账号一个进程，否则保持旧的单 Worker。"""

    multi = _worker_supervisors()
    if multi is not None:
        _retire_legacy_worker()
        return {"ok": True, "workers": multi.run("start")}
    return _worker_supervisor().start()


def _login_error_detail(login: dict[str, Any]) -> str:
    """门禁失败时把自动登录脚本的原始原因一起带上。

    2026-10-12：从一个精简 PATH 的 shell 重启 API 后，自动登录返回
    ``{"status": "failed", "error": "无法启动 Node：[WinError 2] …"}``，而多账号路径
    只把 status 拼进消息，界面只显示「马帮登录未确认：failed」——看不出是 node 找不到。
    """

    error = str(login.get("error") or "").strip()
    return f" - {error}" if error else ""


def _run_account_worker_action(multi: MultiWorkerSupervisor, action: str) -> dict[str, Any]:
    """对每个「账号 × 槽位」的 Worker 执行动作。

    启动类动作先给每个目标准备它自己那只浏览器与登录：准备失败的目标只记录失败、
    不启动，其他目标照常启动——这正是「单账号登录过期不影响其他账号/槽位」。

    `start` / `resume` 对**已经在跑**的目标例外：不重新准备它的浏览器。新建任务或
    「再次尝试」都会唤醒 Worker（`_wake_worker_for_created_jobs` /
    `_schedule_retry_worker_wake`），而「准备浏览器」在复用时会清掉那只浏览器里的
    所有页面（`_close_mabang_browser_pages`）再开一张新建刊登页 —— 在途任务正在填的
    页面就这么被顶掉了。已经在跑的 Worker 不需要这份准备：它自己的启动预检与掉线恢复
    会处理 CDP/登录（`resume` 对运行中的进程本来也是空操作）。
    """

    if action not in {"start", "pause", "resume", "stop", "restart"}:
        raise LookupError("Unknown worker action")
    targets = multi.targets()
    preparations: dict[str, dict[str, Any]] = {}
    ready: list[str] = [str(target.get("key") or "") for target in targets]
    failures: list[dict[str, Any]] = []
    if action in {"start", "resume", "restart"}:
        ready = []
        for target in targets:
            key = str(target.get("key") or "")
            account_id = str(target.get("account_id") or "")
            slot = int(target.get("slot") or 0)
            if action in {"start", "resume"} and _target_worker_is_running(multi, key):
                preparations[key] = {"ok": True, "skipped": "already_running"}
                ready.append(key)
                continue
            try:
                preparation = _prepare_worker_browser_login(account_id, slot=slot)
            except MabangBrowserError as exc:
                failures.append({"account_id": account_id, "slot": slot, "error": str(exc)[:300]})
                continue
            preparations[key] = preparation
            if preparation.get("ok"):
                ready.append(key)
                continue
            login = preparation.get("auto_login") or {}
            failures.append(
                {
                    "account_id": account_id,
                    "slot": slot,
                    "error": f"马帮登录未确认：{str(login.get('status') or 'failed')}"
                    f"{_login_error_detail(login)}",
                }
            )

    workers: list[dict[str, Any]] = []
    for key in ready:
        supervisor = multi.get(key)
        if supervisor is None:
            continue
        target = next((item for item in targets if str(item.get("key") or "") == key), {})
        account_id = str(target.get("account_id") or "")
        slot = int(target.get("slot") or 0)
        try:
            outcome = dict(getattr(supervisor, action)())
            row: dict[str, Any] = {
                "key": key,
                "account_id": account_id,
                "slot": slot,
                "ok": True,
                **outcome,
            }
            if key in preparations:
                row["auto_login"] = preparations[key].get("auto_login")
                row["browser_mode"] = preparations[key].get("browser_mode")
            workers.append(row)
        except Exception as exc:  # noqa: BLE001 - 单个目标失败不影响其他目标
            workers.append(
                {"key": key, "account_id": account_id, "slot": slot, "ok": False, "error": str(exc)[:300]}
            )

    if not workers and failures:
        # 全部目标都没准备好：保持旧的「登录未确认」失败语义，前端能看到原因。
        raise RuntimeError(str(failures[0].get("error") or "马帮登录未确认"))
    return {"ok": True, "action": action, "workers": workers, "failures": failures}


def _start_worker_targets(target_keys: list[str]) -> dict[str, Any]:
    """只启动指定目标（槽位数调大后，新槽位不该等操作员再点一次启动）。

    已经在跑的目标不动：重新准备浏览器可能把它正在填的页面顶掉。
    """

    multi = _worker_supervisors()
    if multi is None or not target_keys:
        return {"ok": True, "workers": [], "failures": []}
    wanted = set(target_keys)
    targets = [target for target in multi.targets() if str(target.get("key") or "") in wanted]
    workers: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for target in targets:
        account_id = str(target.get("account_id") or "")
        slot = int(target.get("slot") or 0)
        key = str(target.get("key") or "")
        supervisor = multi.get(key)
        if supervisor is None:
            continue
        try:
            status = dict(supervisor.status())
            if str(status.get("status") or "") == "running":
                workers.append({"key": key, "account_id": account_id, "slot": slot, "ok": True, **status})
                continue
            preparation = _prepare_worker_browser_login(account_id, slot=slot)
        except MabangBrowserError as exc:
            failures.append({"account_id": account_id, "slot": slot, "error": str(exc)[:300]})
            continue
        except Exception as exc:  # noqa: BLE001 - 单目标失败不影响其他目标
            failures.append({"account_id": account_id, "slot": slot, "error": str(exc)[:300]})
            continue
        if not preparation.get("ok"):
            login = preparation.get("auto_login") or {}
            failures.append(
                {
                    "account_id": account_id,
                    "slot": slot,
                    "error": f"马帮登录未确认：{str(login.get('status') or 'failed')}"
                    f"{_login_error_detail(login)}",
                }
            )
            continue
        try:
            outcome = dict(supervisor.start())
            workers.append(
                {
                    "key": key,
                    "account_id": account_id,
                    "slot": slot,
                    "ok": True,
                    "auto_login": preparation.get("auto_login"),
                    "browser_mode": preparation.get("browser_mode"),
                    **outcome,
                }
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"account_id": account_id, "slot": slot, "error": str(exc)[:300]})
    return {"ok": True, "workers": workers, "failures": failures}


# ===== 按需启动 / 空闲自动关闭（2026-10-09）=====
#
# 现场要求：Worker 平时不跑，有任务才唤醒；执行完 10 分钟没有新任务就自动关闭。
# 三条规则：
#   1. 唤醒数量按「该账号待执行任务数」对齐（上限 = 每账号并行槽位），一有任务不把
#      所有槽位都拉起来；
#   2. 操作员手动「停止/暂停」= 压制：看护轮不再唤醒它，直到操作员点启动/恢复，
#      或者又生成了新任务（新任务会解除这条压制）；
#   3. 空闲关闭：该账号没有可领任务、没有在途任务、Worker 也没在报「正在执行」，
#      从最后一次任务活动算起满 idle_shutdown_minutes 分钟 → 停进程并关掉它那只采集浏览器。
#
# 空闲关闭会跳过「操作员正在从马帮当前页导入」的槽位：那只浏览器是他的可见窗口。

_WORKER_LIFECYCLE_LOCK = threading.Lock()
_WORKER_IDLE_SHUTDOWN_STATE: dict[str, Any] = {"at": "", "stopped": []}

# 「店铺采集」正在跑的时候不能关浏览器：采集用的是同一个账号的同一只采集浏览器
# （见 store_collection_service._default_collector），把窗口关了这轮采集就断在半路。
# 采集记录是跑完才落库的，所以这里用一个进程内的计数标记在途采集。
_STORE_COLLECTION_LOCK = threading.Lock()
_STORE_COLLECTION_ACTIVE = 0


def _store_collection_enter() -> None:
    global _STORE_COLLECTION_ACTIVE
    with _STORE_COLLECTION_LOCK:
        _STORE_COLLECTION_ACTIVE += 1


def _store_collection_leave() -> None:
    global _STORE_COLLECTION_ACTIVE
    with _STORE_COLLECTION_LOCK:
        _STORE_COLLECTION_ACTIVE = max(0, _STORE_COLLECTION_ACTIVE - 1)


def _store_collection_in_progress() -> bool:
    with _STORE_COLLECTION_LOCK:
        return _STORE_COLLECTION_ACTIVE > 0


def _worker_lifecycle_guard(*, blocking: bool, timeout: float = 0.0) -> bool:
    """串行化「唤醒 / 空闲关闭」这类会动进程与浏览器的操作（看护轮抢不到就跳过本轮）。"""

    if blocking:
        return _WORKER_LIFECYCLE_LOCK.acquire(timeout=max(0.1, timeout))
    return _WORKER_LIFECYCLE_LOCK.acquire(blocking=False)


def _worker_queue_snapshot(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """按账号读一遍任务队列（可领 / 在途 / 最近活动时间）；读不到就当没有任务。"""

    worker_config = (config or storage.get_runtime_configuration())["worker"]
    max_attempts = int(worker_config.get("max_attempts") or 2)
    try:
        return storage.automation_worker_queue_snapshot(max_attempts=max_attempts)
    except Exception as exc:  # noqa: BLE001 - 看护轮下一轮再看，不该把调度线程带崩
        print(f"Worker 队列快照读取失败：{exc}", file=sys.stderr)
        return {}


def _worker_wake_plan(
    multi: "MultiWorkerSupervisor", *, extra_accounts: dict[str, int] | None = None
) -> list[str]:
    """该拉起哪些 Worker 目标：按账号把「待执行任务数」和「已在跑的槽位数」对齐。

    ``extra_accounts`` 是「刚建出来的任务」（建单那一刻队列快照可能还没刷到，或者任务
    属于这次调用才见的账号），按账号取两者较大值。
    """

    wanted = {
        account: int(row.get("claimable") or 0)
        for account, row in _worker_queue_snapshot().items()
    }
    for account, count in (extra_accounts or {}).items():
        wanted[str(account)] = max(int(wanted.get(str(account)) or 0), int(count))

    by_account: dict[str, list[dict[str, Any]]] = {}
    for target in multi.targets():
        by_account.setdefault(str(target.get("account_id") or ""), []).append(target)

    keys: list[str] = []
    for account, targets in by_account.items():
        pending = int(wanted.get(account) or 0)
        if pending <= 0:
            continue
        supervisors: dict[str, Any] = {
            str(target.get("key") or ""): multi.get(str(target.get("key") or ""))
            for target in targets
        }
        running = sum(
            1
            for supervisor in supervisors.values()
            if supervisor is not None and str(supervisor.status().get("status") or "") == "running"
        )
        need = min(pending, len(targets)) - running
        for target in targets:
            if need <= 0:
                break
            key = str(target.get("key") or "")
            supervisor = supervisors.get(key)
            if supervisor is None or _supervisor_manual_hold_at(supervisor):
                continue
            if str(supervisor.status().get("status") or "") == "running":
                continue
            keys.append(key)
            need -= 1
    return keys


def _wake_workers_for_pending_jobs(*, extra_accounts: dict[str, int] | None = None) -> dict[str, Any]:
    """有活就把 Worker 拉起来（按需启动）；没有活就什么都不做。"""

    config = storage.get_runtime_configuration()
    if not worker_start_on_demand(config):
        return {"ok": True, "action": "on_demand_disabled", "keys": []}
    multi = _worker_supervisors()
    if multi is None:
        # 单 Worker：一个进程按任务切换账号，所以「任一账号有活」就起它。
        pending_total = sum(
            int(row.get("claimable") or 0) + int(row.get("in_flight") or 0)
            for row in _worker_queue_snapshot(config).values()
        )
        if pending_total <= 0 and not extra_accounts:
            return {"ok": True, "action": "no_work", "keys": []}
        supervisor = _worker_supervisor()
        if _supervisor_manual_hold_at(supervisor):
            return {"ok": True, "action": "held", "keys": []}
        if str(supervisor.status().get("status") or "") == "running":
            return {"ok": True, "action": "already_running", "keys": [""]}
        return {"ok": True, "action": "start", "keys": [""], "outcome": _run_worker_action("start")}
    keys = _worker_wake_plan(multi, extra_accounts=extra_accounts)
    if not keys:
        return {"ok": True, "action": "no_work", "keys": []}
    return {"ok": True, "action": "start", "keys": keys, "outcome": _start_worker_targets(keys)}


def _stop_target_browser(account_id: str, slot: int) -> dict[str, Any]:
    """关掉这个「账号 × 槽位」的采集浏览器（空闲关闭时用）。"""

    try:
        runtime = storage.resolve_mabang_account_runtime(account_id or None, slot=slot)
        cdp_url = str(runtime.get("cdp_url") or "").strip()
        profile_dir = str(runtime.get("profile_dir") or "").strip()
        if not cdp_url:
            return {"ok": False, "error": "no_cdp_url"}
        outcome = stop_mabang_browser(cdp_url, profile_dir=Path(profile_dir) if profile_dir else None)
        return {"ok": bool(outcome.get("ok")), **outcome}
    except Exception as exc:  # noqa: BLE001 - 关不掉浏览器不该让空闲关闭整体失败
        return {"ok": False, "error": str(exc)[:200]}


def _idle_shutdown_decision(
    supervisor: WorkerSupervisor,
    *,
    account_id: str,
    slot: int,
    snapshot: dict[str, dict[str, Any]],
    idle_seconds: float,
    now: datetime,
) -> dict[str, Any] | None:
    """这个 Worker 现在该不该因为空闲被关掉？（该关才返回原因）"""

    status = supervisor.status()
    if str(status.get("status") or "") != "running":
        return None
    if _supervisor_manual_hold_at(supervisor):
        return None

    if account_id:
        row = snapshot.get(account_id) or {}
        if int(row.get("claimable") or 0) > 0 or int(row.get("in_flight") or 0) > 0:
            return None
        # 操作员的页面导入用着同一只浏览器（可见窗口），不能关。
        try:
            if storage.active_mabang_page_import(account_id, slot=slot):
                return None
        except Exception:  # noqa: BLE001 - 读不到导入状态就按「有导入」保守处理
            return None
        latest_job_at = _parse_worker_timestamp(row.get("latest_activity_at"))
    else:
        # 单 Worker 模式：它按任务切账号，任一账号还有活（可领或在途）就不算空闲。
        if any(
            int(row.get("claimable") or 0) > 0 or int(row.get("in_flight") or 0) > 0
            for row in snapshot.values()
        ):
            return None
        latest_job_at = max(
            (
                value
                for value in (
                    _parse_worker_timestamp(row.get("latest_activity_at"))
                    for row in snapshot.values()
                )
                if value is not None
            ),
            default=None,
        )

    activity = _latest_worker_activity(supervisor)
    # 正在执行某一单（结果里带 job_id）时不关，哪怕队列快照看着是空的。
    if str(activity.get("activity_job_id") or "").strip():
        return None
    # 空闲起点**不能**用 activity_at：Worker 空闲时会每 30 秒重复写一条 idle 结果，
    # activity_at 永远是「刚刚」，空闲计时就永远不成立。真正的锚点是「最后一单的活动时间」
    # 和「进程启动时间」——前者是现场口径「执行完 10 分钟没有新任务」，后者管住
    # 「起来了却一直没领到任何任务」的 Worker。
    idle_since = max(
        (
            value
            for value in (
                latest_job_at,
                _parse_worker_timestamp(status.get("updated_at")),
            )
            if value is not None
        ),
        default=None,
    )
    if idle_since is None:
        return None
    idle = (now - idle_since).total_seconds()
    if idle < idle_seconds:
        return None
    return {
        "account_id": account_id,
        "slot": slot,
        "idle_seconds": int(idle),
        "activity": str(activity.get("activity") or ""),
        "pid": status.get("pid"),
    }


def _shutdown_idle_workers() -> list[dict[str, Any]]:
    """空闲够久就把 Worker 连同它那只采集浏览器一起关掉（按需启动模式）。"""

    config = storage.get_runtime_configuration()
    if not worker_start_on_demand(config):
        return []
    idle_seconds = worker_idle_shutdown_seconds(config)
    if idle_seconds <= 0:
        return []
    if _store_collection_in_progress():
        # 采集正用着某只采集浏览器：这轮不关，采集结束后的下一轮再看。
        return []
    snapshot = _worker_queue_snapshot(config)
    now = datetime.now(timezone.utc)
    multi = _worker_supervisors()
    targets: list[tuple[str, str, int, WorkerSupervisor]] = []
    if multi is None:
        targets.append(("", "", 0, _worker_supervisor()))
    else:
        for target in multi.targets():
            key = str(target.get("key") or "")
            supervisor = multi.get(key)
            if supervisor is not None:
                targets.append((key, str(target.get("account_id") or ""), int(target.get("slot") or 0), supervisor))

    stopped: list[dict[str, Any]] = []
    for key, account_id, slot, supervisor in targets:
        decision = _idle_shutdown_decision(
            supervisor,
            account_id=account_id,
            slot=slot,
            snapshot=snapshot,
            idle_seconds=idle_seconds,
            now=now,
        )
        if decision is None:
            continue
        try:
            supervisor.stop()
        except Exception as exc:  # noqa: BLE001 - 单个目标停不掉不影响其他目标
            stopped.append({**decision, "key": key, "ok": False, "error": str(exc)[:200]})
            continue
        browser = _stop_target_browser(account_id, slot)
        stopped.append({**decision, "key": key, "ok": True, "browser": browser})
        print(
            f"[worker-idle] 空闲 {decision['idle_seconds']}s，自动关闭 Worker "
            f"（账号 {account_id or '默认'} 槽位 {slot + 1}）：{browser.get('ok')}"
        )
    if stopped:
        _WORKER_IDLE_SHUTDOWN_STATE.update({"at": _wake_timestamp(), "stopped": stopped})
    return stopped


def _worker_housekeeping_tick() -> None:
    """看护轮里的按需调度：先按需唤醒，再关掉空闲够久的 Worker。

    两件事各自兜异常：唤醒失败（比如某个目标起不来）不该连累空闲关闭，反之亦然。
    """

    if not _worker_lifecycle_guard(blocking=False):
        return
    try:
        try:
            _wake_workers_for_pending_jobs()
        except Exception as exc:  # noqa: BLE001 - 唤醒失败不能把看护循环带走
            print(f"Worker 按需唤醒失败：{exc}", file=sys.stderr)
        try:
            _shutdown_idle_workers()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不能把看护循环带走
            print(f"Worker 空闲关闭失败：{exc}", file=sys.stderr)
    finally:
        _WORKER_LIFECYCLE_LOCK.release()


def _release_worker_holds_for_jobs(ready_jobs: list[dict[str, Any]]) -> list[str]:
    """新任务解除「操作员手动停」的压制：手动停只压旧任务，新任务照样能把它唤醒。"""

    accounts = {str(job.get("account_id") or "").strip() for job in ready_jobs if isinstance(job, dict)}
    if not accounts:
        return []
    multi = _worker_supervisors()
    released: list[str] = []
    if multi is None:
        supervisor = _worker_supervisor()
        if _supervisor_manual_hold_at(supervisor) and _release_worker_hold(supervisor):
            released.append("")
        return released
    for target in multi.targets():
        account_id = str(target.get("account_id") or "")
        if account_id not in accounts:
            continue
        key = str(target.get("key") or "")
        supervisor = multi.get(key)
        if supervisor is not None and _supervisor_manual_hold_at(supervisor) and _release_worker_hold(supervisor):
            released.append(key)
    return released


def _hold_workers_after_manual_stop(reason: str) -> list[dict[str, Any]]:
    """操作员手动停止/暂停 = 压制：按需唤醒不再拉起它，直到手动启动或又建了新任务。"""

    multi = _worker_supervisors()
    if multi is None:
        supervisor = _worker_supervisor()
        return [{"key": "", "ok": _hold_worker(supervisor, reason=reason)}]
    results: list[dict[str, Any]] = []
    for target in multi.targets():
        key = str(target.get("key") or "")
        supervisor = multi.get(key)
        if supervisor is None:
            continue
        results.append({"key": key, "account_id": str(target.get("account_id") or ""), "ok": _hold_worker(supervisor, reason=reason)})
    return results


def _worker_on_demand_status() -> dict[str, Any]:
    """Worker 面板/设置页要的按需启动状态。"""

    config = storage.get_runtime_configuration()
    on_demand = worker_start_on_demand(config)
    idle_seconds = worker_idle_shutdown_seconds(config) if on_demand else 0.0
    return {
        "on_demand": on_demand,
        "idle_shutdown_minutes": int(round(idle_seconds / 60)) if idle_seconds else 0,
        "last_idle_shutdown": dict(_WORKER_IDLE_SHUTDOWN_STATE),
        "last_wake": dict(_WORKER_WAKE_STATE),
    }


def _retire_unused_slot_browsers(previous_slots: int, current_slots: int) -> list[dict[str, Any]]:
    """槽位数调小后，把被裁掉槽位那只采集浏览器关掉。

    不关的话它会一直挂着：端口占着、内存占着，而且下一次把槽位数调回来时
    「复用运行中的浏览器」会让旧登录态/旧页面混进新槽位。
    """

    if current_slots >= previous_slots:
        return []
    results: list[dict[str, Any]] = []
    for account in _enabled_mabang_accounts():
        account_id = str(account.get("id") or "")
        for slot in range(max(0, current_slots), previous_slots):
            try:
                runtime = storage.resolve_mabang_account_runtime(account_id, slot=slot)
                outcome = stop_mabang_browser(
                    str(runtime.get("cdp_url") or ""),
                    profile_dir=Path(str(runtime.get("profile_dir") or "")),
                )
                results.append({"account_id": account_id, "slot": slot, "ok": True, **outcome})
            except Exception as exc:  # noqa: BLE001 - 关不掉旧浏览器不该让设置保存失败
                results.append({"account_id": account_id, "slot": slot, "ok": False, "error": str(exc)[:200]})
    return results


def _reconcile_worker_slots(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """设置里槽位数变化后立即生效：调大就起新槽位，调小就关掉多余浏览器。"""

    previous_slots = _worker_slots_per_account(previous)
    current_slots = _worker_slots_per_account(current)
    if previous_slots == current_slots:
        return {"previous_slots": previous_slots, "slots": current_slots}
    multi = _worker_supervisors()
    retired = _retire_unused_slot_browsers(previous_slots, current_slots)
    started: dict[str, Any] = {"workers": [], "failures": []}
    if multi is not None and current_slots > previous_slots:
        # configure() 已经把新增目标建好了（未启动）；这里只启动新槽位。
        new_keys = [
            str(target.get("key") or "")
            for target in multi.targets()
            if int(target.get("slot") or 0) >= previous_slots
        ]
        if worker_start_on_demand(current):
            # 按需启动：调大槽位只是「允许并行」，有活时按任务数唤醒（新槽位一起参与），
            # 没活就不该凭空起进程和采集浏览器。
            outcome = _wake_workers_for_pending_jobs()
            started = dict(outcome.get("outcome") or {})
            started.setdefault("workers", [])
            started.setdefault("failures", [])
            started["keys"] = outcome.get("keys") or []
        else:
            started = _start_worker_targets(new_keys)
    return {
        "previous_slots": previous_slots,
        "slots": current_slots,
        "retired_browsers": retired,
        "started": started,
    }


def _worker_supervisor() -> WorkerSupervisor:
    global _WORKER_SUPERVISOR, _WORKER_RUNTIME_DIR
    runtime_dir = settings.DATA_DIR / "worker-runtime"
    if _WORKER_SUPERVISOR is not None and _WORKER_RUNTIME_DIR == runtime_dir:
        return _WORKER_SUPERVISOR
    config = storage.get_runtime_configuration()["worker"]
    command = [
        sys.executable, "-m", "scripts.automation_worker",
        "--api-url", _local_api_url(),
        "--cdp-url", str(config["cdp_url"]),
        "--poll-seconds", str(config["poll_seconds"]),
        "--lease-seconds", str(config["lease_seconds"]),
        "--lock-file", str(runtime_dir / "automation-worker.lock"),
    ]
    _WORKER_SUPERVISOR = WorkerSupervisor(
        runtime_dir,
        command=command,
        worker_pid_path=runtime_dir / "automation-worker.lock",
        working_directory=settings.PROJECT_ROOT,
    )
    _WORKER_RUNTIME_DIR = runtime_dir
    return _WORKER_SUPERVISOR


def _restart_worker_supervisor() -> dict[str, Any]:
    global _WORKER_SUPERVISOR, _WORKER_RUNTIME_DIR
    if _WORKER_SUPERVISOR is not None:
        _WORKER_SUPERVISOR.stop()
    _WORKER_SUPERVISOR = None
    _WORKER_RUNTIME_DIR = None
    # The replacement supervisor holds no Popen handle for a worker that
    # outlived an earlier API process, so `start()` on its own would adopt that
    # process straight back — leaving it stuck in its in-memory pause while the
    # UI reports a successful restart. `restart()` stops first, killing it.
    result = _worker_supervisor().restart()
    storage.save_runtime_configuration({"worker": {"restart_required": False}})
    return result


def _ensure_bundled_chrome() -> None:
    """Provision the project-local Chrome on first launch if it is missing.

    Copies from the Playwright browser cache when available (fast, version-matched),
    otherwise falls back to `playwright install`. Kept best-effort: if provisioning
    fails, launch_mabang_browser still falls back to a system Chrome, so we only log.
    """

    if find_bundled_chrome() is not None:
        return
    try:
        from scripts.bundle_chrome import bundle_chrome

        bundle_chrome()
    except Exception as exc:  # noqa: BLE001 - never block launch on provisioning failure
        print(f"内置 Chrome provisioning skipped: {exc}", file=sys.stderr)


_AUTO_LOGIN_SUCCESS_STATUSES = {"logged_in", "already_logged_in"}


def _restore_collection_browser_mode(account_id: str | None = None, *, slot: int = 0) -> None:
    """把账号的采集浏览器切回「设置」里的模式（默认无头），窗口自动收起来。

    页面导入需要临时给人看的窗口；采集完成后必须恢复，不能把窗口一直留在桌面上。
    恢复失败不影响已经完成的采集。
    """

    try:
        configured_headless = bool(
            (storage.get_runtime_configuration().get("mabang") or {}).get("headless")
        )
        _launch_collection_browser(account_id, headless=configured_headless, slot=slot)
    except (MabangBrowserError, RuntimeError):
        pass


def _launch_collection_browser(
    account_id: str | None = None,
    *,
    headless: bool | None = None,
    slot: int = 0,
) -> tuple[dict[str, Any], str]:
    """Start the automation/Worker browser for one 马帮账号（某个槽位）.

    Each account gets its own CDP port and Chrome Profile directory, which is what
    isolates the Mabang login cookies: two accounts sharing a `--user-data-dir`
    would overwrite each other's session. 同一个账号的额外槽位（slot≥1）再各用一套
    端口与目录，于是同一个账号也能并行跑多单。Omitting `account_id` keeps the
    legacy single-account behaviour (`worker.cdp_url` + `storage/browser-use-profile`).

    Returns the launch payload and the resolved CDP url so the caller can run the
    auto-login helper against the same browser.
    """

    _ensure_bundled_chrome()
    runtime = storage.resolve_mabang_account_runtime(account_id, slot=slot)
    cdp_url = str(runtime.get("cdp_url") or "")
    # headless=None 表示用设置里的模式；页面导入会显式传 False 临时开窗口。
    effective_headless = bool(runtime.get("headless")) if headless is None else bool(headless)
    # 「从马帮当前页导入」进行中时，这只浏览器就是操作员正在用的可见窗口：凡是
    # 「没指定模式」的调用（Worker 预检 / 自动登录 / 设置页按钮）一律只复用、不重启，
    # 否则会把它切成设置里的无头模式，窗口凭空消失（2026-10-05 现场问题）。
    # 导入只发生在 1 号槽位的窗口上，所以这里按槽位判断：别的槽位照常跑，不用让路。
    page_import_session = (
        storage.active_mabang_page_import(account_id, slot=int(runtime.get("slot") or 0))
        if headless is None
        else None
    )
    result = launch_mabang_browser(
        cdp_url,
        profile_dir=Path(str(runtime.get("profile_dir") or "")),
        headless=effective_headless,
        # 调用方显式指定了模式（页面导入要开可见窗口 / 采集后要收回无头）时，
        # 探测不到正在运行的模式就重启，保证请求的模式真的生效。
        require_mode=headless is not None,
        reuse_running_only=page_import_session is not None,
    )
    result["account_id"] = str(runtime.get("account_id") or "")
    result["login_account"] = str(runtime.get("login_account") or "")
    result["slot"] = int(runtime.get("slot") or 0)
    if page_import_session is not None:
        result["page_import_run_id"] = str(page_import_session.get("id") or "")
    return result, cdp_url


def _mabang_auto_login(cdp_url: str, account_id: str | None = None) -> dict[str, Any]:
    """Run the Mabang auto-login flow for one account.

    With `account_id`, the credentials come from that 马帮账号; without it the
    legacy 设置 → 马帮连接 binding is used. Returns the auto_login payload
    ({status, error}); status is no_credentials when no account is bound. The
    password only lives in this process and the helper's child environment, never
    in the API response or logs.
    """

    account, password = storage.get_mabang_login_credentials(account_id)
    if not account or not password:
        return {"status": "no_credentials"}
    try:
        return run_mabang_auto_login(cdp_url, account=account, password=password)
    except Exception as exc:  # noqa: BLE001 - auto-login must never break the endpoint
        return {"status": "failed", "error": str(exc)[:300]}


def _attach_mabang_auto_login(
    result: dict[str, Any],
    cdp_url: str,
    account_id: str | None = None,
) -> None:
    auto_login = _mabang_auto_login(cdp_url, account_id)
    status = str(auto_login.get("status") or "failed")
    result["auto_login"] = {"status": status, "error": str(auto_login.get("error") or "")}
    if status in _AUTO_LOGIN_SUCCESS_STATUSES:
        result["login_required"] = False


def _prepare_worker_browser_login(account_id: str | None = None, *, slot: int = 0) -> dict[str, Any]:
    """Prepare one account slot's browser and fail closed unless login is proven."""

    result, cdp_url = _launch_collection_browser(account_id, slot=slot)
    _attach_mabang_auto_login(result, cdp_url, account_id)
    login = result.get("auto_login") or {}
    status = str(login.get("status") or "failed")
    result["ok"] = status in _AUTO_LOGIN_SUCCESS_STATUSES
    return result


def _run_worker_action(action: str) -> dict[str, Any]:
    """Run a Worker lifecycle action, gating active states on Mabang login."""

    if action not in {"start", "pause", "resume", "stop", "restart"}:
        raise LookupError("Unknown worker action")
    multi = _worker_supervisors()
    if multi is not None:
        return _run_account_worker_action(multi, action)
    supervisor = _worker_supervisor()
    # 「启动 / 恢复」在进程已经在跑时都是空操作。恢复分支以前还会先准备浏览器 +
    # 探测马帮登录（几秒到几十秒）；连点「再次尝试」时 N 个请求会先后撞在同一只
    # 浏览器上，现场表现为界面卡顿、在途页面被顶掉。已经在跑就直接返回状态。
    if action in {"start", "resume"}:
        status_fn = getattr(supervisor, "status", None)
        if callable(status_fn):
            current = status_fn()
            activity = _latest_worker_activity(supervisor)
            if current.get("status") == "running":
                return {**current, **activity, "already_running": True}
    preparation: dict[str, Any] | None = None
    if action in {"start", "resume", "restart"}:
        preparation = _prepare_worker_browser_login()
        if not preparation.get("ok"):
            login = preparation.get("auto_login") or {}
            status = str(login.get("status") or "failed")
            error = str(login.get("error") or "")
            detail = f" - {error}" if error else ""
            raise RuntimeError(f"马帮登录未确认：{status}{detail}")
    operation = _restart_worker_supervisor if action == "restart" else getattr(supervisor, action)
    result = operation()
    if preparation is not None:
        result = {
            **result,
            "browser_mode": preparation.get("browser_mode"),
            "auto_login": preparation.get("auto_login"),
        }
    return result


def _wake_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


_WORKER_WAKE_STATE: dict[str, Any] = {"ok": None, "action": "", "at": "", "error": ""}


def _supervisor_manual_hold_at(supervisor: Any) -> str:
    """读一个 Worker 目标的「操作员手动停」压制时间（接口不存在时按未压制处理）。"""

    reader = getattr(supervisor, "manual_hold_at", None)
    if not callable(reader):
        return ""
    try:
        return str(reader() or "")
    except Exception:  # noqa: BLE001 - 读压制状态失败不该挡住唤醒
        return ""


def _release_worker_hold(supervisor: Any) -> bool:
    release = getattr(supervisor, "release_hold", None)
    if not callable(release):
        return False
    try:
        release()
        return True
    except Exception:  # noqa: BLE001
        return False


def _hold_worker(supervisor: Any, *, reason: str) -> bool:
    holder = getattr(supervisor, "hold", None)
    if not callable(holder):
        return False
    try:
        holder(reason=reason)
        return True
    except Exception:  # noqa: BLE001
        return False


def _wake_worker_for_created_jobs_background(ready_jobs: list[dict[str, Any]]) -> None:
    """后台唤醒 Worker：启动 + 马帮登录探测可能要几秒到几十秒，绝不能占着请求线程。

    按需启动模式下还负责两件事：新任务解除「操作员手动停」压制；唤醒数量按该账号的
    待执行任务数对齐（不是一有任务就把所有槽位都拉起来）。
    """

    try:
        _release_worker_holds_for_jobs(ready_jobs)
        multi = _worker_supervisors()
        if multi is not None and worker_start_on_demand(storage.get_runtime_configuration()):
            acquired = _worker_lifecycle_guard(blocking=True, timeout=120.0)
            try:
                outcome = _wake_workers_for_pending_jobs(
                    extra_accounts=_ready_jobs_by_account(ready_jobs)
                )
            finally:
                if acquired:
                    _WORKER_LIFECYCLE_LOCK.release()
            _WORKER_WAKE_STATE.update({
                "ok": bool(outcome.get("ok", True)),
                "action": str(outcome.get("action") or "start"),
                "at": _wake_timestamp(),
                "error": "",
                "outcome": dict(outcome),
            })
            return
        # 单 Worker / 关掉按需启动：保持原有「活动状态决定 start 还是 restart」。
        supervisor = _worker_supervisor()
        activity_payload = _latest_worker_activity(supervisor)
        activity = str(activity_payload.get("activity") or "")
        status_fn = getattr(supervisor, "status", None)
        worker_status = status_fn() if callable(status_fn) else {}
        activity_at = _parse_worker_timestamp(activity_payload.get("activity_at"))
        process_at = _parse_worker_timestamp(worker_status.get("updated_at"))
        stale_activity = bool(activity_at and process_at and activity_at < process_at)
        action = "restart" if activity == "paused" and not stale_activity else "start"
        outcome = _run_worker_action(action)
        _WORKER_WAKE_STATE.update({
            "ok": bool(outcome.get("ok", True)),
            "action": action,
            "at": _wake_timestamp(),
            "error": "",
            "outcome": dict(outcome),
        })
    except (MabangBrowserError, RuntimeError) as exc:
        _WORKER_WAKE_STATE.update({"ok": False, "action": "start", "at": _wake_timestamp(), "error": str(exc)[:500]})
        print(f"[worker-wake] {exc}")
    except Exception as exc:  # noqa: BLE001 - 后台线程不能把异常抛到没人接的地方
        _WORKER_WAKE_STATE.update({"ok": False, "action": "start", "at": _wake_timestamp(), "error": str(exc)[:500]})
        print(f"[worker-wake] unexpected: {exc}")


def _ready_jobs_by_account(ready_jobs: list[dict[str, Any]]) -> dict[str, int]:
    """本次新建的 ready 任务按账号计数（唤醒数量用它，而不是整库数）。"""

    counts: dict[str, int] = {}
    for job in ready_jobs:
        if not isinstance(job, dict):
            continue
        account = str(job.get("account_id") or "").strip()
        counts[account] = counts.get(account, 0) + 1
    return counts


def _wake_worker_for_created_jobs(result: dict[str, Any]) -> dict[str, Any]:
    """任务建好后**触发**唤醒 Worker（不等待）。

    现场反馈「生成会有卡顿」：原先这里同步跑 ``_run_worker_action``（起 Worker 进程 +
    探测马帮登录态），整个 POST 要等它结束才返回，界面就一直是"生成中"。现在只在后台
    线程里唤醒，接口立刻返回；结果写进 ``_WORKER_WAKE_STATE`` 供状态查询。
    """

    ready_jobs = [item for item in result.get("created") or [] if item.get("status") == "ready"]
    if not ready_jobs:
        return result
    result["worker_start"] = {"ok": None, "status": "starting", "count": len(ready_jobs)}
    threading.Thread(
        target=_wake_worker_for_created_jobs_background,
        args=(ready_jobs,),
        name="worker-wake",
        daemon=True,
    ).start()
    return result


# ===== 「再次尝试」的 Worker 唤醒队列（2026-10-23）=====
#
# 现场反馈：连点多个「再次尝试」时界面会卡。重排本身只要 ~0.2s，卡的是每个请求都
# 同步唤醒 Worker（起进程 + 准备采集浏览器 + 探测马帮登录，几秒到几十秒）。这里把
# 唤醒改成后台单线程队列：连点 N 次合并成一批唤醒；正在唤醒时再点只累积到下一批，
# 不会并发去准备同一只浏览器，也不会阻塞「再次尝试」的请求线程。
_RETRY_WAKE_LOCK = threading.Lock()
_RETRY_WAKE_JOBS: list[dict[str, Any]] = []
_RETRY_WAKE_RUNNING = False
_RETRY_WAKE_STATE: dict[str, Any] = {"ok": None, "at": "", "error": "", "count": 0}


def _schedule_retry_worker_wake(job: dict[str, Any]) -> None:
    """把重排后的 Worker 唤醒放进后台队列（接口立即返回，不等待浏览器/登录）。"""

    global _RETRY_WAKE_RUNNING
    if not isinstance(job, dict) or job.get("status") != "ready":
        return
    # 只留唤醒需要的字段：requeue 的返回值带 execution_snapshot（可能十几 MB），
    # 不要把它挂在队列里等后台线程消费。
    slim = {
        key: job.get(key)
        for key in ("id", "status", "account_id", "listing_copy_id")
        if job.get(key) is not None
    }
    with _RETRY_WAKE_LOCK:
        _RETRY_WAKE_JOBS.append(slim)
        if _RETRY_WAKE_RUNNING:
            return
        _RETRY_WAKE_RUNNING = True
    threading.Thread(
        target=_drain_retry_worker_wake,
        name="retry-worker-wake",
        daemon=True,
    ).start()


def _drain_retry_worker_wake() -> None:
    """消费唤醒队列；一轮唤醒期间累积的连点合并成下一轮。"""

    global _RETRY_WAKE_RUNNING
    while True:
        with _RETRY_WAKE_LOCK:
            if not _RETRY_WAKE_JOBS:
                _RETRY_WAKE_RUNNING = False
                return
            ready_jobs = list(_RETRY_WAKE_JOBS)
            _RETRY_WAKE_JOBS.clear()
        try:
            # 复用「新建任务」的唤醒逻辑：按账号/槽位对齐待执行任务数、尊重手动停的压制、
            # 已经在跑的 Worker 不重新准备浏览器。它自己会兜异常并写 _WORKER_WAKE_STATE。
            _wake_worker_for_created_jobs_background(ready_jobs)
            _RETRY_WAKE_STATE.update(
                {"ok": True, "at": _wake_timestamp(), "error": "", "count": len(ready_jobs)}
            )
        except Exception as exc:  # noqa: BLE001 - 后台线程不能把异常抛到没人接的地方
            _RETRY_WAKE_STATE.update(
                {"ok": False, "at": _wake_timestamp(), "error": str(exc)[:300], "count": len(ready_jobs)}
            )
            print(f"[retry-wake] 唤醒 Worker 失败：{exc}", file=sys.stderr)


def _parse_worker_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_WORKER_RESULT_MARKER = "AUTOMATION_WORKER_RESULT "


def _latest_worker_activity(supervisor: WorkerSupervisor) -> dict[str, Any]:
    """Summarize what the worker is actually doing right now from its log tail.

    ``status()`` only reports process liveness, so a worker that is alive but
    stuck (e.g. waiting for the automation browser) still shows as 运行中. This
    reads the most recent ``AUTOMATION_WORKER_RESULT`` line so the UI can explain
    the real activity and surface an actionable reason.
    """

    try:
        lines = supervisor.logs(tail=40).get("lines") or []
    except Exception:
        return {}
    latest: dict[str, Any] | None = None
    latest_at = ""
    for line in lines:
        if not line.startswith(_WORKER_RESULT_MARKER):
            continue
        try:
            payload = json.loads(line[len(_WORKER_RESULT_MARKER):])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        payload_at = str(payload.get("at") or "").strip()
        if latest is not None and payload_at < latest_at:
            continue
        latest = payload
        latest_at = payload_at
    if latest is not None:
        payload = latest
        activity = str(payload.get("status") or "").strip()
        return {
            "activity": activity,
            "activity_error": str(payload.get("error") or "").strip(),
            "activity_job_id": str(payload.get("job_id") or "").strip(),
            "activity_at": str(payload.get("at") or "").strip(),
        }
    return {}


# ---------------------------------------------------------------------------
# 多客户端并发：GET 响应短缓存
#
# 多个操作员/多台电脑同时打开页面时，首屏和轮询会同时请求同一批接口。
# 服务端的 JSON 序列化 + gzip 是每次请求都重做的；这里把成功的 GET 响应
# （未压缩字节 + 已压缩字节）缓存 1 秒左右，让同一瞬间的相同请求只做一次。
#
# 正确性：
# - 任何写请求（POST/PUT/DELETE）开始和结束时清空缓存；
# - storage.save_store 写盘后通过 change listener 立刻清空（后台 Worker 的
#   任务进度/心跳也不会让页面看到过期状态）；
# - 写入期间开始的 GET 用 epoch 判断，不会把旧响应写回缓存。
# ---------------------------------------------------------------------------
_GET_CACHE_LOCK = threading.Lock()
_GET_CACHE: "OrderedDict[str, tuple[float, bytes, bytes | None]]" = OrderedDict()
_GET_CACHE_EPOCH = 0
_GET_CACHE_MAX_ENTRY_BYTES = 12 * 1024 * 1024
_GET_CACHE_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_GET_CACHE_MAX_ENTRIES = 64
# 这些 GET 响应不能走 1 秒缓存：
# - /api/nas/browse 的 from_cache 字段本身就是“服务端目录缓存是否命中”的语义，
#   外层再缓存会让第二次请求看起来仍是冷扫描；
# - /api/mabang-page-imports/active 是 Worker 让路预检，晚 1 秒可能让 Worker
#   在页面导入期间重启采集浏览器。
_GET_CACHE_EXCLUDED_PATHS = frozenset({
    "/api/nas/browse",
    "/api/mabang-page-imports/active",
})


def _get_cache_epoch() -> int:
    with _GET_CACHE_LOCK:
        return _GET_CACHE_EPOCH


def _invalidate_get_response_cache() -> None:
    global _GET_CACHE_EPOCH
    with _GET_CACHE_LOCK:
        _GET_CACHE_EPOCH += 1
        _GET_CACHE.clear()


def _get_cached_response(path: str) -> tuple[bytes, bytes | None] | None:
    if settings.API_GET_CACHE_TTL_SECONDS <= 0:
        return None
    now = time.monotonic()
    with _GET_CACHE_LOCK:
        entry = _GET_CACHE.get(path)
        if entry is None:
            return None
        stored_at, plain, compressed = entry
        if now - stored_at > settings.API_GET_CACHE_TTL_SECONDS:
            _GET_CACHE.pop(path, None)
            return None
        _GET_CACHE.move_to_end(path)
        return plain, compressed


def _store_cached_response(path: str, plain: bytes, compressed: bytes | None, epoch: int) -> None:
    if settings.API_GET_CACHE_TTL_SECONDS <= 0 or len(plain) > _GET_CACHE_MAX_ENTRY_BYTES:
        return
    with _GET_CACHE_LOCK:
        # 序列化期间发生过写入：这份响应可能是旧的，不允许回填。
        if epoch != _GET_CACHE_EPOCH:
            return
        _GET_CACHE[path] = (time.monotonic(), plain, compressed)
        _GET_CACHE.move_to_end(path)
        total = sum(len(item[1]) + (len(item[2]) if item[2] else 0) for item in _GET_CACHE.values())
        while _GET_CACHE and (
            len(_GET_CACHE) > _GET_CACHE_MAX_ENTRIES or total > _GET_CACHE_MAX_TOTAL_BYTES
        ):
            _, (_, old_plain, old_compressed) = _GET_CACHE.popitem(last=False)
            total -= len(old_plain) + (len(old_compressed) if old_compressed else 0)


storage.register_change_listener(_invalidate_get_response_cache)


# ---------------------------------------------------------------------------
# GitHub 版本发布 / 在线更新
#
# 状态读取、检查更新、安装暂存、发布任务都放在这里；真正的文件替换与 API 重启由
# scripts/apply_update.py 在 API 进程退出后执行（避免运行中替换自己的代码）。
# ---------------------------------------------------------------------------
_RELEASE_JOB_LOCK = threading.Lock()
_UPDATE_EXIT_LOCK = threading.Lock()
_UPDATE_EXIT_SCHEDULED = False
_RELEASE_JOB_LOG_LIMIT = 300
MAX_RELEASE_NOTES_BYTES = 60_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _updates_dir() -> Path:
    path = settings.STORAGE_DIR / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _update_settings() -> dict[str, Any]:
    config = storage.get_runtime_configuration().get("updates") or {}
    return config if isinstance(config, dict) else {}


def _github_config() -> "github_updates.GitHubConfig":
    config = _update_settings()
    token = storage.get_github_update_token()
    return github_updates.GitHubConfig.from_runtime({
        "updates": {**config, "token": token},
    })


def _read_update_check_cache() -> dict[str, Any]:
    path = _updates_dir() / "check.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_update_check_cache(payload: dict[str, Any]) -> None:
    path = _updates_dir() / "check.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _update_status_payload(
    *,
    latest: dict[str, Any] | None = None,
    checked_at: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    config = _update_settings()
    try:
        github_config = _github_config()
    except Exception:  # noqa: BLE001 - 配置坏值不应该让状态接口 500
        github_config = github_updates.GitHubConfig(owner="", repo="")
    current = github_updates.read_local_version()
    cache = _read_update_check_cache()
    if latest is None:
        candidate = cache.get("latest")
        latest = candidate if isinstance(candidate, dict) else None
    if checked_at is None:
        checked_at = str(config.get("last_checked_at") or cache.get("checked_at") or "")
    if error is None:
        error = str(config.get("last_check_error") or cache.get("error") or "")
    update_available = False
    if latest and str(latest.get("version") or ""):
        try:
            update_available = github_updates.is_newer(str(latest["version"]), current)
        except github_updates.UpdateError:
            update_available = False
    try:
        state = github_updates.read_update_state(_updates_dir()) or {}
    except OSError:
        state = {}
    try:
        pending = github_updates.read_pending(_updates_dir())
    except OSError:
        pending = None
    pending_view: dict[str, Any] | None = None
    # 兼容旧更新器：安装已成功/已回滚的版本不该继续显示成「待安装」。
    if isinstance(state.get("status"), str) and state.get("status") in {"success", "rolled_back"}:
        if isinstance(pending, dict) and str(pending.get("version") or "") == str(state.get("version") or ""):
            pending = None
    if isinstance(pending, dict):
        pending_view = {
            "version": str(pending.get("version") or ""),
            "staged_at": str(pending.get("staged_at") or ""),
            "package_sha256": str(pending.get("package_sha256") or ""),
            "checksum_verified": pending.get("checksum_verified") is True,
            "restart": pending.get("restart") is not False,
        }
    last_update: dict[str, Any] | None = None
    if state.get("status") in {"success", "failed", "rolled_back"}:
        last_update = {
            "status": str(state.get("status") or ""),
            "version": str(state.get("version") or ""),
            "applied_at": str(state.get("applied_at") or ""),
            "error": str(state.get("error") or ""),
        }
    return {
        "current_version": current,
        "configured": github_config.configured,
        # 未配置时 owner/repo 都是空的，slug 会拼成 "/"；契约里未配置应返回空串。
        "repo": github_config.slug if github_config.configured else "",
        "api_url": github_config.api_url,
        "token_configured": bool(config.get("token_configured")),
        "include_prerelease": config.get("include_prerelease") is True,
        "auto_check": config.get("auto_check") is not False,
        "update_available": update_available,
        "latest": latest,
        "checked_at": checked_at,
        "error": error,
        "pending": pending_view,
        "last_update": last_update,
        "restart_required": bool(pending_view and not pending_view.get("restart")),
        "supported": True,
    }


def _perform_update_check() -> dict[str, Any]:
    config = _update_settings()
    current = github_updates.read_local_version()
    try:
        github_config = _github_config()
    except Exception as exc:  # noqa: BLE001
        return _update_status_payload(error=str(exc))
    if not github_config.configured:
        error = "请先在「版本更新」里填写 GitHub 仓库（owner/name）"
        checked_at = _now_iso()
        _write_update_check_cache({"repo": "", "latest": None, "checked_at": checked_at, "error": error})
        return _update_status_payload(latest=None, checked_at=checked_at, error=error)
    result = github_updates.check_for_update(
        github_config,
        current,
        include_prerelease=config.get("include_prerelease") is True,
    )
    latest = result.get("latest") if isinstance(result.get("latest"), dict) else None
    checked_at = str(result.get("checked_at") or _now_iso())
    error = str(result.get("error") or "")
    _write_update_check_cache({
        "repo": github_config.slug,
        "latest": latest,
        "checked_at": checked_at,
        "error": error,
    })
    return _update_status_payload(latest=latest, checked_at=checked_at, error=error)


def _stage_update_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        github_config = _github_config()
    except Exception as exc:  # noqa: BLE001
        raise ApiRequestError(str(exc), 422) from exc
    if not github_config.configured:
        raise ApiRequestError("请先在「版本更新」里填写 GitHub 仓库（owner/name）", 422)
    config = _update_settings()
    requested = str(payload.get("version") or "").strip()
    if requested:
        try:
            requested = github_updates.normalize_version(requested)
        except github_updates.UpdateError as exc:
            raise ApiRequestError(str(exc), 422) from exc
    cache = _read_update_check_cache()
    cached_latest = cache.get("latest") if isinstance(cache.get("latest"), dict) else None
    release: dict[str, Any] | None = None
    if requested and cached_latest and str(cached_latest.get("version") or "") == requested:
        release = cached_latest
    elif requested:
        try:
            release = github_updates.GitHubClient(github_config).get_release(f"v{requested}").to_dict()
        except github_updates.UpdateError as exc:
            raise ApiRequestError(str(exc), 404) from exc
    else:
        result = github_updates.check_for_update(
            github_config,
            github_updates.read_local_version(),
            include_prerelease=config.get("include_prerelease") is True,
        )
        if not result.get("ok"):
            raise ApiRequestError(str(result.get("error") or "检查更新失败"), 502)
        latest = result.get("latest")
        release = latest if isinstance(latest, dict) else None
    if not release:
        raise ApiRequestError("没有可用的 Release 安装包", 404)
    target_version = str(release.get("version") or "")
    current = github_updates.read_local_version()
    if not requested:
        try:
            if not github_updates.is_newer(target_version, current):
                raise ApiRequestError("已经是最新版本", 409)
        except github_updates.UpdateError as exc:
            raise ApiRequestError(str(exc), 422) from exc
    try:
        pending = github_updates.stage_update(
            config=github_config,
            release=release,
            project_root=settings.PROJECT_ROOT,
            updates_dir=_updates_dir(),
        )
    except github_updates.UpdateError as exc:
        raise ApiRequestError(str(exc), 502) from exc
    restart = payload.get("restart", True) is not False
    result_payload: dict[str, Any] = {
        "status": "staged",
        "version": str(pending.get("version") or target_version),
        "package_sha256": str(pending.get("package_sha256") or ""),
        "checksum_verified": pending.get("checksum_verified") is True,
        "staged_at": str(pending.get("staged_at") or ""),
        "restart_scheduled": False,
        "message": "安装包已下载并校验，重启 API 后由更新器安装",
    }
    if restart:
        try:
            github_updates.spawn_update_runner(
                project_root=settings.PROJECT_ROOT,
                pending_path=_updates_dir() / "pending.json",
                log_dir=_updates_dir() / "logs",
                api_pid=os.getpid(),
            )
        except Exception as exc:  # noqa: BLE001
            # 更新器没起来：清掉 pending，避免 UI 永远卡在「待安装」而无法重试。
            try:
                pending_path = _updates_dir() / "pending.json"
                if pending_path.is_file():
                    pending_path.unlink()
            except OSError:
                pass
            raise ApiRequestError(f"启动更新器失败：{exc}", 500) from exc
        _schedule_api_exit_for_update()
        result_payload["restart_scheduled"] = True
        result_payload["message"] = "正在重启并安装更新，页面稍后自动刷新"
    return result_payload


def _stop_workers_for_update() -> None:
    try:
        multi = _MULTI_WORKER_SUPERVISOR
        if multi is not None:
            multi.run("stop")
    except Exception:  # noqa: BLE001 - 退出前尽量停干净，失败不阻止更新
        pass
    try:
        supervisor = _WORKER_SUPERVISOR
        if supervisor is not None:
            supervisor.stop()
    except Exception:  # noqa: BLE001
        pass


def _schedule_api_exit_for_update(delay: float = 1.2) -> None:
    global _UPDATE_EXIT_SCHEDULED
    with _UPDATE_EXIT_LOCK:
        if _UPDATE_EXIT_SCHEDULED:
            return
        _UPDATE_EXIT_SCHEDULED = True

    def _run() -> None:
        time.sleep(max(0.2, float(delay)))
        _stop_workers_for_update()
        try:
            server, _host, _port = _active_server_snapshot()
            if server is not None:
                server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        # 给响应与连接关闭留一点时间，然后强制退出：更新器在等这个进程结束。
        time.sleep(2.0)
        os._exit(0)

    threading.Thread(target=_run, name="api-exit-for-update", daemon=True).start()


def _release_jobs_dir() -> Path:
    path = _updates_dir() / "release_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_release_job(job: dict[str, Any]) -> None:
    path = _release_jobs_dir() / f"{job.get('id')}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_release_job(job_id: str | None = None) -> dict[str, Any] | None:
    root = _release_jobs_dir()
    if job_id:
        path = root / f"{job_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    candidates = sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _append_release_job_log(job: dict[str, Any], line: str) -> None:
    with _RELEASE_JOB_LOCK:
        log = job.setdefault("log", [])
        if not isinstance(log, list):
            log = job["log"] = []
        log.append(str(line))
        del log[:-_RELEASE_JOB_LOG_LIMIT]
    _write_release_job(job)


def _run_release_job(job: dict[str, Any]) -> None:
    def log(line: str) -> None:
        _append_release_job_log(job, line)

    try:
        result = github_updates.run_release(
            project_root=settings.PROJECT_ROOT,
            config=_github_config(),
            updates_dir=_updates_dir(),
            version=job.get("version") or None,
            bump=job.get("bump") or None,
            notes=str(job.get("notes") or ""),
            prerelease=job.get("prerelease") is True,
            dry_run=job.get("dry_run") is True,
            push=job.get("push") is not False,
            log=log,
        )
        job["status"] = "success"
        job["result"] = result
        job["version"] = str(result.get("version") or job.get("version") or "")
    except Exception as exc:  # noqa: BLE001 - 任务失败要落盘给 UI 看
        job["status"] = "failed"
        job["error"] = str(exc)
        _append_release_job_log(job, f"失败：{exc}")
    finally:
        job["finished_at"] = _now_iso()
        _write_release_job(job)


def _start_release_job(payload: dict[str, Any]) -> dict[str, Any]:
    version = str(payload.get("version") or "").strip()
    bump = str(payload.get("bump") or "").strip().lower()
    if version:
        try:
            version = github_updates.normalize_version(version)
        except github_updates.UpdateError as exc:
            raise ApiRequestError(str(exc), 422) from exc
    if not version and bump not in {"patch", "minor", "major"}:
        raise ApiRequestError("请填写版本号，或选择补丁/次版本/主版本递增", 422)
    notes = str(payload.get("notes") or "")
    if len(notes.encode("utf-8")) > MAX_RELEASE_NOTES_BYTES:
        raise ApiRequestError("发布说明过长（最多 60 KB）", 422)
    dry_run = payload.get("dry_run") is True
    if not dry_run:
        github_config = _github_config()
        if not github_config.configured:
            raise ApiRequestError("发布前请先配置 GitHub 仓库与 Token", 422)
        if not github_config.token:
            raise ApiRequestError("发布前请先配置 GitHub Token", 422)
    job = {
        "id": str(uuid.uuid4()),
        "status": "running",
        "version": version,
        "bump": "" if version else bump,
        "notes": notes,
        "prerelease": payload.get("prerelease") is True,
        "dry_run": dry_run,
        "push": payload.get("push") is not False,
        "started_at": _now_iso(),
        "finished_at": "",
        "error": "",
        "result": None,
        "log": [],
    }
    _write_release_job(job)
    threading.Thread(target=_run_release_job, args=(job,), name=f"release-{job['id'][:8]}", daemon=True).start()
    return job


def _auto_check_updates_background() -> None:
    def _run() -> None:
        try:
            config = _update_settings()
            if config.get("auto_check") is False:
                return
            github_config = _github_config()
            if not github_config.configured:
                return
            result = github_updates.check_for_update(
                github_config,
                github_updates.read_local_version(),
                include_prerelease=config.get("include_prerelease") is True,
            )
            latest = result.get("latest") if isinstance(result.get("latest"), dict) else None
            _write_update_check_cache({
                "repo": github_config.slug,
                "latest": latest,
                "checked_at": str(result.get("checked_at") or _now_iso()),
                "error": str(result.get("error") or ""),
            })
        except Exception:  # noqa: BLE001 - 后台检查失败不打扰启动
            pass

    threading.Thread(target=_run, name="update-auto-check", daemon=True).start()


class ApiRequestError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "AIListingMVP/0.1"
    # HTTP/1.1 连接复用：多台电脑同时轮询时不必每个请求重新握手。
    protocol_version = settings.API_HTTP_PROTOCOL
    # 空闲的 keep-alive 连接最多占一个线程这么久，避免浏览器异常断开后线程堆积。
    timeout = settings.API_KEEPALIVE_TIMEOUT_SECONDS
    disable_nagle_algorithm = True
    _MABANG_IMPORT_COMPLETE_PATH = re.compile(
        r"^/api/mabang-page-imports/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/complete$"
    )

    def _request_origin(self) -> str:
        return str(self.headers.get("Origin") or "").strip()

    def _is_same_origin(self, origin: str) -> bool:
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.netloc.lower() == str(self.headers.get("Host") or "").lower()

    @staticmethod
    def _is_mabang_origin(origin: str) -> bool:
        parsed = urlparse(origin)
        hostname = str(parsed.hostname or "").lower()
        return parsed.scheme == "https" and (hostname == "mabangerp.com" or hostname.endswith(".mabangerp.com"))

    def _cross_origin_write_allowed(self, method: str) -> bool:
        origin = self._request_origin()
        if not origin or self._is_same_origin(origin):
            return True
        path = urlparse(self.path).path
        return (
            method == "POST"
            and self._is_mabang_origin(origin)
            and self._MABANG_IMPORT_COMPLETE_PATH.fullmatch(path) is not None
        )

    def _enforce_write_origin(self, method: str) -> None:
        if not self._cross_origin_write_allowed(method):
            raise ApiRequestError("Cross-origin write is not allowed", status=403)

    def _allowed_cors_origin(self) -> str:
        origin = self._request_origin()
        if not origin or self._is_same_origin(origin):
            return ""
        if self._cross_origin_write_allowed(self.command):
            return origin
        return ""

    def _log_unhandled(self, method: str, exc: Exception) -> None:
        # 记录请求方法/路径 + 完整堆栈，便于定位偶发 500（否则只有一句错误信息）。
        sys.stderr.write(
            f"Unhandled API error [{method} {self.path}]: {exc!r}\n"
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )

    def do_GET(self) -> None:
        try:
            self._handle_get()
        except ApiRequestError as exc:
            self._json({"error": str(exc)}, status=exc.status)
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._log_unhandled("GET", exc)
            self._json({"error": "Internal server error"}, status=500)

    def do_POST(self) -> None:
        # 写请求可能没有读完整个请求体（例如参数校验提前失败）。HTTP/1.1 下
        # 未读完的 body 会被当成下一个请求，因此写请求一律在响应后关闭连接。
        self.close_connection = True
        _invalidate_get_response_cache()
        try:
            self._enforce_write_origin("POST")
            self._handle_post()
        except ApiRequestError as exc:
            self._json({"error": str(exc)}, status=exc.status)
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._log_unhandled("POST", exc)
            self._json({"error": "Internal server error"}, status=500)
        finally:
            _invalidate_get_response_cache()

    def do_PUT(self) -> None:
        self.close_connection = True
        _invalidate_get_response_cache()
        try:
            self._enforce_write_origin("PUT")
            self._handle_put()
        except ApiRequestError as exc:
            self._json({"error": str(exc)}, status=exc.status)
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._log_unhandled("PUT", exc)
            self._json({"error": "Internal server error"}, status=500)
        finally:
            _invalidate_get_response_cache()

    def do_DELETE(self) -> None:
        self.close_connection = True
        _invalidate_get_response_cache()
        try:
            self._enforce_write_origin("DELETE")
            self._handle_delete()
        except ApiRequestError as exc:
            self._json({"error": str(exc)}, status=exc.status)
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._log_unhandled("DELETE", exc)
            self._json({"error": "Internal server error"}, status=500)
        finally:
            _invalidate_get_response_cache()

    def do_OPTIONS(self) -> None:
        requested_method = str(self.headers.get("Access-Control-Request-Method") or "").upper()
        if requested_method not in {"POST", "PUT", "DELETE"} or not self._cross_origin_write_allowed(requested_method):
            self._json({"error": "Cross-origin write is not allowed"}, status=403)
            return
        origin = self._request_origin()
        self.send_response(204)
        if origin and not self._is_same_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", requested_method)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "3600")
        self.send_header("Content-Length", "0")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            self._json({
                "ok": True,
                "service": "ai-listing-system",
                "mode": "m1",
                "version": github_updates.read_local_version(),
                "storage": storage.storage_backend_info(),
            })
            return

        if path == "/api/updates/status":
            self._json(_update_status_payload())
            return

        if path == "/api/releases/publish/status":
            job_id = (query.get("job_id") or [""])[0].strip()
            self._json({"job": _read_release_job(job_id or None)})
            return

        if path == "/api/browser-extension.zip":
            self._serve_browser_extension_zip()
            return

        if path == "/api/project-plan":
            self._json(project_plan.get_project_plan())
            return

        if path.startswith("/api/base-products/") and path.endswith("/prices"):
            parts = path.strip("/").split("/")
            base_product_id = parts[2] if len(parts) == 4 else ""
            product = storage.get_item("base_products", base_product_id)
            if not product:
                raise ApiRequestError("Base product not found", 404)
            self._json({"tiers": product.get("price_tiers") or []})
            return
        if path == "/api/deployment/readiness":
            self._json(storage.deployment_readiness())
            return

        if path == "/api/assets":
            assets = storage.list_items("assets")
            assets = self._filter_assets(assets, query)
            # 先排队再回响应：入队只是提交一个后台任务（不阻塞、不读盘），但保证
            # 客户端拿到列表时母版预热已经开始 —— 第一次打开列表就不用每张等约 400ms。
            # 顺序：商品/副本列表真正引用的主图最优先，其余按最新在前。
            priority = set(storage.priority_thumbnail_asset_ids())
            ordered = [item for item in assets if str(item.get("id") or "") in priority]
            ordered += [item for item in reversed(assets) if str(item.get("id") or "") not in priority]
            asset_thumbnails.schedule_warm_masters(ordered)
            self._json({"items": assets, "count": len(assets)})
            return

        if path.startswith("/api/assets/by-group/"):
            group_id = path.rsplit("/", 1)[-1]
            assets = storage.assets_for_group(group_id)
            self._json({"items": assets, "count": len(assets)})
            return

        if path.startswith("/api/assets/") and path.endswith("/file"):
            asset_id = path.split("/")[-2]
            self._serve_asset_file(asset_id)
            return

        if path.startswith("/api/assets/") and path.endswith("/thumb"):
            # 列表/卡片用的缩略图：长边 w 像素的 JPEG，磁盘缓存。现场素材库 3.5GB，
            # 直接加载原图一屏就是几十 MB，这里第一次生成、之后只发几十 KB。
            asset_id = path.split("/")[-2]
            query = parse_qs(parsed.query)
            edge = asset_thumbnails.normalize_edge((query.get("w") or [""])[0])
            self._serve_asset_thumbnail(asset_id, edge)
            return

        if path.startswith("/api/assets/") and path.endswith("/open"):
            asset_id = path.split("/")[-2]
            self._open_asset(asset_id, open_dir=False)
            return

        if path.startswith("/api/assets/") and path.endswith("/open-dir"):
            asset_id = path.split("/")[-2]
            self._open_asset(asset_id, open_dir=True)
            return

        if path == "/api/products":
            self._json({"items": storage.list_products_with_summary()})
            return

        if path == "/api/base-products":
            self._json({"items": storage.list_base_products_with_summary()})
            return

        if path.startswith("/api/base-products/import-tasks/"):
            task_id = path.rsplit("/", 1)[-1].strip()
            task = _nas_import_task_snapshot(task_id)
            if task is None:
                raise ApiRequestError("NAS 导入任务不存在", status=404)
            self._json(task)
            return

        if path == "/api/store-templates":
            # 模板按马帮账号隔离：传 account_id 只看该账号的模板。
            self._json({"items": storage.list_store_templates(account_id=(query.get("account_id") or [""])[0])})
            return

        if path == "/api/dashboard":
            self._json(build_dashboard(storage.load_store(), now=datetime.now(timezone.utc).isoformat()))
            return

        if path == "/api/settings":
            self._json(storage.get_runtime_configuration())
            return

        if path == "/api/settings/network":
            # refresh=1 给设置页的「刷新地址」按钮用：跳过 1 秒 GET 缓存和
            # 10 秒防火墙状态缓存，拿到网卡/规则的当前真实状态。
            if (query.get("refresh") or [""])[0].strip().lower() in {"1", "true", "yes"}:
                _invalidate_get_response_cache()
                network_access.invalidate_firewall_cache()
            config = storage.get_runtime_configuration().get("network") or {}
            _server, active_host, active_port = _active_server_snapshot()
            self._json(
                network_access.build_network_status(
                    config,
                    active_bind_host=active_host,
                    active_port=active_port,
                )
            )
            return

        if path == "/api/settings/storage/usage":
            config = storage.get_runtime_configuration().get("storage_cleanup") or {}
            store = storage.load_store()
            self._json(
                disk_cleanup.scan_usage(
                    config,
                    active_job_ids=disk_cleanup.active_automation_job_ids(store),
                    store=store,
                )
            )
            return

        if path == "/api/settings/audit":
            try:
                limit = int((query.get("limit") or ["100"])[0])
            except ValueError as exc:
                raise ApiRequestError("limit must be an integer", 422) from exc
            self._json({"items": storage.list_runtime_configuration_audit(limit)})
            return

        if path == "/api/worker/status":
            status = _worker_on_demand_status()
            multi = _worker_supervisors()
            if multi is not None:
                payload = _attach_account_worker_activities(multi, multi.summarize())
                payload.update(status)
                self._json(payload)
                return
            supervisor = _worker_supervisor()
            single = supervisor.status()
            single.update(_latest_worker_activity(supervisor))
            single.update(status)
            self._json(single)
            return

        if path == "/api/worker/workers":
            multi = _worker_supervisors()
            self._json({"items": multi.statuses() if multi is not None else []})
            return

        if path == "/api/store-collection/runs":
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError as exc:
                raise ApiRequestError("limit must be an integer", 422) from exc
            self._json(
                {
                    "items": storage.list_store_collection_runs(
                        limit=limit,
                        account_id=(query.get("account_id") or [""])[0],
                        store_id=(query.get("store_id") or [""])[0],
                    )
                }
            )
            return

        if path == "/api/worker/logs":
            try:
                tail = int((query.get("tail") or ["100"])[0])
            except ValueError as exc:
                raise ApiRequestError("tail must be an integer", 422) from exc
            wanted_tail = max(0, min(tail, 1000))
            multi = _worker_supervisors()
            if multi is not None:
                account_id = (query.get("account_id") or [""])[0].strip()
                raw_slot = (query.get("slot") or [""])[0].strip()
                try:
                    slot = max(0, int(raw_slot)) if raw_slot else 0
                except ValueError:
                    slot = 0
                if not account_id:
                    targets = multi.targets()
                    account_id = str((targets[0] if targets else {}).get("account_id") or "")
                    slot = int((targets[0] if targets else {}).get("slot") or 0)
                self._json(multi.logs(account_id, slot=slot, tail=wanted_tail))
                return
            self._json(_worker_supervisor().logs(tail=wanted_tail))
            return

        if path == "/api/ai/model-connections":
            self._json({"items": storage.list_ai_model_connections()})
            return

        if path == "/api/description-profiles":
            self._json({"items": storage.list_description_profiles()})
            return

        if path == "/api/title-skills":
            self._json({"items": storage.list_title_skills()})
            return

        if path == "/api/hot-words":
            self._json({"items": storage.list_hot_words()})
            return

        if path == "/api/category-profiles/for-category":
            category_id = (query.get("category_id") or [""])[0].strip()
            platform = (query.get("platform") or ["shopee"])[0].strip().lower() or "shopee"
            country = (query.get("country") or [""])[0].strip().upper()
            if not category_id:
                raise ApiRequestError("category_id is required", 400)
            profile_id, version_id, snapshot = storage._profile_binding_for_category(
                category_id, storage.load_store(), platform=platform, country=country or None
            )
            if not profile_id:
                self._json({"found": False})
                return
            self._json({
                "found": True,
                "profile_id": profile_id,
                "version_id": version_id,
                "fields": snapshot.get("fields") or [],
            })
            return

        if path.startswith("/api/uploads/default-sku-image/"):
            resolved = resolve_image_upload(
                path.rsplit("/", 1)[-1], storage_dir=settings.STORAGE_DIR, kind="default_sku"
            )
            if not resolved:
                self.send_error(404, "Default SKU image not found")
                return
            file_path, content_type = resolved
            self._send_file_bytes(file_path, content_type)
            return

        if path.startswith("/api/uploads/size-chart/"):
            chart_id = path.rsplit("/", 1)[-1]
            resolved = resolve_size_chart(chart_id, storage_dir=settings.STORAGE_DIR)
            if not resolved:
                self.send_error(404, "Size chart not found")
                return
            file_path, content_type = resolved
            # ?w= 只要预览小图：现场一张尺码表原图 1.1MB，列表里却只显示 72px。
            # 库里存的仍是原始地址（前端只在渲染时挂参数），生成失败就退回原图。
            raw_edge = str((query.get("w") or [""])[0]).strip()
            if raw_edge:
                thumb_path = asset_thumbnails.ensure_thumbnail(
                    file_path,
                    asset_thumbnails.normalize_edge(raw_edge),
                    key=f"size-chart-{chart_id}",
                )
                if thumb_path is not None:
                    self._send_file_bytes(thumb_path, "image/jpeg")
                    return
            self._send_file_bytes(file_path, content_type)
            return

        if path == "/api/store-promotions":
            store_id = (query.get("store_id") or [""])[0].strip()
            if not store_id:
                raise ApiRequestError("store_id is required", 422)
            items = storage.list_store_promotions(
                store_id,
                available_only=_truthy((query.get("available_only") or ["false"])[0]),
            )
            self._json({"items": items, "count": len(items)})
            return

        if path == "/api/store-watermarks":
            store_id = str(query.get("store_id", [""])[0]).strip()
            if not store_id:
                raise ApiRequestError("store_id is required", 400)
            self._json({
                "items": storage.list_store_watermarks(store_id),
                # 界面要能显示「目录 N 条 · 采集于 …」/「上次采集失败：…」，没有记录时为 null。
                "status": storage.get_store_watermark_catalog_status(store_id),
            })
            return

        if path == "/api/nas/browse":
            target = str(query.get("path", [""])[0])
            refresh = str(query.get("refresh", [""])[0]).strip().lower() in {"1", "true", "yes"}
            shallow = str(query.get("shallow", [""])[0]).strip().lower() in {"1", "true", "yes"}
            self._json(self._browse_nas_directory(target, refresh=refresh, shallow=shallow))
            return

        if path == "/api/store-size-chart-templates":
            store_id = str(query.get("store_id", [""])[0]).strip()
            if not store_id:
                raise ApiRequestError("store_id is required", 400)
            self._json({"items": storage.list_store_size_chart_templates(store_id)})
            return

        if path == "/api/mabang-page-imports/active":
            # Worker 预检用：该账号（该槽位）是否正在做「从马帮当前页导入」。导入期间
            # 采集浏览器是操作员的可见窗口，那个槽位的 Worker 必须让路（不重启浏览器、
            # 不领任务）。页面导入固定落在 1 号槽位；不传 slot 时按账号整体判断（老调用方）。
            account_id = str((query.get("account_id") or [""])[0]).strip()
            raw_slot = (query.get("slot") or [""])[0].strip()
            slot = None
            if raw_slot:
                try:
                    slot = max(0, int(raw_slot))
                except ValueError:
                    slot = None
            session = storage.active_mabang_page_import(account_id, slot=slot)
            self._json({"active": session is not None, "run": session})
            return

        if path.startswith("/api/mabang-page-imports/"):
            run_id = path.rsplit("/", 1)[-1]
            run = storage.get_mabang_page_import(run_id)
            if not run:
                self._json({"error": "Mabang page import not found"}, status=404)
                return
            self._json(run)
            return

        if path == "/api/mabang/accounts":
            self._json(
                {
                    "items": storage.list_mabang_accounts(),
                    "default_account_id": storage.default_mabang_account_id(),
                }
            )
            return

        if path == "/api/mabang/store-options":
            platform = (query.get("platform") or [""])[0]
            account_id = (query.get("account_id") or [""])[0]
            self._json({"items": storage.list_mabang_store_options(platform, account_id)})
            return

        if path == "/api/store-listing-copies":
            self._json({"items": storage.list_store_listing_copies()})
            return

        if path.startswith("/api/store-listing-copies/"):
            if path.endswith("/video/file"):
                parts = path.strip("/").split("/")
                listing_copy = storage.get_item("store_listing_copies", parts[2] if len(parts) == 5 else "")
                video_path = Path(str((listing_copy or {}).get("video", {}).get("path") or ""))
                if not listing_copy or not video_path.is_file() or video_path.suffix.lower() != ".mp4":
                    raise ApiRequestError("Listing video not found", 404)
                self._send_file_bytes(video_path, "video/mp4")
                return
            listing_copy_id = path.rsplit("/", 1)[-1]
            detail = storage.get_store_listing_copy_detail(listing_copy_id)
            if not detail:
                self._json({"error": "Store listing copy not found"}, status=404)
                return
            self._json(detail)
            return

        if path.startswith("/api/base-products/") and path.endswith("/video/file"):
            parts = path.strip("/").split("/")
            base_product = storage.get_item("base_products", parts[2] if len(parts) == 5 else "")
            video_path = Path(str((base_product or {}).get("video", {}).get("path") or ""))
            if not base_product or not video_path.is_file() or video_path.suffix.lower() != ".mp4":
                raise ApiRequestError("Base product video not found", 404)
            self._send_file_bytes(video_path, "video/mp4")
            return

        if path == "/api/automation/jobs":
            self._json({"items": storage.list_automation_jobs()})
            return

        if path == "/api/automation/jobs/next-account":
            # Worker 在领取前先问「下一个 ready 任务属于哪个马帮账号」，据此准备
            # 该账号自己的浏览器与登录；准备不好就不领取，任务留在 ready。
            account_id = storage.next_ready_automation_account()
            self._json(
                {
                    "account_id": account_id,
                    "runtime": storage.resolve_mabang_account_runtime(account_id or None),
                }
            )
            return

        if path.startswith("/api/automation/jobs/") and path.endswith("/payload"):
            job_id = path.split("/")[-2]
            payload = storage.build_automation_payload(job_id)
            if not payload.get("ok"):
                self._json(payload, status=404)
                return
            self._json(payload)
            return

        if path == "/api/mabang/template":
            self._serve_mabang_template()
            return

        if path == "/api/mabang/export/preview":
            product_ids = self._extract_product_ids(query)
            bundles = collect_mabang_bundles(product_ids)
            self._json(self._mabang_preview_payload(bundles))
            return

        if path == "/api/products/name-cleanup/preview":
            group_id = (query.get("group_id") or [""])[0]
            result = storage.preview_product_name_cleanup(group_id)
            self._json(result)
            return

        if path == "/api/product-templates":
            self._json({"items": storage.list_product_templates()})
            return

        if path == "/api/category-profiles":
            self._json({"items": storage.list_category_profiles()})
            return

        if path == "/api/category-quick-templates":
            self._json({"items": storage.list_category_quick_templates()})
            return

        if path == "/api/marketplace-categories/merged":
            try:
                limit = int((query.get("limit") or ["500"])[0])
            except ValueError as exc:
                raise ApiRequestError("limit must be an integer", 422) from exc
            items = storage.list_merged_marketplace_categories(
                query=(query.get("q") or [""])[0],
                limit=limit,
            )
            self._json({"items": items, "count": len(items)})
            return

        if path == "/api/marketplace-categories/children":
            try:
                limit = int((query.get("limit") or ["500"])[0])
            except ValueError as exc:
                raise ApiRequestError("limit must be an integer", 422) from exc
            items = storage.list_merged_category_children(
                parent_id=(query.get("parent_id") or [""])[0],
                query=(query.get("q") or [""])[0],
                limit=limit,
            )
            self._json({"items": items, "count": len(items)})
            return

        if path == "/api/marketplace-categories":
            platform = (query.get("platform") or [""])[0].strip()
            country = (query.get("country") or [""])[0].strip()
            store_id = (query.get("store_id") or [""])[0].strip()
            if not platform or not country or not store_id:
                raise ApiRequestError("platform, country and store_id are required", 422)
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError as exc:
                raise ApiRequestError("limit must be an integer", 422) from exc
            items = storage.list_marketplace_categories(
                platform=platform,
                country=country,
                store_id=store_id,
                query=(query.get("q") or [""])[0],
                parent_id=(query.get("parent_id") or [None])[0],
                leaf_only=_truthy((query.get("leaf_only") or ["true"])[0]),
                limit=limit,
            )
            self._json({"items": items, "count": len(items)})
            return

        if path.startswith("/api/marketplace-category-catalog-runs/"):
            run_id = path.rsplit("/", 1)[-1]
            run = storage.get_marketplace_category_catalog_run(run_id)
            if not run:
                self._json({"error": "Marketplace category catalog run not found"}, status=404)
                return
            self._json(run)
            return

        if path.startswith("/api/category-profiles/") and path.endswith("/versions"):
            profile_id = path.split("/")[3]
            self._json({"items": storage.list_category_profile_versions(profile_id)})
            return

        if path.startswith("/api/category-profile-collection-runs/"):
            run_id = path.rsplit("/", 1)[-1]
            run = storage.get_category_profile_collection_run(run_id)
            if not run:
                self._json({"error": "Category collection run not found"}, status=404)
                return
            self._json(run)
            return

        if path == "/api/products/dedup/preview":
            group_id = (query.get("group_id") or [""])[0]
            self._json(storage.preview_product_dedup(group_id))
            return

        if path.startswith("/api/products/") and path.endswith("/ai/outputs"):
            product_id = path.split("/")[-3]
            product = storage.get_item("products", product_id)
            if not product:
                self._json({"error": "Product not found"}, status=404)
                return
            output_type = (query.get("output_type") or [""])[0]
            self._json({"items": storage.list_ai_outputs_for_product(product_id, output_type)})
            return

        if path.startswith("/api/products/"):
            product_id = path.rsplit("/", 1)[-1]
            product = storage.get_item("products", product_id)
            if not product:
                self._json({"error": "Product not found"}, status=404)
                return
            self._json({
                "item": product,
                "assets": storage.assets_for_base_product(product),
                "skus": storage.list_skus(product_id),
                "prices": storage.list_prices_for_product(product_id),
                "readiness": storage.check_product_readiness(product_id),
            })
            return

        self._serve_static(path)

    def _open_asset(self, asset_id: str, open_dir: bool) -> None:
        asset = storage.get_asset(asset_id)
        if not asset:
            self._json({"ok": False, "error": "Asset not found"}, status=404)
            return
        file_path = Path(asset.get("file_path") or "")
        if not file_path.exists():
            self._json({"ok": False, "error": "Asset path not found"}, status=404)
            return
        try:
            if open_dir:
                target = asset.get("group_root") or str(file_path.parent)
                subprocess.Popen(["explorer", str(target)])
            else:
                subprocess.Popen(["explorer", "/select,", str(file_path)])
            self._json({"ok": True})
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, status=500)

    def _send_file_bytes(self, file_path: Path, content_type: str) -> None:
        """发文件时带 ETag 并允许重新校验。

        现场问题：导入商品后列表里的图片要「刷新页面」才更新 —— 因为文件响应带了
        ``Cache-Control: private, max-age=300``，同一地址 5 分钟内浏览器根本不回服务器问，
        而列表重画用的又是同一个地址。改成 ``no-cache``（可缓存但每次校验）+ ETag：
        文件没变是 304（很便宜），文件变了立刻是 200 新内容。
        """

        try:
            stat = file_path.stat()
        except OSError:
            self.send_error(404, "File not found")
            return
        etag = f'"{int(stat.st_mtime_ns):x}-{int(stat.st_size):x}"'
        if str(self.headers.get("If-None-Match") or "").strip() == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            return
        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, no-cache")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content)

    def _serve_asset_file(self, asset_id: str) -> None:
        asset = storage.get_asset(asset_id)
        if not asset:
            self.send_error(404, "Asset not found")
            return
        file_path = Path(asset.get("file_path") or "")
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "Asset file not found")
            return
        if asset.get("asset_type") == "source":
            self.send_error(403, "Source files are not previewable")
            return
        suffix = file_path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}:
            self.send_error(415, "Unsupported asset type")
            return
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self._send_file_bytes(file_path, content_type)

    def _serve_asset_thumbnail(self, asset_id: str, edge: int) -> None:
        """列表/卡片用的小图：生成失败时退回原图，不因为缩略图坏掉就整页没图。"""

        asset = storage.get_asset(asset_id)
        if not asset:
            self.send_error(404, "Asset not found")
            return
        file_path = Path(asset.get("file_path") or "")
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "Asset file not found")
            return
        thumb_path = asset_thumbnails.ensure_thumbnail(file_path, edge, key=asset_id)
        if thumb_path is None:
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            self._send_file_bytes(file_path, content_type)
            return
        self._send_file_bytes(thumb_path, "image/jpeg")

    def _handle_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/category-quick-templates":
            payload = self._read_json()
            try:
                result = storage.save_category_quick_template(payload)
            except storage.CategoryQuickTemplateError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(result, status=201)
            return
        if path == "/api/cnsc-price-matrix/preview":
            payload = self._read_json()
            tiers = payload.get("tiers") or payload.get("price_tiers") or []
            target_sites = payload.get("target_sites") or payload.get("site_codes") or []
            if not isinstance(tiers, list) or not isinstance(target_sites, list):
                raise ApiRequestError("tiers and target_sites must be arrays", 422)
            self._json(parse_price_matrix(
                str(payload.get("text") or payload.get("tsv") or ""),
                tiers=tiers,
                target_sites=target_sites,
            ))
            return
        if path == "/api/updates/check":
            self._read_json()
            self._json(_perform_update_check())
            return
        if path == "/api/updates/apply":
            payload = self._read_json()
            self._json(_stage_update_payload(payload), status=202)
            return
        if path == "/api/releases/publish":
            payload = self._read_json()
            job = _start_release_job(payload)
            self._json({"job_id": job["id"], "status": job["status"]}, status=202)
            return
        if path == "/api/settings/network/firewall":
            self._read_json()
            _server, _host, port = _active_server_snapshot()
            result = network_access.ensure_firewall_rule(port)
            result["firewall"] = network_access.firewall_status(port)
            self._json(result)
            return
        if path == "/api/settings/storage/cleanup":
            payload = self._read_json()
            if payload.get("confirm") is not True:
                raise ApiRequestError("清理操作需要确认", 422)
            config = storage.get_runtime_configuration().get("storage_cleanup") or {}
            cdp_url = str((storage.get_runtime_configuration().get("worker") or {}).get("cdp_url") or "")
            store = storage.load_store()
            try:
                result = disk_cleanup.cleanup_categories(
                    payload.get("categories") or [],
                    config,
                    active_job_ids=disk_cleanup.active_automation_job_ids(store),
                    cdp_available=lambda: disk_cleanup.cdp_is_available(cdp_url),
                    store=store,
                )
            except disk_cleanup.BrowserInUseError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            except disk_cleanup.CleanupInProgressError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            disk_cleanup.record_cleanup_result(result)
            self._json(result)
            return
        if path == "/api/settings/storage/compact":
            payload = self._read_json()
            if payload.get("confirm") is not True:
                raise ApiRequestError("压缩操作需要确认，请确认后重试", 422)
            config = storage.get_runtime_configuration().get("storage_cleanup") or {}
            store = storage.load_store()
            result = disk_cleanup.run_asset_compaction(
                config,
                store=store,
                active_job_ids=disk_cleanup.active_automation_job_ids(store),
                now=time.time(),
            )
            if result is None:
                raise ApiRequestError("读取不到商品库，压缩未执行", 409)
            if result.get("busy"):
                raise ApiRequestError("已有压缩任务正在执行，请稍后再试", 409)
            disk_cleanup.record_compaction_result(result)
            self._json(result)
            return
        if path == "/api/local-folder-uploads":
            payload = self._read_json()
            try:
                result = LocalFolderUploads(settings.STORAGE_DIR).create(
                    payload.get("folder_name") or "",
                    payload.get("files") or [],
                )
            except LocalFolderUploadError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(result, status=201)
            return
        if path.startswith("/api/local-folder-uploads/") and path.endswith("/complete"):
            upload_id = path.strip("/").split("/")[2]
            payload = self._read_json()
            uploads = LocalFolderUploads(settings.STORAGE_DIR)
            try:
                product = uploads.complete(
                    upload_id,
                    storage.create_base_product_from_folder,
                    category=str(payload.get("category") or ""),
                )
                product = _enrich_with_vision_listing(product, payload)
            except LocalFolderUploadError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(product, status=201)
            return
        body_limit = (
            MAX_VIDEO_BODY_BYTES
            if path.endswith("/video")
            else
            MAX_IMAGE_UPLOAD_BODY_BYTES
            if path.endswith("/product-images") or path.startswith("/api/uploads/")
            else
            MAX_CATEGORY_CATALOG_BODY_BYTES
            if path.startswith("/api/marketplace-category-catalog-runs/") and path.endswith("/complete")
            else MAX_JSON_BODY_BYTES
        )
        payload = self._read_json(max_bytes=body_limit)

        if path.startswith("/api/store-listing-copies/") and path.endswith("/video"):
            parts = path.strip("/").split("/")
            listing_copy_id = parts[2] if len(parts) == 4 else ""
            try:
                item = storage.save_listing_copy_video(listing_copy_id, payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not item:
                raise ApiRequestError("Store listing copy not found", 404)
            self._json(item)
            return

        if path.startswith("/api/base-products/") and path.endswith("/product-images"):
            parts = path.strip("/").split("/")
            base_product_id = parts[2] if len(parts) == 4 else ""
            try:
                result = storage.add_base_product_product_image(base_product_id, payload)
            except SizeChartUploadError as exc:
                # 与 /api/uploads/* 保持同一错误结构：前端读 code 区分类型/大小错误。
                self._json({"error": str(exc), "code": exc.code}, status=422)
                return
            if not result:
                raise ApiRequestError("Base product not found", 404)
            self._json(result, status=201)
            return

        if path.startswith("/api/base-products/") and path.endswith("/video"):
            parts = path.strip("/").split("/")
            base_product_id = parts[2] if len(parts) == 4 else ""
            try:
                item = storage.save_base_product_video(base_product_id, payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not item:
                raise ApiRequestError("Base product not found", 404)
            self._json(item)
            return

        if path.startswith("/api/store-listing-copies/") and path.endswith("/video/remove"):
            parts = path.strip("/").split("/")
            listing_copy_id = parts[2] if len(parts) == 5 else ""
            item = storage.remove_listing_copy_video(listing_copy_id)
            if not item:
                raise ApiRequestError("Store listing copy not found", 404)
            self._json(item)
            return

        if path == "/api/system/folder-picker":
            self._json(self._pick_local_folder(payload))
            return

        if path == "/api/assets/scan":
            root = Path(payload.get("root_path") or settings.NAS_ASSET_ROOT)
            result = scan_asset_root(
                root,
                category_override=payload.get("category") or "",
                max_files=int(payload.get("max_files") or 3000),
                mode=payload.get("mode") or "auto",
            )
            storage.replace_assets(result["assets"])
            self._json(result)
            return

        if path == "/api/products":
            self._json(storage.create_product(payload), status=201)
            return

        if path == "/api/products/from-assets":
            product = storage.create_product_from_assets(payload)
            if not product:
                self._json({"error": "No assets found for group_id"}, status=404)
                return
            self._json(product, status=201)
            return

        if path == "/api/base-products/batch-delete":
            ids = payload.get("ids") or payload.get("base_product_ids") or []
            if not isinstance(ids, list):
                raise ApiRequestError("ids must be a list", 422)
            result = storage.delete_base_products(ids)
            self._json(result)
            return

        if path == "/api/base-products/from-folders":
            self._json(_create_nas_import_task(payload), status=202)
            return

        if path == "/api/base-products/from-folder":
            product = storage.create_base_product_from_folder(payload)
            if not product:
                self._json({"error": "No assets found for folder"}, status=404)
                return
            product = _enrich_with_vision_listing(product, payload)
            self._json(product, status=201)
            return

        if path == "/api/store-templates":
            try:
                self._json(storage.create_store_template(payload), status=201)
            except CategoryProfileConflict as exc:
                raise ApiRequestError(str(exc), 422) from exc
            return

        if path == "/api/settings/nas/test":
            config = storage.get_runtime_configuration()
            root = Path(str(payload.get("asset_root") or config["nas"]["asset_root"]))
            ok = root.is_dir()
            self._json({
                "ok": ok,
                "message": "NAS 素材目录可访问" if ok else "NAS 素材目录不可访问",
                "tested_at": datetime.now(timezone.utc).isoformat(),
            })
            return

        if path == "/api/settings/mabang/test":
            config = storage.get_runtime_configuration()
            configured = bool(str(config["mabang"].get("workspace_url") or "").strip())
            self._json({
                "ok": configured,
                "message": "马帮工作台地址已配置；登录状态由 Worker 执行时确认" if configured else "马帮工作台地址未配置",
                "tested_at": datetime.now(timezone.utc).isoformat(),
            })
            return

        if path.startswith("/api/store-templates/") and path.endswith("/copy"):
            parts = path.strip("/").split("/")
            template_id = parts[2] if len(parts) == 4 else ""
            try:
                copied = storage.copy_store_template(template_id, payload)
            except LookupError as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(copied, status=201)
            return

        if path.startswith("/api/store-templates/") and path.endswith("/duplicate"):
            # 同店复制：卡片「复制」用，保留全部店铺绑定值（区别于换店铺的 /copy）。
            parts = path.strip("/").split("/")
            template_id = parts[2] if len(parts) == 4 else ""
            try:
                copied = storage.duplicate_store_template(template_id, payload)
            except LookupError as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(copied, status=201)
            return

        if path.startswith("/api/worker/"):
            action = path.rsplit("/", 1)[-1]
            try:
                result = _run_worker_action(action)
                if action in {"stop", "pause"}:
                    # 手动停 = 压制：按需启动不再把它拉回来（新任务或手动启动才解除）。
                    result["manual_hold"] = _hold_workers_after_manual_stop(action)
            except LookupError as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except (MabangBrowserError, RuntimeError) as exc:
                raise ApiRequestError(str(exc), 409) from exc
            self._json(result)
            return

        if path == "/api/ai/model-connections":
            try:
                self._json(storage.create_ai_model_connection(payload), status=201)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            return

        if path.startswith("/api/ai/model-connections/"):
            parts = path.strip("/").split("/")
            connection_id = parts[3] if len(parts) > 3 else ""
            action = parts[4] if len(parts) > 4 else ""
            try:
                if action == "set-default":
                    result = storage.set_default_ai_model_connection(connection_id)
                elif action == "rotate-key":
                    result = storage.rotate_ai_model_connection_key(connection_id, payload.get("api_key") or "")
                elif action == "test":
                    connection = storage.get_ai_model_connection_private(connection_id)
                    if not connection:
                        result = None
                    else:
                        generated = ModelGateway(
                            crypto=CredentialCrypto(settings.get_ai_credentials_master_key()),
                            adapters=_model_adapters(),
                        ).generate(
                            connection,
                            GenerationRequest(system="Reply with OK only.", user="Connection test.", max_tokens=64),
                        )
                        self._json({
                            "ok": True,
                            "model_id": generated.model_id,
                            "message": "AI 模型连接正常",
                            "tested_at": datetime.now(timezone.utc).isoformat(),
                        })
                        return
                elif action == "refresh-balance":
                    connection = storage.get_ai_model_connection_private(connection_id)
                    if not connection:
                        result = None
                    else:
                        from services.api import ai_balance
                        crypto = CredentialCrypto(settings.get_ai_credentials_master_key())
                        api_key = crypto.decrypt(connection.get("encrypted_api_key") or "")
                        balance = ai_balance.fetch_balance(
                            base_url=connection.get("base_url") or "",
                            api_key=api_key,
                            timeout_seconds=float(connection.get("timeout_seconds") or 15),
                            now=datetime.now(timezone.utc).isoformat(),
                        )
                        result = storage.record_ai_model_connection_balance(connection_id, balance.to_dict())
                else:
                    result = None
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            except RuntimeError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not result:
                self._json({"error": "Model connection or action not found"}, status=404)
                return
            self._json(result)
            return

        if path == "/api/description-profiles":
            try:
                self._json(storage.create_description_profile(payload), status=201)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            return

        if path.startswith("/api/description-profiles/") and path.endswith("/set-default"):
            profile_id = path.strip("/").split("/")[2]
            try:
                profile = storage.set_default_description_profile(profile_id)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not profile:
                self._json({"error": "Description profile not found"}, status=404)
                return
            self._json(profile)
            return

        if path == "/api/title-skills":
            try:
                self._json(storage.create_title_skill(payload), status=201)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            return

        if path.startswith("/api/title-skills/") and path.endswith("/set-default"):
            skill_id = path.strip("/").split("/")[2]
            try:
                skill = storage.set_default_title_skill(skill_id)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not skill:
                self._json({"error": "Title skill not found"}, status=404)
                return
            self._json(skill)
            return

        if path == "/api/hot-words":
            # 支持单个 {word, tag} 或批量 {words: [...], tag}；批量按小写去重。
            if isinstance(payload.get("words"), list):
                result = storage.create_hot_words_batch(payload["words"], str(payload.get("tag") or ""))
                self._json(result, status=201)
                return
            try:
                self._json(storage.create_hot_word(payload), status=201)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            return

        if path == "/api/uploads/size-chart":
            try:
                self._json(save_size_chart(payload, storage_dir=settings.STORAGE_DIR), status=201)
            except SizeChartUploadError as exc:
                self._json({"error": str(exc), "code": exc.code}, status=422)
            return

        if path == "/api/uploads/default-sku-image":
            # 店铺模板「默认 SKU(白T恤)」的变种图：本土专属，与尺码图共用上传实现。
            try:
                self._json(
                    save_image_upload(payload, storage_dir=settings.STORAGE_DIR, kind="default_sku"),
                    status=201,
                )
            except SizeChartUploadError as exc:
                self._json({"error": str(exc), "code": exc.code}, status=422)
            return

        if path.startswith("/api/automation/jobs/") and path.endswith("/cancel"):
            job_id = path.strip("/").split("/")[3]
            try:
                result = storage.cancel_automation_job(job_id, reason=payload.get("reason") or "")
            except LookupError as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            self._json(result)
            return

        if path.startswith("/api/automation/jobs/") and path.endswith("/self-heal"):
            job_id = path.strip("/").split("/")[3]
            self._json(self._self_heal_automation_job(job_id))
            return

        if path.startswith("/api/base-products/") and path.endswith("/rollback"):
            parts = path.strip("/").split("/")
            base_product_id = parts[2] if len(parts) == 4 else ""
            try:
                result = storage.rollback_base_product(base_product_id)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            if result is None:
                raise ApiRequestError("Base product not found", 404)
            self._json(result)
            return

        if path.startswith("/api/base-products/") and path.endswith("/regenerate-listing"):
            parts = path.strip("/").split("/")
            base_product_id = parts[2] if len(parts) == 4 else ""
            product = storage.get_item("base_products", base_product_id)
            if not product:
                raise ApiRequestError("Base product not found", 404)
            # payload was already parsed by the shared body read above — do not
            # call _read_json() again or the second read blocks on the drained socket.
            # aspect 来自基础商品批量栏的三个按钮；不带 aspect 时保持原「重试 AI 识别」行为。
            aspect = str(payload.get("aspect") or "").strip().lower()
            if aspect and aspect not in {"title", "description", "colors"}:
                raise ApiRequestError("aspect 必须是 title、description 或 colors", 422)
            product = _enrich_with_vision_listing(product, payload, mark_edited=True, aspect=aspect)
            self._json(product)
            return

        if path == "/api/ai/attribute-recommendations":
            fields = payload.get("fields")
            if not isinstance(fields, list) or not fields:
                raise ApiRequestError("fields is required", 400)
            connection = storage.get_default_ai_model_connection_private()
            if not connection:
                raise ApiRequestError("未配置 AI 模型连接", 422)
            from services.api.attribute_recommendation import recommend_attribute_values
            try:
                gateway = ModelGateway(
                    crypto=CredentialCrypto(settings.get_ai_credentials_master_key()),
                    adapters=_model_adapters(),
                )
                result = recommend_attribute_values(
                    gateway,
                    connection,
                    fields,
                    payload.get("context") if isinstance(payload.get("context"), dict) else {},
                )
            except ValueError as exc:
                raise ApiRequestError(f"AI 推荐属性失败: {exc}", 422) from exc
            except Exception as exc:  # noqa: BLE001 - 推荐失败不阻塞编辑器
                raise ApiRequestError(f"AI 推荐属性异常: {exc}", 422) from exc
            self._json({"ok": True, **result})
            return

        if path.startswith("/api/base-products/") and path.endswith("/recognize-sizes"):
            parts = path.strip("/").split("/")
            base_product_id = parts[2] if len(parts) == 4 else ""
            product = storage.get_item("base_products", base_product_id)
            if not product:
                raise ApiRequestError("Base product not found", 404)
            size_chart_image = str(payload.get("size_chart_image") or "").strip()
            assets = storage.assets_for_base_product(product)
            asset = next(
                (a for a in assets if str(a.get("file_name") or "") == size_chart_image),
                None,
            )
            if not asset:
                raise ApiRequestError("尺码表图片不在该商品素材中", 422)
            file_path = str(asset.get("file_path") or "").strip()
            if not file_path:
                raise ApiRequestError("尺码表图片缺少本地路径", 422)
            from pathlib import Path as _Path
            if not _Path(file_path).is_file():
                raise ApiRequestError("尺码表图片文件不存在", 422)
            # 尺码识别是视觉任务：优先用视觉能力连接（模型名含 vision 或
            # deepseek anthropic 视觉端点），回退默认连接。
            connection = storage.get_default_ai_model_connection_private()
            for candidate_id in [c.get("id") for c in (storage.list_ai_model_connections() or [])]:
                candidate = storage.get_ai_model_connection_private(candidate_id)
                model = str((candidate or {}).get("model_id") or "").lower()
                base = str((candidate or {}).get("base_url") or "").lower()
                if "vision" in model or "deepseek.com/anthropic" in base:
                    connection = candidate
                    break
            if not connection:
                raise ApiRequestError("未配置 AI 模型连接", 422)
            from services.api import vision_listing
            try:
                gateway = ModelGateway(
                    crypto=CredentialCrypto(settings.get_ai_credentials_master_key()),
                    adapters=_model_adapters(),
                )
                result = vision_listing.recognize_size_chart(
                    gateway=gateway,
                    connection=connection,
                    image_path=file_path,
                )
            except Exception as exc:  # noqa: BLE001 - vision must never break create
                raise ApiRequestError(f"AI 识别尺码异常: {exc}", 422) from exc
            if not result.ok:
                raise ApiRequestError(result.error or "AI 识别尺码失败", 422)
            updated = storage.update_base_product(
                base_product_id,
                {"recognized_sizes": result.sizes},
                mark_edited=True,
            )
            self._json({"sizes": result.sizes, "ok": True, "product": updated})
            return


        if path.startswith("/api/store-listing-copies/") and path.endswith("/description/generate"):
            listing_copy_id = path.strip("/").split("/")[2]
            listing_copy = storage.get_item("store_listing_copies", listing_copy_id)
            connection = storage.get_ai_model_connection_private(payload.get("connection_id") or "") or storage.get_default_ai_model_connection_private()
            profile = storage.get_description_profile(payload.get("profile_id") or "") or storage.get_default_description_profile()
            if not listing_copy or not connection or not profile:
                raise ApiRequestError("店铺副本、模型连接或描述方案不存在", 404)
            # 旧副本创建时未携带基础商品的 AI 识别属性：生成前从基础商品补上，
            # 让「字段行 → 基础属性」能取到真实值（新副本在复制时已随带）。
            if not listing_copy.get("recognized_attributes"):
                base_product = storage.get_item("base_products", listing_copy.get("base_product_id") or "")
                attrs = (base_product or {}).get("recognized_attributes")
                if isinstance(attrs, dict) and attrs:
                    listing_copy = {**listing_copy, "recognized_attributes": dict(attrs)}
            try:
                gateway = ModelGateway(
                    crypto=CredentialCrypto(settings.get_ai_credentials_master_key()),
                    adapters=_model_adapters(),
                )
                result = DescriptionGenerationService(gateway).generate(
                    profile=profile, product=listing_copy, connection=connection
                ).as_dict()
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            storage.record_listing_description_generation(listing_copy_id, result)
            if result["status"] == "ready":
                storage.update_store_listing_copy(
                    listing_copy_id,
                    {
                        "description": result["description"],
                        "description_status": "ready",
                        "description_profile_version_id": result["profile_version_id"],
                    },
                )
            self._json(result)
            return

        if path == "/api/mabang-page-imports":
            try:
                self._json(storage.create_mabang_page_import(payload), status=201)
            except MabangPageImportError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            return

        if path == "/api/mabang-browser/start":
            account_id = str(payload.get("account_id") or "").strip() or None
            # 槽位：Worker 的登录恢复要落到它自己那只浏览器上（同账号多槽位时不能混淆）；
            # 不传就是 1 号槽位（操作员的可见窗口 / 页面导入）。
            slot = _payload_slot(payload)
            # 页面导入这一步需要操作员在窗口里手动进商品编辑页，所以显式传 headless=false
            # 临时开一个可见窗口；其它调用方不传，继续跟随设置。
            raw_headless = payload.get("headless")
            headless = None if raw_headless is None else bool(raw_headless)
            try:
                result, cdp_url = _launch_collection_browser(account_id, headless=headless, slot=slot)
            except MabangBrowserError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            _attach_mabang_auto_login(result, cdp_url, account_id)
            self._json(result)
            return

        if path == "/api/mabang-browser/auto-login":
            # Worker 预检/登录恢复入口：浏览器未启动时先拉起，再用该账号登录马帮。
            account_id = str(payload.get("account_id") or "").strip() or None
            slot = _payload_slot(payload)
            try:
                result, cdp_url = _launch_collection_browser(account_id, slot=slot)
            except MabangBrowserError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            _attach_mabang_auto_login(result, cdp_url, account_id)
            self._json(result)
            return

        if path == "/api/store-collection/collect":
            # 按账号跑采集：每个店铺用它所属账号的浏览器，返回逐店铺结果与失败原因。
            store_ids = payload.get("store_ids")
            if store_ids is not None and not isinstance(store_ids, list):
                raise ApiRequestError("store_ids must be a list", 422)
            _store_collection_enter()
            try:
                self._json(
                    run_store_collection(
                        store_ids=[str(item) for item in store_ids] if store_ids else None,
                        trigger="manual",
                    ),
                    status=201,
                )
            finally:
                # 采集期间不让「空闲自动关闭」把采集用的浏览器关掉。
                _store_collection_leave()
            return

        if path == "/api/store-watermarks/collect":
            store_id = str(payload.get("store_id") or "").strip()
            if not store_id:
                raise ApiRequestError("store_id is required", 400)
            # 走 Playwright/CDP 采集（browser-harness 的 daemon 在本机 Python 3.14 下起不来，
            # 过去这条路由必然 422）。采集器只读：打开「添加水印」读目录 + 图片范围结构，
            # 绝不点任何水印选项，读完把菜单收回去。
            collect_result = run_store_watermark_collect(
                cdp_url=str(payload.get("cdp_url") or "").strip() or "http://127.0.0.1:9223",
            )
            if not collect_result.get("ok"):
                message = str(collect_result.get("error") or "读取马帮水印目录失败")
                storage.record_store_watermark_collect_failure(store_id, message)
                raise ApiRequestError(message, 422)
            captured_store_id = str((collect_result.get("store") or {}).get("id") or "").strip()
            captured_store_name = str((collect_result.get("store") or {}).get("name") or "").strip()
            if captured_store_id and captured_store_id != store_id:
                message = (
                    f"当前马帮页面是店铺「{captured_store_name or captured_store_id}」，"
                    f"不是要采集的店铺；水印目录未写入"
                )
                storage.record_store_watermark_collect_failure(store_id, message)
                raise ApiRequestError(message, 422)
            raw_items = collect_result.get("templates")
            if not isinstance(raw_items, list):
                message = "水印采集结果里没有模板列表，未写入（原目录保持不变）"
                storage.record_store_watermark_collect_failure(store_id, message)
                raise ApiRequestError(message, 422)
            try:
                items = storage.replace_store_watermark_catalog(store_id, raw_items)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json({
                "ok": True,
                "store_id": store_id,
                "count": len(items),
                "items": items,
                "status": storage.get_store_watermark_catalog_status(store_id),
                # 「选完水印模板之后要选的图片范围」控件的真实结构：采集时顺手读出来，
                # 执行器那一段要按它接（不写库，只在响应里回给界面/排查用）。
                "range_probe": collect_result.get("range_probe") or {},
                "catalog_source": collect_result.get("catalog_source") or "",
                "page_url": collect_result.get("page_url") or "",
            })
            return

        if path.startswith("/api/mabang-page-imports/") and path.endswith("/complete"):
            run_id = path.strip("/").split("/")[2]
            try:
                result = storage.stage_mabang_page_import(run_id, payload.get("raw_capture") or payload)
            except storage.MabangPageImportNotFound as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except MabangPageImportError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(result, status=201)
            return

        if path.startswith("/api/mabang-page-imports/") and path.endswith("/confirm"):
            run_id = path.strip("/").split("/")[2]
            try:
                result = storage.confirm_mabang_page_import(run_id)
            except storage.MabangPageImportNotFound as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except MabangPageImportError as exc:
                message = str(exc)
                status = 409 if "请先采集" in message or "当前状态" in message else 422
                raise ApiRequestError(message, status) from exc
            self._json(result, status=200 if result["run"].get("already_confirmed") else 201)
            return

        if path.startswith("/api/mabang-page-imports/") and path.endswith("/collect"):
            run_id = path.strip("/").split("/")[2]
            run = storage.get_mabang_page_import(run_id)
            if not run:
                raise ApiRequestError("Mabang page import not found", 404)
            if run.get("status") == "completed":
                raise ApiRequestError("马帮页面导入已确认，不能重新采集", 409)
            # 采集必须读「这个 run 所属账号」的浏览器：不同账号是不同的 CDP 端口与
            # Chrome Profile，读错端口会采到别的账号的页面。
            import_account_id = str(run.get("account_id") or "").strip() or None
            import_runtime = storage.resolve_mabang_account_runtime(import_account_id)
            capture_result = run_mabang_page_import_collect(
                {
                    "platform": run.get("platform") or "shopee",
                    "country": run.get("country") or "",
                },
                cdp_url=str(import_runtime.get("cdp_url") or ""),
            )
            if not capture_result.get("ok"):
                error = capture_result.get("error") or "Unable to read the current Mabang page"
                storage.record_mabang_page_import_error(run_id, error)
                raise ApiRequestError(error, 422)
            try:
                result = storage.stage_mabang_page_import(run_id, capture_result["capture"])
            except MabangPageImportError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            # 现场要求（2026-10-01）：马帮采集的时候就顺手把水印目录更新到最新，
            # 不用再单独点一次「采集/更新水印」。best-effort：失败只记状态，不影响本次采集。
            self._refresh_watermark_catalog_after_collect(
                capture_result["capture"], account_id=import_account_id
            )
            # 采集结束：把窗口收回无头。页面导入期间为了让人能手动进商品编辑页临时开了窗口，
            # 采完就必须恢复，否则服务器桌面上会一直挂着一个窗口。
            _restore_collection_browser_mode(import_account_id)
            self._json(result, status=201)
            return

        if path == "/api/category-profiles/collection-runs":
            try:
                self._json(storage.create_category_profile_collection_run(payload), status=201)
            except CategoryProfileConflict as exc:
                raise ApiRequestError(str(exc), 422) from exc
            return

        if path == "/api/marketplace-categories/collection-runs":
            try:
                self._json(storage.create_marketplace_category_catalog_run(payload), status=201)
            except MarketplaceCategoryCatalogConflict as exc:
                raise ApiRequestError(str(exc), 422) from exc
            return

        if path.startswith("/api/marketplace-category-catalog-runs/") and path.endswith("/complete"):
            run_id = path.strip("/").split("/")[2]
            try:
                result = storage.complete_marketplace_category_catalog_run(run_id, payload.get("raw_capture") or payload)
            except MarketplaceCategoryCatalogNotFound as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except MarketplaceCategoryCatalogConflict as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(result, status=201)
            return

        if path.startswith("/api/category-profile-collection-runs/") and path.endswith("/complete"):
            run_id = path.strip("/").split("/")[2]
            try:
                result = storage.complete_category_profile_collection_run(run_id, payload.get("raw_capture") or payload)
            except CategoryProfileNotFound as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except CategoryProfileConflict as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(result, status=201)
            return

        if path.startswith("/api/category-profile-versions/"):
            parts = path.strip("/").split("/")
            version_id = parts[2] if len(parts) > 2 else ""
            action = parts[3] if len(parts) > 3 else ""
            try:
                if action in {"publish", "reject"}:
                    raise ApiRequestError("类目规则已改为有效采集后自动激活，无需审核", 410)
                elif action == "validate":
                    result = storage.validate_category_profile_values(version_id, payload.get("values") or {})
                else:
                    raise ApiRequestError(f"Unknown category profile version action: {action}", 404)
            except CategoryProfileNotFound as exc:
                raise ApiRequestError(str(exc), 404) from exc
            except CategoryProfileConflict as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(result)
            return

        if path == "/api/mabang/accounts":
            try:
                result = storage.create_mabang_account(payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json(result, status=201)
            return

        if path == "/api/mabang/store-options":
            result = storage.upsert_mabang_store_options(
                payload.get("items") or payload,
                account_id=payload.get("account_id"),
            )
            self._json(result, status=201)
            return

        if path == "/api/store-listing-copies/fill-missing-attributes":
            # 勾选了副本就只补这些；没勾选就扫描全部副本，只补缺值的那些。
            result = storage.fill_listing_copy_missing_attributes(payload.get("listing_copy_ids") or [])
            self._json(result)
            return

        if path.startswith("/api/store-listing-copies/") and path.endswith("/fill-missing-attributes"):
            listing_copy_id = path.strip("/").split("/")[2]
            result = storage.fill_listing_copy_missing_attributes([listing_copy_id])
            self._json(result)
            return

        if path == "/api/store-listing-copies/from-base":
            # Batch copy: base_product_ids (array) takes precedence for the multi
            # product UI; the legacy single base_product_id stays supported.
            base_product_ids = payload.get("base_product_ids")
            if isinstance(base_product_ids, list) and base_product_ids:
                result = storage.copy_base_products_to_store_templates(
                    base_product_ids,
                    payload.get("template_ids") or [],
                )
            else:
                result = storage.copy_base_product_to_store_templates(
                    payload.get("base_product_id") or "",
                    payload.get("template_ids") or [],
                )
            self._json(result, status=201)
            return

        if path == "/api/automation/jobs/from-listing-copies":
            result = storage.create_automation_jobs_from_listing_copies(
                payload.get("listing_copy_ids") or payload.get("ids") or []
            )
            _wake_worker_for_created_jobs(result)
            self._json(result, status=201)
            return

        if path == "/api/automation/jobs/claim":
            try:
                result = storage.claim_next_automation_job(
                    payload.get("worker_id") or "",
                    lease_seconds=int(payload.get("lease_seconds") or 120),
                    account_id=payload.get("account_id"),
                )
            except (TypeError, ValueError) as exc:
                raise ApiRequestError(str(exc), 409) from exc
            self._json(result)
            return

        if path.startswith("/api/automation/jobs/") and path.endswith("/heartbeat"):
            job_id = path.strip("/").split("/")[3]
            try:
                result = storage.heartbeat_automation_job(
                    job_id,
                    payload.get("worker_id") or "",
                    payload.get("lease_token") or "",
                    phase=payload.get("phase") or "running",
                    lease_seconds=int(payload.get("lease_seconds") or 120),
                )
            except (TypeError, ValueError) as exc:
                raise ApiRequestError(str(exc), 409) from exc
            self._json(result)
            return

        if path.startswith("/api/automation/jobs/") and path.endswith("/progress"):
            job_id = path.strip("/").split("/")[3]
            try:
                result = storage.record_automation_job_progress(
                    job_id,
                    payload.get("worker_id") or "",
                    payload.get("lease_token") or "",
                    event=payload.get("event") or {},
                )
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            self._json(result)
            return

        if path.startswith("/api/automation/jobs/") and path.endswith("/manual-finish"):
            job_id = path.strip("/").split("/")[3]
            try:
                result = storage.manual_finish_automation_job(
                    job_id,
                    status=payload.get("status") or "",
                    note=payload.get("note") or "",
                    operator=payload.get("operator") or "",
                )
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            self._json(result)
            return

        if path.startswith("/api/automation/jobs/") and path.endswith("/finish"):
            job_id = path.strip("/").split("/")[3]
            try:
                result = storage.finish_automation_job(
                    job_id,
                    payload.get("worker_id") or "",
                    payload.get("lease_token") or "",
                    status=payload.get("status") or "failed",
                    result=payload.get("result") or {},
                    error=payload.get("error") or "",
                )
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            self._json(result)
            return

        if path == "/api/mabang/export":
            product_ids = self._normalize_product_ids(payload.get("product_ids") or payload.get("ids") or [])
            save_copy = _truthy(payload.get("save_copy", True))
            result = self._export_mabang(product_ids, save_copy=save_copy)
            if not result.get("ok"):
                status = 400 if result.get("validation") else 500
                self._json(result, status=status)
                return
            self._send_file(result["xlsx_path"], result["filename"])
            return

        if path == "/api/products/from-asset-groups":
            self._json(storage.create_products_from_asset_groups(payload), status=201)
            return

        if path == "/api/products/name-cleanup/apply":
            self._json(storage.apply_product_name_cleanup(payload), status=201)
            return

        if path == "/api/products/dedup/run":
            self._json(storage.dedup_products(payload.get("group_id") or "", payload.get("keep_product_id") or ""), status=201)
            return

        if path.startswith("/api/products/") and path.endswith("/ai/title-suggestion"):
            product_id = path.split("/")[-3]
            product = storage.get_item("products", product_id)
            if not product:
                self._json({"error": "Product not found"}, status=404)
                return
            assets = storage.assets_for_base_product(product)
            skus = storage.list_skus(product_id)
            suggestion = DeepSeekAdapter().suggest_title(product, assets, skus, payload)
            output = storage.create_ai_output({
                "product_id": product_id,
                "output_type": "title_suggestion",
                "prompt": suggestion.get("prompt") or "",
                "response": suggestion.get("title") or "",
                "model": suggestion.get("model") or "",
                "status": "suggested",
                "payload_json": {
                    "suggestion_only": True,
                    "external_call_used": suggestion.get("external_call_used") is True,
                    "mode": suggestion.get("mode") or "",
                    "validation": suggestion.get("validation") or {},
                    "warning": suggestion.get("warning") or "",
                },
            })
            self._json({
                "ok": suggestion.get("ok") is True,
                "suggestion": suggestion,
                "ai_output": output,
                "policy": {
                    "suggestion_only": True,
                    "auto_apply": False,
                    "browser_control": False,
                    "publish_control": False,
                },
            }, status=201)
            return

        if path.startswith("/api/products/") and path.endswith("/ai/attribute-suggestion"):
            product_id = path.split("/")[-3]
            product = storage.get_item("products", product_id)
            if not product:
                self._json({"error": "Product not found"}, status=404)
                return
            assets = storage.assets_for_base_product(product)
            skus = storage.list_skus(product_id)
            suggestion = DeepSeekAdapter().suggest_attributes(product, assets, skus, payload)
            attributes = suggestion.get("attributes") or {}
            output = storage.create_ai_output({
                "product_id": product_id,
                "output_type": "attribute_suggestion",
                "prompt": suggestion.get("prompt") or "",
                "response": json.dumps(attributes, ensure_ascii=False, sort_keys=True),
                "model": suggestion.get("model") or "",
                "status": "suggested",
                "payload_json": {
                    "suggestion_only": True,
                    "external_call_used": suggestion.get("external_call_used") is True,
                    "mode": suggestion.get("mode") or "",
                    "validation": suggestion.get("validation") or {},
                    "warning": suggestion.get("warning") or "",
                    "allowed_fields": suggestion.get("allowed_fields") or [],
                },
            })
            self._json({
                "ok": suggestion.get("ok") is True,
                "suggestion": suggestion,
                "ai_output": output,
                "policy": {
                    "suggestion_only": True,
                    "auto_apply": False,
                    "browser_control": False,
                    "publish_control": False,
                },
            }, status=201)
            return

        if path.startswith("/api/products/") and path.endswith("/skus"):
            product_id = path.split("/")[-2]
            result = storage.replace_product_skus(product_id, payload.get("items") or [])
            if not result.get("ok"):
                self._json({"error": result.get("error") or "Unable to save SKUs"}, status=404)
                return
            self._json(result)
            return

        if path.startswith("/api/products/") and path.endswith("/prices"):
            product_id = path.split("/")[-2]
            result = storage.replace_product_prices(product_id, payload.get("items") or [])
            if not result.get("ok"):
                self._json({"error": result.get("error") or "Unable to save prices"}, status=404)
                return
            self._json(result)
            return

        self._json({"error": f"Unknown route: {path}"}, status=404)

    def _handle_put(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/local-folder-uploads/") and path.endswith("/files"):
            upload_id = path.strip("/").split("/")[2]
            relative_path = (parse_qs(parsed.query).get("path") or [""])[0]
            try:
                content_length = int(self.headers.get("content-length") or 0)
            except ValueError as exc:
                raise ApiRequestError("Invalid Content-Length", 400) from exc
            if content_length < 0 or content_length > MAX_LOCAL_UPLOAD_FILE_BYTES:
                raise ApiRequestError("文件大小超限", 413)
            try:
                LocalFolderUploads(settings.STORAGE_DIR).save_stream(
                    upload_id,
                    relative_path,
                    self.rfile,
                    content_length,
                )
            except LocalFolderUploadError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json({"ok": True, "path": relative_path})
            return
        payload = self._read_json()

        if path == "/api/settings/network":
            raw = payload.get("lan_enabled")
            if isinstance(raw, bool):
                pass
            elif isinstance(raw, int) and raw in {0, 1}:
                raw = bool(raw)
            elif isinstance(raw, str):
                normalized = raw.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    raw = True
                elif normalized in {"0", "false", "no", "off"}:
                    raw = False
                else:
                    raise ApiRequestError("lan_enabled 必须是布尔值", 422)
            else:
                raise ApiRequestError("lan_enabled 必须是布尔值", 422)
            if network_access.env_bind_override():
                raise ApiRequestError(
                    f"监听地址由启动参数 API_HOST={settings.API_HOST} 指定，请修改启动脚本后重启 API",
                    409,
                )
            try:
                saved = storage.save_runtime_configuration({"network": {"lan_enabled": raw}})
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            _server, active_host, active_port = _active_server_snapshot()
            target_host = network_access.resolve_bind_host(saved.get("network") or {})
            scheduled = _schedule_network_rebind(target_host)
            status = network_access.build_network_status(
                saved.get("network") or {},
                active_bind_host=active_host,
                active_port=active_port,
                rebind_scheduled=scheduled,
            )
            status["ok"] = True
            if scheduled:
                status["message"] = "已切换监听地址，服务正在重新监听；当前页面如断开会自动恢复。"
            elif network_access.same_bind_host(target_host, active_host):
                status["message"] = "监听地址已是目标状态。"
            else:
                status["message"] = "设置已保存，重启 API 后生效。"
            self._json(status)
            return

        if path.startswith("/api/base-products/") and path.endswith("/prices"):
            parts = path.strip("/").split("/")
            base_product_id = parts[2] if len(parts) == 4 else ""
            updated = storage.update_base_product(
                base_product_id,
                {"price_tiers": payload.get("tiers") or []},
                mark_edited=True,
            )
            if not updated:
                raise ApiRequestError("Base product not found", 404)
            self._json({"tiers": updated.get("price_tiers") or []})
            return

        if path.startswith("/api/mabang/accounts/"):
            account_id = path.strip("/").split("/")[3]
            try:
                result = storage.update_mabang_account(account_id, payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if result is None:
                self._json({"error": "Mabang account not found"}, status=404)
                return
            self._json(result)
            return

        if path == "/api/settings":
            previous = storage.get_runtime_configuration()
            try:
                saved = storage.save_runtime_configuration(payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            # 并行槽位数改了要当场生效：调大就起新槽位（各开一只浏览器并自动登录），
            # 调小就关掉被裁掉槽位那只浏览器。失败只记录原因，绝不让保存失败。
            try:
                saved["worker_slots"] = _reconcile_worker_slots(previous, saved)
            except Exception as exc:  # noqa: BLE001 - 设置保存不能因为收尾动作失败而回滚
                print(f"Worker 槽位调整收尾失败：{exc}", file=sys.stderr)
                saved["worker_slots"] = {"error": str(exc)[:300]}
            # 兼容直接 PUT /api/settings 携带 network 的客户端：监听地址变了就
            # 当场重绑；设置页走的是 /api/settings/network 专用接口。
            network_patch = payload.get("network")
            if isinstance(network_patch, dict) and "lan_enabled" in network_patch:
                if network_access.env_bind_override():
                    saved["network_rebind_error"] = (
                        f"监听地址由启动参数 API_HOST={settings.API_HOST} 指定，本次开关不会生效"
                    )
                else:
                    target_host = network_access.resolve_bind_host(saved.get("network") or {})
                    saved["network_rebind_scheduled"] = _schedule_network_rebind(target_host)
            self._json(saved)
            return

        if path == "/api/system/folder-picker":
            self._json(self._pick_local_folder(payload))
            return

        if path.startswith("/api/base-products/"):
            base_product_id = path.rsplit("/", 1)[-1]
            product = storage.update_base_product(base_product_id, payload, mark_edited=True)
            if not product:
                self._json({"error": "Base product not found"}, status=404)
                return
            self._json(product)
            return

        if path.startswith("/api/products/"):
            product_id = path.rsplit("/", 1)[-1]
            product = storage.update_product(product_id, payload)
            if not product:
                self._json({"error": "Product not found"}, status=404)
                return
            self._json(product)
            return

        if path.startswith("/api/store-templates/"):
            template_id = path.rsplit("/", 1)[-1]
            try:
                template = storage.update_store_template(template_id, payload)
            except CategoryProfileConflict as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not template:
                self._json({"error": "Store template not found"}, status=404)
                return
            self._json(template)
            return

        if path.startswith("/api/store-listing-copies/"):
            listing_copy_id = path.rsplit("/", 1)[-1]
            try:
                copy = storage.update_store_listing_copy(listing_copy_id, payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not copy:
                self._json({"error": "Store listing copy not found"}, status=404)
                return
            self._json(copy)
            return

        if path.startswith("/api/ai/model-connections/"):
            connection_id = path.rsplit("/", 1)[-1]
            try:
                connection = storage.update_ai_model_connection(connection_id, payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not connection:
                self._json({"error": "Model connection not found"}, status=404)
                return
            self._json(connection)
            return

        if path.startswith("/api/description-profiles/"):
            profile_id = path.rsplit("/", 1)[-1]
            try:
                profile = storage.update_description_profile(profile_id, payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not profile:
                self._json({"error": "Description profile not found"}, status=404)
                return
            self._json(profile)
            return

        if path.startswith("/api/title-skills/"):
            skill_id = path.rsplit("/", 1)[-1]
            try:
                skill = storage.update_title_skill(skill_id, payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not skill:
                self._json({"error": "Title skill not found"}, status=404)
                return
            self._json(skill)
            return

        if path.startswith("/api/hot-words/"):
            word_id = path.rsplit("/", 1)[-1]
            try:
                word = storage.update_hot_word(word_id, payload)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            if not word:
                self._json({"error": "Hot word not found"}, status=404)
                return
            self._json(word)
            return

        self._json({"error": f"Unknown route: {path}"}, status=404)

    def _handle_delete(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/mabang/accounts/"):
            account_id = path.strip("/").split("/")[3]
            try:
                result = storage.delete_mabang_account(account_id)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            if result is None:
                self._json({"error": "Mabang account not found"}, status=404)
                return
            self._json(result)
            return
        if path.startswith("/api/category-quick-templates/"):
            template_id = path.rsplit("/", 1)[-1]
            result = storage.delete_category_quick_template(template_id)
            if result is None:
                self._json({"error": "Category quick template not found"}, status=404)
                return
            self._json(result)
            return
        if path.startswith("/api/local-folder-uploads/"):
            upload_id = path.strip("/").split("/")[2]
            try:
                LocalFolderUploads(settings.STORAGE_DIR).cancel(upload_id)
            except LocalFolderUploadError as exc:
                raise ApiRequestError(str(exc), 422) from exc
            self._json({"ok": True, "upload_id": upload_id})
            return
        if path.startswith("/api/automation/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            try:
                result = storage.delete_automation_job(job_id)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            if result is None:
                self._json({"error": "Automation job not found"}, status=404)
                return
            self._json(result)
            return
        if path.startswith("/api/base-products/"):
            base_product_id = path.rsplit("/", 1)[-1]
            result = storage.delete_base_product(base_product_id)
            if result is None:
                self._json({"error": "Base product not found"}, status=404)
                return
            self._json(result)
            return
        if path.startswith("/api/store-listing-copies/"):
            listing_copy_id = path.rsplit("/", 1)[-1]
            try:
                result = storage.delete_store_listing_copy(listing_copy_id)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            if result is None:
                self._json({"error": "Store listing copy not found"}, status=404)
                return
            self._json(result)
            return
        if path.startswith("/api/description-profiles/"):
            profile_id = path.rsplit("/", 1)[-1]
            result = storage.delete_description_profile(profile_id)
            if result is None:
                self._json({"error": "Description profile not found"}, status=404)
                return
            self._json(result)
            return
        if path == "/api/hot-words":
            self._json(storage.delete_all_hot_words())
            return
        if path.startswith("/api/title-skills/"):
            skill_id = path.rsplit("/", 1)[-1]
            result = storage.delete_title_skill(skill_id)
            if result is None:
                self._json({"error": "Title skill not found"}, status=404)
                return
            self._json(result)
            return
        if path.startswith("/api/hot-words/"):
            word_id = path.rsplit("/", 1)[-1]
            result = storage.delete_hot_word(word_id)
            if result is None:
                self._json({"error": "Hot word not found"}, status=404)
                return
            self._json(result)
            return
        if path.startswith("/api/store-templates/"):
            template_id = path.rsplit("/", 1)[-1]
            result = storage.delete_store_template(template_id)
            if result is None:
                self._json({"error": "Store template not found"}, status=404)
                return
            self._json(result)
            return
        if path.startswith("/api/ai/model-connections/"):
            connection_id = path.rsplit("/", 1)[-1]
            try:
                deleted = storage.delete_ai_model_connection(connection_id)
            except ValueError as exc:
                raise ApiRequestError(str(exc), 409) from exc
            if deleted is None:
                self._json({"error": "Model connection not found"}, status=404)
                return
            self._json({"deleted": True, "id": connection_id})
            return
        self._json({"error": f"Unknown route: {path}"}, status=404)

    def _refresh_watermark_catalog_after_collect(
        self,
        capture: dict[str, Any],
        *,
        account_id: str | None = None,
    ) -> None:
        """马帮采集之后顺手更新该店铺的水印目录（best-effort，绝不打断采集）。

        优先级：
        1. 采集结果本身带了 ``watermark_templates`` → 直接写库（不额外开浏览器）；
        2. 否则用 Playwright/CDP 采集器补一次（采集时浏览器本来就停在商品编辑页）。
        任何失败都只记一条采集状态（界面显示「上次采集失败：…」），不抛异常。
        """
        try:
            store_category = capture.get("store_category_state")
            store = {}
            if isinstance(store_category, dict) and isinstance(store_category.get("store"), dict):
                store = store_category["store"]
            store_id = str(store.get("id") or "").strip()
            if not store_id:
                return
            raw_items = capture.get("watermark_templates")
            if isinstance(raw_items, list) and capture.get("watermark_catalog_loaded") is True:
                storage.replace_store_watermark_catalog(store_id, raw_items)
                return
            # 补采水印也要走同一个账号的浏览器：读错端口会读到别的账号的页面。
            account_cdp_url = str(
                storage.resolve_mabang_account_runtime(account_id).get("cdp_url") or ""
            )
            collect_result = run_store_watermark_collect(
                cdp_url=account_cdp_url or "http://127.0.0.1:9223"
            )
            if not collect_result.get("ok"):
                storage.record_store_watermark_collect_failure(
                    store_id,
                    str(collect_result.get("error") or "读取马帮水印目录失败"),
                )
                return
            captured_store_id = str((collect_result.get("store") or {}).get("id") or "").strip()
            if captured_store_id and captured_store_id != store_id:
                return
            templates = collect_result.get("templates")
            if isinstance(templates, list):
                storage.replace_store_watermark_catalog(store_id, templates)
        except Exception:  # noqa: BLE001 - 水印目录只是附带产物，不能影响页面采集
            return

    def _serve_mabang_template(self) -> None:
        try:
            template_path = _resolve_template_path()
        except FileNotFoundError as exc:
            self._json({"error": "Mabang template not found", "code": "MABANG_TEMPLATE_NOT_FOUND", "detail": str(exc)}, status=404)
            return
        if not template_path.exists() or not template_path.is_file():
            self._json({"error": "Mabang template not found"}, status=404)
            return
        self._send_file(template_path, template_path.name)

    def _export_mabang(self, product_ids: list[str], save_copy: bool = True) -> dict[str, Any]:
        bundles = collect_mabang_bundles(product_ids)
        result = export_mabang_products(bundles, save_copy=save_copy)
        if not result.get("ok"):
            return result
        return {
            "ok": True,
            "filename": result["filename"],
            "xlsx_path": Path(result["xlsx_path"]),
            "validation": result.get("validation") or {},
        }

    def _send_file(self, file_path: Path, download_name: str | None = None) -> None:
        if not file_path.exists() or not file_path.is_file():
            self._json({"error": "File not found"}, status=404)
            return
        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Content-Length", str(len(content)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content)

    def _pick_local_folder(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("dry_run") is True:
            return {"ok": False, "path": "", "mode": "dry_run"}
        root = None
        try:
            import tkinter
            from tkinter import filedialog

            root = tkinter.Tk()
            root.withdraw()
            # Windows can leave a withdrawn Tk root behind the browser. Make the
            # native folder dialog modal and foreground it before showing it.
            root.attributes("-topmost", True)
            root.update_idletasks()
            root.deiconify()
            root.lift()
            root.focus_force()
            root.grab_set()
            selected = filedialog.askdirectory(
                title=str(payload.get("title") or "Select NAS product folder"),
                initialdir=str(payload.get("initial_dir") or ""),
                parent=root,
            )
            return {"ok": bool(selected), "path": selected or ""}
        except Exception as exc:
            return {"ok": False, "path": "", "error": str(exc), "mode": "unavailable"}
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass

    def _browse_nas_directory(
        self, path: str, *, refresh: bool = False, shallow: bool = False
    ) -> dict[str, Any]:
        # Server-side folder browser used by the LAN UI. Lists direct child directories
        # under `path`, or under the NAS root when path is empty. The server process
        # is what reads the disk, so UNC shares (`\\host\share`) work here the same as
        # they do for scan_asset_root — clients don't need drive-mapping.
        #
        # `shallow=True` skips the per-child `has_subdirs` probe and returns null for
        # it, so opening a big directory (e.g. a style series with hundreds of leaf
        # folders) comes back after a single listing instead of waiting for one extra
        # network round-trip per child. A full browse (`shallow=False`) of the same
        # directory is then launched in the background (see
        # `_schedule_nas_browse_probe`) so leaf markers are usually ready by the time
        # the operator needs them; while that probe is running, full requests return
        # a `probe_running` shallow payload instead of blocking on the network share.
        runtime = storage.get_runtime_configuration()
        raw_root = str((runtime.get("nas") or {}).get("asset_root") or "").strip()
        root_str = raw_root or str(settings.NAS_ASSET_ROOT)
        try:
            root_path = Path(root_str).resolve(strict=False)
        except OSError as exc:
            raise ApiRequestError(f"NAS 根目录无效：{exc}", status=422) from exc

        # First access after a restart: warm the memory cache from disk so
        # recently scanned directories stay instant. Also drop any entries whose
        # root no longer matches the configured root.
        global _NAS_BROWSE_CACHE_DISK_WARMED
        if not _NAS_BROWSE_CACHE_DISK_WARMED:
            now_warm = datetime.now(timezone.utc).timestamp()
            _nas_browse_cache_invalidate_root(str(root_path))
            _nas_browse_cache_load(str(root_path), now_warm)
            _NAS_BROWSE_CACHE_DISK_WARMED = True

        target_raw = (path or "").strip()
        if not target_raw:
            target = root_path
        else:
            try:
                target = Path(target_raw).resolve(strict=False)
            except OSError as exc:
                raise ApiRequestError(f"路径无效：{exc}", status=400) from exc
            # Reject paths that escape the configured NAS root. `.resolve()` above
            # already collapses `..`, so relative_to catches any escape attempt.
            try:
                target.relative_to(root_path)
            except ValueError as exc:
                raise ApiRequestError("不允许跳出 NAS 根目录", status=403) from exc

        cache_key = f"{root_path}|{'s' if shallow else 'f'}|{target}"
        now = datetime.now(timezone.utc).timestamp()
        if not refresh:
            with _NAS_BROWSE_CACHE_LOCK:
                cached = _NAS_BROWSE_CACHE.get(cache_key)
            if cached is not None:
                cached_at, payload = cached
                cache_age = now - cached_at
                # Serve whatever we have while it could plausibly still be the
                # real listing (stale-while-revalidate). An expired entry is
                # returned immediately and re-scanned in the background so the
                # next visitor gets fresh data — never block a second visit on
                # the network share just because the entry went stale.
                if cache_age < _NAS_BROWSE_CACHE_DISK_TTL_SECONDS:
                    payload = dict(payload)
                    payload["from_cache"] = True
                    payload["cached_at"] = cached_at
                    payload["cache_age"] = round(cache_age, 1)
                    if cache_age >= _NAS_BROWSE_CACHE_FRESH_SECONDS:
                        payload["stale"] = True
                        self._schedule_nas_browse_rescan(str(target), shallow=shallow)
                    return payload

        if not target.exists():
            raise ApiRequestError("目录不存在", status=404)
        if not target.is_dir():
            raise ApiRequestError("不是目录", status=400)

        # A full listing is still being probed in the background: do not start a
        # second full scan here (that would block the request for the whole probe
        # duration on a slow share). Serve the directory names immediately from a
        # quick shallow scan and tell the UI the leaf markers are on their way.
        if not shallow and not refresh:
            with _NAS_BROWSE_PROBE_LOCK:
                probe_running = str(target) in _NAS_BROWSE_PROBE_INFLIGHT
            if probe_running:
                # Reuse the shallow listing we cached when this directory was
                # opened, or fall back to a fast name-only scan.
                shallow_key = f"{root_path}|s|{target}"
                cached = None
                with _NAS_BROWSE_CACHE_LOCK:
                    cached = _NAS_BROWSE_CACHE.get(shallow_key)
                if cached is not None and now - cached[0] < _NAS_BROWSE_CACHE_FRESH_SECONDS:
                    entries = list(cached[1].get("entries") or [])
                else:
                    entries = _scan_nas_directory(target, shallow=True)
                payload = {
                    "path": str(target),
                    "parent": "" if target == root_path else str(target.parent),
                    "is_root": target == root_path,
                    "root": str(root_path),
                    "shallow": True,
                    "probe_running": True,
                    "entries": entries,
                }
                return payload

        entries = _scan_nas_directory(target, shallow=shallow)
        is_root = target == root_path
        parent = "" if is_root else str(target.parent)
        payload = {
            "path": str(target),
            "parent": parent,
            "is_root": is_root,
            "root": str(root_path),
            "shallow": shallow,
            "entries": entries,
        }
        now = datetime.now(timezone.utc).timestamp()
        self._nas_browse_cache_store(root_path, cache_key, payload, now)
        payload["from_cache"] = False
        payload["cached_at"] = now
        payload["cache_age"] = 0.0
        # A shallow open only lists names. Kick off a background full listing so
        # leaf markers (`has_subdirs`) are ready for the operator's next visit /
        # refresh instead of costing a blocking wait on the very next click.
        if shallow and entries:
            self._schedule_nas_browse_probe(str(target))
        # While the operator looks at this level, pre-fetch a shallow listing of
        # every child directory so the next click into a sub-folder is served
        # from cache instead of waiting on the network share.
        if entries:
            self._schedule_nas_children_prefetch(str(target), entries)
        return payload

    @staticmethod
    def _nas_browse_cache_store(root_path: Path, cache_key: str, payload: dict[str, Any], now: float) -> None:
        """Insert a scanned payload into the bounded browse cache and persist it.

        Shared by the request path and every background writer (probe, rescan,
        prefetch, warm-up). Always copies the payload so the caller's dict can
        never mutate what a later cache hit returns."""
        with _NAS_BROWSE_CACHE_LOCK:
            _NAS_BROWSE_CACHE[cache_key] = (now, copy.deepcopy(payload))
            if len(_NAS_BROWSE_CACHE) > _NAS_BROWSE_CACHE_MAX_ENTRIES:  # bounded: drop the oldest half
                for stale_key in sorted(_NAS_BROWSE_CACHE, key=lambda key: _NAS_BROWSE_CACHE[key][0])[: len(_NAS_BROWSE_CACHE) // 2]:
                    _NAS_BROWSE_CACHE.pop(stale_key, None)
        _nas_browse_cache_save(str(root_path), now)

    @staticmethod
    def _nas_browse_scan_payload(target: Path, *, shallow: bool) -> tuple[dict[str, Any], Path, str] | None:
        """Re-read the configured NAS root and scan `target`, returning the
        payload plus the canonical root/key. None when the target escaped the
        current root or vanished. Used by background rescan/probe/prefetch/warm."""
        root_path = _nas_browse_resolve_root()
        if root_path is None:
            return None
        try:
            target = Path(target).resolve(strict=False)
            target.relative_to(root_path)
        except (OSError, ValueError):
            return None
        entries = _scan_nas_directory(target, shallow=shallow)
        payload = {
            "path": str(target),
            "parent": "" if target == root_path else str(target.parent),
            "is_root": target == root_path,
            "root": str(root_path),
            "shallow": shallow,
            "entries": entries,
        }
        cache_key = f"{root_path}|{'s' if shallow else 'f'}|{target}"
        return payload, root_path, cache_key

    @staticmethod
    def _schedule_nas_browse_rescan(target_str: str, *, shallow: bool) -> None:
        """Re-scan `target_str` in the background and refresh its cache entry.

        A stale entry was just served, so refresh it without blocking the
        request that found it stale (stale-while-revalidate). At most one
        re-scan per cache key runs at a time; the re-scan failing (NAS
        unreachable) simply leaves the old entry in place for the next visitor.
        """

        def run_rescan() -> None:
            try:
                rescanned = ApiHandler._nas_browse_scan_payload(Path(target_str), shallow=shallow)
                if rescanned is not None:
                    payload, root_path, cache_key = rescanned
                    ApiHandler._nas_browse_cache_store(
                        root_path,
                        cache_key,
                        payload,
                        datetime.now(timezone.utc).timestamp(),
                    )
            except (ApiRequestError, OSError):
                pass  # NAS unreachable / directory gone — stale copy stays usable
            finally:
                with _NAS_BROWSE_PROBE_LOCK:
                    _NAS_BROWSE_PROBE_INFLIGHT.discard(dedup_key)

        dedup_key = f"rescan|{'s' if shallow else 'f'}|{target_str}"
        if not shallow:
            # A full probe of the same directory already running will refresh
            # this cache entry when it finishes — do not scan the share twice.
            with _NAS_BROWSE_PROBE_LOCK:
                if target_str in _NAS_BROWSE_PROBE_INFLIGHT:
                    return
        with _NAS_BROWSE_PROBE_LOCK:
            if dedup_key in _NAS_BROWSE_PROBE_INFLIGHT:
                return
            _NAS_BROWSE_PROBE_INFLIGHT.add(dedup_key)
        try:
            _NAS_BROWSE_RESCAN_EXECUTOR.submit(run_rescan)
        except Exception:
            with _NAS_BROWSE_PROBE_LOCK:
                _NAS_BROWSE_PROBE_INFLIGHT.discard(dedup_key)

    @staticmethod
    def _schedule_nas_children_prefetch(target_str: str, entries: list[dict[str, Any]]) -> None:
        """Pre-fetch a shallow listing of every child directory of `target_str`.

        Called after the current level renders so the operator's next click into
        a sub-folder is served from cache instead of paying one network
        round-trip. Only children without a cached shallow listing are queued,
        each once at a time, up to `_NAS_BROWSE_PREFETCH_MAX_CHILDREN` per level
        so opening a huge series never floods the NAS with scans."""

        def run_child(child_path_str: str) -> None:
            try:
                prefetched = ApiHandler._nas_browse_scan_payload(Path(child_path_str), shallow=True)
                if prefetched is not None:
                    payload, root_path, cache_key = prefetched
                    ApiHandler._nas_browse_cache_store(
                        root_path,
                        cache_key,
                        payload,
                        datetime.now(timezone.utc).timestamp(),
                    )
            except (ApiRequestError, OSError):
                pass  # a child may vanish while the operator browses — tolerate
            finally:
                with _NAS_BROWSE_PROBE_LOCK:
                    _NAS_BROWSE_PREFETCH_INFLIGHT.discard(child_path_str)

        now = datetime.now(timezone.utc).timestamp()
        sep = "\\" if "\\" in target_str else "/"
        target = target_str.rstrip("\\/")
        cache_prefix_root = _nas_browse_resolve_root()
        if cache_prefix_root is None:
            return
        root_prefix = f"{cache_prefix_root}|"
        queued = 0
        for entry in entries:
            if not entry.get("is_dir"):
                continue
            child_path = f"{target}{sep}{entry['name']}"
            with _NAS_BROWSE_CACHE_LOCK:
                cached = _NAS_BROWSE_CACHE.get(f"{root_prefix}s|{child_path}")
            if cached is not None and now - cached[0] < _NAS_BROWSE_CACHE_FRESH_SECONDS:
                continue
            with _NAS_BROWSE_PROBE_LOCK:
                if child_path in _NAS_BROWSE_PREFETCH_INFLIGHT:
                    continue
                _NAS_BROWSE_PREFETCH_INFLIGHT.add(child_path)
            try:
                _NAS_BROWSE_PREFETCH_EXECUTOR.submit(run_child, child_path)
            except Exception:
                with _NAS_BROWSE_PROBE_LOCK:
                    _NAS_BROWSE_PREFETCH_INFLIGHT.discard(child_path)
                continue
            queued += 1
            if queued >= _NAS_BROWSE_PREFETCH_MAX_CHILDREN:
                break

    @staticmethod
    def _schedule_nas_browse_probe(target_str: str) -> None:
        """Run a full (leaf-probing) listing of `target_str` in the background
        and cache it under the full cache key. The shallow listing the operator is
        looking at stays interactive; when the probe finishes, a subsequent full
        browse of this path is served from the cache with `has_subdirs` filled in.
        At most one probe per directory runs at a time. The NAS being unreachable
        is tolerated — the shallow listing remains usable either way."""

        def run_probe() -> None:
            try:
                probed = ApiHandler._nas_browse_scan_payload(Path(target_str), shallow=False)
                if probed is not None:
                    payload, root_path, cache_key = probed
                    ApiHandler._nas_browse_cache_store(root_path, cache_key, payload, datetime.now(timezone.utc).timestamp())
            except (ApiRequestError, OSError):
                pass  # directory vanished / escaped root — shallow copy is still valid
            finally:
                with _NAS_BROWSE_PROBE_LOCK:
                    _NAS_BROWSE_PROBE_INFLIGHT.discard(target_str)

        with _NAS_BROWSE_PROBE_LOCK:
            if target_str in _NAS_BROWSE_PROBE_INFLIGHT:
                return
            _NAS_BROWSE_PROBE_INFLIGHT.add(target_str)
        try:
            thread = threading.Thread(target=run_probe, name=f"nas-browse-probe-{target_str!r}", daemon=True)
            thread.start()
        except Exception:
            with _NAS_BROWSE_PROBE_LOCK:
                _NAS_BROWSE_PROBE_INFLIGHT.discard(target_str)

    def _self_heal_automation_job(self, job_id: str) -> dict[str, Any]:
        # Recovery flow for a blocked automation job. Two things go wrong together
        # when a Mabang session tripping a captcha blocks a task:
        #   1) worker's in-process paused_reason is stuck, no more polls will claim jobs
        #   2) the original job is in a terminal (blocked/failed/needs_manual) status
        #      and won't be picked up even after worker resumes.
        # We requeue **the same job record** (storage.requeue_automation_job rebuilds
        # its execution snapshot from the current listing copy), then wake the worker
        # through a background queue. The operator sees one row per copy going back
        # to 待执行 instead of a new row per retry; the previous attempt stays in the
        # row's run_log, and the executor re-checks login on the current Chrome — if
        # the operator solved the captcha on the server desktop, the retry runs to
        # completion; if not, it goes back to blocked and the UI shows it via the
        # live poll.
        #
        # 2026-10-23: the wake used to run synchronously (_resume_workers: start the
        # process + prepare its browser + probe Mabang login, seconds to tens of
        # seconds). Rapid clicks on several「再次尝试」stacked those heavy calls in the
        # request threads and the page stuttered; requeue itself is ~0.2s. It now
        # goes through _schedule_retry_worker_wake, which coalesces clicks into one
        # background wake per batch (see the retry wake queue above).
        jobs = storage.list_automation_jobs()
        job = next((item for item in jobs if item.get("id") == job_id), None)
        if not job:
            raise ApiRequestError("Automation job not found", 404)
        if job.get("status") not in storage.AUTOMATION_RETRYABLE_STATUSES:
            raise ApiRequestError(
                f"Only blocked/failed jobs can self-heal. Current status: {job.get('status')}",
                status=409,
            )
        listing_copy_id = str(job.get("listing_copy_id") or "").strip()
        if not listing_copy_id:
            raise ApiRequestError("Job has no listing_copy_id, cannot regenerate", 422)

        result: dict[str, Any] = {"ok": True, "original_job_id": job_id, "steps": []}
        requeued: dict[str, Any] = {}

        # Step 1 — requeue this very job (same id, attempt_count back to 0).
        try:
            requeued = storage.requeue_automation_job(job_id)
            if not requeued.get("id"):
                result["ok"] = False
                result["steps"].append({
                    "step": "requeue_job",
                    "ok": False,
                    "error": requeued.get("error") or "unknown",
                    "missing_fields": requeued.get("missing_fields") or [],
                })
            else:
                result["job_id"] = requeued.get("id")
                result["job_status"] = requeued.get("status")
                result["retry_count"] = requeued.get("retry_count")
                result["missing_fields"] = requeued.get("missing_fields") or []
                result["steps"].append({
                    "step": "requeue_job",
                    "ok": True,
                    "job_id": requeued.get("id"),
                    "job_status": requeued.get("status"),
                    "retry_count": requeued.get("retry_count"),
                })
        except storage.ActiveAutomationJobExists as exc:
            result["ok"] = False
            result["steps"].append({
                "step": "requeue_job",
                "ok": False,
                "error": "该副本还有另一条活动任务，请先取消或等它完成再重试",
                "cause": str(exc),
            })
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["steps"].append({"step": "requeue_job", "ok": False, "error": str(exc)})

        # Step 2 — wake a worker for the requeued job through the background queue.
        # Only a ready job is claimable, so needs_manual / failed requeues are not
        # worth a browser wake-up. The request returns immediately; the queue
        # coalesces rapid retries and writes _RETRY_WAKE_STATE for diagnostics.
        if str(requeued.get("status") or "") == "ready":
            try:
                _schedule_retry_worker_wake(requeued)
                result["worker_start"] = {"ok": None, "status": "starting", "count": 1}
                result["steps"].append({
                    "step": "worker_wake",
                    "ok": True,
                    "status": "queued",
                    "background": True,
                })
            except Exception as exc:  # noqa: BLE001
                result["ok"] = False
                result["steps"].append({"step": "worker_wake", "ok": False, "error": str(exc)})

        # Hint for the UI: if the original block reason was a captcha / manual
        # verification, tell the operator to check the server-side Chrome. The
        # executor's next run will re-detect this and re-block if unresolved.
        original_error = str((job.get("result") or {}).get("error") or job.get("last_error") or "")
        if "needs_manual_login_or_verification" in original_error:
            result["hint"] = (
                "上一次因马帮登录/滑块验证被阻止：Worker 已尝试用绑定的账号自动登录；"
                "如遇滑块验证请 RDP 到服务器人工完成一次登录，或在 设置 → 马帮连接 检查绑定的账号密码。"
            )
        return result

    def _normalize_product_ids(self, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item or "").strip() for item in value if str(item or "").strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return []

    def _extract_product_ids(self, query: dict[str, list[str]]) -> list[str]:
        return self._normalize_product_ids((query.get("product_ids") or [""])[0])

    def _mabang_preview_payload(self, bundles: list[dict[str, Any]]) -> dict[str, Any]:
        return build_mabang_preview_payload(bundles)

    def _discard_request_body(self, length: int, keep_bytes: int = 8 * 1024 * 1024) -> None:
        """丢掉尚未读到的请求体（最多 keep_bytes）并关闭连接。

        超限请求多半还在上传中，留着未读字节会被 keep-alive 当成下一个请求；
        先关连接、再尽量读干净，客户端才能稳定收到 413 而不是网络错误。"""
        self.close_connection = True
        remaining = min(max(length, 0), keep_bytes)
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _read_json(self, *, max_bytes: int = MAX_JSON_BODY_BYTES) -> dict:
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError as exc:
            raise ApiRequestError("Invalid Content-Length", status=400) from exc
        if length <= 0:
            return {}
        if length > max_bytes:
            # 请求体超限时客户端还在发送：直接回错误会让连接状态错乱，浏览器只能报
            # "Failed to fetch"（操作员看不到真正的原因）。丢弃已到达的数据并关掉连接，
            # 保证 413 能被读到。
            self._discard_request_body(length)
            raise ApiRequestError("Request body too large", status=413)
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiRequestError("Invalid JSON body", status=400) from exc
        if not isinstance(payload, dict):
            raise ApiRequestError("JSON body must be an object", status=400)
        return payload

    _JSON_PRETTY_LIMIT = 16 * 1024

    def _send_json_bytes(self, plain: bytes, compressed: bytes | None, status: int) -> None:
        """发送已经序列化好的 JSON 字节（缓存命中时不再重新 dumps/gzip）。"""

        accept_encoding = self.headers.get("Accept-Encoding") or ""
        use_gzip = compressed is not None and "gzip" in accept_encoding.lower()
        body = compressed if use_gzip else plain
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        allowed_origin = self._allowed_cors_origin()
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Vary", "Origin, Accept-Encoding" if use_gzip else "Origin")
        elif use_gzip:
            self.send_header("Vary", "Accept-Encoding")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        """JSON 响应：大响应走紧凑格式 + gzip，GET 响应带 1 秒左右的并发缓存。

        现场首屏一次拉 7.4MB JSON（素材 2.3MB、副本 1.9MB、任务 1.3MB）。缩进版比紧凑版
        大 1.5~1.8 倍，而且服务器与浏览器都要多解析那些空格；gzip 后同一份数据只剩零头。
        小响应保留缩进，方便人直接看接口返回。

        多台电脑同时打开页面时，同一路径的 GET 会在内存里复用序列化 + gzip 后的字节；
        任何写入都会让缓存立刻失效（见 storage.register_change_listener）。
        """

        request_path = urlparse(self.path).path
        refresh_requested = str((parse_qs(urlparse(self.path).query).get("refresh") or [""])[0]).strip().lower() in {"1", "true", "yes"}
        cacheable = (
            self.command == "GET"
            and status == 200
            and settings.API_GET_CACHE_TTL_SECONDS > 0
            and request_path not in _GET_CACHE_EXCLUDED_PATHS
            and not refresh_requested
        )
        cache_key = self.path if cacheable else ""
        epoch = _get_cache_epoch() if cacheable else 0
        if cacheable:
            cached = _get_cached_response(cache_key)
            if cached is not None:
                plain, compressed = cached
                self._send_json_bytes(plain, compressed, status)
                return

        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        body = (
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            if len(compact) <= self._JSON_PRETTY_LIMIT
            else compact
        )
        accept_encoding = self.headers.get("Accept-Encoding") or ""
        # 小于 1KB 压了反而更大（gzip 头 + 短体），不值得。
        # 可缓存的 GET 即使当前客户端不支持 gzip 也预先压一份，后续客户端直接复用。
        compressed = None
        if len(body) >= 1024 and ("gzip" in accept_encoding.lower() or cacheable):
            compressed = gzip.compress(body, compresslevel=6)
        if cacheable and len(body) <= _GET_CACHE_MAX_ENTRY_BYTES:
            _store_cached_response(cache_key, body, compressed, epoch)
        self._send_json_bytes(body, compressed, status)

    # MIME types worth gzipping — text/code compresses well, images/fonts/video don't.
    _STATIC_GZIP_TYPES = frozenset({
        "text/html", "text/css", "text/javascript", "text/plain", "text/xml",
        "application/javascript", "application/json", "application/xml",
        "image/svg+xml",
    })

    def _serve_browser_extension_zip(self) -> None:
        ext_dir = settings.PROJECT_ROOT / "browser_extension"
        if not ext_dir.is_dir():
            self._json({"error": "Browser extension not found"}, status=404)
            return
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(ext_dir.rglob("*")):
                if file.is_file():
                    zf.write(file, f"browser_extension/{file.relative_to(ext_dir)}")
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="ai-listing-browser-extension.zip"')
        self.send_header("Content-Length", str(len(data)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        if path in ["", "/"]:
            file_path = settings.WEB_DIR / "index.html"
        else:
            safe_path = path.lstrip("/")
            file_path = settings.WEB_DIR / safe_path

        try:
            web_root = settings.WEB_DIR.resolve()
            resolved_file = file_path.resolve()
            resolved_file.relative_to(web_root)
        except ValueError:
            self.send_error(403, "Forbidden")
            return

        if not resolved_file.exists() or not resolved_file.is_file():
            self.send_error(404, "Not found")
            return

        stat = resolved_file.stat()
        # Weak ETag derived from (size, mtime_ns) — recomputed only when the file
        # actually changes on disk, so ordinary edits invalidate caches immediately.
        etag = f'W/"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            return

        content = resolved_file.read_bytes()
        content_type = mimetypes.guess_type(str(resolved_file))[0] or "application/octet-stream"
        if resolved_file.suffix == ".js":
            content_type = "text/javascript"

        # gzip when the client accepts it and the mime type benefits from compression.
        # Frontend assets like app.js (207KB → ~55KB) are the whole reason this exists.
        accept_enc = self.headers.get("Accept-Encoding") or ""
        use_gzip = "gzip" in accept_enc.lower() and content_type in self._STATIC_GZIP_TYPES
        if use_gzip:
            content = gzip.compress(content, compresslevel=6)

        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("ETag", etag)
        # `no-cache` = client MUST revalidate every time, but ETag lets that
        # revalidation return 304 with an empty body. Safe for actively developed
        # frontends: no stale JS after a deploy, no wasted bytes when unchanged.
        self.send_header("Cache-Control", "no-cache")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content)

    @staticmethod
    def _filter_assets(assets: list[dict], query: dict[str, list[str]]) -> list[dict]:
        group_id = (query.get("group_id") or [""])[0].strip().upper()
        category = (query.get("category") or [""])[0].strip().lower()
        brand = (query.get("brand") or [""])[0].strip().lower()

        def match(asset: dict) -> bool:
            if group_id and (asset.get("group_id") or "").upper() != group_id:
                return False
            if category and (asset.get("category") or "").lower() != category:
                return False
            if brand and (asset.get("brand") or "").lower() != brand:
                return False
            return True

        return [asset for asset in assets if match(asset)]

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))


WORKER_WATCHDOG_INTERVAL_SECONDS = 15.0


def _worker_watchdog_tick() -> None:
    """Heal a crashed worker once, honoring the ``auto_restart`` setting.

    Kept side-effect-light and exception-safe so the daemon loop can call it on
    a timer without ever taking the process down.
    """

    try:
        worker_config = storage.get_runtime_configuration()
    except Exception:  # noqa: BLE001 - never let a config read kill the watchdog
        return
    if worker_start_on_demand(worker_config):
        # 按需启动：有任务才唤醒、空闲够久就关闭。同步执行：没有活时只是一次只读判断
        # （微秒级）；真要起进程/关浏览器时才付几秒，而这几秒里没有别的看护要做
        # （崩溃自愈不差这几十秒，被看护的 Worker 本来就没在跑任务）。
        _worker_housekeeping_tick()
    if not worker_config["worker"].get("auto_restart", True):
        return
    multi = _worker_supervisors()
    if multi is not None:
        # 多账号部署下每个账号一个 Worker：看护的对象是账号 Worker，旧的全局
        # Worker 不再存在（见 _retire_legacy_worker），不能在这里把它复活。
        try:
            multi.auto_heal_all()
        except Exception as exc:  # noqa: BLE001 - a failed heal must not stop the loop
            print(f"Worker 自愈失败：{exc}", file=sys.stderr)
        return
    supervisor = _worker_supervisor()
    # 空闲关闭会主动把进程停成 stopped（不是 failed），自愈不该把它复活；
    # 只有 failed 才自愈，stopped/paused 是明确的选择。
    status = supervisor.status()
    if status.get("status") == "failed":
        try:
            supervisor.auto_heal()
        except Exception as exc:  # noqa: BLE001 - a failed heal must not stop the loop
            print(f"Worker 自愈失败：{exc}", file=sys.stderr)


def _start_worker_watchdog(stop_event: threading.Event) -> threading.Thread:
    def _loop() -> None:
        while not stop_event.wait(WORKER_WATCHDOG_INTERVAL_SECONDS):
            _worker_watchdog_tick()

    thread = threading.Thread(target=_loop, name="worker-watchdog", daemon=True)
    thread.start()
    return thread


STORAGE_CLEANUP_INTERVAL_SECONDS = 3600.0


def _start_storage_cleanup_scheduler(stop_event: threading.Event) -> threading.Thread:
    def _loop() -> None:
        if stop_event.wait(60.0):
            return
        while not stop_event.is_set():
            try:
                config = storage.get_runtime_configuration().get("storage_cleanup") or {}
                store = storage.load_store()
                disk_cleanup.run_automatic_cleanup_if_due(
                    config,
                    active_job_ids=disk_cleanup.active_automation_job_ids(store),
                    store=store,
                )
            except Exception as exc:  # noqa: BLE001 - cleanup must never stop API
                print(f"Storage cleanup failed: {exc}", file=sys.stderr)
            if stop_event.wait(STORAGE_CLEANUP_INTERVAL_SECONDS):
                return

    thread = threading.Thread(target=_loop, name="storage-cleanup", daemon=True)
    thread.start()
    return thread


def _start_nas_browse_warm(stop_event: threading.Event) -> threading.Thread:
    """Warm the NAS folder browser at startup (daemon thread, fully optional).

    After a restart the first "从 NAS 导入" dialog would otherwise wait for a
    cold scan of the network share. This thread pre-loads the disk cache, then
    shallow-scans the root and pre-fetches its immediate children in the
    background so the first dialog opening is usually served from cache too.
    The NAS being unreachable is silently tolerated — browsing simply falls
    back to the normal on-demand path, and startup is never delayed."""

    def _warm() -> None:
        try:
            if stop_event.wait(2.0):
                return  # shut down before we even started
            root_path = _nas_browse_resolve_root()
            if root_path is None:
                return
            now = datetime.now(timezone.utc).timestamp()
            _nas_browse_cache_invalidate_root(str(root_path))
            _nas_browse_cache_load(str(root_path), now)
            root_payload = ApiHandler._nas_browse_scan_payload(root_path, shallow=True)
            if root_payload is not None:
                payload, resolved_root, cache_key = root_payload
                ApiHandler._nas_browse_cache_store(resolved_root, cache_key, payload, datetime.now(timezone.utc).timestamp())
                entries = payload["entries"]
                ApiHandler._schedule_nas_children_prefetch(str(resolved_root), entries)
        except Exception as exc:  # noqa: BLE001 - warm-up must never crash startup
            print(f"NAS browse warm-up skipped: {exc}", file=sys.stderr)

    thread = threading.Thread(target=_warm, name="nas-browse-warm", daemon=True)
    thread.start()
    return thread


class ApiServer(ThreadingHTTPServer):
    """API 监听队列（多客户端）。

    一次页面加载会同时打出十几个请求，服务端忙（大列表序列化、图片读写）时
    accept 循环会被拖慢。socketserver 默认 listen backlog 只有 5，多出来的连接
    会被 Windows 直接 RST，浏览器侧只看到 "Failed to fetch" —— 首屏就会出现
    "N 项数据加载失败"。把队列放大，让突发排队而不是被拒。

    HTTP/1.1 keep-alive 由 ApiHandler 打开；这里同时给空闲连接一个超时，
    避免浏览器异常断开后线程永久占着。
    """

    request_queue_size = settings.API_REQUEST_QUEUE_SIZE
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        # HTTPServer.server_bind() 会对监听地址做一次反向 DNS（socket.getfqdn）。
        # 绑定 0.0.0.0 时这步在部分 Windows/DNS 环境下会卡住十几秒甚至更久，
        # 局域网开关一开服务就像“死了”。这里跳过反向解析，直接用地址本身。
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def handle_error(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        # 局域网/多客户端下客户端随时关页面：连接重置、断管、超时都是常态，
        # 不要为每条打印一整段 traceback 把日志刷满。
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


# 监听地址的生命周期：PUT /api/settings/network 只改持久化设置并请求重绑，
# 真正的 shutdown/重新 bind 由 serve 循环完成（不能在请求线程里直接 shutdown，
# 否则当前响应还没写完连接就被关了）。
_SERVER_STATE_LOCK = threading.Lock()
_REBIND_STATE_LOCK = threading.Lock()
_ACTIVE_SERVER = None
_ACTIVE_BIND_HOST = ""
_ACTIVE_BIND_PORT = 0
_PENDING_BIND_HOST = None


def _active_server_snapshot():
    with _SERVER_STATE_LOCK:
        return _ACTIVE_SERVER, _ACTIVE_BIND_HOST, _ACTIVE_BIND_PORT


def _schedule_network_rebind(target_host: str) -> bool:
    """请求把监听地址切到 target_host；返回是否真的安排了切换。"""

    global _PENDING_BIND_HOST
    server, current_host, _current_port = _active_server_snapshot()
    if server is None:
        return False
    if network_access.same_bind_host(target_host, current_host):
        return False
    with _REBIND_STATE_LOCK:
        _PENDING_BIND_HOST = network_access.normalize_bind_host(target_host)

    def _stop() -> None:
        # 等当前响应写完再关监听，避免设置页收到一半就断。
        time.sleep(0.45)
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001 - 重绑失败不能把请求线程带崩
            pass

    threading.Thread(target=_stop, name="api-rebind", daemon=True).start()
    return True


def _take_pending_bind_host() -> str | None:
    global _PENDING_BIND_HOST
    with _REBIND_STATE_LOCK:
        host = _PENDING_BIND_HOST
        _PENDING_BIND_HOST = None
    return host


def _local_api_url() -> str:
    """本机 Worker/子进程应使用的 API 地址。

    监听 0.0.0.0 时用 127.0.0.1；绑定到具体网卡 IP 时用那个 IP（否则子进程
    连 127.0.0.1 会失败）。
    """

    _server, host, port = _active_server_snapshot()
    host = network_access.normalize_bind_host(host)
    if not host or network_access.is_wildcard_host(host):
        host = "127.0.0.1"
    return f"http://{host}:{port or settings.API_PORT}"


def _serve_api_forever(initial_host: str) -> None:
    """监听循环：支持在同一个进程内切换监听地址（局域网开关即时生效）。"""

    global _ACTIVE_SERVER, _ACTIVE_BIND_HOST, _ACTIVE_BIND_PORT
    host = network_access.normalize_bind_host(initial_host) or "127.0.0.1"
    fallback_host = host
    bind_port = settings.API_PORT

    while True:
        try:
            server = ApiServer((host, bind_port), ApiHandler)
        except OSError as exc:
            print(f"API 无法监听 {host}:{bind_port}：{exc}", file=sys.stderr)
            if host != fallback_host:
                # 目标地址绑定失败（例如网卡 IP 已变化）：退回上一个可用地址，
                # 至少保证本机页面还能打开，设置页会显示实际监听地址。
                host = fallback_host
                continue
            time.sleep(2.0)
            continue

        actual_host, actual_port = server.server_address[0], int(server.server_address[1])
        bind_port = actual_port  # 端口 0（测试）时后续重绑保持同一个端口
        with _SERVER_STATE_LOCK:
            _ACTIVE_SERVER = server
            _ACTIVE_BIND_HOST = str(actual_host)
            _ACTIVE_BIND_PORT = actual_port

        print(f"AI Listing System M1 running at http://{actual_host}:{actual_port}")
        if network_access.is_lan_bind_host(str(actual_host)):
            lan_urls = [row["url"] for row in network_access.list_lan_addresses(actual_port)]
            if lan_urls:
                print("局域网访问地址：" + "  ".join(lan_urls))

        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
            with _SERVER_STATE_LOCK:
                _ACTIVE_SERVER = None
                _ACTIVE_BIND_HOST = ""
                _ACTIVE_BIND_PORT = 0

        next_host = _take_pending_bind_host()
        if next_host is None:
            return
        fallback_host = str(actual_host)
        host = next_host


def _warm_store_read_cache() -> None:
    """启动后立刻把 JSON 库解析进读缓存。

    否则重启后的第一个用户请求要独自承担整库解析（主库 + 任务 + 类目，实测
    500~800ms，`/api/deployment/readiness` 首屏就会卡这一下）。失败不影响服务。
    """

    try:
        storage.load_store()
    except Exception:
        pass


def main() -> None:
    storage.ensure_dirs()
    runtime_config = storage.get_runtime_configuration()
    worker_config = runtime_config["worker"]
    initial_host = network_access.resolve_bind_host(runtime_config.get("network") or {})

    if worker_config.get("auto_start"):
        if worker_start_on_demand({"worker": worker_config}):
            # 按需启动：API 起来时不拉 Worker，只有队列里已经有活才顺手唤醒它，
            # 否则等建任务/看护轮按需唤醒。这样「重启 API」不会再凭空拉起三个进程
            # 和它们的采集浏览器。
            try:
                outcome = _wake_workers_for_pending_jobs()
                if outcome.get("keys"):
                    print(f"Automation worker started on demand: {outcome.get('keys')}")
            except RuntimeError as exc:
                print(f"Automation worker did not auto-start: {exc}")
        else:
            try:
                _start_workers()
            except RuntimeError as exc:
                print(f"Automation worker did not auto-start: {exc}")
    stop_event = threading.Event()
    _start_worker_watchdog(stop_event)
    _start_storage_cleanup_scheduler(stop_event)
    _start_nas_browse_warm(stop_event)
    threading.Thread(target=_warm_store_read_cache, name="store-cache-warm", daemon=True).start()
    # 启动后后台静默检查一次 GitHub Release（配置了仓库才发请求），
    # 这样打开设置页时能直接看到缓存结果，不用操作员先点一次「检查更新」。
    _auto_check_updates_background()
    print(f"Project root: {settings.PROJECT_ROOT}")
    _serve_api_forever(initial_host)


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    main()
