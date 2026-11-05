"""修复被「印花」关键词误判的商品照（素材分类 + 空图片字段 + 颜色识别）。

背景：文件名形如 ``T恤印花改图任务.png`` / ``T恤印花改图任务 (1).png`` 的商品照
曾被扫描器当成生产用印刷图（print）。整组只有这批照片的商品导入后 main_image、
product_images 全是空，颜色识别也拿不到候选图。扫描器已修正（见
``workers/asset_scanner/scanner.py`` 的 PRODUCT_PHOTO_PRINT_NAME_PATTERN），
这个模块负责把已经导入的商品数据按修正后的规则重扫一遍。

约束（与需求一致）：
- 只认商品当前 ``asset_root`` 下的素材，不混入历史导入目录；
- 只补空的主图/商品图列表，已有手工选择和其他字段保持原值；
- 颜色走现有 ``regenerate-listing`` 的 ``aspect=colors`` 专用流程，已有颜色的
  商品不重跑（避免 AI 改掉手工颜色），标题/描述不重新生成；
- 重复执行不新建商品或素材记录，结果一致；
- 预览模式只读，执行模式先备份 ``base_products`` / ``assets`` 数据文件。
"""
from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

# 本次现场确认受影响的 6 个商品（其它 SKU 可用 --sku 显式指定）。
DEFAULT_SKUS: tuple[str, ...] = (
    "JB0366",
    "JB0368",
    "JB0373",
    "JB0374",
    "JB0375",
    "JB0377",
)

_SKU_FIELDS = ("main_sku", "group_id")
_BACKUP_COLLECTIONS = ("base_products", "assets", "store")


def _normalize_sku(value: Any) -> str:
    return str(value or "").strip().upper()


def find_base_products(skus: Iterable[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """按 main_sku / group_id 找商品，返回（按入参顺序的商品, 找不到的 SKU）。"""
    from services.api import storage

    wanted = [sku for sku in dict.fromkeys(_normalize_sku(sku) for sku in skus) if sku]
    products = storage.list_items("base_products")
    index: dict[str, dict[str, Any]] = {}
    for product in products:
        for field in _SKU_FIELDS:
            key = _normalize_sku(product.get(field))
            if key and key not in index:
                index[key] = product
    found = [index[sku] for sku in wanted if sku in index]
    missing = [sku for sku in wanted if sku not in index]
    return found, missing


def _asset_counts(assets: Iterable[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(asset.get("asset_type") or "unknown") for asset in assets).items()))


def _scan_assets(product: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    from workers.asset_scanner.scanner import scan_asset_root

    root = str(product.get("asset_root") or "").strip()
    if not root:
        return [], ["商品没有 asset_root"]
    if not Path(root).exists():
        return [], [f"asset_root 不存在: {root}"]
    result = scan_asset_root(
        root,
        category_override=str(product.get("category") or "").strip(),
        mode="recursive",
    )
    return [dict(asset) for asset in result.get("assets") or []], list(result.get("errors") or [])


def _color_target_names(scanned: list[dict[str, Any]]) -> list[str]:
    """会被「重新识别颜色图」看到的文件：变体图 + 无信息主图（沿用现有规则）。"""
    from services.api import storage, vision_listing

    names: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        name = str(value or "").strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)

    for asset in scanned:
        if str(asset.get("asset_type") or "").lower() == "variant":
            add(asset.get("file_name"))
    for asset in vision_listing.plain_main_color_assets(scanned):
        add(asset.get("file_name"))
    return sorted(names, key=storage.natural_name_key)[: vision_listing.MAX_COLOR_IMAGES]


def build_repair_plan(skus: Iterable[str]) -> dict[str, Any]:
    """只读：算出每个商品重扫后的分类、要补的图片字段和颜色识别目标。"""
    from services.api import storage

    products, missing = find_base_products(skus)
    items: list[dict[str, Any]] = []
    for product in products:
        current_assets = storage.assets_for_base_product(product)
        scanned, scan_errors = _scan_assets(product)
        main_after = storage._pick_main_asset(scanned) if scanned else None
        default_images = storage._default_product_images(scanned, main_after) if scanned else []
        color_targets = _color_target_names(scanned) if scanned else []

        current_main = str(product.get("main_image") or "").strip()
        current_images = [str(name) for name in product.get("product_images") or [] if str(name).strip()]
        changes: list[str] = []
        if scanned and not current_main and main_after:
            changes.append("main_image")
        if scanned and not current_images and default_images:
            changes.append("product_images")
        changed_types = [
            asset
            for asset in scanned
            if str(asset.get("asset_type") or "") != str(
                next(
                    (
                        item.get("asset_type")
                        for item in current_assets
                        if item.get("id") == asset.get("id")
                    ),
                    "",
                )
            )
        ]
        if changed_types:
            changes.append(f"asset_type ×{len(changed_types)}")

        colors = [str(item) for item in product.get("recognized_colors") or [] if str(item).strip()]
        if colors:
            color_stage = "skipped_existing_colors"
        elif not color_targets:
            color_stage = "skipped_no_targets"
        else:
            color_stage = "needed"

        warnings = list(scan_errors)
        if not scanned:
            warnings.append("重扫没有拿到任何素材，保持原样")
        if any(not Path(str(asset.get("file_path") or "")).exists() for asset in scanned):
            warnings.append("部分素材文件当前不可访问（NAS 离线？）")

        items.append(
            {
                "sku": str(product.get("main_sku") or product.get("group_id") or ""),
                "product_id": str(product.get("id") or ""),
                "asset_root": str(product.get("asset_root") or ""),
                "before": {
                    "main_image": current_main,
                    "product_images": current_images,
                    "recognized_colors": colors,
                    "variant_image_map": dict(product.get("variant_image_map") or {}),
                    "asset_counts": _asset_counts(current_assets),
                },
                "after": {
                    "main_image": str((main_after or {}).get("file_name") or "") if not current_main else current_main,
                    "product_images": default_images if not current_images else current_images,
                    "variant_count": sum(1 for asset in scanned if str(asset.get("asset_type") or "").lower() == "variant"),
                    "asset_counts": _asset_counts(scanned),
                },
                "changes": changes,
                "color_targets": color_targets,
                "color_stage": color_stage,
                "warnings": warnings,
                "_product": product,
                "_scanned": scanned,
            }
        )
    return {"items": items, "missing": missing}


def _backup_data_files() -> str:
    from services.api import settings

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(settings.DATA_DIR) / "backups" / f"repair-print-photo-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in _BACKUP_COLLECTIONS:
        source = Path(settings.DATA_DIR) / f"{name}.json"
        if source.exists():
            shutil.copy2(source, backup_dir / source.name)
            copied.append(source.name)
    return f"{backup_dir} ({', '.join(copied) or '无文件可备份'})"


def apply_repair_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """执行：更新素材角色 + 只补空的主图/商品图字段。返回每个商品的变更结果。"""
    from services.api import storage

    raw_items = [item for item in plan.get("items") or [] if item.get("_scanned")]
    applied: list[dict[str, Any]] = []
    if not raw_items:
        return {"backup": "", "applied": applied}
    # 没有任何要改的（重复执行）就不再制造备份、也不重写数据文件。
    if not any(item.get("changes") for item in raw_items):
        return {"backup": "", "applied": applied, "unchanged": True}

    backup = _backup_data_files()
    with storage.locked_store(split_keys=("assets", "base_products"), load_main=False) as (data, save):
        assets = data.setdefault("assets", [])
        products = data.setdefault("base_products", [])
        assets_by_id = {str(asset.get("id") or ""): asset for asset in assets if str(asset.get("id") or "")}
        products_by_id = {str(item.get("id") or ""): item for item in products if str(item.get("id") or "")}

        for item in raw_items:
            product = products_by_id.get(item["product_id"])
            if product is None:
                applied.append({"sku": item["sku"], "error": "商品在写入时已不存在"})
                continue
            updated_assets = 0
            added_assets = 0
            for scanned in item["_scanned"]:
                asset_id = str(scanned.get("id") or "")
                if not asset_id:
                    continue
                existing = assets_by_id.get(asset_id)
                if existing is None:
                    assets.append(scanned)
                    assets_by_id[asset_id] = scanned
                    added_assets += 1
                    continue
                changed = False
                for field in ("asset_type", "sku_option", "width", "height", "status"):
                    if field in scanned and existing.get(field) != scanned.get(field):
                        existing[field] = scanned.get(field)
                        changed = True
                if changed:
                    updated_assets += 1

            updates: dict[str, Any] = {}
            if not str(product.get("main_image") or "").strip() and item["after"]["main_image"]:
                updates["main_image"] = item["after"]["main_image"]
            if not (product.get("product_images") or []) and item["after"]["product_images"]:
                updates["product_images"] = list(item["after"]["product_images"])
            if updates:
                product.update(updates)
                product["updated_at"] = storage.utc_now()
            applied.append(
                {
                    "sku": item["sku"],
                    "product_id": item["product_id"],
                    "updated_fields": sorted(updates),
                    "updated_assets": updated_assets,
                    "added_assets": added_assets,
                }
            )
        save(data)
    return {"backup": backup, "applied": applied}


def regenerate_colors(product_id: str, api_base: str, timeout: float = 240.0) -> dict[str, Any]:
    """调用现有「重新识别颜色图」流程（aspect=colors，不碰标题/描述）。"""
    url = f"{str(api_base).rstrip('/')}/api/base-products/{product_id}/regenerate-listing"
    body = json.dumps({"aspect": "colors"}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:500]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "stage": "failed", "error": f"HTTP {exc.code}: {detail}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "stage": "failed", "error": f"无法连接 API: {exc}"}

    stages = payload.get("ai_stages") or {}
    error = str(payload.get("ai_generation_error") or "").strip()
    stage = str(stages.get("colors") or "").strip()
    return {
        "ok": stage == "done" and not error,
        "stage": stage or "unknown",
        "error": error,
        "colors": payload.get("recognized_colors") or [],
        "variant_count": payload.get("variant_count") or 0,
    }


def format_plan(plan: dict[str, Any]) -> str:
    """预览/执行结果的纯文本报告。"""
    lines: list[str] = []
    for item in plan.get("items") or []:
        before = item["before"]
        after = item["after"]
        lines.append(f"== {item['sku']}  ({item['product_id']})")
        lines.append(f"   asset_root: {item['asset_root']}")
        lines.append(
            "   素材分类: "
            f"{json.dumps(before['asset_counts'], ensure_ascii=False)} → {json.dumps(after['asset_counts'], ensure_ascii=False)}"
        )
        lines.append(
            "   主图: "
            f"{before['main_image'] or '(空)'} → {after['main_image'] or '(空)'}"
            if "main_image" in item["changes"]
            else f"   主图: {before['main_image'] or '(空)'}（保持）"
        )
        if "product_images" in item["changes"]:
            lines.append(f"   商品图: (空) → {len(after['product_images'])} 张")
        else:
            lines.append(f"   商品图: {len(before['product_images'])} 张（保持）")
        lines.append(f"   已有颜色: {', '.join(before['recognized_colors']) or '(无)'}")
        lines.append(
            f"   颜色识别: {item['color_stage']}，候选 {len(item['color_targets'])} 张"
            + (f"：{', '.join(item['color_targets'][:5])}{'…' if len(item['color_targets']) > 5 else ''}" if item["color_targets"] else "")
        )
        lines.append(f"   变更: {', '.join(item['changes']) or '无'}")
        for warning in item["warnings"]:
            lines.append(f"   ! {warning}")
    for sku in plan.get("missing") or []:
        lines.append(f"== {sku}: 找不到对应商品（跳过）")
    return "\n".join(lines)
