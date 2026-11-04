"""Built-in operator-template presets distilled from the retired「Shopee标题」project.

The rules below are a manually verified snapshot (2026-10-25 spec); the old
project's code is never executed, nothing syncs from it, and edits here only
change future presets. Creating a rule from a preset goes through the normal
``/api/title-skills`` create endpoint — the preset itself is read-only.
"""
from __future__ import annotations

from copy import deepcopy

OPERATOR_TEMPLATE_SOURCE = "Shopee标题 项目运营模板 · 人工核对快照"

# PH 男装印花 T 恤（v2）：AI 从商品图识别主题名+主题角色名，标题 = 主题名+角色名+核心词；
# 图片没有具体主题/角色时用通用描述兜底，不编造 IP 名。
# 超长按 Ootd → 100% Cotton → Streetwear 顺序删词，不切断单词。
# 100% Cotton 是保留的运营例外（使用时标待核对，不算已确认材质）；
# Faux Embroidery 仅在有仿刺绣视觉依据时使用；示例里的人物/IP 只做格式参考。
PH_MEN_PRINT_TSHIRT = {
    "id": "ph-men-print-tshirt",
    "name": "PH 男装印花 T 恤 · 仿运营模板",
    "version": 2,
    "source": OPERATOR_TEMPLATE_SOURCE,
    "scope": {"market": "PH", "category": "tshirt", "audience": "men"},
    "rule": {
        "name": "PH 男装印花 T 恤 · 仿运营模板",
        "market": "PH",
        "category": "tshirt",
        "audience": "men",
        "language": "en",
        "title_min_chars": 20,
        "title_max_chars": 100,
        "hot_word_policy": "reference",
        "ranking": "volume",
        "max_age_days": 30,
        "generation_mode": "operator_template",
        "fixed_terms": ["tshirt for men"],
        "banned_terms": [],
        "operator_allowances": ["100% Cotton"],
        "instructions": (
            "AI 从商品图识别印制的主题名与主题角色名，标题按「主题名+角色名+核心词」组成；"
            "识别不出具体主题/角色时用通用描述兜底，不要编造 IP 名，不足长度不强行填词。"
        ),
        "operator_template": {
            "core_term": "tshirt for men",
            "style_words": ["Anime", "Cartoon", "Retro", "Streetwear"],
            "companion_words": ["Print", "Short Sleeve", "Ootd"],
            "patterns": [
                "{theme} {character} {core}",
                "{theme} {character} {companion} {core}",
                "{style} {companion} {core}",
            ],
            "trim_order": ["Ootd", "100% Cotton", "Streetwear"],
            "rules": [
                "主题名与角色名必须来自商品图上真实印制的内容，识别不出就用通用描述（风格词或 Graphic），绝不猜测或编造 IP 名。",
                "示例里的人物和 IP 只做格式参考，不能用到没印这些内容的商品上；模板只适用于男装，不套到童装。",
                "Faux Embroidery 仅在商品图有仿刺绣视觉依据时使用，没有就不写。",
                "风格词描述的是图片观感，不是已确认的商品属性；材质说法只允许运营例外词。",
            ],
        },
        "examples": (
            "Naruto Sasuke tshirt for men\n"
            "Dragon Ball Goku tshirt for men\n"
            "Retro Graphic Print tshirt for men"
        ),
    },
}

CHILDREN_TEMPLATE_SOURCE = "shopee-ph-title-optimizer · 用户更新 2026-11-02"


def _children_preset(audience: str) -> dict:
    boys = audience == "boys"
    name = f"PH {'男童' if boys else '女童'}印花 T 恤 · 仿运营模板"
    core = "t shirt for kids boy" if boys else "T-Shirt for Kids Girl"
    pattern = (
        "{style} damit {theme} {core} ootd short-sleeve clothes printed 2-12 years old"
        if boys else "Cute {theme} {core} Printed Cotton Short-Sleeve Tops korean OOTD 2-12 years old"
    )
    optional = ["white", "blue"] if boys else ["Princess", "white", "toddler", "kuromi", "Casual"]
    return {
        "id": f"ph-{audience}-print-tshirt", "name": name, "version": 1,
        "source": CHILDREN_TEMPLATE_SOURCE,
        "scope": {"market": "PH", "category": "tshirt", "audience": audience},
        "rule": {
            "name": name, "market": "PH", "category": "tshirt", "audience": audience,
            "language": "taglish" if boys else "en", "title_min_chars": 20,
            "title_max_chars": 100, "generation_mode": "operator_template",
            "hot_word_policy": "reference", "fixed_terms": [core],
            "operator_allowances": [],
            "instructions": "使用 2026-11-02 最新童装模板，优先于历史词序。输出模板参考标题，模板词不代表流量证据。不默认加入 COD。",
            "operator_template": {
                "core_term": core, "style_words": ["Cartoon", "Anime"] if boys else ["Cute"],
                "companion_words": optional, "patterns": [pattern],
                "trim_order": optional + (["ootd", "clothes", "damit", "2-12 years old"] if boys else ["korean", "OOTD", "Tops", "2-12 years old", "Cotton"]),
                "reference_terms": (["2-12 years old"] if boys else ["Cotton", "2-12 years old"]),
                "source": CHILDREN_TEMPLATE_SOURCE, "version": 1,
                "rules": [
                    "Preserve the relative order of retained words. Replace {theme} with the current product theme; never copy unrelated IPs from examples.",
                    "Cartoon/Anime denotes alternatives: select one matching the theme. Optional/backup words are not a mandatory suffix.",
                    "Only use colors supported by the listing. kuromi is theme-specific; Princess and toddler must fit the product and audience.",
                    "Plain Cotton and 2-12 years old require material/size confirmation. Without it they may appear only in a REVIEW reference candidate, with per-term warnings. Never expand Cotton to 100% Cotton.",
                    "After theme substitution count all characters including spaces. Keep theme, item type and gender first; omit optional words before redundant style words. List omissions; never exceed 100 characters.",
                    "Template-only words are not traffic evidence. Do not invent search metrics. Do not add COD unless explicitly requested as a fixed term.",
                ],
            },
            "examples": pattern.replace("{style}", "Cartoon").replace("{theme}", "Cat").replace("{core}", core),
        },
    }


OPERATOR_TEMPLATE_PRESETS = [PH_MEN_PRINT_TSHIRT, _children_preset("boys"), _children_preset("girls")]


def list_presets() -> list[dict]:
    return deepcopy(OPERATOR_TEMPLATE_PRESETS)
