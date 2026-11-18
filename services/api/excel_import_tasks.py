"""Background orchestration for the Excel product import.

Two phases:

* ``create_batch`` parses the workbook, matches existing main SKUs and writes
  ``plan.json`` next to the uploaded workbook. Nothing is downloaded yet.
* ``start_batch`` downloads the selected products' images into
  ``uploads/excel-imports/<batch>/<main_sku>/`` and creates one base product
  each. Progress is persisted after every product so a restart can report an
  interrupted batch and a retry reuses already-downloaded files.

Only base products are created here; store copies and listing jobs stay in the
existing operator-driven flow.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.api import storage
from services.api.excel_import_assets import fetch_image, download_image
from services.api.excel_product_import import (
    ExcelImportError,
    apply_existing_skus,
    parse_export_workbook,
    preview_payload,
    safe_file_stem,
)

PRODUCT_WORKERS = 2
IMAGE_WORKERS = 4
_BATCH_ID_PATTERN = re.compile(r"^[0-9A-Za-z._-]{1,80}$")

_TASKS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


class ExcelImportTaskError(ValueError):
    """A batch/task could not be created or started."""


def batch_root(storage_dir: Path) -> Path:
    return Path(storage_dir) / "uploads" / "excel-imports"


def batch_dir(storage_dir: Path, batch_id: str) -> Path:
    text = str(batch_id or "").strip()
    if not _BATCH_ID_PATTERN.fullmatch(text):
        raise ExcelImportTaskError("批次编号无效")
    return batch_root(storage_dir) / text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def create_batch(
    storage_dir: Path,
    content: bytes,
    *,
    file_name: str = "",
    existing_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Parse the workbook and persist a preview batch. No image downloads."""
    try:
        plan = parse_export_workbook(content)
    except ExcelImportError:
        raise
    plan["file_name"] = str(file_name or "").strip()
    if existing_keys is None:
        existing_keys = storage.existing_base_product_identifiers()
    apply_existing_skus(plan["products"], existing_keys)

    batch_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    directory = batch_dir(storage_dir, batch_id)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "source.xlsx").write_bytes(content)
    _write_json(directory / "plan.json", plan)

    return {
        "batch_id": batch_id,
        **preview_payload(plan),
    }


def load_plan(storage_dir: Path, batch_id: str) -> dict[str, Any]:
    plan = _read_json(batch_dir(storage_dir, batch_id) / "plan.json")
    if not plan:
        raise ExcelImportTaskError("导入批次不存在或已清理")
    return plan


def _summary(state: dict[str, Any]) -> dict[str, int]:
    counts = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    for item in state.get("items") or []:
        status = str(item.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = len(state.get("items") or [])
    counts["completed"] = counts["succeeded"] + counts["failed"] + counts["skipped"]
    return counts


def _update_item(task_id: str, key: str, **changes: Any) -> None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        state = task["state"]
        for item in state.get("items") or []:
            if item.get("key") == key:
                item.update(changes)
                break
        state["summary"] = _summary(state)
        state["updated_at"] = _now()


def _update_task(task_id: str, **changes: Any) -> None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        task["state"].update(changes)
        task["state"]["summary"] = _summary(task["state"])
        task["state"]["updated_at"] = _now()


def _persist(task_id: str) -> None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        state = json.loads(json.dumps(task["state"], ensure_ascii=False))
        path = Path(task["state_path"])
    _write_json(path, state)


def _product_directory(directory: Path, main_sku: str) -> Path:
    folder = directory / safe_file_stem(main_sku, "product")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _download_product_images(
    task_id: str,
    item_key: str,
    product: dict[str, Any],
    directory: Path,
    *,
    fetcher,
) -> tuple[list[str], dict[str, str]]:
    """Download all gallery + variant images.

    Returns ``(gallery_file_names_in_order, color -> variant_file_name)``.
    """
    folder = _product_directory(directory, str(product.get("main_sku") or "product"))
    gallery = list(product.get("gallery") or [])
    variant_images = dict(product.get("variant_images") or {})
    total = len(gallery) + len(variant_images)
    _update_item(task_id, item_key, status="running", stage="downloading", downloaded=0, total_images=total)

    gallery_paths: dict[str, Path] = {}
    failures: list[str] = []

    def _bump_downloaded() -> None:
        with _LOCK:
            task = _TASKS.get(task_id)
            if task:
                for item in task["state"].get("items") or []:
                    if item.get("key") == item_key:
                        item["downloaded"] = int(item.get("downloaded") or 0) + 1
                        break

    def _download(url: str, stem: str) -> Path:
        path = download_image(url, folder, stem, fetcher=fetcher)
        _bump_downloaded()
        return path

    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS, thread_name_prefix="excel-image") as executor:
        futures = {}
        for entry in gallery:
            url = str(entry.get("url") or "")
            stem = str(entry.get("file_stem") or "image")
            if url:
                futures[executor.submit(_download, url, stem)] = ("gallery", url, stem)
        for future in as_completed(futures):
            kind, url, stem = futures[future]
            try:
                gallery_paths[url] = future.result()
            except Exception as exc:  # noqa: BLE001 - report, do not abort the batch
                failures.append(f"{stem}: {exc}")

        variant_paths: dict[str, Path] = {}
        url_paths = dict(gallery_paths)
        # Colour images often reuse a gallery photo (same bytes, occasionally a
        # different CDN URL). Point the colour at the existing file instead of
        # keeping a second copy — the executor only needs a resolvable path.
        digest_paths: dict[str, Path] = {}
        for entry in gallery:
            path = gallery_paths.get(str(entry.get("url") or ""))
            if path is not None:
                digest_paths.setdefault(_file_digest(path), path)
        digest_lock = threading.Lock()
        variant_futures = {}
        for color, entry in variant_images.items():
            url = str((entry or {}).get("url") or "")
            stem = str((entry or {}).get("file_stem") or f"颜色-{safe_file_stem(str(color), 'color')}")
            if not url:
                failures.append(f"颜色 {color}: 缺少变种图")
                continue
            existing = url_paths.get(url)
            if existing is not None:
                variant_paths[color] = existing
                _bump_downloaded()
                continue
            variant_futures[executor.submit(_download, url, stem)] = (color, url, stem)
        for future in as_completed(variant_futures):
            color, url, stem = variant_futures[future]
            try:
                path = future.result()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"颜色 {color}: {exc}")
                continue
            digest = _file_digest(path)
            with digest_lock:
                duplicate = digest_paths.get(digest)
                if duplicate is None:
                    digest_paths[digest] = path
                else:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    path = duplicate
                url_paths[url] = path
            variant_paths[color] = path

    if failures:
        raise ExcelImportTaskError("图片下载失败：" + "；".join(failures[:3]))

    gallery_names = [
        gallery_paths[str(entry.get("url") or "")].name
        for entry in gallery
        if str(entry.get("url") or "") in gallery_paths
    ]
    return gallery_names, {
        color: variant_paths[color].name
        for color in variant_images
        if color in variant_paths
    }


def _run_product(
    task_id: str,
    directory: Path,
    product: dict[str, Any],
    *,
    fetcher,
) -> None:
    item_key = str(product.get("key") or "")
    main_sku = str(product.get("main_sku") or "")
    if product.get("existing"):
        # Skip before downloading: a re-import of the same workbook must not pull
        # hundreds of images just to discover the SKU already exists.
        _update_item(
            task_id,
            item_key,
            status="skipped",
            stage="skipped",
            error="主 SKU 已存在，已跳过",
        )
        return
    try:
        gallery_names, variant_names = _download_product_images(
            task_id, item_key, product, directory, fetcher=fetcher
        )
        _update_item(task_id, item_key, stage="creating")
        result = storage.create_base_product_from_excel({
            "main_sku": main_sku,
            "title": product.get("title") or "",
            "asset_root": str(_product_directory(directory, main_sku)),
            "gallery": gallery_names,
            "variant_images": variant_names,
            "colors": product.get("colors") or [],
            "source_batch_id": task_id,
            "source_meta": product.get("source_meta") or {},
        })
    except Exception as exc:  # noqa: BLE001 - one product must not kill the batch
        _update_item(task_id, item_key, status="failed", stage="failed", error=str(exc))
        return

    if result.get("status") == "skipped":
        _update_item(
            task_id,
            item_key,
            status="skipped",
            stage="skipped",
            error="主 SKU 已存在，已跳过",
            product_id=result.get("product_id") or "",
        )
        return
    product_payload = result.get("product") or {}
    _update_item(
        task_id,
        item_key,
        status="succeeded",
        stage="done",
        error="",
        product_id=product_payload.get("id") or "",
        asset_count=result.get("asset_count") or 0,
        warnings=product.get("warnings") or [],
    )


def _run_batch(
    task_id: str,
    directory: Path,
    products: list[dict[str, Any]],
    *,
    fetcher,
) -> None:
    _update_task(task_id, status="running")
    _persist(task_id)
    with ThreadPoolExecutor(max_workers=PRODUCT_WORKERS, thread_name_prefix="excel-product") as executor:
        futures = [
            executor.submit(_run_product, task_id, directory, product, fetcher=fetcher)
            for product in products
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:  # noqa: BLE001 - per-item failures are recorded
                pass
            _persist(task_id)
    _update_task(task_id, status="completed", completed_at=_now())
    _persist(task_id)


def start_batch(
    storage_dir: Path,
    batch_id: str,
    keys: list[str] | None = None,
    *,
    fetcher=fetch_image,
) -> dict[str, Any]:
    directory = batch_dir(storage_dir, batch_id)
    plan = load_plan(storage_dir, batch_id)
    products = list(plan.get("products") or [])
    wanted = {str(key or "").strip() for key in keys or [] if str(key or "").strip()}
    selected = [product for product in products if not wanted or str(product.get("key") or "") in wanted]
    if not selected:
        raise ExcelImportTaskError("没有选中任何商品")

    with _LOCK:
        running = _TASKS.get(batch_id)
        if running and str(running["state"].get("status")) == "running":
            raise ExcelImportTaskError("这个批次正在导入，请等待完成")

    items = [
        {
            "key": str(product.get("key") or ""),
            "main_sku": str(product.get("main_sku") or ""),
            "title": str(product.get("title") or ""),
            "colors": len(product.get("colors") or []),
            "total_images": len(product.get("gallery") or []) + len(product.get("variant_images") or {}),
            "status": "pending",
            "stage": "",
            "downloaded": 0,
            "error": "",
            "product_id": "",
        }
        for product in selected
    ]
    state = {
        "task_id": batch_id,
        "batch_id": batch_id,
        "status": "running",
        "created_at": _now(),
        "updated_at": _now(),
        "items": items,
        "summary": _summary({"items": items}),
        "warnings": plan.get("warnings") or [],
        "file_name": plan.get("file_name") or "",
    }
    with _LOCK:
        _TASKS[batch_id] = {"state": state, "state_path": str(directory / "state.json")}
    _persist(batch_id)

    thread = threading.Thread(
        target=_run_batch,
        args=(batch_id, directory, selected),
        kwargs={"fetcher": fetcher},
        name=f"excel-import-{batch_id}",
        daemon=True,
    )
    thread.start()
    return task_snapshot(storage_dir, batch_id) or state


def task_snapshot(storage_dir: Path, task_id: str) -> dict[str, Any] | None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if task:
            return json.loads(json.dumps(task["state"], ensure_ascii=False))
    try:
        directory = batch_dir(storage_dir, task_id)
    except ExcelImportTaskError:
        return None
    state = _read_json(directory / "state.json")
    if not state:
        return None
    if str(state.get("status") or "") == "running":
        # The background thread does not survive an API restart.
        state["status"] = "interrupted"
        state["interrupted"] = True
    return state


def delete_batch(storage_dir: Path, batch_id: str) -> dict[str, Any]:
    directory = batch_dir(storage_dir, batch_id)
    if not directory.is_dir():
        raise ExcelImportTaskError("导入批次不存在或已清理")
    with _LOCK:
        task = _TASKS.get(batch_id)
        if task and str(task["state"].get("status")) == "running":
            raise ExcelImportTaskError("批次正在导入，不能删除")
    state = _read_json(directory / "state.json") or {}
    if int((state.get("summary") or {}).get("succeeded") or 0) > 0:
        raise ExcelImportTaskError("批次里已有导入成功的商品，图片仍被商品引用，不能删除")
    shutil.rmtree(directory, ignore_errors=True)
    with _LOCK:
        _TASKS.pop(batch_id, None)
    return {"ok": True, "batch_id": batch_id}
