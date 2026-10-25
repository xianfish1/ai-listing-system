from __future__ import annotations

from typing import Any


def get_project_plan() -> dict[str, Any]:
    """Project-level operating policy exposed to the Web console."""

    return {
        "title": "马帮 Excel 导出工作台",
        "primary_output": "mabang_excel",
        "summary": [
            "NAS 素材扫描 -> 商品资料维护 -> 马帮导出预览 -> Excel 下载。",
            "Web 控制台只维护商品、SKU、价格和素材路径。",
            "系统只生成马帮导入模板，不连接第三方刊登页面。",
        ],
        "runtime_chain": [
            {
                "name": "NAS 素材库",
                "owner": "central",
                "status": "implemented",
                "notes": "扫描、分组、预览图片，并保留 NAS 路径作为 Excel 图片交付方式。",
            },
            {
                "name": "商品资料维护",
                "owner": "central",
                "status": "implemented",
                "notes": "维护商品、SKU、价格、尺寸、主图和变体图路径。",
            },
            {
                "name": "马帮导出预览",
                "owner": "central",
                "status": "implemented",
                "notes": "检查模板、必填字段、图片路径和可选字段缺口。",
            },
            {
                "name": "马帮 Excel 下载",
                "owner": "operator",
                "status": "implemented",
                "notes": "按马帮模板生成 .xlsx，并记录本地导出审计。",
            },
        ],
        "ai_policy": {
            "runtime_depends_on_codex": False,
            "suggestion_only": True,
            "auto_apply": False,
            "codex_role": "开发期维护代码、测试和导出映射，不作为运行时依赖。",
            "deepseek_role": "仅辅助生成标题和属性建议；采用建议后仍需人工保存商品资料。",
            "blocked_ai_actions": [
                "AI 不自动修改商品资料",
                "AI 不自动导出文件",
                "AI 不绕过马帮导出预览检查",
            ],
        },
        "execution_defaults": {
            "primary_action": "export_mabang_template",
            "export_format": ".xlsx",
            "image_handoff": "NAS path",
            "audit_failure_blocks_export": False,
            "template_source": "MABANG_TEMPLATE_PATH with local fallback",
        },
        "observability": {
            "watched_error_types": [
                "template_missing",
                "no_products_selected",
                "missing_required_fields",
                "audit_write_failed",
            ],
            "web_entry_points": ["商品", "素材", "计划", "导出准备度"],
        },
        "implementation_tasks": [
            {
                "phase": "M0",
                "title": "马帮导出主线",
                "owner": "developer",
                "priority": "done",
                "mode": "template_export",
                "status": "implemented",
                "acceptance": "商品选择后可预览并下载马帮 .xlsx，文件可由 openpyxl 打开。",
            },
            {
                "phase": "M1",
                "title": "字段质量巡检",
                "owner": "operator",
                "priority": "now",
                "mode": "product_maintenance",
                "status": "ready",
                "acceptance": "导出前能看到缺失字段、图片路径和 SKU 维度问题。",
            },
            {
                "phase": "M2",
                "title": "模板映射维护",
                "owner": "developer",
                "priority": "next",
                "mode": "mapping_update",
                "status": "planned",
                "acceptance": "马帮模板字段变化时，导出映射和回归测试同步更新。",
            },
        ],
        "next_milestones": [
            "持续补齐商品资料字段，提高马帮导入成功率。",
            "按实际马帮模板变化维护列映射。",
            "保留导出审计记录，方便追踪每次 Excel 生成依据。",
        ],
    }
