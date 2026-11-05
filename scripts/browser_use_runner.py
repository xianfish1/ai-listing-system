from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYWRIGHT_EXECUTOR_PATH = PROJECT_ROOT / "scripts" / "mabang_playwright_executor.cjs"
MABANG_WATERMARK_COLLECT_PATH = PROJECT_ROOT / "scripts" / "mabang_watermark_collect.cjs"
MABANG_PAGE_IMPORT_COLLECT_PATH = PROJECT_ROOT / "scripts" / "mabang_page_import_collect.cjs"
MABANG_PROMOTION_COLLECT_PATH = PROJECT_ROOT / "scripts" / "mabang_promotion_collect.cjs"
MABANG_PAGE_IMPORT_EXECUTOR_PATH = PROJECT_ROOT / "scripts" / "mabang_page_import_executor.cjs"
MABANG_PAGE_IMPORT_CAPTURE_PATH = PROJECT_ROOT / "scripts" / "mabang_page_import_capture.js"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api import settings, storage  # noqa: E402
from services.api.subprocess_utils import no_console_window_kwargs  # noqa: E402


SAVE_BUTTON_TEXT = "保存"
PUBLISH_BUTTON_TEXTS = ["保存并刊登", "刊登", "发布", "提交审核"]
FORBIDDEN_BUTTON_TEXTS = [SAVE_BUTTON_TEXT, *PUBLISH_BUTTON_TEXTS]
SHOPEE_TITLE_MAX_LENGTH = 100

# Executor prints progress events as MABANG_PLAYWRIGHT_EVENT {json} lines while it
# fills the form. Each line carries {event, ...data}; we keep the ordered stage
# trail (填写店铺 → 选类目 → 填基础信息 → SKU → 价格 → 物流 → 保存…) so the job
# detail can show exactly what happened and in what order.
PLAYWRIGHT_EVENT_MARKER = "MABANG_PLAYWRIGHT_EVENT "


def parse_playwright_step_trail(output: str) -> list[dict[str, Any]]:
    """Extract ordered executor progress steps from its stdout.

    Returns a list of {stage, ok, at, ...payload} in the order the executor emitted
    them (deduplicated per stage: only the first emission of a stage is kept, since
    retry attempts re-emit earlier stages and the trail should read top-to-bottom).
    """
    trail: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in (output or "").splitlines():
        if not line.startswith(PLAYWRIGHT_EVENT_MARKER):
            continue
        try:
            event = json.loads(line[len(PLAYWRIGHT_EVENT_MARKER):])
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        # Only the ordered form-filling stages (填写店铺/类目/SKU/价格/保存…);
        # attempt/retry bookkeeping is internal noise for the operator.
        if str(event.get("event") or "") != "stage":
            continue
        stage = str(event.get("stage") or "")
        if not stage or stage in seen:
            continue
        seen.add(stage)
        trail.append({
            "kind": "stage",
            "stage": stage,
            "ok": event.get("ok"),
            # 媒体阶段（视频/水印）用 status 表达结果（attached / skipped / warning…），
            # 只透传 ok 会让「视频已按模板默认视频附加」在执行步骤里显示成未完成；
            # code 供失败/跳过时的原因文案使用。
            "status": str(event.get("status") or ""),
            "code": str(event.get("code") or ""),
            "at": event.get("at") or "",
        })
    return trail


def _ascii_slug(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or fallback


def _shopee_listing_title(value: Any) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(title) <= SHOPEE_TITLE_MAX_LENGTH:
        return title
    clipped = title[:SHOPEE_TITLE_MAX_LENGTH].rstrip()
    boundary = clipped.rfind(" ")
    if boundary >= SHOPEE_TITLE_MAX_LENGTH // 2:
        return clipped[:boundary]
    return clipped


_NAS_COPY_ATTEMPTS = 4
_NAS_COPY_BACKOFF_SECONDS = 0.5


def _copy_with_retry(
    source_path: Path,
    upload_path: Path,
    *,
    attempts: int = _NAS_COPY_ATTEMPTS,
    backoff_seconds: float = _NAS_COPY_BACKOFF_SECONDS,
) -> None:
    """Copy a NAS asset, retrying transient Windows share errors.

    Network shares intermittently raise PermissionError/OSError ([WinError 5]
    拒绝访问, [WinError 64] 网络名不再可用) under load or when the SMB session
    briefly drops. These are not permanent failures, so retry with backoff
    before letting the exception bubble up and fail the whole job.
    """

    last_exc: OSError | None = None
    for attempt in range(attempts):
        try:
            shutil.copy2(source_path, upload_path)
            return
        except OSError as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(backoff_seconds * (attempt + 1))
    raise OSError(
        f"NAS copy {source_path} -> {upload_path} failed after {attempts} attempts: {last_exc}"
    ) from last_exc


def prepare_variant_thumbnail_uploads(
    variant_image_paths: dict[str, Any],
    upload_dir: Path,
) -> dict[str, dict[str, Any]]:
    upload_dir.mkdir(parents=True, exist_ok=True)
    uploads: dict[str, dict[str, Any]] = {}
    used_names: set[str] = set()

    for index, (color, raw_source) in enumerate(variant_image_paths.items(), start=1):
        source_text = str(raw_source or "").strip()
        if not source_text:
            continue
        source_path = Path(source_text)
        suffix = source_path.suffix.lower() or ".jpg"
        stem = _ascii_slug(color, f"variant-{index}")
        file_name = f"{stem}{suffix}"
        duplicate_index = 2
        while file_name in used_names:
            file_name = f"{stem}-{duplicate_index}{suffix}"
            duplicate_index += 1
        used_names.add(file_name)

        upload_path = upload_dir / file_name
        exists = source_path.is_file()
        if exists:
            _copy_with_retry(source_path, upload_path)
            _downscale_variant_thumbnail(upload_path)
        uploads[str(color)] = {
            "source_path": str(source_path),
            "upload_path": str(upload_path),
            "file_name": file_name,
            "exists": exists,
            "copied": exists,
        }

    return uploads


# 马帮变种图区对超大原图不显示缩略图：实测 4752×5120 的 PNG（16MB）传到
# 「自定义缩略图」后预览一直是空的，执行器等满 30s × 3 轮也等不到图片加载 →
# variant_thumbnail_not_verified；同一张图缩到 2048px 就能正常显示（其他颜色
# 的 2048×2048 原图一直没问题）。这里在生成上传副本时先把超限的边缩掉，
# 原图与商品素材不动。Pillow 缺失时退回原样上传（与旧行为一致）。
VARIANT_THUMBNAIL_MAX_SIDE = 2048


def _downscale_variant_thumbnail(path: Path) -> None:
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - 没有 Pillow 不能阻断任务
        return
    try:
        with Image.open(path) as image:
            if max(image.size) <= VARIANT_THUMBNAIL_MAX_SIDE:
                return
            image.thumbnail((VARIANT_THUMBNAIL_MAX_SIDE, VARIANT_THUMBNAIL_MAX_SIDE))
            if path.suffix.lower() in {".jpg", ".jpeg"}:
                image.convert("RGB").save(path, quality=90)
            else:
                image.save(path)
    except Exception:  # noqa: BLE001 - 坏图/编码器缺失都保持原文件
        return


def prepare_video_upload(video: dict[str, Any], upload_dir: Path) -> dict[str, Any]:
    prepared = dict(video or {})
    # 模板默认视频（本土）：只要带 videoId 就不再依赖上传文件——执行器直接在马帮
    # 视频库里按 ID 选择（视频标题下拉 → 视频ID → 搜索 → 选择）。副本没带视频文件
    # 也能走视频阶段。
    if str(prepared.get("default_video_id") or "").strip():
        prepared["default_video_id"] = str(prepared.get("default_video_id")).strip()
        prepared["select_by_id"] = True
        prepared["action_status"] = "ready"
        prepared["validation_status"] = "valid"
        prepared["validation_errors"] = []
        return prepared
    errors = list(prepared.get("validation_errors") or [])
    warnings = list(prepared.get("validation_warnings") or [])
    source_text = str(prepared.get("path") or "").strip()
    if prepared.get("validation_status") != "valid" or not source_text:
        prepared["action_status"] = "skipped"
        prepared["validation_errors"] = errors or ["video_missing"]
        prepared["validation_warnings"] = warnings
        return prepared

    source_path = Path(source_text)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    if not source_path.is_file():
        prepared["action_status"] = "skipped"
        prepared["validation_status"] = "invalid"
        prepared["validation_errors"] = [*errors, "video_file_missing"]
        prepared["validation_warnings"] = warnings
        return prepared

    video_dir = upload_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    upload_path = video_dir / "product.mp4"
    _copy_with_retry(source_path, upload_path)
    prepared.update({
        "source_path": str(source_path),
        "upload_path": str(upload_path),
        "action_status": "ready",
        "validation_errors": errors,
        # Metadata we could not verify locally (e.g. ffprobe missing) rides along as
        # a warning: the original file is uploaded as-is and the server decides. If it
        # rejects the clip, the media stage records the reason for human handling.
        "validation_warnings": warnings,
    })
    return prepared


def resolve_category_attribute_fields(
    category_attributes: dict[str, Any],
    category_profile_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve numeric Shopee attribute IDs into human-readable form labels.

    The stored ``category_attributes`` map ``{field_id: value_id(s)}`` using
    Shopee's numeric IDs, but the Mabang listing form matches attributes by their
    display label (e.g. "材料") and options by their display text (e.g.
    "棉花 (Cotton)"). This walks ``category_profile_snapshot.fields`` to produce
    the label-based structure the browser executor needs.
    """
    fields_by_id = {
        str(field.get("field_id")): field
        for field in (category_profile_snapshot.get("fields") or [])
        if isinstance(field, dict) and field.get("field_id") is not None
    }
    resolved: list[dict[str, Any]] = []
    for field_id, raw_value in category_attributes.items():
        field = fields_by_id.get(str(field_id))
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        value_ids = [str(value) for value in values if str(value or "").strip()]
        if not value_ids:
            continue
        options_by_value = {
            str(option.get("value")): str(option.get("label") or "")
            for option in ((field or {}).get("options") or [])
            if isinstance(option, dict)
        }
        option_labels = [options_by_value.get(value_id, "") for value_id in value_ids]
        # Fall back to the raw id only when the profile snapshot lacks the mapping,
        # so the executor still has something deterministic to match against.
        option_labels = [
            label or value_id for label, value_id in zip(option_labels, value_ids)
        ]
        # Free-text/allow-custom attributes (尺寸 100-150cm, 条件 Brand New, 型号…)
        # have no enum options on the Mabang form: fill them by typing, never by
        # opening a (nonexistent) dropdown. A select field whose stored value is not
        # among its options is likewise treated as a text input for automation.
        field_type = str((field or {}).get("type") or "").lower()
        labels_by_label = {label or value: value for value, label in options_by_value.items()}
        value_is_option = bool(options_by_value) and all(
            value_id in options_by_value or value_id in labels_by_label
            for value_id in value_ids
        )
        # 纯文本属性（尺寸/条件/型号，input_type=3）→ 打字填写。
        # 枚举属性但值不在枚举里（原产地=Philippines 这类"自选值"）→ 仍走下拉：
        # 马帮的下拉支持「搜索 → 添加自填选项 → 勾选」，执行器会走 select_custom
        # 分支；当成文本框会让它对着 readonly 的下拉输入框打字，值根本进不去。
        has_enum = bool(options_by_value)
        if field_type == "text" or not has_enum:
            input_kind = "text"
        elif value_is_option:
            input_kind = "select"
        else:
            input_kind = "select_custom"
        resolved.append({
            "field_id": str(field_id),
            "label": str((field or {}).get("label") or field_id),
            "type": str((field or {}).get("type") or ""),
            "required": bool((field or {}).get("required")),
            "value_ids": value_ids,
            "option_labels": option_labels,
            "input_kind": input_kind,
        })
    return resolved


def _build_watermark_task_slots(
    main_ref: dict[str, Any],
    product_ref: dict[str, Any],
    product_image_count: int,
) -> dict[str, dict[str, Any]]:
    """把模板上的两类水印快照编成执行器参数。

    每个 slot = ``{template_id, name, image_positions, image_range}``：
    - ``image_positions``：具体到第几张（证据 + 越界校验，执行器按它检查角色边界）；
    - ``image_range``：给人看、也给执行器后续做「选图片范围」这一步用的意图描述
      （操作员 2026-10-01 确认：马帮那边选完水印模板之后还要选图片范围）。
    mode="custom" 用操作员勾的位置；否则用默认（main=[1]，product=2..N）。
    """

    def _slot(ref: dict[str, Any], default_positions: list[int], role: str) -> dict[str, Any]:
        base = dict(ref or {})
        positions = base.pop("positions", None)
        mode = str(base.pop("mode", "auto") or "auto").lower()
        chosen: list[int]
        if mode == "custom" and isinstance(positions, list) and positions:
            filtered = []
            seen: set[int] = set()
            for entry in positions:
                try:
                    slot = int(entry)
                except (TypeError, ValueError):
                    continue
                if 1 <= slot <= max(1, product_image_count) and slot not in seen:
                    seen.add(slot)
                    filtered.append(slot)
            chosen = sorted(filtered) or list(default_positions)
        else:
            chosen = list(default_positions)
        return {**base, "image_positions": chosen, "image_range": _watermark_image_range(role, chosen, default_positions)}

    return {
        "main_image": _slot(main_ref, [1], "main_image"),
        "product_images": _slot(product_ref, list(range(2, product_image_count + 1)), "product_images"),
    }


def _watermark_image_range(role: str, chosen: list[int], default_positions: list[int]) -> dict[str, Any]:
    """水印要作用的图片范围（意图，不是点击脚本）。

    - main_image → ``first``：主图那一张；
    - product_images → 和默认 2..N 一致时 ``from_second``（第 2 张起），
      否则 ``explicit`` + 具体张数。
    """
    if role == "main_image":
        return {"kind": "first", "positions": [1]}
    if chosen == list(default_positions):
        return {"kind": "from_second", "positions": list(chosen)}
    return {"kind": "explicit", "positions": list(chosen)}


def build_browser_use_task_plan(
    payload: dict[str, Any],
    thumbnail_upload_dir: Path | None = None,
    *,
    allow_save: bool = False,
) -> dict[str, Any]:
    if not payload.get("ok"):
        return {
            "ok": False,
            "status": "failed",
            "error": payload.get("error") or "Automation payload is not available",
        }

    readiness = payload.get("readiness") or {}
    job = payload.get("job") or {}
    listing_copy = payload.get("listing_copy") or {}
    browser_inputs = payload.get("browser_inputs") or {}
    # browser_inputs 里的一份才是网格顺序（默认 SKU 行排在末尾，与 variation.colors 一致）。
    # 快照顶层曾按 color_code 排序、默认 SKU 夹在中间，直接取它会让执行器从那一行起整块
    # 错行——SKU 和单尺码覆盖价都落到别的颜色行上。优先取 browser_inputs，旧快照也能修正。
    skus = list(browser_inputs.get("skus") or payload.get("skus") or [])
    prices = list(browser_inputs.get("prices") or payload.get("prices") or [])
    missing_fields = list(readiness.get("missing_fields") or job.get("missing_fields") or [])
    if not readiness.get("ready"):
        return {
            "ok": False,
            "status": "blocked",
            "mode": "save_draft" if allow_save else "safe_fill",
            "job_id": job.get("id") or "",
            "missing_fields": missing_fields,
            "safety": {
                "allow_submit": False,
                "allow_save": bool(allow_save),
                "allow_publish": False,
                "forbidden_button_texts": PUBLISH_BUTTON_TEXTS if allow_save else FORBIDDEN_BUTTON_TEXTS,
            },
        }

    # 「产品图最后一张放尺码表」（本土开关）：真正传进马帮「产品图」区的是 form 列表，
    # 它的末位可能是尺码表。产品图计数按 form 里的**真实产品图**算，这样默认水印槽位
    # （2..N）不会盖到那张尺码表上。
    size_chart_last_product_image = bool(browser_inputs.get("size_chart_last_product_image"))
    product_image_form_paths = [
        str(path or "").strip()
        for path in (browser_inputs.get("product_image_form_paths") or [])
        if str(path or "").strip()
    ]
    if not product_image_form_paths:
        product_image_form_paths = [
            str(path or "").strip()
            for path in (browser_inputs.get("product_image_paths") or [])
            if str(path or "").strip()
        ]
    product_image_count = len(product_image_form_paths) - (1 if size_chart_last_product_image and product_image_form_paths else 0)

    upload_paths = [
        *product_image_form_paths,
        (
            (browser_inputs.get("size_chart") or {}).get("image_path")
            if (browser_inputs.get("size_chart") or {}).get("source") != "template"
            else ""
        ) or browser_inputs.get("size_chart_image_path") or "",
        *list(dict(browser_inputs.get("variant_image_paths") or {}).values()),
    ]
    missing_uploads = []
    for raw_path in upload_paths:
        path_text = str(raw_path or "").strip()
        if not path_text:
            continue
        candidate = Path(path_text)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        if not candidate.is_file():
            missing_uploads.append(Path(path_text).name or "unknown-file")
    if missing_uploads:
        return {
            "ok": False,
            "status": "blocked",
            "mode": "save_draft" if allow_save else "safe_fill",
            "job_id": job.get("id") or "",
            "error": "Automation upload files are missing",
            "missing_files": sorted(set(missing_uploads)),
            "safety": {
                "allow_submit": False,
                "allow_save": bool(allow_save),
                "allow_publish": False,
                "forbidden_button_texts": PUBLISH_BUTTON_TEXTS if allow_save else FORBIDDEN_BUTTON_TEXTS,
            },
        }

    upload_dir = thumbnail_upload_dir or PROJECT_ROOT / "tmp_upload" / "variant-thumbnails"
    thumbnail_uploads = prepare_variant_thumbnail_uploads(
        dict(browser_inputs.get("variant_image_paths") or {}),
        upload_dir,
    )
    video = prepare_video_upload(dict(browser_inputs.get("video") or {}), upload_dir)
    # 视频标题 = 主 SKU（跨境/本土一致）：马帮视频库里这条视频的名字。没有主 SKU
    # 时退回原文件名。执行器优先取 video_title。
    parent_sku = (
        browser_inputs.get("main_sku")
        or listing_copy.get("main_sku")
        or browser_inputs.get("group_id")
        or listing_copy.get("group_id")
        or ""
    )
    if str(parent_sku or "").strip():
        video["video_title"] = str(parent_sku).strip()
    # product_image_count（真实产品图张数，见上）用于默认水印槽位 2..N。
    category_attributes = {
        str(key): value
        for key, value in dict(browser_inputs.get("category_attributes") or {}).items()
        if str(key) != "display" and not isinstance(value, dict)
    }
    category_attribute_fields = resolve_category_attribute_fields(
        category_attributes,
        dict(browser_inputs.get("category_profile_snapshot") or {}),
    )

    return {
        "ok": True,
        "status": "ready",
        "mode": "save_draft" if allow_save else "safe_fill",
        "job_id": job.get("id") or "",
        "listing_copy_id": job.get("listing_copy_id") or listing_copy.get("id") or "",
        "source_url": "https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1",
        "safety": {
            "allow_submit": False,
            "allow_save": bool(allow_save),
            "allow_publish": False,
            "save_token": str(uuid.uuid4()) if allow_save else "",
            "stop_condition": "verify_saved_draft" if allow_save else "operator_review_before_save",
            "forbidden_button_texts": PUBLISH_BUTTON_TEXTS if allow_save else FORBIDDEN_BUTTON_TEXTS,
        },
        "store": {
            "name": listing_copy.get("store_name") or job.get("store_name") or browser_inputs.get("store_name") or "",
            "mabang_store_id": browser_inputs.get("mabang_store_id") or "",
            "listing_mode": browser_inputs.get("listing_mode") or listing_copy.get("listing_mode") or "site",
            "cnsc_store": dict(browser_inputs.get("cnsc_store") or {}),
            "target_site_stores": list(browser_inputs.get("target_site_stores") or []),
            "default_site_discount_percent": browser_inputs.get("default_site_discount_percent") or 40,
        },
        "category": {
            "path": browser_inputs.get("marketplace_category_path") or "",
            "mabang_category_id": browser_inputs.get("mabang_category_id") or "",
        },
        "category_attributes": category_attributes,
        "category_attribute_fields": category_attribute_fields,
        # AI 属性推荐使用冻结的商品事实作为上下文；模板缺失时仍由页面现场
        # options 约束可选值，重试不会重新读取已变化的基础商品。
        "recognized_attributes": deepcopy(listing_copy.get("recognized_attributes") or {}),
        "recognized_colors": list(listing_copy.get("recognized_colors") or []),
        "basic_info": {
            "title": _shopee_listing_title(browser_inputs.get("title") or listing_copy.get("listing_title")),
            "description": browser_inputs.get("description") or listing_copy.get("description") or "",
            # Parent SKU = the product's 主 SKU (main_sku), which the operator edits
            # on the base product and which prefixes the child SKU codes. Fall back
            # to group_id (folder code) only for legacy copies that predate main_sku.
            "parent_sku": parent_sku,
        },
        "images": {
            "main_image_path": browser_inputs.get("main_image_path") or "",
            # 真实产品图（描述插图按它插；末位放尺码表的那一份见 product_image_form_paths）。
            "product_image_paths": list(browser_inputs.get("product_image_paths") or []),
            # 实际上传到「产品图」区的顺序，末尾可能是尺码表（本土开关）。
            "product_image_form_paths": product_image_form_paths,
            "size_chart_last_product_image": size_chart_last_product_image,
            "size_chart_slot_skipped": browser_inputs.get("size_chart_slot_skipped") or "",
            "size_chart_image_path": browser_inputs.get("size_chart_image_path") or "",
            "size_chart": dict(browser_inputs.get("size_chart") or {
                "source": "upload",
                "template_id": "",
                "template_name": "",
                "image_path": browser_inputs.get("size_chart_image_path") or "",
            }),
        },
        "video": video,
        "watermarks": _build_watermark_task_slots(
            browser_inputs.get("main_image_watermark") or {},
            browser_inputs.get("product_image_watermark") or {},
            product_image_count,
        ),
        "variation": browser_inputs.get("variation") or {},
        "discount": {
            "label": browser_inputs.get("discount_label") or listing_copy.get("discount_label") or "",
            "mabang_discount_id": browser_inputs.get("mabang_discount_id") or "",
        },
        "cnsc": {
            "enabled": (browser_inputs.get("listing_mode") or listing_copy.get("listing_mode") or "site") == "cnsc_global",
            "price_tiers": list(browser_inputs.get("price_tiers") or []),
            "default_site_discount_percent": browser_inputs.get("default_site_discount_percent") or 40,
            "warehouse_stock_defaults": dict(browser_inputs.get("warehouse_stock_defaults") or {}),
        },
        "variant_thumbnail_uploads": thumbnail_uploads,
        "sku_grid": {
            "expected_rows": int(browser_inputs.get("sku_count") or len(skus)),
            "skus": skus,
            "prices": prices,
            "listing_mode": browser_inputs.get("listing_mode") or listing_copy.get("listing_mode") or "site",
            "warehouse_stock_defaults": dict(browser_inputs.get("warehouse_stock_defaults") or {}),
            "transaction_information": dict(browser_inputs.get("transaction_info") or {}),
        },
        "actions": [
            {"type": "open_listing_workspace", "label": "打开马帮 Shopee 商品工作台"},
            {"type": "open_draft_product", "label": "进入待发布商品"},
            {"type": "select_store", "label": "选择店铺"},
            {"type": "select_category", "label": "选择类目"},
            {"type": "set_category_attributes", "label": "填写分类属性"},
            {"type": "fill_basic_info", "label": "填写标题、描述和 Parent SKU"},
            {"type": "upload_product_images", "label": "上传并排序 9 张商品图"},
            *([] if (browser_inputs.get("listing_mode") or listing_copy.get("listing_mode") or "site") == "cnsc_global" else [{"type": "append_description_media", "label": "在商品描述末尾追加尺码表与全部产品图"}]),
            {"type": "apply_main_image_watermark", "label": "应用主图水印"},
            {"type": "apply_product_image_watermark", "label": "应用产品图水印"},
            {"type": "upload_product_video", "label": "上传商品视频"},
            {"type": "configure_variants", "label": "创建 color 和 size 变种"},
            {"type": "fill_sku_grid", "label": "填写 SKU 表格"},
            *([{"type": "fill_cnsc_site_prices_and_discounts", "label": "填写站点价格、活动和 %OFF"}] if (browser_inputs.get("listing_mode") or listing_copy.get("listing_mode") or "site") == "cnsc_global" else []),
            *([] if (browser_inputs.get("listing_mode") or listing_copy.get("listing_mode") or "site") == "cnsc_global" else [{"type": "select_discount_activity", "label": "选择折扣活动"}]),
            {"type": "upload_variant_thumbnails", "label": "上传颜色缩略图"},
            *(
                [{"type": "save_draft", "label": "保存马帮草稿，不刊登"}]
                if allow_save
                else [{"type": "stop_for_operator_review", "label": "停在人工检查，不保存"}]
            ),
        ],
    }


def build_mabang_login_recovery_script(
    *,
    account_env: str = "MABANG_ACCOUNT",
    password_env: str = "MABANG_PASSWORD",
) -> str:
    account_env_json = json.dumps(account_env, ensure_ascii=True)
    password_env_json = json.dumps(password_env, ensure_ascii=True)
    workspace_url = "https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1"
    workspace_url_json = json.dumps(workspace_url, ensure_ascii=True)
    return f"""from __future__ import annotations

import json
import os
import time

ACCOUNT_ENV = {account_env_json}
PASSWORD_ENV = {password_env_json}
WORKSPACE_URL = {workspace_url_json}


def emit(event, **data):
    print("LOGIN_RECOVERY_EVENT " + json.dumps({{"event": event, **data}}, ensure_ascii=True))


def js_eval(script):
    for helper_name in ("execute_js", "evaluate_js", "eval_js", "run_js", "js"):
        helper = globals().get(helper_name)
        if callable(helper):
            return helper(script)
    raise RuntimeError("No Browser-Use JavaScript helper is available")


def open_workspace():
    for helper_name in ("go_to", "navigate", "open_url"):
        helper = globals().get(helper_name)
        if callable(helper):
            helper(WORKSPACE_URL)
            return
    js_eval("location.href = " + json.dumps(WORKSPACE_URL) + "; true")


def page_login_state():
    return js_eval(r'''
(() => {{
  const text = document.body?.innerText || "";
  return {{
    url: location.href,
    title: document.title,
    needsLogin: location.href.includes("index.htm") || !!document.querySelector("#account,#password,#account-btn"),
    hasAccountInput: !!document.querySelector("#account"),
    hasPasswordInput: !!document.querySelector("#password"),
    hasLoginButton: !!document.querySelector("#account-btn"),
    needsManualLogin: /短信|验证码|滑块|captcha|verify|verification/i.test(text)
  }};
}})()
''')


def perform_login():
    account = os.environ.get(ACCOUNT_ENV, "")
    password = os.environ.get(PASSWORD_ENV, "")
    if not account or not password:
        return {{"ok": False, "status": "needs_manual_login", "reason": "missing_login_environment"}}
    login_js = r'''
((payload) => {{
  const account = payload.account;
  const password = payload.password;
  const accountInput = document.querySelector("#account");
  const passwordInput = document.querySelector("#password");
  const loginButton = document.querySelector("#account-btn");
  if (!accountInput || !passwordInput || !loginButton) {{
    return {{ ok: false, status: "needs_manual_login", reason: "login_controls_not_found" }};
  }}
  const setValue = (input, value) => {{
    input.focus();
    input.value = value;
    input.dispatchEvent(new Event("input", {{ bubbles: true }}));
    input.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }};
  setValue(accountInput, account);
  setValue(passwordInput, password);
  loginButton.click();
  return {{ ok: true, status: "login_submitted" }};
}})(''' + json.dumps({{"account": account, "password": password}}, ensure_ascii=False) + ")"
    return js_eval(login_js)


ensure_real_tab()
open_workspace()
time.sleep(1)
state = page_login_state()
emit("state", result=state)
result = {{"ok": True, "status": "already_logged_in", "state": state}}
if state.get("needsManualLogin"):
    result = {{"ok": False, "status": "needs_manual_login", "state": state}}
elif state.get("needsLogin"):
    result = perform_login()
    time.sleep(2)
    result = {{"ok": bool(result.get("ok")), "status": result.get("status"), "state": page_login_state(), "login": result}}
print("LOGIN_RECOVERY_RESULT " + json.dumps(result, ensure_ascii=True))
"""


def build_mabang_workspace_navigation_script() -> str:
    workspace_url = "https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1"
    workspace_url_json = json.dumps(workspace_url, ensure_ascii=True)
    pending_label = json.dumps("待发布商品", ensure_ascii=True)
    new_listing_label = json.dumps("新增刊登", ensure_ascii=True)
    cnsc_pending_label = json.dumps("\u0043\u004e\u0053\u0043\u5f85\u53d1\u5e03\u5546\u54c1", ensure_ascii=True)
    local_pending_label = json.dumps("\u672c\u571f\u5f85\u53d1\u5e03\u5546\u54c1", ensure_ascii=True)
    return f"""from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse
import base64

WORKSPACE_URL = {workspace_url_json}
PENDING_LABEL = {pending_label}
NEW_LISTING_LABEL = {new_listing_label}
CNSC_PENDING_LABEL = {cnsc_pending_label}
LOCAL_PENDING_LABEL = {local_pending_label}
CLICK_FRAGMENT = \"\"\"
const visible = (el) => {{
  const s = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
}};
const all = [...document.querySelectorAll("button,a,span,div,li")]
  .filter(visible)
  .filter((el) => (el.innerText || el.textContent || "").trim().includes(label));
let target = all[0];
if (all.length > 1) {{
  // Tab groups often render a long parent item next to a short child item
  // (e.g. CNSC pending / local pending). Label-matched
  // inside a longer text usually means the parent container, not the item.
  all.sort((a, b) => (a.innerText || "").trim().length - (b.innerText || "").trim().length);
  target = all[0];
}}
if (!target) return {{ ok: false, reason: "text_not_found", label }};
target.click();
return {{ ok: true, label, text: (target.innerText || target.textContent || "").trim() }};
\"\"\"


def emit(event, **data):
    print("WORKSPACE_NAVIGATION_EVENT " + json.dumps({{"event": event, **data}}, ensure_ascii=True))


def js_eval(script):
    for helper_name in ("execute_js", "evaluate_js", "eval_js", "run_js", "js"):
        helper = globals().get(helper_name)
        if callable(helper):
            return helper(script)
    raise RuntimeError("No Browser-Use JavaScript helper is available")


def open_mabang_workspace():
    ensure_real_tab()
    for helper_name in ("go_to", "navigate", "open_url"):
        helper = globals().get(helper_name)
        if callable(helper):
            helper(WORKSPACE_URL)
            emit("workspace_open_requested", url=WORKSPACE_URL)
            return
    js_eval("location.href = " + json.dumps(WORKSPACE_URL) + "; true")
    emit("workspace_open_requested", url=WORKSPACE_URL)


def click_text_in_workspace(label):
    script = "((label) => {" + CLICK_FRAGMENT + "})(" + json.dumps(label, ensure_ascii=True) + ")"
    return js_eval(script)


def click_local_pending_products():
    # Mabang workspace has both a global pending tab and a CNSC pending tab.
    # A local (site) task must click the local pending tab; clicking the generic
    # pending label can land on the global tab and wander into the CNSC flow.
    result = click_text_in_workspace(LOCAL_PENDING_LABEL)
    emit("local_pending_clicked", result=result)
    if not result.get("ok"):
        result = click_text_in_workspace(PENDING_LABEL)
        emit("local_pending_fallback_clicked", result=result)
    return result


def click_new_listing():
    result = click_text_in_workspace(NEW_LISTING_LABEL)
    emit("new_listing_clicked", result=result)
    return result


def is_mabang_shopee_detail_url(url):
    if "productDetailShopee" in url:
        return True
    if "main.goPublish" not in url:
        return False
    encoded = (parse_qs(urlparse(url).query).get("url") or [""])[0]
    if not encoded:
        return False
    padded = encoded + ("=" * (-len(encoded) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return False
    return "productDetailShopee" in decoded


def wait_for_detail_tab(timeout_seconds=15):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for tab in list_tabs():
            url = tab.get("url", "")
            if is_mabang_shopee_detail_url(url):
                switch_tab(tab.get("target_id") or tab.get("id"))
                return {{"ok": True, "url": url}}
        time.sleep(0.5)
    return {{"ok": False, "status": "needs_manual_detail_tab"}}


open_mabang_workspace()
time.sleep(2)
pending = click_local_pending_products()
time.sleep(1)
created = click_new_listing() if pending.get("ok") else {{"ok": False, "status": "needs_manual_pending_tab"}}
detail = wait_for_detail_tab() if created.get("ok") else {{"ok": False, "status": "needs_manual_create_button"}}
result = {{"ok": bool(detail.get("ok")), "pending": pending, "created": created, "detail": detail}}
print("WORKSPACE_NAVIGATION_RESULT " + json.dumps(result, ensure_ascii=True))
"""


def build_browser_use_safe_fill_script(task_plan: dict[str, Any]) -> str:
    encoded_task = base64.b64encode(json.dumps(task_plan, ensure_ascii=False).encode("utf-8")).decode("ascii")
    forbidden_texts = json.dumps(task_plan.get("safety", {}).get("forbidden_button_texts") or FORBIDDEN_BUTTON_TEXTS, ensure_ascii=True)
    publish_texts = json.dumps(PUBLISH_BUTTON_TEXTS, ensure_ascii=True)
    save_text = json.dumps(SAVE_BUTTON_TEXT, ensure_ascii=True)
    return f"""from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

TASK_PLAN = json.loads(base64.b64decode("{encoded_task}").decode("utf-8"))
FORBIDDEN_BUTTON_TEXTS = {forbidden_texts}
PUBLISH_BUTTON_TEXTS = {publish_texts}
SAVE_BUTTON_TEXT = {save_text}


def emit(event, **data):
    print("SAFE_FILL_EVENT " + json.dumps({{"event": event, **data}}, ensure_ascii=True))


def require_safe_plan():
    safety = TASK_PLAN.get("safety") or {{}}
    if not TASK_PLAN.get("ok"):
        raise RuntimeError("Task plan is not ready")
    if TASK_PLAN.get("mode") not in {{"safe_fill", "save_draft"}}:
        raise RuntimeError("Task plan mode must be safe_fill or save_draft")
    if safety.get("allow_submit"):
        raise RuntimeError("Refusing to run a plan that allows submit")
    if safety.get("allow_publish"):
        raise RuntimeError("Refusing to run a plan that allows publish")
    if TASK_PLAN.get("mode") == "save_draft" and (not safety.get("allow_save") or not safety.get("save_token")):
        raise RuntimeError("Save-draft plan requires allow_save and a one-time save token")
    actions = TASK_PLAN.get("actions") or []
    blocked = {{"save", "submit", "publish", "save_and_publish"}}
    unsafe = [action for action in actions if action.get("type") in blocked]
    if unsafe:
        raise RuntimeError("Refusing unsafe action types: " + json.dumps(unsafe, ensure_ascii=False))


def js_eval(script):
    for helper_name in ("execute_js", "evaluate_js", "eval_js", "run_js", "js"):
        helper = globals().get(helper_name)
        if callable(helper):
            return helper(script)
    raise RuntimeError("No Browser-Use JavaScript helper is available")


def is_mabang_shopee_detail_url(url):
    if "productDetailShopee" in url:
        return True
    if "main.goPublish" not in url:
        return False
    encoded = (parse_qs(urlparse(url).query).get("url") or [""])[0]
    if not encoded:
        return False
    padded = encoded + ("=" * (-len(encoded) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return False
    return "productDetailShopee" in decoded


def switch_to_mabang_detail_page():
    tabs = list_tabs()
    for tab in tabs:
        url = tab.get("url", "")
        if is_mabang_shopee_detail_url(url):
            switch_tab(tab.get("target_id") or tab.get("id"))
            emit("tab_selected", url=url)
            return url
    info = page_info()
    emit("tab_not_found", page_info=str(info))
    return ""


def install_publish_guard():
    guard_js = r'''
(() => {{
  const forbidden = {forbidden_texts};
  const state = window.__AI_LISTING_SAFE_GUARD__ || {{ blockedClicks: 0 }};
  state.forbidden = forbidden;
  window.__AI_LISTING_SAFE_GUARD__ = state;
  const textOf = (node) => (node && (node.innerText || node.textContent || node.value || "") || "").trim();
  const closestAction = (node) => {{
    let cur = node;
    for (let i = 0; cur && i < 6; i += 1, cur = cur.parentElement) {{
      const text = textOf(cur);
      if (forbidden.some((label) => text.includes(label))) return {{ node: cur, text }};
    }}
    return null;
  }};
  if (!window.__AI_LISTING_SAFE_GUARD_INSTALLED__) {{
    document.addEventListener("click", (event) => {{
      const match = closestAction(event.target);
      if (match) {{
        state.blockedClicks += 1;
        event.preventDefault();
        event.stopImmediatePropagation();
        console.warn("[ai-listing] blocked forbidden Mabang action", match.text);
      }}
    }}, true);
    window.__AI_LISTING_SAFE_GUARD_INSTALLED__ = true;
  }}
  return {{
    installed: true,
    forbidden,
    blockedClicks: state.blockedClicks,
    url: location.href,
    title: document.title
  }};
}})()
'''
    result = js_eval(guard_js)
    emit("publish_guard_installed", result=result)
    return result


def click_save_draft_once():
    safety = TASK_PLAN.get("safety") or {{}}
    if TASK_PLAN.get("mode") != "save_draft" or not safety.get("allow_save"):
        return {{"ok": False, "reason": "save_not_allowed", "save_clicked": False, "publish_clicked": False}}
    save_js = r'''
(() => {{
  const saveButtonText = __SAVE_BUTTON_TEXT__;
  const publishButtonTexts = __PUBLISH_BUTTON_TEXTS__;
  const task = window.__AI_LISTING_TASK_PLAN__ || {{}};
  const safety = task.safety || {{}};
  const state = window.__AI_LISTING_SAVE_STATE__ || {{ saveToken: safety.save_token || "", used: false }};
  window.__AI_LISTING_SAVE_STATE__ = state;
  if (!state.saveToken || state.used || state.saveToken !== safety.save_token) {{
    return {{ ok: false, reason: "save_token_invalid_or_used", save_clicked: false, publish_clicked: false }};
  }}
  const actionNodes = [...document.querySelectorAll("button, [role=button], input[type=button], input[type=submit]")];
  const textOf = (node) => ((node.innerText || node.textContent || node.value || "").trim());
  const candidates = actionNodes.filter((node) => textOf(node) === saveButtonText && !publishButtonTexts.some((label) => textOf(node).includes(label)));
  if (candidates.length !== 1) {{
    return {{ ok: false, reason: "exact_save_button_not_unique", count: candidates.length, save_clicked: false, publish_clicked: false }};
  }}
  state.used = true;
  state.saveToken = "";
  HTMLElement.prototype.click.call(candidates[0]);
  return {{ ok: true, save_clicked: true, publish_clicked: false, text: textOf(candidates[0]) }};
}})()
'''
    save_js = save_js.replace("__SAVE_BUTTON_TEXT__", json.dumps(SAVE_BUTTON_TEXT, ensure_ascii=True))
    save_js = save_js.replace("__PUBLISH_BUTTON_TEXTS__", json.dumps(PUBLISH_BUTTON_TEXTS, ensure_ascii=True))
    result = js_eval(save_js)
    emit("save_draft_clicked", result=result)
    return result


def verify_save_draft_result():
    verify_js = r'''
(() => {{
  const bodyText = (document.body?.innerText || "").trim();
  const successText = ["\\u4fdd\\u5b58\\u6210\\u529f", "\\u64cd\\u4f5c\\u6210\\u529f", "\\u4fdd\\u5b58\\u8349\\u7a3f\\u6210\\u529f"].find((text) => bodyText.includes(text)) || "";
  const url = location.href;
  const hasProductId = /(?:productId|id)=[0-9]+/.test(url) || !!document.querySelector("[data-product-id]");
  return {{
    ok: !!successText || hasProductId,
    success_text: successText,
    has_product_id: hasProductId,
    url: location.origin + location.pathname,
    save_clicked: true,
    publish_clicked: false
  }};
}})()
'''
    result = js_eval(verify_js)
    emit("save_draft_verified", result=result)
    return result


def install_task_payload():
    task_json = json.dumps(TASK_PLAN, ensure_ascii=False)
    payload_js = "window.__AI_LISTING_TASK_PLAN__ = " + task_json + "; ({{ok:true, jobId: window.__AI_LISTING_TASK_PLAN__.job_id}})"
    result = js_eval(payload_js)
    emit("task_payload_installed", result=result)
    return result


def run_sku_grid_safe_fill():
    fill_js = r'''
(() => {{
  const task = window.__AI_LISTING_TASK_PLAN__ || {{}};
  const grid = task.sku_grid || {{}};
  const skus = grid.skus || [];
  const prices = grid.prices || [];
  const transaction = grid.transaction_information || {{}};
  // Only `stock` is required per row. Weight/package dims are product-level (物流信息
  // card) when 分sku设置件重尺 is unchecked, so their columns may be absent from the
  // grid; setFirstExisting below writes them only when the column still exists.
  if (transaction.stock === undefined || transaction.stock === null || transaction.stock === "") {{
    return {{ ok: false, reason: "transaction_information_incomplete" }};
  }}
  const tables = [...document.querySelectorAll(".vxe-table")].map((el) => el.__vue__).filter(Boolean);
  const skuTable = tables.find((vm) => (vm.tableColumn || []).some((col) => col.field === "sku") && (vm.tableData || []).length);
  if (!skuTable) return {{ ok: false, reason: "sku_table_not_found", expectedRows: grid.expected_rows || skus.length }};
  const rows = skuTable.tableData || [];
  const priceBySku = new Map(prices.map((price) => [price.sku_code, price]));
  const setFirstExisting = (row, names, value) => {{
    for (const name of names) {{
      if (Object.prototype.hasOwnProperty.call(row, name)) {{
        row[name] = value;
        return name;
      }}
    }}
    return "";
  }};
  rows.forEach((row, index) => {{
    const sku = skus[index] || {{}};
    const price = priceBySku.get(sku.sku_code) || prices[index] || {{}};
    setFirstExisting(row, ["sku", "sellerSku", "variationSku"], sku.sku_code || row.sku || "");
    setFirstExisting(row, ["stock", "quantity", "availableStock"], transaction.stock);
    setFirstExisting(row, ["price", "sitePrice", "originalPrice"], price.normal_price || row.price || "");
    setFirstExisting(row, ["discountPrice", "salePrice", "promotionPrice"], price.sale_price || row.discountPrice || "");
    setFirstExisting(row, ["weight"], transaction.weight_kg);
    setFirstExisting(row, ["packageLength", "length"], transaction.package_length_cm);
    setFirstExisting(row, ["packageWidth", "width"], transaction.package_width_cm);
    setFirstExisting(row, ["packageHeight", "height"], transaction.package_height_cm);
  }});
  if (typeof skuTable.updateData === "function") skuTable.updateData();
  if (typeof skuTable.recalculate === "function") skuTable.recalculate();
  return {{
    ok: true,
    expectedRows: grid.expected_rows || skus.length,
    actualRows: rows.length,
    firstSku: rows[0] && (rows[0].sku || rows[0].sellerSku || rows[0].variationSku || "")
  }};
}})()
'''
    result = js_eval(fill_js)
    emit("sku_grid_safe_fill", result=result)
    return result


def audit_page_state():
    audit_js = r'''
(() => {{
  const task = window.__AI_LISTING_TASK_PLAN__ || {{}};
  const tables = [...document.querySelectorAll(".vxe-table")].map((el) => el.__vue__).filter(Boolean);
  const skuTable = tables.find((vm) => (vm.tableColumn || []).some((col) => col.field === "sku") && (vm.tableData || []).length);
  const thumbnailItems = [...document.querySelectorAll(".custom-img .img-center > div")]
    .map((node) => ({{ color: node.querySelector(".mb5")?.innerText?.trim() || "", hasImage: !!node.__vue__?.imgItem?.url }}));
  return {{
    url: location.href,
    title: document.title,
    expectedRows: task.sku_grid?.expected_rows || 0,
    actualRows: skuTable?.tableData?.length || 0,
    discountLabel: task.discount?.label || "",
    thumbnailItems
  }};
}})()
'''
    result = js_eval(audit_js)
    emit("page_audit", result=result)
    return result


require_safe_plan()
emit("start", job_id=TASK_PLAN.get("job_id"), mode=TASK_PLAN.get("mode"))
ensure_real_tab()
switch_to_mabang_detail_page()
install_publish_guard()
install_task_payload()
results = {{}}
for action in TASK_PLAN.get("actions") or []:
    action_type = action.get("type")
    emit("action", action=action_type, label=action.get("label", ""))
    if action_type == "fill_sku_grid":
        results["sku_grid"] = run_sku_grid_safe_fill()
    elif action_type == "upload_variant_thumbnails":
        uploads = TASK_PLAN.get("variant_thumbnail_uploads") or {{}}
        results["variant_thumbnail_uploads"] = {{
            "prepared": [color for color, item in uploads.items() if item.get("copied")],
            "pending_browser_upload": True
        }}
        emit("variant_thumbnail_uploads", result=results["variant_thumbnail_uploads"])
    elif action_type == "stop_for_operator_review":
        results["audit"] = audit_page_state()
        emit("stop_for_operator_review", reason="safe_fill_complete_without_save")
    elif action_type == "save_draft":
        results["pre_save_audit"] = audit_page_state()
        results["save_draft"] = click_save_draft_once()
        if results["save_draft"].get("ok"):
            time.sleep(2)
            results["save_verification"] = verify_save_draft_result()
    time.sleep(0.05)
print("SAFE_FILL_RESULT " + json.dumps(results, ensure_ascii=True))
"""


def build_browser_use_safe_audit_script(task_plan: dict[str, Any]) -> str:
    encoded_task = base64.b64encode(json.dumps(task_plan, ensure_ascii=False).encode("utf-8")).decode("ascii")
    forbidden_texts = json.dumps(FORBIDDEN_BUTTON_TEXTS, ensure_ascii=True)
    return f"""from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

TASK_PLAN = json.loads(base64.b64decode("{encoded_task}").decode("utf-8"))
FORBIDDEN_BUTTON_TEXTS = {forbidden_texts}


def emit(event, **data):
    print("SAFE_AUDIT_EVENT " + json.dumps({{"event": event, **data}}, ensure_ascii=True))


def require_safe_plan():
    safety = TASK_PLAN.get("safety") or {{}}
    if not TASK_PLAN.get("ok"):
        raise RuntimeError("Task plan is not ready")
    if TASK_PLAN.get("mode") != "safe_fill":
        raise RuntimeError("Task plan mode must be safe_fill")
    if safety.get("allow_submit"):
        raise RuntimeError("Refusing to run a plan that allows submit")


def js_eval(script):
    for helper_name in ("execute_js", "evaluate_js", "eval_js", "run_js", "js"):
        helper = globals().get(helper_name)
        if callable(helper):
            return helper(script)
    raise RuntimeError("No Browser-Use JavaScript helper is available")


def is_mabang_shopee_detail_url(url):
    if "productDetailShopee" in url:
        return True
    if "main.goPublish" not in url:
        return False
    encoded = (parse_qs(urlparse(url).query).get("url") or [""])[0]
    if not encoded:
        return False
    padded = encoded + ("=" * (-len(encoded) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return False
    return "productDetailShopee" in decoded


def switch_to_mabang_detail_page():
    tabs = list_tabs()
    for tab in tabs:
        url = tab.get("url", "")
        if is_mabang_shopee_detail_url(url):
            switch_tab(tab.get("target_id") or tab.get("id"))
            emit("tab_selected", url=url)
            return url
    info = page_info()
    emit("tab_not_found", page_info=str(info))
    return ""


def install_save_guard():
    guard_js = r'''
(() => {{
  const forbidden = {forbidden_texts};
  const state = window.__AI_LISTING_SAFE_GUARD__ || {{ blockedClicks: 0 }};
  state.forbidden = forbidden;
  window.__AI_LISTING_SAFE_GUARD__ = state;
  const textOf = (node) => (node && (node.innerText || node.textContent || node.value || "") || "").trim();
  const closestAction = (node) => {{
    let cur = node;
    for (let i = 0; cur && i < 6; i += 1, cur = cur.parentElement) {{
      const text = textOf(cur);
      if (forbidden.some((label) => text.includes(label))) return {{ node: cur, text }};
    }}
    return null;
  }};
  if (!window.__AI_LISTING_SAFE_GUARD_INSTALLED__) {{
    document.addEventListener("click", (event) => {{
      const match = closestAction(event.target);
      if (match) {{
        state.blockedClicks += 1;
        event.preventDefault();
        event.stopImmediatePropagation();
        console.warn("[ai-listing] blocked forbidden Mabang action", match.text);
      }}
    }}, true);
    window.__AI_LISTING_SAFE_GUARD_INSTALLED__ = true;
  }}
  return {{
    installed: true,
    forbidden,
    blockedClicks: state.blockedClicks,
    url: location.href,
    title: document.title
  }};
}})()
'''
    result = js_eval(guard_js)
    emit("save_guard_installed", result=result)
    return result


def install_task_payload():
    task_json = json.dumps(TASK_PLAN, ensure_ascii=False)
    payload_js = "window.__AI_LISTING_TASK_PLAN__ = " + task_json + "; ({{ok:true, jobId: window.__AI_LISTING_TASK_PLAN__.job_id}})"
    result = js_eval(payload_js)
    emit("task_payload_installed", result=result)
    return result


def audit_page_state():
    audit_js = r'''
(() => {{
  const task = window.__AI_LISTING_TASK_PLAN__ || {{}};
  const tables = [...document.querySelectorAll(".vxe-table")].map((el) => el.__vue__).filter(Boolean);
  const skuTable = tables.find((vm) => (vm.tableColumn || []).some((col) => col.field === "sku") && (vm.tableData || []).length);
  const thumbnailItems = [...document.querySelectorAll(".custom-img .img-center > div")]
    .map((node) => ({{ color: node.querySelector(".mb5")?.innerText?.trim() || "", hasImage: !!node.__vue__?.imgItem?.url }}));
  return {{
    url: location.href,
    title: document.title,
    hasSafeGuard: !!window.__AI_LISTING_SAFE_GUARD__,
    guardInstalled: !!window.__AI_LISTING_SAFE_GUARD_INSTALLED__,
    expectedRows: task.sku_grid?.expected_rows || 0,
    actualRows: skuTable?.tableData?.length || 0,
    discountLabel: task.discount?.label || "",
    thumbnailItems
  }};
}})()
'''
    result = js_eval(audit_js)
    emit("page_audit", result=result)
    return result


require_safe_plan()
emit("start", job_id=TASK_PLAN.get("job_id"), mode="safe_audit")
ensure_real_tab()
selected_url = switch_to_mabang_detail_page()
if not selected_url:
    raise RuntimeError("Mabang Shopee product detail tab not found")
guard = install_save_guard()
payload = install_task_payload()
audit = audit_page_state()
print("SAFE_AUDIT_RESULT " + json.dumps({{"selected_url": selected_url, "guard": guard, "payload": payload, "audit": audit}}, ensure_ascii=True))
"""


def build_category_collection_audit_script(collection_plan: dict[str, Any]) -> str:
    """Build a read-only Browser-Use script that inspects category fields."""

    encoded_plan = base64.b64encode(json.dumps(collection_plan, ensure_ascii=False).encode("utf-8")).decode("ascii")
    forbidden_texts = json.dumps(FORBIDDEN_BUTTON_TEXTS, ensure_ascii=True)
    return f"""from __future__ import annotations

import base64
import json

PLAN = json.loads(base64.b64decode("{encoded_plan}").decode("utf-8"))
FORBIDDEN = {forbidden_texts}


def emit(event, **data):
    print("CATEGORY_COLLECTION_EVENT " + json.dumps({{"event": event, **data}}, ensure_ascii=True))


def js_eval(script):
    for helper_name in ("execute_js", "evaluate_js", "eval_js", "run_js", "js"):
        helper = globals().get(helper_name)
        if callable(helper):
            return helper(script)
    raise RuntimeError("No Browser-Use JavaScript helper is available")


def install_read_only_guard():
    result = js_eval(r'''\
(() => {{
  const forbidden = {forbidden_texts};
  const state = window.__AI_LISTING_CATEGORY_COLLECTION_GUARD__ || {{ blockedClicks: 0 }};
  state.forbidden = forbidden;
  window.__AI_LISTING_CATEGORY_COLLECTION_GUARD__ = state;
  const textOf = (node) => (node && (node.innerText || node.textContent || node.value || "") || "").trim();
  if (!window.__AI_LISTING_CATEGORY_COLLECTION_GUARD_INSTALLED__) {{
    document.addEventListener("click", (event) => {{
      let cur = event.target;
      for (let i = 0; cur && i < 6; i += 1, cur = cur.parentElement) {{
        const text = textOf(cur);
        if (forbidden.some((label) => text.includes(label))) {{
          state.blockedClicks += 1;
          event.preventDefault();
          event.stopImmediatePropagation();
          return;
        }}
      }}
    }}, true);
    window.__AI_LISTING_CATEGORY_COLLECTION_GUARD_INSTALLED__ = true;
  }}
  return {{ installed: true, blockedClicks: state.blockedClicks, url: location.href }};
}})()
''')
    emit("read_only_guard", result=result)
    return result


def collect_fields():
    result = js_eval(r'''\
(() => {{
  const labelOf = (node) => {{
    const id = node.id;
    const label = id ? document.querySelector(`label[for="${{CSS.escape(id)}}"]`) : null;
    return (label?.innerText || node.getAttribute("aria-label") || node.getAttribute("name") || node.id || "").trim();
  }};
  const fieldId = (node, label) => (node.getAttribute("data-field-id") || node.name || node.id || label)
    .trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  const fields = [...document.querySelectorAll("input, select, textarea, [role=combobox]")]
    .filter((node) => !node.disabled && !node.closest("button"))
    .map((node) => {{
      const label = labelOf(node);
      const type = node.tagName === "SELECT" ? (node.multiple ? "multi_select" : "single_select")
        : node.type === "number" ? "number"
        : node.type === "checkbox" ? "boolean" : "text";
      const options = node.tagName === "SELECT"
        ? [...node.options].map((option) => ({{ value: option.value, label: option.textContent.trim() }})).filter((item) => item.value)
        : [];
      return {{
        field_id: fieldId(node, label),
        label,
        type,
        required: !!node.required || node.getAttribute("aria-required") === "true" || !!node.closest(".required"),
        options,
        automation: {{ page_field_id: node.getAttribute("data-field-id") || node.id || node.name || "", label }},
      }};
    }}).filter((item) => item.field_id && item.label);
  const unique = new Map();
  fields.forEach((item) => {{ if (!unique.has(item.field_id)) unique.set(item.field_id, item); }});
  const categoryControl = document.querySelector(
    'input[name="category_id"], input[name="categoryId"], select[name="category_id"], [data-field-id="category_id"]'
  );
  const observedCategoryId = String(categoryControl?.value || categoryControl?.getAttribute("data-value") || "").trim();
  return {{ url: location.href, title: document.title, observed_category_id: observedCategoryId, fields: [...unique.values()] }};
}})()
''')
    emit("fields_read", count=len(result.get("fields") or []) if isinstance(result, dict) else 0)
    return result


if not PLAN.get("category_id"):
    raise RuntimeError("category_id is required")
guard = install_read_only_guard()
capture = collect_fields()
if not isinstance(capture, dict) or not capture.get("fields"):
    raise RuntimeError("No category fields were found on the current page")
capture_url = str(capture.get("url") or "")
if "productDetailShopee" not in capture_url and "main.goPublish" not in capture_url:
    raise RuntimeError("Current page is not a Mabang Shopee product detail editor")
capture["platform"] = PLAN.get("platform") or "shopee"
capture["country"] = PLAN.get("country") or ""
capture["store_id"] = PLAN.get("store_id") or ""
capture["marketplace_category_id"] = PLAN.get("category_id")
if not capture.get("observed_category_id") or str(capture.get("observed_category_id")) != str(PLAN.get("category_id")):
    raise RuntimeError("Current Mabang page category does not match the requested category_id")
capture["marketplace_category_path"] = PLAN.get("category_path") or ""
capture["display_name"] = PLAN.get("display_name") or capture.get("title") or ""
capture["variation"] = PLAN.get("variation") or {{"required": True, "axes": ["color", "size"]}}
print("CATEGORY_COLLECTION_RESULT " + json.dumps({{"guard": guard, "capture": capture}}, ensure_ascii=True))
"""


def build_category_catalog_collection_audit_script(collection_plan: dict[str, Any]) -> str:
    """Build a read-only Browser-Use script that discovers the loaded category tree."""

    encoded_plan = base64.b64encode(json.dumps(collection_plan, ensure_ascii=False).encode("utf-8")).decode("ascii")
    forbidden_texts = json.dumps(FORBIDDEN_BUTTON_TEXTS, ensure_ascii=True)
    return f"""from __future__ import annotations

import base64
import json

PLAN = json.loads(base64.b64decode("{encoded_plan}").decode("utf-8"))
FORBIDDEN = {forbidden_texts}


def emit(event, **data):
    print("CATEGORY_CATALOG_EVENT " + json.dumps({{"event": event, **data}}, ensure_ascii=True))


def js_eval(script):
    for helper_name in ("execute_js", "evaluate_js", "eval_js", "run_js", "js"):
        helper = globals().get(helper_name)
        if callable(helper):
            return helper(script)
    raise RuntimeError("No Browser-Use JavaScript helper is available")


def install_read_only_guard():
    result = js_eval(r'''\
(() => {{
  const forbidden = {forbidden_texts};
  const state = window.__AI_LISTING_CATEGORY_CATALOG_GUARD__ || {{ blockedClicks: 0 }};
  state.forbidden = forbidden;
  window.__AI_LISTING_CATEGORY_CATALOG_GUARD__ = state;
  const textOf = (node) => (node && (node.innerText || node.textContent || node.value || "") || "").trim();
  if (!window.__AI_LISTING_CATEGORY_CATALOG_GUARD_INSTALLED__) {{
    document.addEventListener("click", (event) => {{
      let cur = event.target;
      for (let i = 0; cur && i < 6; i += 1, cur = cur.parentElement) {{
        const text = textOf(cur);
        if (forbidden.some((label) => text.includes(label))) {{
          state.blockedClicks += 1;
          event.preventDefault();
          event.stopImmediatePropagation();
          return;
        }}
      }}
    }}, true);
    window.__AI_LISTING_CATEGORY_CATALOG_GUARD_INSTALLED__ = true;
  }}
  return {{ installed: true, blockedClicks: state.blockedClicks, url: location.href }};
}})()
''')
    emit("read_only_guard", result=result)
    return result


def collect_catalog_from_api():
    script = r'''\
(async () => {{
  const storeId = __STORE_ID__;
  const power = JSON.parse(sessionStorage.getItem("powerInfo") || "{{}}");
  const headers = {{
    key: sessionStorage.getItem("memcacheKey") || "",
    ProjectId: sessionStorage.getItem("projectId") || "erp",
    Authorization: "Bearer " + (sessionStorage.getItem("token") || ""),
    "cluster-id": power.cloud === 1 ? "2" : "1",
  }};
  if (!sessionStorage.getItem("token")) return {{ categories: [], discovery_source: "api_session_unavailable" }};
  const endpoint = "https://api.mabangerp.com/kandeng/api/v2/shopee/getCategory";
  const seen = new Map();
  const requestedParents = new Set();
  let parents = ["0"];
  let requestCount = 0;
  while (parents.length) {{
    const levelParents = parents.filter((id) => !requestedParents.has(id));
    parents = [];
    for (let offset = 0; offset < levelParents.length; offset += 8) {{
      const batch = levelParents.slice(offset, offset + 8);
      batch.forEach((id) => requestedParents.add(id));
      const responses = await Promise.all(batch.map(async (parentId) => {{
        const params = new URLSearchParams({{
          shop_id: storeId,
          parent_category_id: parentId,
          search_name: "",
          site: "",
          platformId: "17",
          is_global: "2",
        }});
        const response = await fetch(endpoint + "?" + params, {{ headers }});
        const payload = await response.json();
        requestCount += 1;
        if (payload.code !== 200 || !Array.isArray(payload.data)) {{
          throw new Error("Mabang category request failed for parent " + parentId);
        }}
        return payload.data;
      }}));
      responses.forEach((rows, index) => {{
        const requestedParent = batch[index];
        rows.forEach((raw) => {{
          const categoryId = String(raw.category_id || raw.categoryId || raw.id || "").trim();
          if (!categoryId) return;
          const isLeaf = Number(raw.is_leaf ?? raw.isLeaf) === 1;
          seen.set(categoryId, {{
            category_id: categoryId,
            parent_id: String(raw.parent_id || raw.parentId || (requestedParent === "0" ? "" : requestedParent)),
            name: String(raw.name_en || raw.name_zh || raw.category_name || raw.name || "").trim(),
            name_zh: String(raw.name_zh || raw.category_name || "").trim(),
            name_en: String(raw.name_en || "").trim(),
            is_leaf: isLeaf,
          }});
          if (!isLeaf && !requestedParents.has(categoryId)) parents.push(categoryId);
        }});
      }});
      if (seen.size > 20000) throw new Error("Category catalog exceeded the 20000 item safety limit");
      await new Promise((resolve) => setTimeout(resolve, 35));
    }}
  }}
  return {{
    source_url: location.origin + location.pathname,
    page_title: document.title,
    discovery_source: "mabang_shopee_getCategory_api",
    category_count: seen.size,
    request_count: requestCount,
    categories: [...seen.values()],
  }};
}})()
'''.replace("__STORE_ID__", json.dumps(str(PLAN.get("store_id") or "")))
    result = js_eval(script)
    emit(
        "catalog_api_read",
        count=result.get("category_count") if isinstance(result, dict) else 0,
        requests=result.get("request_count") if isinstance(result, dict) else 0,
    )
    return result


def collect_catalog():
    result = js_eval(r'''\
(() => {{
  const idKeys = ["category_id", "categoryId", "cat_id", "catId", "id", "value"];
  const nameKeys = ["category_name", "categoryName", "name_zh", "name_en", "name", "label", "title"];
  const childKeys = ["children", "subcategories", "subCategories", "category_list", "categoryList"];
  const first = (value, keys) => {{
    for (const key of keys) {{
      if (value && value[key] !== undefined && value[key] !== null && String(value[key]).trim()) return value[key];
    }}
    return "";
  }};
  const childrenOf = (value) => {{
    for (const key of childKeys) if (Array.isArray(value?.[key])) return value[key];
    return [];
  }};
  const cloneCategory = (value, depth = 0) => {{
    if (!value || typeof value !== "object" || depth > 12) return null;
    const categoryId = first(value, idKeys);
    const name = first(value, nameKeys);
    if (!String(categoryId || "").trim() || !String(name || "").trim()) return null;
    const children = childrenOf(value).map((item) => cloneCategory(item, depth + 1)).filter(Boolean);
    return {{
      category_id: String(categoryId),
      parent_id: String(first(value, ["parent_id", "parentId", "parent_category_id", "pid"]) || ""),
      name: String(name).trim(),
      path: value.path || value.category_path || value.categoryPath || value.categoryNamePath || "",
      is_leaf: value.is_leaf ?? value.isLeaf ?? (children.length === 0),
      children,
    }};
  }};
  const countNodes = (nodes) => nodes.reduce((total, node) => total + 1 + countNodes(node.children || []), 0);
  const candidates = [];
  const consider = (value, source) => {{
    if (!Array.isArray(value) || !value.length) return;
    const nodes = value.map((item) => cloneCategory(item)).filter(Boolean);
    if (!nodes.length) return;
    candidates.push({{ source, nodes, count: countNodes(nodes) }});
  }};
  const seen = new WeakSet();
  let visited = 0;
  const walk = (value, source, depth = 0) => {{
    if (!value || typeof value !== "object" || depth > 9 || visited > 50000) return;
    if (value instanceof Node || value === window || value === document || seen.has(value)) return;
    seen.add(value);
    visited += 1;
    if (Array.isArray(value)) consider(value, source);
    let entries;
    try {{ entries = Object.entries(value); }} catch (_) {{ return; }}
    for (const [key, child] of entries) {{
      if (typeof child === "function" || key === "$el" || key === "$parent" || key === "$root") continue;
      if (/category|classif|catalog/i.test(key) && Array.isArray(child)) consider(child, `${{source}}.${{key}}`);
      walk(child, `${{source}}.${{key}}`, depth + 1);
    }}
  }};

  const roots = [
    [window.__INITIAL_STATE__, "window.__INITIAL_STATE__"],
    [window.__NUXT__, "window.__NUXT__"],
    [window.__MABANG__, "window.__MABANG__"],
  ];
  document.querySelectorAll("*").forEach((node) => {{
    if (node.__vue__ && roots.length < 250) roots.push([node.__vue__, "element.__vue__"]);
  }});
  for (const [root, source] of roots) walk(root, source);

  if (!candidates.length) {{
    const domNodes = [...document.querySelectorAll("[role=treeitem], .el-tree-node__content, .el-cascader-menu li, .ant-cascader-menu-item")]
      .map((node, index) => ({{
        category_id: String(node.dataset.categoryId || node.dataset.value || node.getAttribute("value") || node.id || `dom-${{index}}`),
        parent_id: "",
        name: (node.innerText || node.textContent || "").trim(),
        is_leaf: !node.getAttribute("aria-expanded"),
        children: [],
      }})).filter((item) => item.name);
    if (domNodes.length) candidates.push({{ source: "rendered_dom", nodes: domNodes, count: domNodes.length }});
  }}
  candidates.sort((a, b) => b.count - a.count);
  const best = candidates[0] || {{ source: "none", nodes: [], count: 0 }};
  const resources = performance.getEntriesByType("resource")
    .map((entry) => entry.name)
    .filter((url) => /category|classif|catalog/i.test(url))
    .slice(-50);
  return {{
    source_url: location.origin + location.pathname,
    page_title: document.title,
    discovery_source: best.source,
    category_count: best.count,
    categories: best.nodes,
    category_resources: resources,
    inspected_objects: visited,
  }};
}})()
''')
    emit(
        "catalog_read",
        count=result.get("category_count") if isinstance(result, dict) else 0,
        source=result.get("discovery_source") if isinstance(result, dict) else "none",
    )
    return result


if not PLAN.get("country") or not PLAN.get("store_id"):
    raise RuntimeError("country and store_id are required")
guard = install_read_only_guard()
capture = collect_catalog_from_api()
if not isinstance(capture, dict) or not capture.get("categories"):
    capture = collect_catalog()
if not isinstance(capture, dict) or not capture.get("categories"):
    raise RuntimeError("No marketplace category catalog was found in the current Mabang page")
capture_url = str(capture.get("source_url") or "")
if "mabangerp.com" not in capture_url:
    raise RuntimeError("Current page is not a Mabang page")
capture["platform"] = PLAN.get("platform") or "shopee"
capture["country"] = PLAN.get("country") or ""
capture["store_id"] = PLAN.get("store_id") or ""
print("CATEGORY_CATALOG_RESULT " + json.dumps({{"guard": guard, "capture": capture}}, ensure_ascii=True))
"""


def build_mabang_page_import_audit_script(import_plan: dict[str, Any]) -> str:
    """Build a Browser-Use script that reads the active Mabang editor iframe."""

    encoded_plan = base64.b64encode(json.dumps(import_plan, ensure_ascii=False).encode("utf-8")).decode("ascii")
    capture_path = json.dumps(str(MABANG_PAGE_IMPORT_CAPTURE_PATH), ensure_ascii=True)
    return rf"""from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PLAN = json.loads(base64.b64decode("{encoded_plan}").decode("utf-8"))
CAPTURE_PATH = Path({capture_path})


def is_mabang_detail(url):
    if "productDetailShopee" in url:
        return True
    if "main.goPublish" not in url:
        return False
    encoded = (parse_qs(urlparse(url).query).get("url") or [""])[0]
    if not encoded:
        return False
    padded = encoded + ("=" * (-len(encoded) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return False
    return "productDetailShopee" in decoded


def mabang_detail_identity(url):
    text = str(url or "")
    if "main.goPublish" in text:
        encoded = (parse_qs(urlparse(text).query).get("url") or [""])[0]
        padded = encoded + ("=" * (-len(encoded) % 4))
        try:
            text = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        except Exception:
            return ""
    parsed = urlparse(text)
    fragment = parsed.fragment or ""
    query = parse_qs(urlparse(fragment).query)
    return str((query.get("id") or [""])[0])


def find_editor_frame(frame_tree):
    frame = frame_tree.get("frame") or {{}}
    if "publish.mabangerp.com" in str(frame.get("url") or ""):
        return frame.get("id")
    for child in frame_tree.get("childFrames") or []:
        found = find_editor_frame(child)
        if found:
            return found
    return None


def prepare_mabang_page_import_catalogs():
    expression = r'''(async () => {{
  const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
  const isVisible = (node) => {{
    if (!node || !node.isConnected) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
  }};
  const clickForText = (scope, text) => [...scope.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
    .filter((node) => isVisible(node))
    .find((node) => clean(node.textContent) === text || clean(node.textContent).startsWith(text));
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const waitUntil = async (predicate, timeout = 6000) => {{
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeout) {{
      const value = predicate();
      if (value) return value;
      await sleep(100);
    }}
    return null;
  }};
  const promotionDialog = () => [...document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"]')]
    .filter((node) => isVisible(node))
    .find((node) => clean(node.textContent).includes('\u9009\u62e9\u6298\u6263\u6d3b\u52a8'));
  const state = {{ watermarkOpenedByCollector: false, watermarkMenuMethod: '', promotionOpenedByCollector: false, sitePickerOpenedByCollector: false, sitePickerWasOpen: false, sizeChartSelectorOpenedByCollector: false, sizeChartModeValue: '', sizeChartModeChanged: false, cnscPage: false, target_site_stores_loaded: false, target_site_stores: [] }};
  const currentUrl = new URL(location.href);
  let hashParams = new URL(location.origin).searchParams;
  try {{ hashParams = new URL(String(location.hash || '').replace(/^#/, ''), location.origin).searchParams; }} catch (_error) {{}}
  const cnscPage = currentUrl.searchParams.get('global') === '4' || hashParams.get('global') === '4';
  state.cnscPage = cnscPage;
  // 2026-10-01 fix: when this block moved to hover-first opening, this declaration was
  // dropped, so watermarkMenuVisible()/openWatermarkMenu() referenced an undeclared
  // watermarkButton -> ReferenceError swallowed by cdp() -> the whole prep block below
  // (watermark menu, size-chart sources, shop picker, promotions) silently stopped
  // running. Keep the declaration right here.
  const watermarkButton = clickForText(document, '\u6dfb\u52a0\u6c34\u5370');
  const watermarkMenuVisible = () => {{
    if (watermarkButton?.getAttribute('aria-expanded') === 'true') return true;
    const triggerRect = watermarkButton?.getBoundingClientRect();
    return [...document.querySelectorAll('.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]')]
      .some((node) => isVisible(node) && (!triggerRect || (() => {{
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.top <= triggerRect.bottom + 8 && rect.left <= triggerRect.right && rect.right >= triggerRect.left;
      }})()));
  }};
  const openWatermarkMenu = async () => {{
    if (!watermarkButton) return;
    if (watermarkMenuVisible()) {{ state.watermarkMenuMethod = 'already_open'; return; }}
    // Prefer a real hover first: Mabang's add-watermark control opens its menu on
    // mouseenter. When the page only answers clicks (e.g. no mouse), fall back to a
    // plain .click() as a compatibility trigger.
    for (const eventName of ['mouseenter', 'mouseover', 'mouseleave', 'mouseout']) {{
      const rect = watermarkButton.getBoundingClientRect();
      watermarkButton.dispatchEvent(new MouseEvent(eventName, {{
        bubbles: true, cancelable: true, view: window,
        clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2,
      }}));
    }}
    state.watermarkMenuMethod = await waitUntil(watermarkMenuVisible, 1500) ? 'hover' : '';
    if (!state.watermarkMenuMethod) {{
      watermarkButton.click();
      state.watermarkMenuMethod = await waitUntil(watermarkMenuVisible, 4000) ? 'click' : '';
    }}
    if (state.watermarkMenuMethod) {{
      state.watermarkOpenedByCollector = true;
      await waitUntil(() => {{
        if (!watermarkMenuVisible()) return true;
        const menu = [...document.querySelectorAll('.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]')]
          .filter((node) => isVisible(node))
          .find((node) => clean(node.textContent).includes('\u6dfb\u52a0\u6c34\u5370') || !clean(node.textContent).includes('\u9009\u62e9\u5c3a\u7801\u56fe'));
        const loadingMasks = [...document.querySelectorAll('.el-loading-mask')];
        if (loadingMasks.some((node) => isVisible(node))) return false;
        return Boolean(menu) && (menu.querySelector('.el-dropdown-menu__item,.el-select-dropdown__item,[role="menuitem"],li') || clean(menu.textContent).includes('\u6682\u65e0'));
      }}, 6000);
    }}
  }};
  await openWatermarkMenu();
  const sizeChartScope = [...document.querySelectorAll('.el-form-item,.form-item')].find((node) => clean(node.textContent).includes('\u5c3a\u7801\u56fe\u8868')) || document.body;
  const currentMode = [...sizeChartScope.querySelectorAll('input[type="radio"]')].find((node) => node.checked);
  state.sizeChartModeValue = currentMode?.value || '';
  const templateMode = clickForText(sizeChartScope, '\u9009\u62e9\u6a21\u677f');
  const templateModeInput = templateMode?.matches('input[type="radio"]') ? templateMode : templateMode?.querySelector('input[type="radio"]');
  if (templateMode && !templateModeInput?.checked) {{
    templateMode.click();
    state.sizeChartModeChanged = Boolean(state.sizeChartModeValue);
  }}
  const templateSelector = sizeChartScope.querySelector('.el-select input,[role="combobox"],input[placeholder*="\u8bf7\u9009\u62e9"]');
  const selectorTrigger = templateSelector?.closest('.el-select') || templateSelector;
  if (selectorTrigger) {{
    selectorTrigger.click();
    state.sizeChartSelectorOpenedByCollector = true;
  }}
  if (cnscPage) {{
    const picker = document.querySelector('#shops-wap');
    const pickerTrigger = picker?.querySelector('.shop-box');
    const pickerContent = () => picker?.querySelector('.feat-content');
    const pickerIsOpen = () => isVisible(pickerContent());
    state.sitePickerWasOpen = pickerIsOpen();
    if (pickerTrigger && !pickerIsOpen()) {{
      pickerTrigger.click();
      state.sitePickerOpenedByCollector = true;
      await waitUntil(() => pickerIsOpen());
    }}
    const requiredManualSites = ['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR'];
    const aliases = {{ '\u53f0\u6e7e': 'TW', '\u83f2\u5f8b\u5bbe': 'PH', '\u65b0\u52a0\u5761': 'SG', '\u6cf0\u56fd': 'TH', '\u9a6c\u6765\u897f\u4e9a': 'MY', '\u58a8\u897f\u54e5': 'MX', '\u8d8a\u5357': 'VN', '\u5df4\u897f': 'BR', SIP: 'SIP' }};
    const makeTargetStore = (siteCode, shop) => {{
      const storeId = clean(shop?.id || shop?.shopId || shop?.store_id || shop?.storeId);
      const storeName = clean(shop?.name || shop?.store_name || shop?.storeName);
      if (!storeId && !storeName) return null;
      return {{ site_code: siteCode, store_id: storeId, store_name: storeName, discount_catalog_loaded: false, discount_catalog: [] }};
    }};
    const shopVm = picker?.__vue__ || picker?.__vueParentComponent?.proxy;
    const vueSiteGroups = Array.isArray(shopVm?.shops) ? shopVm.shops : [];
    if (vueSiteGroups.length) {{
      // Mabang renders one checkbox per shop, not a radio group. Vue's model is
      // the authoritative source for the selected shop id/name and avoids
      // guessing when a site has zero or multiple checked shops.
      for (const siteGroup of vueSiteGroups) {{
        const siteCode = clean(siteGroup?.site).toUpperCase();
        if (!requiredManualSites.includes(siteCode)) continue;
        const selected = (Array.isArray(siteGroup?.shops) ? siteGroup.shops : [])
          .filter((shop) => shop?.isChecked === true);
        if (selected.length !== 1) continue;
        const target = makeTargetStore(siteCode, selected[0]);
        if (target) state.target_site_stores.push(target);
      }}
    }} else {{
      // DOM fallback for older Mabang builds without the Vue instance exposed.
      const siteGroups = [...(picker?.querySelectorAll('.site') || [])];
      for (const siteGroup of siteGroups) {{
        const titleText = clean(siteGroup.querySelector('.title-wap-checkbox')?.textContent || siteGroup.textContent);
        const rawCode = clean(siteGroup.dataset.siteCode || '').toUpperCase();
        const siteCode = /^(TW|PH|SG|TH|MY|MX|VN|BR|SIP)$/.test(rawCode)
          ? rawCode
          : Object.entries(aliases).find(([name]) => titleText.includes(name))?.[1] || '';
        if (!requiredManualSites.includes(siteCode)) continue;
        const selected = [...siteGroup.querySelectorAll('.shops-checkbox')]
          .filter((label) => label.querySelector('input[type="checkbox"]')?.checked);
        if (selected.length !== 1) continue;
        const label = selected[0];
        const input = label.querySelector('input[type="checkbox"]');
        const target = makeTargetStore(siteCode, {{
          id: label.dataset.storeId || label.dataset.shopId || input?.dataset.storeId || input?.dataset.shopId || input?.value,
          name: label.dataset.storeName || label.dataset.shopName || label.textContent,
        }});
        if (target) state.target_site_stores.push(target);
      }}
    }}
    const targetCodes = state.target_site_stores.map((item) => item.site_code);
    state.target_site_stores_loaded = Boolean(
      picker && requiredManualSites.every((code) => targetCodes.filter((value) => value === code).length === 1)
      && new Set(targetCodes).size === requiredManualSites.length
    );
    if (pickerIsOpen() && pickerTrigger) pickerTrigger.click();
    window.__AI_LISTING_CNSC_IMPORT_STATE__ = {{ target_site_stores_loaded: state.target_site_stores_loaded, target_site_stores: state.target_site_stores }};
  }}
  if (!cnscPage && !promotionDialog()) {{
    const promotionText = clickForText(document, '\u6298\u6263\u6d3b\u52a8');
    const promotionCell = promotionText?.closest('.vxe-cell--title');
    const promotionTrigger = promotionCell?.querySelector('.change-discount') || document.querySelector('.change-discount') || promotionCell || promotionText;
    if (promotionTrigger && isVisible(promotionTrigger)) {{
      promotionTrigger.click();
      state.promotionOpenedByCollector = true;
      await waitUntil(promotionDialog);
    }}
  }}
  await waitUntil(() => {{
    const dialog = promotionDialog();
    if (!dialog) return true;
    const loadingMasks = [...dialog.querySelectorAll('.el-loading-mask')];
    if (loadingMasks.some((node) => isVisible(node))) return false;
    // Only a real data row counts. A freshly opened dialog renders its empty state
    // first; accepting that as "loaded" lets the capture read an empty table and
    // degrade the promotion to a bare id+name entry (no status/validity, which the
    // backend then reports as unavailable).
    return Boolean(dialog.querySelector('.vxe-body--row,.el-table__row,tbody tr,[role="row"]'));
  }});
  const promotionHasRow = () => Boolean(
    promotionDialog()?.querySelector('.vxe-body--row,.el-table__row,tbody tr,[role="row"]')
  );
  if (promotionDialog() && !promotionHasRow()) {{
    // Still no row: give the list a grace period before accepting a genuinely empty catalog.
    await sleep(2500);
  }}
  return state;
}})()'''
    evaluation = cdp("Runtime.evaluate", expression=expression, contextId=context_id, returnByValue=True, awaitPromise=True)
    result = evaluation.get("result") or {{}}
    return result.get("value") if isinstance(result.get("value"), dict) else {{}}


def collect_mabang_cnsc_discount_catalogs(state):
    if not isinstance(state, dict) or not state.get("cnscPage"):
        return state

    def apply_selected_activity(target_store, selected_id):
        matches = [
            item for item in target_store.get("discount_catalog") or []
            if str(item.get("mabang_discount_id") or "") == selected_id
        ]
        if len(matches) != 1:
            return
        selected = matches[0]
        target_store.update({{
            "discount_catalog_loaded": True,
            "mabang_discount_id": selected_id,
            "discount_name": str(selected.get("name") or ""),
            "discount_label": str(selected.get("name") or ""),
        }})
        if selected.get("discount_percent") not in (None, ""):
            target_store["discount_percent"] = selected["discount_percent"]

    previous_activity_store_id = ""
    previous_store = None
    wraparound_selected_id = ""
    for store in state.get("target_site_stores") or []:
        site_code = str(store.get("site_code") or "").strip().upper()
        if site_code not in {{"TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR"}}:
            continue
        expression = r'''(() => {{
  const requestedSite = ''' + json.dumps(site_code, ensure_ascii=True) + r''';
  const previousStoreId = ''' + json.dumps(previous_activity_store_id, ensure_ascii=True) + r''';
  const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
  const isVisible = (node) => {{
    if (!node || !node.isConnected) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
  }};
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const deadline = Date.now() + 10000;
  const waitUntil = async (predicate) => {{
    while (Date.now() < deadline) {{
      const value = predicate();
      if (value) return value;
      await sleep(100);
    }}
    return null;
  }};
  const clickForText = (scope, text) => [...scope.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
    .filter((node) => isVisible(node))
    .find((node) => clean(node.textContent) === text || clean(node.textContent).startsWith(text));
  const promotionDialogs = () => [...document.querySelectorAll('.el-dialog,[role="dialog"]')]
    .filter((node) => isVisible(node))
    .filter((node) => clean(node.textContent).includes('\u9009\u62e9\u6298\u6263\u6d3b\u52a8'));
  const promotionDialog = () => promotionDialogs()[0] || null;
  window.__AI_LISTING_CNSC_ACTIVITY_TASKS__ ||= {{}};
  const taskState = window.__AI_LISTING_CNSC_ACTIVITY_TASKS__;
  taskState[requestedSite] = {{ status: 'running' }};
  const collect = async () => {{
  const state = window.__AI_LISTING_CNSC_IMPORT_STATE__;
  const store = state?.target_site_stores?.find((item) => clean(item?.site_code).toUpperCase() === requestedSite);
  if (!store) return null;
  const tables = [...document.querySelectorAll('.vxe-table')].filter(isVisible);
  const table = tables.find((node) => {{
    const texts = [...node.querySelectorAll('.vxe-header--column')].filter(isVisible).map((header) => clean(header.textContent));
    return texts.some((text) => text.toUpperCase() === requestedSite) && texts.some((text) => text.includes('\u6298\u6263\u6d3b\u52a8'));
  }}) || document;
  const headers = [...table.querySelectorAll('.vxe-header--column')].filter(isVisible);
  const siteCodes = [...new Set(headers.map((node) => clean(node.textContent).toUpperCase()).filter((text) => /^(TW|PH|SG|TH|MY|MX|VN|BR)$/.test(text)))];
  const seenActivityColumns = new Set();
  const activityHeaders = headers.filter((node) => clean(node.textContent).includes('\u6298\u6263\u6d3b\u52a8')).filter((node) => {{
    const columnId = clean(node.getAttribute('colid') || clean(node.className).split(' ').find((name) => /^col_\d+$/.test(name)) || '');
    if (!columnId || seenActivityColumns.has(columnId)) return !columnId;
    seenActivityColumns.add(columnId);
    return true;
  }});
  const siteIndex = siteCodes.indexOf(requestedSite);
  const header = siteIndex >= 0 ? activityHeaders[siteIndex] : null;
  if (!header) return store;
  const headerText = clean(header.textContent);
  const identityNode = header.matches('[data-discount-id],[data-activity-id]') ? header : header.querySelector('[data-discount-id],[data-activity-id]');
  let selectedId = clean(identityNode?.dataset?.discountId || identityNode?.dataset?.activityId);
  let headerStoreId = clean(header.dataset?.shopId || header.dataset?.storeId || identityNode?.dataset?.shopId || identityNode?.dataset?.storeId);
  for (const node of [header, ...header.querySelectorAll('*')]) {{
    for (let vm = node.__vue__ || node.__vueParentComponent?.proxy, depth = 0; !selectedId && vm && depth < 5; vm = vm.$parent, depth += 1) {{
      selectedId = clean(vm.currentDiscountId || vm.selectedDiscountId || vm.discountId || vm.activityId || vm.changeDiscountInfo?.discountId || vm.discountInfo?.discountId || vm.$data?.currentDiscountId || vm.$data?.selectedDiscountId || vm.$data?.changeDiscountInfo?.discountId || vm.$data?.discountInfo?.discountId);
    }}
    for (let vm = node.__vue__ || node.__vueParentComponent?.proxy, depth = 0; !headerStoreId && vm && depth < 5; vm = vm.$parent, depth += 1) {{
      headerStoreId = clean(vm.shopId || vm.storeId || vm.currentShopId || vm.changeDiscountInfo?.shopId || vm.$data?.shopId || vm.$data?.storeId || vm.$data?.currentShopId);
    }}
    if (selectedId) break;
  }}
  store._previous_activity_id = selectedId;
  const staleStoreId = previousStoreId || headerStoreId;
  const percentMatch = headerText.match(/(\d+(?:\.\d+)?)\s*%/);
  if (percentMatch) store.discount_percent = Number(percentMatch[1]);
  const trigger = header.querySelector('.change-discount')
    || [...header.querySelectorAll('button,[role="button"],.el-button,.el-link,a,span')]
      .find((node) => isVisible(node) && (clean(node.textContent) === '\u9009\u62e9\u6d3b\u52a8' || clean(node.textContent).includes('\u9009\u62e9\u6298\u6263')));
  if (!trigger) return store;
  trigger.click();
  let lastTriggerAt = Date.now();
  await sleep(200);
  const dialog = await waitUntil(promotionDialog);
  if (!dialog) return store;
  let containers = [];
  let rawOptions = [];
  let rawStoreIds = [];
  await waitUntil(() => {{
    containers = [];
    const roots = [dialog, ...dialog.querySelectorAll('.vxe-table,.el-table')];
    for (const root of roots) {{
      for (let vm = root.__vue__ || root.__vueParentComponent?.proxy, depth = 0; vm && depth < 8; vm = vm.$parent, depth += 1) containers.push(vm, vm.$data);
    }}
    rawOptions = containers.flatMap((item) => ['tableData', 'activityList', 'promotionList', 'discountList'].flatMap((key) => Array.isArray(item?.[key]) ? item[key] : []));
    rawStoreIds = [...new Set(rawOptions.map((item) => clean(item.shopId || item.shop_id || item.store_id || item.storeId)).filter(Boolean))];
    const changedStoreIds = staleStoreId ? rawStoreIds.filter((id) => id !== staleStoreId) : rawStoreIds;
    if (rawOptions.length && (!staleStoreId || changedStoreIds.length === 1)) return true;
    if (Date.now() - lastTriggerAt >= 1500) {{
      trigger.click();
      lastTriggerAt = Date.now();
    }}
    return false;
  }});
    const changedStoreIds = staleStoreId ? rawStoreIds.filter((id) => id !== staleStoreId) : rawStoreIds;
    const unchangedFirstStore = !previousStoreId && staleStoreId && rawStoreIds.length === 1
      && rawStoreIds[0] === staleStoreId
      && rawOptions.some((item) => clean(item.discount_id || item.discountId || item.activity_id || item.activityId || item.id || item.value) === selectedId);
    const acceptedStoreId = changedStoreIds.length === 1 ? changedStoreIds[0] : unchangedFirstStore ? staleStoreId : '';
    if (acceptedStoreId) {{
      rawOptions = rawOptions.filter((item) => {{
        const optionStoreId = clean(item.shopId || item.shop_id || item.store_id || item.storeId);
        return !optionStoreId || optionStoreId === acceptedStoreId;
      }});
    }} else if (rawStoreIds.length) {{
      rawOptions = [];
    }}
    if (unchangedFirstStore) {{
      store._current_activity_id = selectedId;
      store._previous_activity_id = '';
    }}
    store._activity_store_id = acceptedStoreId;
    const seen = new Set();
    store.discount_catalog = rawOptions.map((item) => {{
      const id = clean(item.discount_id || item.discountId || item.activity_id || item.activityId || item.id || item.value);
      return {{
        mabang_discount_id: id,
        name: clean(item.discount_name || item.discountName || item.activity_name || item.activityName || item.name || item.label),
        discount_percent: Number(clean(item.discount_percent || item.discountPercent || item.discount_rate || item.discountRate).replace('%', '')) || '',
        selected: item.selected === true || item.checked === true,
      }};
    }}).filter((item) => item.mabang_discount_id && item.name && !seen.has(item.mabang_discount_id) && seen.add(item.mabang_discount_id));
    const normalizeActivityLabel = (value) => clean(value)
      .replace(/^\u6298\u6263\u6d3b\u52a8/, '')
      .replace(/\u6279\u91cf\u4fee\u6539|\u9009\u62e9\u6d3b\u52a8|\u6d3b\u52a8\u5f85\u914d\u7f6e/g, '')
      .replace(/\u6d3b\u52a8/g, '')
      .replace(/\s+/g, '')
      .toLowerCase();
    const normalizedHeaderLabel = normalizeActivityLabel(headerText);
    let selected = store.discount_catalog.filter((item) => item.selected);
    if (selected.length !== 1 && normalizedHeaderLabel) selected = store.discount_catalog.filter((item) => normalizeActivityLabel(item.name) === normalizedHeaderLabel);
    if (selected.length !== 1) selected = store.discount_catalog.filter((item) => headerText.includes(item.name));
    store.discount_catalog_loaded = store.discount_catalog.length > 0 && selected.length === 1;
    if (store.discount_catalog_loaded) {{
      store.mabang_discount_id = selected[0].mabang_discount_id;
      store.discount_name = selected[0].name;
      store.discount_label = selected[0].name;
      if (selected[0].discount_percent !== '') store.discount_percent = selected[0].discount_percent;
    }}
  return store;
  }};
  collect().then((value) => {{ taskState[requestedSite] = {{ status: 'completed', value }}; }})
    .catch((error) => {{ taskState[requestedSite] = {{ status: 'failed', error: String(error?.stack || error?.message || error) }}; }});
  return {{ status: 'started' }};
}})()'''
        try:
            evaluation = cdp("Runtime.evaluate", expression=expression, contextId=context_id, returnByValue=True, awaitPromise=False)
        except Exception as exc:
            raise RuntimeError(f"CNSC {{site_code}} activity collection failed: {{exc}}") from exc
        result = evaluation.get("result") or {{}}
        exception = evaluation.get("exceptionDetails") or {{}}
        if exception or result.get("subtype") == "error":
            description = result.get("description") or exception.get("text") or "unknown JavaScript error"
            raise RuntimeError(f"CNSC {{site_code}} activity collection failed: {{description}}")
        started = result.get("value") if isinstance(result.get("value"), dict) else None
        if not started or started.get("status") != "started":
            raise RuntimeError(f"CNSC {{site_code}} activity collection did not start")
        poll_expression = "window.__AI_LISTING_CNSC_ACTIVITY_TASKS__?.[" + json.dumps(site_code, ensure_ascii=True) + "] || null"
        task = None
        poll_deadline = time.monotonic() + 25
        while time.monotonic() < poll_deadline:
            poll_evaluation = cdp("Runtime.evaluate", expression=poll_expression, contextId=context_id, returnByValue=True, awaitPromise=False)
            poll_result = poll_evaluation.get("result") or {{}}
            poll_exception = poll_evaluation.get("exceptionDetails") or {{}}
            if poll_exception or poll_result.get("subtype") == "error":
                description = poll_result.get("description") or poll_exception.get("text") or "unknown JavaScript error"
                raise RuntimeError(f"CNSC {{site_code}} activity status failed: {{description}}")
            task = poll_result.get("value") if isinstance(poll_result.get("value"), dict) else None
            if task and task.get("status") in {{"completed", "failed"}}:
                break
            time.sleep(0.1)
        if not task or task.get("status") == "running":
            raise RuntimeError(f"CNSC {{site_code}} activity collection timed out")
        if task.get("status") == "failed":
            raise RuntimeError(f"CNSC {{site_code}} activity collection failed: {{task.get('error') or 'unknown JavaScript error'}}")
        collected = task.get("value") if isinstance(task.get("value"), dict) else None
        if not collected:
            raise RuntimeError(f"CNSC {{site_code}} activity collection returned no data")
        activity_store_id = str(collected.pop("_activity_store_id", "") or "").strip()
        previous_selected_id = str(collected.pop("_previous_activity_id", "") or "").strip()
        current_selected_id = str(collected.pop("_current_activity_id", "") or "").strip()
        if activity_store_id:
            previous_activity_store_id = activity_store_id
        if previous_store is None:
            wraparound_selected_id = previous_selected_id
        elif previous_selected_id:
            apply_selected_activity(previous_store, previous_selected_id)
        store.clear()
        store.update(collected)
        if current_selected_id:
            apply_selected_activity(store, current_selected_id)
        previous_store = store
    if previous_store is not None and wraparound_selected_id:
        apply_selected_activity(previous_store, wraparound_selected_id)
    final_state = {{
        "target_site_stores_loaded": state.get("target_site_stores_loaded") is True,
        "target_site_stores": state.get("target_site_stores") or [],
    }}
    sync_expression = "window.__AI_LISTING_CNSC_IMPORT_STATE__ = " + json.dumps(final_state, ensure_ascii=False) + "; delete window.__AI_LISTING_CNSC_ACTIVITY_TASKS__; true"
    sync_evaluation = cdp("Runtime.evaluate", expression=sync_expression, contextId=context_id, returnByValue=True, awaitPromise=False)
    sync_result = sync_evaluation.get("result") or {{}}
    sync_exception = sync_evaluation.get("exceptionDetails") or {{}}
    if sync_exception or sync_result.get("subtype") == "error":
        description = sync_result.get("description") or sync_exception.get("text") or "unknown JavaScript error"
        raise RuntimeError(f"CNSC activity state sync failed: {{description}}")
    return state


def restore_mabang_page_import_catalogs(state):
    expression = r'''(async () => {{
  const state = ''' + json.dumps(state or {{}}, ensure_ascii=False) + r''';
  const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
  const isVisible = (node) => {{
    if (!node || !node.isConnected) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
  }};
  const clickForText = (scope, text) => [...scope.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
    .filter((node) => isVisible(node))
    .find((node) => clean(node.textContent) === text || clean(node.textContent).startsWith(text));
  const promotionDialog = [...document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"]')]
    .filter((node) => isVisible(node))
    .find((node) => clean(node.textContent).includes('\u9009\u62e9\u6298\u6263\u6d3b\u52a8'));
  if (promotionDialog) {{
    const closeButton = promotionDialog.querySelector(
      '.el-dialog__headerbtn,[aria-label="Close"],[aria-label="\u5173\u95ed"]'
    ) || clickForText(promotionDialog, '\u5173\u95ed') || clickForText(promotionDialog, '\u53d6\u6d88');
    if (closeButton && isVisible(closeButton)) closeButton.click();
  }}
  const picker = document.querySelector('#shops-wap');
  const pickerContent = picker?.querySelector('.feat-content');
  const pickerTrigger = picker?.querySelector('.shop-box');
  if (pickerContent && isVisible(pickerContent) && pickerTrigger) pickerTrigger.click();
  const sizeChartScope = [...document.querySelectorAll('.el-form-item,.form-item')]
    .find((node) => clean(node.textContent).includes('\u5c3a\u7801\u56fe\u8868'));
  const sizeChartDropdown = [...document.querySelectorAll('.el-select-dropdown')].find(isVisible);
  const sizeChartSelect = sizeChartScope?.querySelector('.el-select');
  if (sizeChartDropdown && sizeChartSelect) sizeChartSelect.click();
  if (state.sizeChartModeChanged && state.sizeChartModeValue) {{
    const sizeChartScope = [...document.querySelectorAll('.el-form-item,.form-item')].find((node) => clean(node.textContent).includes('\u5c3a\u7801\u56fe\u8868')) || document.body;
    const originalMode = [...sizeChartScope.querySelectorAll('input[type="radio"]')].find((node) => node.value === state.sizeChartModeValue);
    const originalLabel = originalMode?.closest('label') || originalMode;
    if (originalLabel) originalLabel.click();
  }}
  if (state.watermarkOpenedByCollector) {{
    const watermarkButton = clickForText(document, '\u6dfb\u52a0\u6c34\u5370');
    const isVisible = (node) => {{
      if (!node || !node.isConnected) return false;
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
    }};
    const watermarkMenuVisible = () => {{
      if (watermarkButton?.getAttribute('aria-expanded') === 'true') return true;
      const triggerRect = watermarkButton?.getBoundingClientRect();
      return [...document.querySelectorAll('.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]')]
        .some((node) => isVisible(node) && (!triggerRect || (() => {{
          const rect = node.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && rect.top <= triggerRect.bottom + 8 && rect.left <= triggerRect.right && rect.right >= triggerRect.left;
        }})()));
    }};
    if (watermarkMenuVisible()) {{
      const rect = watermarkButton?.getBoundingClientRect();
      if (watermarkButton) {{
        for (const eventName of ['mouseleave', 'mouseout']) {{
          watermarkButton.dispatchEvent(new MouseEvent(eventName, {{
            bubbles: true, cancelable: true, view: window,
            clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2,
          }}));
        }}
      }}
      await new Promise((resolve) => setTimeout(resolve, 200));
      if (watermarkMenuVisible() && watermarkButton) {{
        if (watermarkButton?.getAttribute('aria-expanded') === 'true') watermarkButton.click();
      }}
    }}
  }}
  delete window.__AI_LISTING_CNSC_IMPORT_STATE__;
  return true;
}})()'''
    cdp("Runtime.evaluate", expression=expression, contextId=context_id, returnByValue=True, awaitPromise=True)


ensure_real_tab()
tabs = list_tabs()
detail_tabs = [tab for tab in tabs if is_mabang_detail(str(tab.get("url") or ""))]
page = page_info()
current_page_url = str(page.get("url") or "") if isinstance(page, dict) else ""
detail_tab = next((tab for tab in detail_tabs if str(tab.get("url") or "") == current_page_url), None)
if detail_tab is None and len(detail_tabs) == 1:
    detail_tab = detail_tabs[0]
if detail_tab is None and len(detail_tabs) > 1:
    detail_ids = {{mabang_detail_identity(str(tab.get("url") or "")) for tab in detail_tabs}}
    if len(detail_ids) == 1 and "" not in detail_ids:
        detail_tab = detail_tabs[-1]
    else:
        raise RuntimeError("Multiple Mabang product detail tabs are open; keep only the target page and retry")
if detail_tab is None:
    raise RuntimeError("Current Mabang Shopee product detail tab was not found")
switch_tab(detail_tab.get("targetId") or detail_tab.get("target_id") or detail_tab.get("id"))

frame_tree = cdp("Page.getFrameTree").get("frameTree") or {{}}
editor_frame_id = find_editor_frame(frame_tree)
if not editor_frame_id:
    raise RuntimeError("Current Mabang editor iframe was not found")

cdp("Runtime.enable")
context_id = None
for event in drain_events():
    if event.get("method") != "Runtime.executionContextCreated":
        continue
    context = (event.get("params") or {{}}).get("context") or {{}}
    aux = context.get("auxData") or {{}}
    if aux.get("frameId") == editor_frame_id and aux.get("isDefault"):
        context_id = context.get("id")
if context_id is None:
    raise RuntimeError("Current Mabang editor execution context was not found")

prepared_catalogs = {{}}
try:
    prepared_catalogs = prepare_mabang_page_import_catalogs()
    prepared_catalogs = collect_mabang_cnsc_discount_catalogs(prepared_catalogs)
    capture_source = CAPTURE_PATH.read_text(encoding="utf-8")
    expression = "(" + capture_source + ")(" + json.dumps(PLAN, ensure_ascii=False) + ")"
    evaluation = cdp(
        "Runtime.evaluate",
        expression=expression,
        contextId=context_id,
        returnByValue=True,
        awaitPromise=True,
    )
    remote = evaluation.get("result") or {{}}
    capture = remote.get("value")
    if not isinstance(capture, dict):
        description = remote.get("description") or (evaluation.get("exceptionDetails") or {{}}).get("text") or "unknown error"
        raise RuntimeError("Mabang page capture did not return structured data: " + str(description))
    if capture.get("source") != "mabang_current_page":
        raise RuntimeError("Mabang page capture returned an unexpected source")
finally:
    try:
        restore_mabang_page_import_catalogs(prepared_catalogs)
    except Exception:
        pass
print("MABANG_PAGE_IMPORT_RESULT " + json.dumps(capture, ensure_ascii=True))
"""


def _compact_mabang_page_import_error(output: str) -> str:
    text = str(output or "")
    lowered = text.lower()
    if "permissionerror" in lowered or "bu-default.pid" in lowered:
        return "Browser-Use 启动失败，请关闭残留进程后重试"
    if "multiple mabang product detail tabs" in lowered:
        return "检测到多个马帮商品详情页，请只保留目标页面后重试"
    if "current mabang shopee product detail tab was not found" in lowered:
        return "未找到马帮商品详情页 tab：请在服务器 Chrome 里打开一个商品的编辑页后重试"
    if "current mabang editor iframe was not found" in lowered:
        return "马帮编辑 iframe 未加载：等页面完全打开后重试"
    if "current mabang editor execution context was not found" in lowered:
        return "马帮编辑 Vue 上下文未就绪：刷新页面等待完全加载后重试"
    if "mabang page capture did not return structured data" in lowered:
        # Pull the description tail the injected script attaches — that's the raw JS error.
        marker = "Mabang page capture did not return structured data: "
        tail = ""
        for line in text.splitlines()[::-1]:
            index = line.find(marker)
            if index >= 0:
                tail = line[index + len(marker):].strip()
                break
        return f"采集脚本执行异常：{tail}" if tail else "采集脚本执行异常，请重试"
    if "mabang page capture returned an unexpected source" in lowered:
        return "采集结果结构异常：请重试"
    # Fall back to a compact tail of stderr so operators can see the real cause.
    tail_lines = [line.strip() for line in text.splitlines() if line.strip()][-3:]
    if tail_lines:
        return "Browser-Use 读取当前马帮页面失败：" + " / ".join(tail_lines)
    return "Browser-Use 读取当前马帮页面失败，请确认目标详情页已打开并重试"


def run_mabang_page_import_audit(
    import_plan: dict[str, Any],
    *,
    browser_use_exe: Path | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Read the current Mabang editor through Browser-Use's authorized session."""

    exe = browser_use_exe or find_browser_use_exe()
    if not exe:
        return {
            "ok": False,
            "status": "blocked",
            "error": "Browser-Use 运行环境未安装或不可用",
        }
    if not MABANG_PAGE_IMPORT_CAPTURE_PATH.is_file():
        return {"ok": False, "status": "failed", "error": "Mabang page capture source is missing"}

    script = build_mabang_page_import_audit_script(import_plan)
    browser_env = os.environ.copy()
    try:
        runtime = storage.get_runtime_configuration()
        configured_cdp = str((runtime.get("worker") or {}).get("cdp_url") or "").strip()
    except Exception:
        configured_cdp = ""
    if configured_cdp:
        browser_env.setdefault("BU_CDP_URL", configured_cdp)
    try:
        result = subprocess.run(
            [str(exe)],
            cwd=str(PROJECT_ROOT),
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=browser_env,
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "error": "Browser-Use 读取当前马帮页面超时"}
    except OSError as exc:
        return {"ok": False, "status": "failed", "error": f"Unable to launch Browser-Use: {exc}"}

    output = (result.stdout or "") + (result.stderr or "")
    marker = "MABANG_PAGE_IMPORT_RESULT "
    line = next((item[len(marker):] for item in reversed(output.splitlines()) if item.startswith(marker)), "")
    if not line:
        return {"ok": False, "status": "failed", "error": _compact_mabang_page_import_error(output)}
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "status": "failed", "error": "Mabang page capture was not valid JSON"}

    if isinstance(payload, dict) and payload.get("ok") is False:
        return {
            "ok": False,
            "status": str(payload.get("status") or "failed"),
            "error": str(payload.get("error") or "Mabang page capture failed"),
        }
    if result.returncode != 0:
        return {"ok": False, "status": "failed", "error": _compact_mabang_page_import_error(output)}

    capture = payload.get("capture") if isinstance(payload, dict) and isinstance(payload.get("capture"), dict) else payload
    if not isinstance(capture, dict) or capture.get("source") != "mabang_current_page":
        return {"ok": False, "status": "failed", "error": "Mabang page capture returned an invalid result"}
    return {"ok": True, "status": "completed", "capture": capture}


def collect_mabang_page_import_via_playwright(
    import_plan: dict[str, Any] | None = None,
    *,
    node_executable: str = "node",
    cdp_url: str = "http://127.0.0.1:9223",
    timeout_seconds: int = 240,
) -> dict[str, Any]:
    """采集马帮「当前商品编辑页」的快照（走 Playwright/CDP，不再依赖 browser-harness）。

    与 ``run_mabang_page_import_audit`` 同样的返回形状（``{ok, status, capture}``），
    区别只是驱动方式：这里直接连 CDP、在商品编辑页 frame 里跑
    ``scripts/mabang_page_import_capture.js`` 那份采集函数。返回 ``ok=False`` 时
    ``error`` 是给操作员看的中文原因（例如"没有打开商品编辑页"）。
    """

    if not MABANG_PAGE_IMPORT_COLLECT_PATH.is_file():
        return {"ok": False, "status": "failed", "error": "页面采集脚本缺失"}

    plan = dict(import_plan or {})
    node_env = os.environ.copy()
    node_modules = (PROJECT_ROOT / "node_modules").resolve()
    module_paths = [node_modules / ".pnpm" / "node_modules", node_modules]
    inherited_node_path = str(node_env.get("NODE_PATH") or "").strip()
    discovered = [str(path) for path in module_paths if path.is_dir()]
    if inherited_node_path:
        discovered.append(inherited_node_path)
    if discovered:
        node_env["NODE_PATH"] = os.pathsep.join(dict.fromkeys(discovered))

    node_cmd, node_version = _resolve_node_executable(node_executable, node_env)
    if node_executable == "node" and node_version is None:
        return {"ok": False, "status": "failed", "error": f"Node 无法启动（{node_cmd} --version 执行失败）"}

    try:
        completed = subprocess.run(
            [
                node_cmd,
                str(MABANG_PAGE_IMPORT_COLLECT_PATH),
                "--cdp-url",
                cdp_url,
                "--plan",
                json.dumps(plan, ensure_ascii=False),
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=node_env,
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "error": "读取当前马帮页面超时（页面没响应）"}
    except OSError as exc:
        return {"ok": False, "status": "failed", "error": f"无法启动 Node：{exc}"}

    output = (completed.stdout or "") + (completed.stderr or "")
    marker = "MABANG_PAGE_IMPORT_RESULT "
    line = next((item[len(marker):] for item in reversed(output.splitlines()) if item.startswith(marker)), "")
    if not line:
        return {"ok": False, "status": "failed", "error": _compact_mabang_page_import_error(output)}
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "status": "failed", "error": "页面采集结果不是合法 JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "status": "failed", "error": "页面采集结果格式不对"}
    if payload.get("ok") is False:
        return {
            "ok": False,
            "status": str(payload.get("status") or "failed"),
            "error": str(payload.get("error") or "页面采集失败"),
        }
    capture = payload.get("capture")
    if not isinstance(capture, dict) or capture.get("source") != "mabang_current_page":
        return {"ok": False, "status": "failed", "error": "页面采集返回的结果无效"}
    return {"ok": True, "status": "completed", "capture": capture}


def collect_store_watermarks_via_playwright(
    *,
    node_executable: str = "node",
    cdp_url: str = "http://127.0.0.1:9223",
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """采集马帮水印目录（走 Playwright/CDP，不再依赖 browser-harness）。

    browser-harness 的 daemon 在这台机器的 Python 3.14 下起不来
    （``asyncio base_events.py:297 assert self._sockets is not None``），
    所有走它的采集过去必然失败。自动化一直在用的 Playwright/CDP 这条路是通的，
    所以水印采集改走 ``scripts/mabang_watermark_collect.cjs``。

    返回 ``{"ok", "status", "templates", "catalog_loaded", "store", "range_probe", "error"}``：
    只有 ``ok=True`` 才代表读到了页面（``templates`` 为空表示这个店铺确实没有水印）。
    """

    if not MABANG_WATERMARK_COLLECT_PATH.is_file():
        return {"ok": False, "status": "failed", "error": "水印采集脚本缺失"}

    node_env = os.environ.copy()
    node_modules = (PROJECT_ROOT / "node_modules").resolve()
    module_paths = [node_modules / ".pnpm" / "node_modules", node_modules]
    inherited_node_path = str(node_env.get("NODE_PATH") or "").strip()
    discovered = [str(path) for path in module_paths if path.is_dir()]
    if inherited_node_path:
        discovered.append(inherited_node_path)
    if discovered:
        node_env["NODE_PATH"] = os.pathsep.join(dict.fromkeys(discovered))

    node_cmd, node_version = _resolve_node_executable(node_executable, node_env)
    if node_executable == "node" and node_version is None:
        return {
            "ok": False,
            "status": "failed",
            "error": f"Node 无法启动（{node_cmd} --version 执行失败），无法采集水印",
        }

    try:
        completed = subprocess.run(
            [node_cmd, str(MABANG_WATERMARK_COLLECT_PATH), "--cdp-url", cdp_url],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=node_env,
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "error": "读取马帮水印目录超时（页面没响应）"}
    except OSError as exc:
        return {"ok": False, "status": "failed", "error": f"无法启动 Node：{exc}"}

    output = (completed.stdout or "") + (completed.stderr or "")
    marker = "MABANG_WATERMARK_RESULT "
    line = next((item[len(marker):] for item in reversed(output.splitlines()) if item.startswith(marker)), "")
    if not line:
        return {
            "ok": False,
            "status": "failed",
            "error": _compact_mabang_page_import_error(output),
        }
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "status": "failed", "error": "水印采集结果不是合法 JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "status": "failed", "error": "水印采集结果格式不对"}
    return payload


def collect_store_promotions_via_playwright(
    store_id: str,
    *,
    node_executable: str = "node",
    cdp_url: str = "http://127.0.0.1:9223",
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """定向重采一个本土店铺的促销活动目录（走 Playwright/CDP，只读）。

    页面导入没读到活动列表时，模板里的活动只剩名称、没有有效期，后端随之判成
    unresolved（模板下拉看不到它）。这里单独展开一次「选择折扣活动」弹窗，把活动行
    （start_time / end_time / flag）读出来，供调用方写回店铺活动目录。

    返回 ``scripts/mabang_promotion_collect.cjs`` 的 payload：
    ``{ok, status, store, items, catalog_loaded, diagnostics, error}``。
    """

    if not MABANG_PROMOTION_COLLECT_PATH.is_file():
        return {"ok": False, "status": "failed", "error": "活动采集脚本缺失"}

    node_env = os.environ.copy()
    node_modules = (PROJECT_ROOT / "node_modules").resolve()
    module_paths = [node_modules / ".pnpm" / "node_modules", node_modules]
    inherited_node_path = str(node_env.get("NODE_PATH") or "").strip()
    discovered = [str(path) for path in module_paths if path.is_dir()]
    if inherited_node_path:
        discovered.append(inherited_node_path)
    if discovered:
        node_env["NODE_PATH"] = os.pathsep.join(dict.fromkeys(discovered))

    node_cmd, node_version = _resolve_node_executable(node_executable, node_env)
    if node_executable == "node" and node_version is None:
        return {
            "ok": False,
            "status": "failed",
            "error": f"Node 无法启动（{node_cmd} --version 执行失败），无法采集店铺活动",
        }

    command = [
        node_cmd,
        str(MABANG_PROMOTION_COLLECT_PATH),
        "--cdp-url",
        cdp_url,
        "--store-id",
        str(store_id or "").strip(),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=node_env,
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "error": "重采店铺活动超时（页面没响应）"}
    except OSError as exc:
        return {"ok": False, "status": "failed", "error": f"无法启动 Node：{exc}"}

    output = (completed.stdout or "") + (completed.stderr or "")
    marker = "MABANG_PAGE_IMPORT_RESULT "
    line = next((item[len(marker):] for item in reversed(output.splitlines()) if item.startswith(marker)), "")
    if not line:
        return {"ok": False, "status": "failed", "error": _compact_mabang_page_import_error(output)}
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "status": "failed", "error": "店铺活动采集结果不是合法 JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "status": "failed", "error": "店铺活动采集结果格式不对"}
    return payload


def build_dry_run_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return {
            "ok": False,
            "status": "failed",
            "error": payload.get("error") or "Automation payload is not available",
            "next_step": "inspect_automation_job",
        }

    job = payload.get("job") or {}
    listing_copy = payload.get("listing_copy") or {}
    browser_inputs = payload.get("browser_inputs") or {}
    readiness = payload.get("readiness") or {}
    missing_fields = list(readiness.get("missing_fields") or job.get("missing_fields") or [])

    if not readiness.get("ready"):
        return {
            "ok": False,
            "status": "blocked",
            "mode": "dry_run",
            "job_id": job.get("id") or "",
            "listing_copy_id": job.get("listing_copy_id") or listing_copy.get("id") or "",
            "store_name": listing_copy.get("store_name") or job.get("store_name") or "",
            "missing_fields": missing_fields,
            "next_step": "complete_store_template_ids",
        }

    return {
        "ok": True,
        "status": "ready",
        "mode": "dry_run",
        "job_id": job.get("id") or "",
        "listing_copy_id": job.get("listing_copy_id") or listing_copy.get("id") or "",
        "store_name": listing_copy.get("store_name") or job.get("store_name") or "",
        "platform": listing_copy.get("platform") or job.get("platform") or "",
        "country": listing_copy.get("country") or job.get("country") or "",
        "listing_title": listing_copy.get("listing_title") or "",
        "mabang_store_id": browser_inputs.get("mabang_store_id") or "",
        "mabang_category_id": browser_inputs.get("mabang_category_id") or "",
        "mabang_discount_id": browser_inputs.get("mabang_discount_id") or "",
        "main_image": browser_inputs.get("main_image") or "",
        "sku_count": int(browser_inputs.get("sku_count") or len(payload.get("skus") or [])),
        "price_count": len(payload.get("prices") or []),
        "planned_actions": [
            "open_mabang_product_create_page",
            "select_store_category_and_discount_ids",
            "fill_title_description_images_skus_and_prices",
            "stop_before_submit_for_operator_review",
        ],
        "next_step": "run_browser_use_after_chrome_remote_debugging",
    }


def interpret_doctor_result(exit_code: int, output: str) -> dict[str, Any]:
    lowered = output.lower()
    needs_remote_debugging = "devtoolsactiveport" in lowered or "remote-debugging" in lowered
    needs_daemon_restart = exit_code != 0 and "daemon alive" in lowered and "[fail]" in lowered
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "needs_remote_debugging": needs_remote_debugging,
        "next_step": (
            "enable_chrome_remote_debugging"
            if needs_remote_debugging
            else "restart_browser_use_daemon"
            if needs_daemon_restart
            else ""
        ),
        "output": output,
    }


def find_browser_use_exe(project_root: Path = PROJECT_ROOT) -> Path | None:
    candidates = [project_root / ".venv-browser-use" / "Scripts" / "browser-use.exe"]
    # Worktree deployments may reuse the managed Browser-Use runtime from the main project.
    candidates.append(project_root.parents[3] / "outputs" / "ai-listing-system" / ".venv-browser-use" / "Scripts" / "browser-use.exe")
    for exe in candidates:
        if exe.is_file():
            return exe
    return None


def run_doctor(browser_use_exe: Path | None = None, timeout_seconds: int = 120) -> dict[str, Any]:
    exe = browser_use_exe or find_browser_use_exe()
    if not exe:
        return {
            "ok": False,
            "exit_code": 127,
            "needs_remote_debugging": False,
            "next_step": "install_browser_use",
            "output": "browser-use.exe not found in .venv-browser-use",
        }
    result = subprocess.run(
        [str(exe), "--doctor"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        **no_console_window_kwargs(),
    )
    return interpret_doctor_result(result.returncode, (result.stdout or "") + (result.stderr or ""))


def run_browser_use_safe_fill(
    task_plan: dict[str, Any],
    browser_use_exe: Path | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    if not task_plan.get("ok"):
        return {
            "ok": False,
            "exit_code": 2,
            "status": "blocked",
            "script": "",
            "output": task_plan.get("error") or "Task plan is not ready",
        }

    exe = browser_use_exe or find_browser_use_exe()
    if not exe:
        return {
            "ok": False,
            "exit_code": 127,
            "status": "failed",
            "script": "",
            "output": "browser-use.exe not found in .venv-browser-use",
            "next_step": "install_browser_use",
        }

    script = build_browser_use_safe_fill_script(task_plan)
    try:
        result = subprocess.run(
            [str(exe)],
            cwd=str(PROJECT_ROOT),
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        output = "".join(
            str(part or "")
            for part in (
                exc.output,
                exc.stderr,
                "\nBrowser-Use timed out while connecting to Chrome. Open chrome://inspect/#remote-debugging, tick Allow remote debugging for this browser instance, click Allow if Chrome asks, then retry.",
            )
        )
        return {
            "ok": False,
            "exit_code": 124,
            "status": "timeout",
            "script": script,
            "output": output,
            "next_step": "allow_chrome_remote_debugging",
        }
    output = (result.stdout or "") + (result.stderr or "")
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "status": "completed" if result.returncode == 0 else "failed",
        "script": script,
        "output": output,
    }


def run_browser_use_safe_audit(
    task_plan: dict[str, Any],
    browser_use_exe: Path | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    if not task_plan.get("ok"):
        return {
            "ok": False,
            "exit_code": 2,
            "status": "blocked",
            "script": "",
            "output": task_plan.get("error") or "Task plan is not ready",
        }

    exe = browser_use_exe or find_browser_use_exe()
    if not exe:
        return {
            "ok": False,
            "exit_code": 127,
            "status": "failed",
            "script": "",
            "output": "browser-use.exe not found in .venv-browser-use",
            "next_step": "install_browser_use",
        }

    script = build_browser_use_safe_audit_script(task_plan)
    try:
        result = subprocess.run(
            [str(exe)],
            cwd=str(PROJECT_ROOT),
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        output = "".join(
            str(part or "")
            for part in (
                exc.output,
                exc.stderr,
                "\nBrowser-Use timed out during the safe page audit.",
            )
        )
        return {
            "ok": False,
            "exit_code": 124,
            "status": "timeout",
            "script": script,
            "output": output,
            "next_step": "inspect_browser_use_connection",
        }
    output = (result.stdout or "") + (result.stderr or "")
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "status": "completed" if result.returncode == 0 else "failed",
        "script": script,
        "output": output,
    }


# Windows NTSTATUS exit codes that indicate the child node process died during
# startup rather than reporting a structured result. Translating them turns a
# bare exit code into a message an operator can act on (the classic case is a
# third-party RPA tool's bundled node hijacking PATH resolution).
_NTSTATUS_EXIT_HINTS = {
    0xC0000005: "STATUS_ACCESS_VIOLATION（内存访问违规，常见于损坏的 native 模块或系统不稳定）",
    0xC0000135: "STATUS_DLL_NOT_FOUND（找不到 DLL）",
    0xC0000139: "STATUS_ENTRYPOINT_NOT_FOUND（入口点不存在，node 与系统组件不兼容）",
    0xC0000142: "STATUS_DLL_INIT_FAILED（DLL 初始化失败，进程启动即崩溃）。"
    "通常是 PATH 中被解析到的 node.exe 本身无法启动——例如 Aibote 等工具捆绑的 node "
    "抢先命中且已损坏，或系统不稳定/内存不足。",
    0xC0000374: "STATUS_HEAP_CORRUPTION（堆损坏）",
}


def _exit_code_hint(exit_code: int) -> str:
    unsigned = exit_code if exit_code >= 0 else exit_code + (1 << 32)
    hint = _NTSTATUS_EXIT_HINTS.get(unsigned)
    if hint:
        return f" (0x{unsigned:08X} {hint})"
    return f" (0x{unsigned:08X})"


# Cache of (node_cmd, node_version) so a long-running worker does not pay a
# `node --version` spawn on every polled job. Keyed by resolved node path.
_NODE_PROBE_CACHE: dict[str, str | None] = {}


def _node_fallback_candidates() -> list[str]:
    """标准安装位置的 node.exe，仅在 ``node`` 不在 PATH 上时用。

    API 可能被一个精简环境的启动器拉起（2026-10-12：从一个只剩两条 PATH 条目的 shell
    重启 API → ``shutil.which("node")`` 返回 None → 自动登录子进程 ``[WinError 2]``
    起不来 → 启动 Worker 只报「马帮登录未确认：failed」，看不出是 node 找不到）。
    PATH 靠不住的时候这条兜底能让自动化继续跑；真的都没有，就仍旧交回字面量 ``node``，
    让报错保持原样（``无法启动 Node：[WinError 2]``）。
    """

    roots = [
        Path(os.environ.get("ProgramFiles") or r"C:\Program Files"),
        Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"),
    ]
    candidates = [root / "nodejs" / "node.exe" for root in roots]
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "nodejs" / "node.exe")
    return [str(path) for path in candidates if path.is_file()]


def _which_node() -> str | None:
    """PATH 优先，其次标准安装位置；都没有返回 None。"""

    found = shutil.which("node")
    if found:
        return found
    return next(iter(_node_fallback_candidates()), None)


def _resolve_node_executable(
    node_executable: str,
    node_env: dict[str, str],
) -> tuple[str, str | None]:
    """Return (node_cmd, node_version) for the child process.

    For the default bare ``node`` this resolves through PATH to the absolute
    binary the subprocess would actually start and verifies it runs, so a
    hijacked/broken node (e.g. a stale PATH entry left by an uninstalled RPA
    tool) is reported clearly instead of the child crashing with no output.
    Explicit executables (tests / callers that pin one) are used as-is.
    """
    if node_executable != "node":
        return node_executable, None
    node_cmd = _which_node() or "node"
    if node_cmd in _NODE_PROBE_CACHE:
        return node_cmd, _NODE_PROBE_CACHE[node_cmd]
    version: str | None = None
    try:
        probe = subprocess.run(
            [node_cmd, "--version"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=node_env,
            timeout=5,
            check=False,
            **no_console_window_kwargs(),
        )
        if probe.returncode == 0:
            version = (probe.stdout or probe.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    _NODE_PROBE_CACHE[node_cmd] = version
    return node_cmd, version


def _close_leftover_detail_pages(
    node_cmd: str,
    node_env: dict[str, str],
    cdp_url: str,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    """Close detail pages left behind by a killed executor (best effort).

    Uses the executor's own ``--close-detail-pages`` mode: it only closes Mabang detail
    pages, keeps the workspace page, and reports how many pages carried the
    「刊登信息保存成功」 dialog (proof the draft was in fact saved before the timeout).
    """

    try:
        completed = subprocess.run(
            [node_cmd, str(PLAYWRIGHT_EXECUTOR_PATH), "--cdp-url", cdp_url, "--close-detail-pages"],
            cwd=str(PROJECT_ROOT),
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=node_env,
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "close-detail-pages timed out"}
    except OSError as exc:
        return {"ok": False, "error": f"Unable to launch Node: {exc}"}

    output = (completed.stdout or "") + (completed.stderr or "")
    marker = "MABANG_PLAYWRIGHT_RESULT "
    for line in reversed(output.splitlines()):
        if not line.startswith(marker):
            continue
        try:
            decoded = json.loads(line[len(marker) :])
        except json.JSONDecodeError:
            break
        if isinstance(decoded, dict):
            return decoded
        break
    return {"ok": False, "error": "close-detail-pages returned no structured result"}


def run_playwright_save_draft(
    task_plan: dict[str, Any],
    *,
    node_executable: str = "node",
    cdp_url: str = "http://127.0.0.1:9223",
    timeout_seconds: int = 900,
    stop_before_save: bool = False,
    preserve_detail_on_failure: bool = False,
    resume_from_stage: str | None = None,
    api_url: str | None = None,
) -> dict[str, Any]:
    safety = task_plan.get("safety") or {}
    blocked_result = {
        "ok": False,
        "status": "blocked",
        "save_clicked": False,
        "publish_clicked": False,
        "page_closed": False,
    }
    if (
        not task_plan.get("ok")
        or task_plan.get("mode") != "save_draft"
        or safety.get("allow_save") is not True
        or safety.get("allow_publish") is not False
        or not safety.get("save_token")
    ):
        return {**blocked_result, "error": "Playwright save-draft requires an explicit safe save plan"}
    if not PLAYWRIGHT_EXECUTOR_PATH.is_file():
        return {**blocked_result, "status": "failed", "error": "Playwright executor is missing"}

    node_env = os.environ.copy()
    # 执行器现场 AI 判断必填项用的本地 API 地址（worker 传自己的 base_url）。
    node_env["MABANG_TASK_API_URL"] = str(api_url or "").strip() or "http://127.0.0.1:8788"
    node_modules = (PROJECT_ROOT / "node_modules").resolve()
    configured_module_dir = str(node_env.get("PLAYWRIGHT_NODE_MODULE_DIR") or "").strip()
    module_paths = [
        *([Path(configured_module_dir)] if configured_module_dir else []),
        node_modules / ".pnpm" / "node_modules",
        node_modules,
    ]
    discovered = [str(path) for path in module_paths if str(path) and path.is_dir()]
    inherited_node_path = str(node_env.get("NODE_PATH") or "").strip()
    if inherited_node_path:
        discovered.append(inherited_node_path)
    if discovered:
        node_env["NODE_PATH"] = os.pathsep.join(dict.fromkeys(discovered))

    node_cmd, node_version = _resolve_node_executable(node_executable, node_env)
    if node_executable == "node" and node_version is None:
        return {
            **blocked_result,
            "status": "failed",
            "exit_code": None,
            "node_path": node_cmd,
            "error": (
                f"Node 无法启动（{node_cmd} --version 执行失败）。"
                "通常是 PATH 中第三方工具捆绑的 node 抢先命中且已损坏，"
                "或系统不稳定。请确认 C:\\Program Files\\nodejs\\node.exe 存在且可运行。"
            ),
        }

    try:
        command = [node_cmd, str(PLAYWRIGHT_EXECUTOR_PATH), "--cdp-url", cdp_url]
        if stop_before_save:
            command.append("--stop-before-save")
        if preserve_detail_on_failure:
            command.append("--preserve-detail-on-failure")
        if resume_from_stage:
            command.extend(["--resume-from-stage", str(resume_from_stage)])
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            input=json.dumps(task_plan, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=node_env,
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        # 超时会把 node 直接杀掉，那一次运行的 close_saved_detail_page 就没跑成：浏览器里会留下
        # 详情页（保存成功时还带着「刊登信息保存成功」弹窗，现场 2026-09-27 22:21 任务 45fff5a5）。
        # 这里用执行器自带的收尾模式补一次，best-effort，不改变超时本身仍算失败的事实。
        leftover = _close_leftover_detail_pages(node_cmd, node_env, cdp_url)
        return {
            **blocked_result,
            "status": "failed",
            "error": "Playwright save-draft executor timed out",
            "timeout_seconds": timeout_seconds,
            "page_closed": bool(leftover.get("closed")),
            "leftover_detail_pages": leftover,
        }
    except OSError as exc:
        return {**blocked_result, "status": "failed", "error": f"Unable to launch Node: {exc}"}

    output = (completed.stdout or "") + (completed.stderr or "")
    marker = "MABANG_PLAYWRIGHT_RESULT "
    payload: dict[str, Any] | None = None
    for line in reversed(output.splitlines()):
        if not line.startswith(marker):
            continue
        try:
            decoded = json.loads(line[len(marker) :])
        except json.JSONDecodeError:
            break
        if isinstance(decoded, dict):
            payload = decoded
        break
    if payload is None:
        error = "Playwright executor did not return a structured result"
        if completed.returncode != 0:
            error += _exit_code_hint(completed.returncode)
        return {
            **blocked_result,
            "status": "failed",
            "exit_code": completed.returncode,
            "node_path": node_cmd,
            "node_version": node_version,
            "raw_output_tail": (output or "").strip()[-2000:],
            "error": error,
        }
    payload.setdefault("exit_code", completed.returncode)
    payload.setdefault("node_path", node_cmd)
    if node_version:
        payload.setdefault("node_version", node_version)
    # Ordered step trail from the executor's progress events → job detail timeline.
    steps = parse_playwright_step_trail(output)
    if steps:
        payload["steps"] = steps
    if payload.get("status") == "saved" and completed.returncode != 0:
        return {
            **blocked_result,
            "status": "failed",
            "exit_code": completed.returncode,
            "error": "Playwright executor reported saved with a nonzero exit code",
        }
    if payload.get("status") == "saved" and payload.get("ok") is not True:
        return {
            **blocked_result,
            "status": "failed",
            "exit_code": completed.returncode,
            "error": "Playwright executor reported saved with ok=false",
        }
    if payload.get("publish_clicked") is not False:
        return {
            **blocked_result,
            "status": "failed",
            "exit_code": completed.returncode,
            "error": "Playwright executor did not prove publish_clicked=false",
        }
    if payload.get("status") == "saved" and payload.get("save_clicked") is not True:
        return {
            **blocked_result,
            "status": "failed",
            "exit_code": completed.returncode,
            "error": "Playwright executor reported saved without save evidence",
        }
    if payload.get("status") == "saved" and payload.get("page_closed") is not True:
        return {
            **blocked_result,
            "status": "failed",
            "exit_code": completed.returncode,
            "error": "Playwright executor reported saved without closing the detail page",
        }
    if payload.get("status") == "saved" and payload.get("close_method") not in {
        "success_dialog",
        "playwright_fallback",
    }:
        return {
            **blocked_result,
            "status": "failed",
            "exit_code": completed.returncode,
            "error": "Playwright executor reported saved with an unknown close method",
        }
    return payload


def load_job_payload(job_id: str) -> dict[str, Any]:
    return storage.build_automation_payload(job_id)


def run_job(
    job_id: str,
    *,
    include_doctor: bool = False,
    execute_safe_fill: bool = False,
    execute_safe_audit: bool = False,
) -> dict[str, Any]:
    payload = load_job_payload(job_id)
    result = {
        "dry_run": build_dry_run_summary(payload),
        "task_plan": build_browser_use_task_plan(payload),
    }
    if include_doctor:
        result["doctor"] = run_doctor()
    if execute_safe_audit:
        result["safe_audit"] = run_browser_use_safe_audit(result["task_plan"])
    if execute_safe_fill:
        result["safe_fill"] = run_browser_use_safe_fill(result["task_plan"])
    return result


def _configure_data_dir(data_dir: str | None) -> None:
    if not data_dir:
        return
    path = Path(data_dir)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    settings.DATA_DIR = path
    settings.STORE_FILE = path / "store.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run or safely execute Browser-Use automation jobs.")
    parser.add_argument("--job-id", required=True, help="automation_jobs id to inspect")
    parser.add_argument("--data-dir", help="override DATA_DIR for local JSON store")
    parser.add_argument("--doctor", action="store_true", help="also run browser-use --doctor")
    parser.add_argument("--execute-safe-audit", action="store_true", help="run guarded Browser-Use page audit only")
    parser.add_argument("--execute-safe-fill", action="store_true", help="run guarded Browser-Use safe-fill script")
    args = parser.parse_args(argv)

    _configure_data_dir(args.data_dir)
    result = run_job(
        args.job_id,
        include_doctor=args.doctor,
        execute_safe_fill=args.execute_safe_fill,
        execute_safe_audit=args.execute_safe_audit,
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["dry_run"].get("ok"):
        return 2
    if args.execute_safe_audit and not result.get("safe_audit", {}).get("ok"):
        return 3
    if args.execute_safe_fill and not result.get("safe_fill", {}).get("ok"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
