"""修复被「印花」关键词误判的商品照（预览 / 执行）。

用法（在项目根目录）：
    # 1) 只读预览（默认）：看每个商品重扫后的分类、要补的图片和颜色候选
    python scripts/repair_print_named_product_photos.py

    # 2) 显式执行：先备份 data 里的 base_products/assets，再写入并重跑颜色识别
    python scripts/repair_print_named_product_photos.py --execute

    # 指定 SKU / 数据目录 / 不跑颜色识别
    python scripts/repair_print_named_product_photos.py --sku JB0366 --sku JB0373 --data-dir D:/other/data
    python scripts/repair_print_named_product_photos.py --execute --no-colors

默认处理 6 个现场确认的 SKU：JB0366 / JB0368 / JB0373 / JB0374 / JB0375 / JB0377。
颜色识别走现有 /api/base-products/{id}/regenerate-listing（aspect=colors），
已有颜色的商品会跳过，避免 AI 改掉手工颜色；标题和描述不会重新生成。

退出码：0 成功；1 有 SKU 找不到或写入失败；2 颜色识别失败（图片已保留，可重试）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="修复被「印花」误判的商品照：重扫素材分类、补空商品图、重跑颜色识别。"
    )
    parser.add_argument(
        "--sku",
        action="append",
        default=[],
        help="要修复的主 SKU（可重复）；不传时用默认的 6 个现场 SKU。",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="数据目录（默认用项目 data/）；指定后只作用于本进程的 DATA_DIR。",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真正写入数据；不传时只做只读预览。",
    )
    parser.add_argument(
        "--no-colors",
        action="store_true",
        help="执行时不调用颜色识别（只恢复分类和商品图片）。",
    )
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8788",
        help="颜色识别调用的 API 地址（默认 http://127.0.0.1:8788）。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出计划/结果（便于脚本消费）。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.data_dir:
        os.environ["DATA_DIR"] = str(Path(args.data_dir).expanduser())

    from services.api import photo_asset_repair as repair

    skus = args.sku or list(repair.DEFAULT_SKUS)
    plan = repair.build_repair_plan(skus)

    if not args.json:
        print(repair.format_plan(plan))
        print()

    if not args.execute:
        if args.json:
            print(json.dumps({"mode": "preview", **plan}, ensure_ascii=False, default=str, indent=2))
        else:
            print("预览模式：未写入任何数据。确认无误后加 --execute 执行。")
        return 0 if not plan["missing"] else 1

    result = repair.apply_repair_plan(plan)
    if not args.json:
        print(f"已备份: {result['backup']}")
        for item in result["applied"]:
            if item.get("error"):
                print(f"  {item['sku']}: 失败 - {item['error']}")
                continue
            print(
                f"  {item['sku']}: 更新字段 {', '.join(item['updated_fields']) or '无'}；"
                f"素材角色 {item['updated_assets']} 个，新增素材 {item['added_assets']} 个"
            )
        print()

    color_results: list[dict] = []
    if not args.no_colors:
        for item in plan["items"]:
            if item["color_stage"] != "needed":
                if not args.json:
                    reason = "已有颜色，跳过" if item["color_stage"] == "skipped_existing_colors" else "没有颜色候选图，跳过"
                    print(f"  {item['sku']}: 颜色识别{reason}")
                color_results.append({"sku": item["sku"], "ok": None, "stage": item["color_stage"]})
                continue
            outcome = repair.regenerate_colors(item["product_id"], args.api_base)
            color_results.append({"sku": item["sku"], **outcome})
            if not args.json:
                if outcome["ok"]:
                    print(f"  {item['sku']}: 颜色识别完成 {outcome['colors']}（{outcome['variant_count']} 张颜色图）")
                else:
                    print(f"  {item['sku']}: 颜色识别失败（图片已保留，可重试）: {outcome['error'] or outcome['stage']}")

    if args.json:
        print(
            json.dumps(
                {
                    "mode": "execute",
                    "backup": result["backup"],
                    "applied": result["applied"],
                    "colors": color_results,
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )

    if plan["missing"]:
        return 1
    if any(item.get("error") for item in result["applied"]):
        return 1
    if any(item.get("ok") is False for item in color_results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
