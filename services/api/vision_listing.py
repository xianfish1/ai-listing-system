"""AI vision-based listing title + description generation.

Given a base_product with local product images, ask the configured AI model to write
an English title and description. The model must be vision-capable (Claude 4 family,
GPT-4o, Gemini). Failures are non-fatal — callers should treat this as a best-effort
enrichment step.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from services.api import storage, title_selection
from services.api.model_gateway import GenerationImage, GenerationRequest


MAX_IMAGES = 3
# A color-only pass needs every SKU image so the model can name each variant;
# regular title/description generation keeps the smaller representative set.
MAX_COLOR_IMAGES = 12
MAX_IMAGE_BYTES = 3 * 1024 * 1024  # 3 MB per image keeps latency + cost bounded

# 图像档位：颜色识别只要看清"这块布是什么颜色"，512px 足够；
# 12 张 1280px 大图的请求体是 3~5MB，色卡级小图能小一个数量级，视觉模型 prefill 也快得多
# （现场反馈「导入识别太慢」，这是导入路径上最大的一笔）。
IMAGE_PROFILE_TEXT = {"edge": 1280, "quality": 85, "max_bytes": MAX_IMAGE_BYTES}
IMAGE_PROFILE_COLOR = {"edge": 512, "quality": 70, "max_bytes": 400 * 1024, "always_downscale": True}
# AI 生图工具导出的商品图常常单张 4-5MB，直接跳过会让视觉识别拿不到任何图片、
# 静默退化成「款式A/B/C」并把标题详情识别一起带坏。超过阈值先缩到最长边
# VISION_IMAGE_EDGE 再发；Pillow 不可用时退回原始字节，但保留一个硬上限。
MAX_IMAGE_BYTES_HARD = 12 * 1024 * 1024
VISION_IMAGE_EDGE = 1280
VISION_IMAGE_QUALITY = 85
TITLE_MAX_CHARS = 120  # Shopee title limit is 100; leave slack for editing
DESCRIPTION_MAX_CHARS = 1200  # Roughly 300–500 words
ATTRIBUTES_MAX_ENTRIES = 12  # cap recognized attributes so one bad model reply
ATTRIBUTE_KEY_MAX_CHARS = 60  # can't flood the description block
ATTRIBUTE_VALUE_MAX_CHARS = 120
COLOR_TRANSLATION_MAX_ENTRIES = 20
COLOR_TRANSLATION_MAX_CHARS = 60

# AI 识别颜色时只允许用这 9 个固定颜色（操作员指定）：模型看到什么颜色都要归到最
# 接近的一个，避免 LightPurple / Cream White 这类自造名进入颜色轴（2026-09-21）。
FIXED_PRODUCT_COLORS = (
    "Apricot",
    "Purple",
    "Blue",
    "Red",
    "Black",
    "White",
    "Pink",
    "Brown",
    "Green",
)
FIXED_COLOR_PROMPT_LIST = ", ".join(FIXED_PRODUCT_COLORS)
# 近似色/同义写法归到色板上最接近的一个；色板里没有的颜色（灰/黄/橙/卡其…）保持
# 原样，宁可让操作员看到再手动改，也不悄悄改成另一个颜色。
_COLOR_SYNONYMS = {
    "navy": "Blue",
    "azure": "Blue",
    "sky": "Blue",
    "denim": "Blue",
    "cobalt": "Blue",
    "indigo": "Blue",
    "royal": "Blue",
    "sapphire": "Blue",
    "cyan": "Blue",
    "ocean": "Blue",
    "lavender": "Purple",
    "violet": "Purple",
    "lilac": "Purple",
    "plum": "Purple",
    "mauve": "Purple",
    "orchid": "Purple",
    "grape": "Purple",
    "maroon": "Red",
    "wine": "Red",
    "burgundy": "Red",
    "crimson": "Red",
    "scarlet": "Red",
    "cherry": "Red",
    "rose": "Pink",
    "blush": "Pink",
    "fuchsia": "Pink",
    "magenta": "Pink",
    "salmon": "Pink",
    "coral": "Pink",
    "mint": "Green",
    "olive": "Green",
    "lime": "Green",
    "emerald": "Green",
    "sage": "Green",
    "army": "Green",
    "forest": "Green",
    "cream": "White",
    "ivory": "White",
    "offwhite": "White",
    "pearl": "White",
    "eggshell": "White",
    "chocolate": "Brown",
    "coffee": "Brown",
    "tan": "Brown",
    "camel": "Brown",
    "charcoal": "Black",
    "peach": "Apricot",
}
# 深浅/修饰前缀：darkgreen、light blue、off white 这类写法先去前缀再认色板。
_COLOR_MODIFIER_PREFIXES = (
    "dark",
    "light",
    "deep",
    "pale",
    "bright",
    "soft",
    "off",
    "pure",
    "true",
)


@dataclass(frozen=True)
class VisionListingResult:
    title: str
    description: str
    model_id: str
    input_image_count: int
    error: str = ""
    attributes: dict[str, str] | None = None
    color_translations: dict[str, str] | None = None
    title_candidates: list[dict[str, Any]] | None = None

    @property
    def ok(self) -> bool:
        return not self.error


def _read_image(path: Path, *, profile: dict[str, Any] | None = None) -> GenerationImage | None:
    """Read an image file into a GenerationImage. Returns None on any I/O error or
    unsupported format — this is best-effort enrichment, missing images are fine.

    ``profile`` 决定缩图档位：颜色识别用小图（快），标题/描述用默认档。"""
    if not path or not path.exists() or not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    suffix = path.suffix.lower().lstrip(".")
    media_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    media_type = media_map.get(suffix)
    if not media_type:
        return None
    profile = profile or IMAGE_PROFILE_TEXT
    max_bytes = int(profile.get("max_bytes") or MAX_IMAGE_BYTES)
    if profile.get("always_downscale") or len(raw) > max_bytes:
        # 颜色识别固定缩图（哪怕原图不大）：请求体越小，上传 + 视觉 prefill 越快
        shrunk = _downscale_image(path, edge=int(profile["edge"]), quality=int(profile["quality"]))
        if shrunk:
            raw, media_type = shrunk, "image/jpeg"
        elif not profile.get("always_downscale") and len(raw) > MAX_IMAGE_BYTES_HARD:
            return None
    return GenerationImage(media_type=media_type, data_base64=base64.b64encode(raw).decode("ascii"))


def _downscale_image(path: Path, *, edge: int = VISION_IMAGE_EDGE, quality: int = VISION_IMAGE_QUALITY) -> bytes | None:
    """缩到 ``edge`` 并重新编码成 JPEG；Pillow 缺失或解码失败返回 None。

    视觉模型只需要看清款式和颜色，原图尺寸毫无收益，只会拖慢并顶满请求体。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow 是可选依赖
        return None
    try:
        with Image.open(path) as image:
            if image.mode in {"RGBA", "LA", "P"}:
                # 透明通道压到白底，避免转 RGB 后变成黑底（马帮/Shopee 主图都是浅底）。
                converted = image.convert("RGBA")
                background = Image.new("RGB", converted.size, (255, 255, 255))
                background.paste(converted, (0, 0), mask=converted.split()[-1])
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((edge, edge))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
    except Exception:  # noqa: BLE001 - 坏图/缺编解码器都不能阻断导入
        return None
    return buffer.getvalue() or None


def _is_plain_main_asset(asset: dict[str, Any]) -> bool:
    """主图角色、但文件名没有任何角色线索的照片（``1.png``、``1 (2).png``、
    ``服装颜色替换生成 (3).png``、``变清晰 (3).png`` 这类）。

    这类文件在文件夹里没有显式主图、也没有认得出的颜色名时，扫描器会把整组改判成
    「未命名颜色图」交给视觉识别（见 scanner._apply_unnamed_variant_set_policy）。
    但文件夹里只要出现一张显式主图（``主图.png`` / ``main-01.jpg``）或一个认得出的
    颜色文件名，扫描器就按「多角度商品图」保留主图角色，AI 颜色识别于是完全看不到
    它们——现场把改色图重命名成 1-9 再导入，结果一个颜色都没识别出来。"""
    if str(asset.get("asset_type") or "").lower() != "main":
        return False
    if str(asset.get("sku_option") or "").strip():
        return False
    stem = Path(str(asset.get("file_name") or "")).stem.strip()
    if not stem:
        return False
    lowered = stem.lower()
    return not (lowered.startswith("main") or "\u4e3b\u56fe" in stem)


def plain_main_color_assets(assets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """一整套无信息主图（``1-9``、``1 (1).png``…）当作颜色图交给视觉识别。

    只有它们占了本商品「主图 + 颜色图」的一半以上才算：主图 + 多角度商品图 +
    已经命名的颜色图这种文件夹里，少量无信息图仍然是商品照片，不能拿去配色。
    模特图/详情图是显式角色，不参与这个比例。"""
    photos = [
        asset
        for asset in assets or []
        if str(asset.get("asset_type") or "").lower() in {"main", "variant"}
        and str(asset.get("file_name") or "").strip()
    ]
    plain = [asset for asset in photos if _is_plain_main_asset(asset)]
    if len(plain) < 2 or len(plain) * 2 < len(photos):
        return []
    return plain


def ordered_unnamed_variant_assets(assets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """没有颜色名的 SKU 图（数字文件名）按自然序排好。

    提示里的文件名顺序、发给模型的图片顺序、写回颜色图映射的顺序都取这一份，
    三处必须一致。

    除了扫描器判成颜色图的未命名文件，还会带上「一整套无信息主图」
    （见 :func:`plain_main_color_assets`）：图片命名成 1-9、文件夹里另有主图时，
    这些图仍然要被 AI 命名成预设的常用颜色。"""
    files = [
        asset
        for asset in assets or []
        if str(asset.get("asset_type") or "").lower() == "variant"
        and str(asset.get("file_name") or "").strip()
        and not str(asset.get("sku_option") or "").strip()
    ]
    files.extend(plain_main_color_assets(assets))
    unique: dict[str, dict[str, Any]] = {}
    for asset in files:
        unique.setdefault(str(asset.get("file_name") or "").strip().lower(), asset)
    return sorted(unique.values(), key=lambda asset: storage.natural_name_key(asset.get("file_name")))


def _pick_image_paths(
    base_product: dict[str, Any],
    assets: list[dict[str, Any]],
    *,
    max_images: int = MAX_IMAGES,
    color_only: bool = False,
    target_names: list[str] | None = None,
) -> list[Path]:
    """Pick up to ``max_images`` representative images, with the main image first.
    Falls back to whatever product_images points at.

    ``color_only``（颜色命名那一趟）按自然序取未命名 SKU 图，并且不掺入商品上已有的
    颜色图映射：那份映射可能正是错的（重试识别时），掺进来会把图片顺序打乱。

    ``target_names``（重新识别颜色图）只发调用方点名的文件，顺序就是给定的顺序；
    这类重识别要连已经命名的颜色图一起重看，所以不能再让主图或旧映射插队。"""
    by_name: dict[str, Path] = {}
    for asset in assets:
        name = str(asset.get("file_name") or "").strip()
        file_path = str(asset.get("file_path") or "").strip()
        if name and file_path:
            by_name[name] = Path(file_path)

    if target_names is not None:
        # 指定文件重识别：按调用方给的顺序原样取图，不掺主图/旧颜色映射。
        target_picks: list[Path] = []
        for name in target_names:
            path = by_name.get(str(name or "").strip())
            if path is not None and path not in target_picks:
                target_picks.append(path)
        return target_picks[:max_images]

    picks: list[Path] = []
    seen: set[str] = set()

    def take(name: str) -> None:
        if not name or name in seen:
            return
        path = by_name.get(name)
        if path is None:
            return
        seen.add(name)
        picks.append(path)

    if not color_only:
        # 颜色那一趟不预置主图：主图通常不是颜色图，混进来会顶掉一张改色图，
        # 还会让提示里的 #N 编号与写回映射的下标错开一格（颜色整体错位）。
        take(str(base_product.get("main_image") or ""))

    if not color_only:
        variant_map = base_product.get("variant_image_map") or {}
        if isinstance(variant_map, dict):
            for color_image in variant_map.values():
                if len(picks) >= max_images:
                    break
                take(str(color_image or ""))

    if color_only:
        # 颜色命名那一趟：只发未命名的 SKU 图，顺序与提示里的文件名顺序一致。
        ordered = [asset.get("file_name") for asset in ordered_unnamed_variant_assets(assets)]
    else:
        # Numeric SKU filenames intentionally have no color key until vision names the
        # color. Include those variant assets so the model can inspect their actual photos.
        ordered = sorted(
            (
                asset.get("file_name")
                for asset in assets
                if str(asset.get("asset_type") or "").lower() == "variant"
            ),
            key=storage.natural_name_key,
        )
    for file_name in ordered:
        if len(picks) >= max_images:
            break
        take(str(file_name or ""))

    for name in base_product.get("product_images") or []:
        if len(picks) >= max_images:
            break
        take(str(name or ""))

    return picks[:max_images]


_SYSTEM_PROMPT = """You are a Southeast Asia cross-border e-commerce copywriter for a
Chinese seller listing on Shopee and Lazada. Given product photos plus optional attribute
hints, produce a listing in ENGLISH that will save as a draft in the seller's ERP.

Return STRICTLY valid JSON with three keys and nothing else — no markdown fences, no prose.

{
  "title": "<English title, plain text, 40–100 characters, keyword-forward>",
  "description": "<English description, plain text, 200–500 characters, 2–4 short paragraphs>",
  "attributes": {"<English attribute name>": "<English value>"},
  "color_translations": {"<source color>": "<English color name>"}
}

Rules:
- Never invent measurements, certifications, warranties, or pricing.
- Never use exaggerations like 'best', 'guaranteed', 'lifetime'.
- Do not use markdown, HTML, or emoji.
- Title: keyword-forward. Include product type, key attribute (material / style / theme),
  and 1–2 use cases if visible. Under 100 chars.
- Description: what it is, what it's made of (if visible), who it's for, when to wear /
  where to use it, care hints only if universally safe. Neutral, factual, benefit-led.
  Do NOT include the attribute list in the description — the service formats attributes separately.
- attributes: 5–10 standard cross-border e-commerce attributes that fit this item, e.g.
  Item Type, Material, Style, Applicable Season, Applicable Scene, Sleeve Length(cm),
  Collar, Hooded, Fit Type, Closure Type, Pattern Type, Gender, Detachable Part.
  Names must be in English exactly as above when they apply. Values must be short English
  facts visible in the photos or given in the hints (e.g. "Polyester", "Casual",
  "Four Seasons", "Full", "Hoodies"). If an attribute clearly does not apply to this item
  (e.g. no detachable hood), use "NONE" as the value instead of guessing.
  Omit an attribute entirely when even a NONE-style answer would be meaningless.
- color_translations: use ONLY these fixed color names — Apricot, Purple, Blue, Red,
  Black, White, Pink, Brown, Green. Pick the closest one for what the photo shows; never
  invent a color name, never write compound or self-made names (no "LightPurple",
  "DarkBlue", "Cream White", "Navy"), and never answer in Chinese. Keep an English input
  unchanged when it is already one of the fixed names.
  For numeric/unlabeled SKU images, use the exact image filename from the hints as the
  key (never use "01"/"02" as a color) and name the visible color with one of the fixed
  names.
- Variant-set naming: when the SKU images show what the package contains rather than a
  color difference (e.g. wig sold separately, costume sold separately, and the full set),
  name each variant after its contents in English — "Costume" for the garment alone,
  "Wig" for the wig alone, "Costume + Wig" for the full set. The same applies to other
  accessories (e.g. "Costume + Shoes"). Only fall back to real colors when every SKU
  image shows the same item type. Vendors may also name these files "Costumes" /
  "Only Wig" — map those to the same labels ("Costume", "Wig"). These content labels are
  the only values that may sit outside the fixed color list.
- If the images are ambiguous (e.g. blank template photos), keep the title generic and
  the description brief; don't fabricate specifics."""


def build_system_prompt(
    title_skill: dict[str, Any] | None = None,
    hot_words: list[str] | None = None,
    title_context: dict[str, Any] | None = None,
) -> str:
    """标题 Skill 注入点：有 Skill 时在基础 prompt 后追加补充段（标题字数目标、
    操作员补充规则、热词使用策略），没有 Skill 则原样返回基础 prompt——导入与
    重试的行为在「未配置 Skill」时与以前完全一致。"""
    if not title_skill:
        return _SYSTEM_PROMPT
    min_chars = int(title_skill.get("title_min_chars") or 40)
    max_chars = int(title_skill.get("title_max_chars") or 100)
    lines: list[str] = [
        'Title skill supplement (applies to the "title" field only) — these rules '
        "override the title length guidance above:",
        f"- Title length target: {min_chars}-{max_chars} characters.",
    ]
    instructions = str(title_skill.get("instructions") or "").strip()
    if instructions:
        for raw in instructions.splitlines():
            line = raw.strip()
            if line:
                lines.append(f"- {line}")
    policy = str(title_skill.get("hot_word_policy") or "reference").strip().lower()
    words = [str(word or "").strip() for word in (hot_words or []) if str(word or "").strip()]
    if policy != "off" and words and not title_context:
        joined = ", ".join(words)
        if policy == "prefer":
            lines.append(
                f"- Latest trending search keywords: {joined}. Prefer to include as many of "
                "these as genuinely fit this product at natural positions; never force an "
                "unrelated one."
            )
        else:
            lines.append(
                f"- Latest trending search keywords: {joined}. Weave in ONLY the ones that "
                "genuinely fit this product; never force an unrelated one."
            )
    return _SYSTEM_PROMPT + "\n\n" + "\n".join(lines) + (title_selection.prompt_context(title_context) if title_context else "")


def _build_user_prompt(
    base_product: dict[str, Any],
    attribute_hints: dict[str, str],
    assets: list[dict[str, Any]] | None = None,
    attached_image_names: list[str] | None = None,
    color_only: bool = False,
) -> str:
    parts: list[str] = []
    category = str(base_product.get("category") or "").strip()
    if category:
        parts.append(f"Category: {category}")
    main_sku = str(base_product.get("main_sku") or base_product.get("group_id") or "").strip()
    if main_sku:
        parts.append(f"Main SKU: {main_sku}")
    colors = base_product.get("recognized_colors") or []
    if colors:
        parts.append(f"Detected color variants: {', '.join(str(c) for c in colors if c)}")
    variant_count = int(base_product.get("variant_count") or 0)
    if variant_count:
        parts.append(f"Variant images count: {variant_count}")
    unnamed_variant_files = [
        str(asset.get("file_name") or "").strip()
        for asset in ordered_unnamed_variant_assets(assets)
    ]
    if attached_image_names:
        # 编号清单就是这一趟实际附带的图片顺序（数量、顺序都与请求一致），
        # 模型不需要再去猜第 N 张图对应哪个文件名。
        parts.append(
            "Attached product images, in this exact order — use these filenames as the "
            "keys when naming SKU/color variants (numeric parts are image indexes, not "
            "colors):\n"
            + "\n".join(f"#{index + 1} {name}" for index, name in enumerate(attached_image_names))
        )
        if color_only:
            # 颜色那一趟把色板直接摆在模型面前：颜色名只许从这 9 个里选最接近的一个。
            parts.append(
                "Fixed color palette — every color value in color_translations must be "
                f"exactly one of: {FIXED_COLOR_PROMPT_LIST}. Pick the closest match; do "
                "not invent, mix, or translate into other color names."
            )
    elif unnamed_variant_files:
        parts.append(
            "Color image filename hints (numeric parts are image indexes, not colors): "
            + ", ".join(unnamed_variant_files)
        )

    if attribute_hints:
        hint_lines = [f"- {k}: {v}" for k, v in attribute_hints.items() if str(v or "").strip()]
        if hint_lines:
            parts.append("Attribute hints from operator:\n" + "\n".join(hint_lines))

    parts.append("Now examine the product photos and produce the JSON listing.")
    return "\n\n".join(parts)


def _parse_result(text: str) -> tuple[str, str, dict[str, str], dict[str, str]]:
    """Extract the first JSON object with `title`, `description` and optional
    `attributes` keys. Tolerates ```json fences or leading/trailing prose so a
    poorly-formatted model still works."""
    stripped = text.strip()
    # Strip common markdown fences
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        # Fall back to grabbing the first {...} block
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            raise ValueError("AI 未返回 JSON 结果") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("AI 返回的 JSON 不是对象")
    title = str(parsed.get("title") or "").strip()
    description = str(parsed.get("description") or "").strip()[:DESCRIPTION_MAX_CHARS]
    attributes = _normalize_attributes(parsed.get("attributes"))
    color_translations = _normalize_color_translations(parsed.get("color_translations"))
    if not title and not description and not attributes and not color_translations:
        raise ValueError("AI 返回空标题和空描述")
    return title, description, attributes, color_translations


def _normalize_attributes(value: Any) -> dict[str, str]:
    """Normalize the model's attributes payload into an ordered {name: value} dict.
    Non-dict payloads, empty keys/values and over-long entries are dropped so one
    sloppy model reply can't poison the description block."""
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()[:ATTRIBUTE_KEY_MAX_CHARS]
        val = " ".join(str(raw_value if raw_value is not None else "").split())[:ATTRIBUTE_VALUE_MAX_CHARS]
        if key and val:
            normalized[key] = val
        if len(normalized) >= ATTRIBUTES_MAX_ENTRIES:
            break
    return normalized


def format_attributes_block(attributes: dict[str, str]) -> str:
    """Render attributes as the ERP-style block the operator pastes into 详情:
    'Key : Value' lines separated by blank lines, e.g.
    Applicable Season : Four Seasons\\n\\nMaterial : Polyester"""
    lines = [f"{key} : {value}" for key, value in (attributes or {}).items() if str(key or "").strip()]
    return "\n\n".join(lines)


def _palette_match(word: str) -> str:
    """单个词/规范写法命中固定色板时返回色板里的写法，认不出返回 ""。"""
    key = re.sub(r"[^a-z0-9]+", "", str(word or "").lower())
    if not key:
        return ""
    for color in FIXED_PRODUCT_COLORS:
        if key == color.lower():
            return color
    return _COLOR_SYNONYMS.get(key, "")


def snap_color_to_palette(value: Any) -> str:
    """把模型给的颜色名归到固定色板，认不出来返回 ""（调用方保留原值）。

    先整串认（``Purple``/``navy``），再去掉深浅修饰前缀认一遍（``DarkGreen``、
    ``light blue``、``off white``），最后逐词认并取最靠后的命中词——英文颜色名的
    中心词在末尾（``cream white`` → White、``sky blue`` → Blue）。套装标签
    （Costume/Wig）和色板外的颜色不含颜色词，一律返回 ""，由调用方原样保留。"""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    hit = _palette_match(text)
    if hit:
        return hit
    compact = re.sub(r"[^a-z0-9]+", "", text.lower())
    for prefix in _COLOR_MODIFIER_PREFIXES:
        if compact.startswith(prefix) and len(compact) > len(prefix):
            hit = _palette_match(compact[len(prefix):])
            if hit:
                return hit
    last_hit = ""
    for word in re.findall(r"[a-z]+", text.lower()):
        last_hit = _palette_match(word) or last_hit
    return last_hit


def _normalize_color_translations(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()[:COLOR_TRANSLATION_MAX_CHARS]
        val = " ".join(str(raw_value or "").split())[:COLOR_TRANSLATION_MAX_CHARS]
        if key and val:
            # AI 的颜色名统一归到固定色板；套装标签与色板外的颜色保持模型原话。
            result[key] = snap_color_to_palette(val) or val
        if len(result) >= COLOR_TRANSLATION_MAX_ENTRIES:
            break
    return result


def with_attributes_block(description: str, attributes: dict[str, str]) -> str:
    """Use the recognized-attribute block as the 详情 when attributes are available.
    Preserve model prose only when no attributes were recognized."""
    block = format_attributes_block(attributes)
    text = str(description or "").strip()
    return block or text


# 款式人群识别（2026-11-03）：导入/预览时先让视觉模型判断这件款式适合男童/女童/男装/
# 女装，再据此匹配标题规则。导入瞬间商品还没有人群证据（只能落到 unknown），
# 靠属性推断不可靠；而「按标题里的词判人群」误伤太大，硬校验已移除。
# 识别失败绝不阻断流程，调用方退回默认规则。
AUDIENCE_CHOICES = ("boys", "girls", "men", "women")
MAX_AUDIENCE_IMAGES = 2
AUDIENCE_MAX_TOKENS = 512

_AUDIENCE_SYSTEM_PROMPT = """You classify the target audience of a garment for a Chinese
cross-border seller listing on Shopee PH. Return STRICTLY valid JSON with two keys and
nothing else — no markdown fences, no prose.

{"audience": "boys|girls|men|women|unknown", "reason": "<one short English sentence>"}

Rules:
- Judge from the garment itself: cut and fit, neckline, sleeve length, proportions, the
  apparent age of the model in the photos, any visible size labels, and the theme/style.
- boys = boys' childrenswear (roughly 2-12 years); girls = girls' childrenswear;
  men / women = adult wear.
- A character, cartoon or franchise print must NOT decide the audience on its own — the
  same print is sold for children and for adults.
- Flat-lay photos with no model and a unisex cut are ambiguous: answer "unknown".
- Never output any value outside the list above."""


def _normalize_audience(value: Any) -> str:
    """把模型给的人群写法归到 boys/girls/men/women；认不出返回 ""。"""
    text = re.sub(r"[^a-z\u4e00-\u9fff]+", " ", str(value or "").casefold()).strip()
    if not text:
        return ""
    aliases = {
        "boys": "boys", "boy": "boys", "male child": "boys", "男童": "boys", "男孩": "boys",
        "girls": "girls", "girl": "girls", "female child": "girls", "女童": "girls", "女孩": "girls",
        "men": "men", "man": "men", "mens": "men", "male": "men", "男装": "men",
        "women": "women", "woman": "women", "womens": "women", "female": "women", "女装": "women",
    }
    return aliases.get(text, "")


def _parse_audience(text: str) -> str:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    parsed: Any = None
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        match = re.search(r"\{[\s\S]*\}", stripped)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except ValueError:
                parsed = None
    if isinstance(parsed, dict):
        return _normalize_audience(parsed.get("audience"))
    # 模型没给合法 JSON 时，只要还有 "audience": "..." 片段也认。
    match = re.search(r'"audience"\s*[:=]\s*"?([A-Za-z ]+)', stripped)
    return _normalize_audience(match.group(1)) if match else ""


def detect_product_audience(
    *,
    gateway: Any,
    connection: dict[str, Any] | None,
    base_product: dict[str, Any],
    assets: list[dict[str, Any]] | None = None,
    attribute_hints: dict[str, str] | None = None,
) -> tuple[str, str]:
    """判断款式适合的人群（boys/girls/men/women）。返回 (人群, 失败原因)。

    只发 1–2 张代表图、只要一个人群结论，比标题生成轻得多；任何失败都返回
    空人群 + 原因，调用方据此退回默认规则。"""
    if not connection:
        return "", "没有可用的 AI 连接"
    paths = _pick_image_paths(base_product, assets or [], max_images=MAX_AUDIENCE_IMAGES)
    loaded = [(path, image) for path in paths if (image := _read_image(path, profile=IMAGE_PROFILE_TEXT)) is not None]
    if not loaded:
        return "", "没有可用的产品图片"
    parts: list[str] = []
    category = str(base_product.get("category") or "").strip()
    if category:
        parts.append(f"Category: {category}")
    hints = {str(k): str(v) for k, v in (attribute_hints or {}).items() if str(v or "").strip()}
    if hints:
        parts.append("Operator attribute hints:\n" + "\n".join(f"- {k}: {v}" for k, v in hints.items()))
    parts.append("Look at the attached product photos and answer who this garment is made for.")
    request = GenerationRequest(
        system=_AUDIENCE_SYSTEM_PROMPT,
        user="\n\n".join(parts),
        max_tokens=AUDIENCE_MAX_TOKENS,
        temperature=0.0,
        images=tuple(image for _, image in loaded),
        timeout_override_seconds=90,
    )
    try:
        generated = gateway.generate(connection, request)
    except Exception as exc:  # noqa: BLE001 - 识别失败只退回默认规则
        return "", f"AI 调用失败: {exc}"
    audience = _parse_audience(str(getattr(generated, "text", "") or ""))
    if not audience:
        return "", "AI 未返回可识别的人群"
    return audience, ""


def generate_listing_from_images(
    *,
    gateway: Any,
    connection: dict[str, Any] | None,
    base_product: dict[str, Any],
    assets: list[dict[str, Any]],
    attribute_hints: dict[str, str] | None = None,
    want_title: bool = True,
    want_description: bool = True,
    fill_attributes_into_description: bool = True,
    color_targets: list[str] | None = None,
    title_skill: dict[str, Any] | None = None,
    hot_words: list[str] | None = None,
    title_context: dict[str, Any] | None = None,
    candidate_count: int = 1,
) -> VisionListingResult:
    """Best-effort vision generation. Never raises — returns a result with `error`
    set on failure so callers can log and move on without breaking the create flow.

    ``color_targets`` 是「重新识别颜色图」用的显式文件清单：给了就只重看这些图，
    包括已经有颜色名的 SKU 图；不给则沿用导入时的行为，只补未命名的数字 SKU 图。"""

    unnamed_variant_files = ordered_unnamed_variant_assets(assets)
    target_names: list[str] | None = None
    if color_targets is None:
        # 只有「纯颜色趟」才自动切成颜色模式。调用方明确要标题/详情时，即使还有
        # 未命名的数字 SKU 图，也必须走文本趟；颜色由导入流程的独立颜色趟处理
        # （它显式传 color_targets）。否则标题会被静默跳过，预览/重识别拿到空标题。
        color_only = bool(unnamed_variant_files) and not (want_title or want_description)
    else:
        target_names = []
        seen_targets: set[str] = set()
        for raw_name in color_targets:
            name = str(raw_name or "").strip()
            key = name.lower()
            if name and key not in seen_targets:
                seen_targets.add(key)
                target_names.append(name)
        color_only = bool(target_names)
    if not (want_title or want_description or color_only):
        return VisionListingResult(title="", description="", model_id="", input_image_count=0, error="生成开关未启用")
    if not connection:
        return VisionListingResult(title="", description="", model_id="", input_image_count=0, error="没有默认 AI 模型连接")

    paths = _pick_image_paths(
        base_product,
        assets,
        max_images=MAX_COLOR_IMAGES if color_only else MAX_IMAGES,
        color_only=color_only,
        target_names=target_names,
    )
    # 图片和它的文件名一起带走：标题/编号清单只列真正读进来的图，坏图被跳过时
    # 提示里的 #N 也不会跟着错位。
    image_profile = IMAGE_PROFILE_COLOR if color_only else IMAGE_PROFILE_TEXT
    loaded = [(path, image) for path in paths if (image := _read_image(path, profile=image_profile)) is not None]
    images = [image for _, image in loaded]
    if not images:
        return VisionListingResult(title="", description="", model_id="", input_image_count=0, error="没有可用的产品图片")

    # 重新识别颜色时不要把上一次的颜色结果再塞给模型：那正是这次要纠正的内容，
    # 留在提示里只会让模型照着旧名字答。
    prompt_product = base_product
    if target_names is not None:
        prompt_product = {**base_product, "recognized_colors": [], "variant_image_map": {}}
    # 标题 Skill 只作用于会产出标题的文本趟；颜色趟不注入，避免挤占颜色指令的注意力。
    active_skill = title_skill if (want_title and not color_only) else None
    request = GenerationRequest(
        system=build_system_prompt(active_skill, hot_words, title_context if active_skill else None),
        user=_build_user_prompt(
            prompt_product,
            attribute_hints or {},
            assets,
            [path.name for path, _ in loaded],
            color_only=color_only,
        ),
        # Reasoning models spend thinking tokens out of this budget before any text
        # (900 left the reply at stop_reason=max_tokens with an empty text block).
        # Thinking length varies between runs, so pair a generous budget with a retry.
        max_tokens=8192,
        temperature=0.4,
        images=tuple(images),
        # Vision generation with reasoning models (thinking + 12 images)
        # routinely runs past 120s — the connection default of 60s times out.
        timeout_override_seconds=180,
    )
    if want_title and not color_only and candidate_count > 1:
        request = replace(request, user=request.user + '\nReturn the recommended "title" and exactly two distinct alternatives in an "alternatives" array of strings. All three must satisfy the same rules. Keep other requested JSON fields.')
    generated = None
    last_error = ""
    for attempt in range(2):  # one retry: empty replies are intermittent
        try:
            generated = gateway.generate(connection, request)
            break
        except Exception as exc:  # noqa: BLE001 - vision is best-effort
            last_error = f"AI 调用失败: {exc}"
    if generated is None:
        return VisionListingResult(
            title="", description="", model_id=str(connection.get("model_id") or ""),
            input_image_count=len(images), error=last_error,
        )

    try:
        title, description, attributes, color_translations = _parse_result(str(generated.text or ""))
    except (ValueError, TypeError) as exc:
        return VisionListingResult(
            title="", description="", model_id=str(getattr(generated, "model_id", "") or ""),
            input_image_count=len(images), error=str(exc),
        )

    if want_description and fill_attributes_into_description:
        description = with_attributes_block(description, attributes)

    title_candidates = None
    if want_title and not color_only:
        def inspect_titles(raw: str, primary: str) -> list[dict]:
            alternatives = []
            if candidate_count > 1:
                try:
                    match = re.search(r"\{[\s\S]*\}", raw)
                    obj = json.loads(match.group(0) if match else raw)
                    alternatives = obj.get("alternatives", [])
                    if not isinstance(alternatives, list):
                        alternatives = []
                except (ValueError, TypeError):
                    alternatives = []
            titles = [primary] + [str(v).strip() for v in alternatives if isinstance(v, str)][:max(0, candidate_count - 1)]
            audited = []
            for value in titles:
                if title_context:
                    audit = title_selection.validate_title(value, title_context)
                else:
                    limit = min(TITLE_MAX_CHARS, int((active_skill or {}).get("title_max_chars") or TITLE_MAX_CHARS))
                    errors = ["标题为空"] if not value else ["标题超过字符上限，需要重新缩短"] if len(value) > limit else []
                    audit = {"title": value, "chars": len(value), "status": "BLOCKED" if errors else "PASS", "warnings": errors, "errors": errors, "accepted_terms": []}
                if any(title_selection.tokens(a["title"]) == title_selection.tokens(value) for a in audited):
                    audit["status"] = "BLOCKED"
                    audit["errors"].append("备选标题重复")
                    audit["warnings"] = list(dict.fromkeys(audit["warnings"] + ["备选标题重复"]))
                audited.append(audit)
            return audited

        title_candidates = inspect_titles(str(generated.text or ""), title)
        if any(t["status"] == "BLOCKED" for t in title_candidates) or len(title_candidates) < candidate_count:
            correction = "\nTitle validation failed. Rewrite the complete JSON; do not cut a word or invent product claims. Required title count: " + str(candidate_count) + ". Validation results: " + json.dumps(title_candidates, ensure_ascii=False)
            try:
                generated = gateway.generate(connection, replace(request, user=request.user + correction))
                title, description, attributes, color_translations = _parse_result(str(generated.text or ""))
                title_candidates = inspect_titles(str(generated.text or ""), title)
                if want_description and fill_attributes_into_description:
                    description = with_attributes_block(description, attributes)
            except Exception as exc:  # correction is bounded, never changes the old product on failure
                return VisionListingResult(title="", description=description if want_description else "", model_id=str(getattr(generated, "model_id", "") or ""), input_image_count=len(images), error=f"标题校验重试失败: {exc}", title_candidates=title_candidates)
        valid = [t for t in title_candidates if t["status"] != "BLOCKED"]
        if not valid:
            return VisionListingResult(title="", description=description if want_description else "", model_id=str(getattr(generated, "model_id", "") or ""), input_image_count=len(images), error="标题校验未通过，已保留原商品标题", title_candidates=title_candidates)
        title = valid[0]["title"]
        if len(title_candidates) < candidate_count:
            for t in title_candidates:
                t["warnings"].append("AI 未返回足够的不同备选，未补造标题")
                if t["status"] == "PASS":
                    t["status"] = "REVIEW"

    return VisionListingResult(
        title=title if want_title else "",
        description=description if want_description else "",
        model_id=str(getattr(generated, "model_id", "") or ""),
        input_image_count=len(images),
        attributes=attributes,
        color_translations=color_translations,
        title_candidates=title_candidates,
    )


def fill_attributes_flag(runtime_configuration: dict[str, Any] | None) -> bool:
    """Whether recognized product attributes should be appended to the generated
    description. Defaults to True — the operator asked for attribute-filled 详情;
    the settings toggle only needs to be explicit to turn it off."""
    if not isinstance(runtime_configuration, dict):
        return True
    ai_gen = runtime_configuration.get("ai_listing_generation") or {}
    if not isinstance(ai_gen, dict):
        return True
    return ai_gen.get("fill_attributes_into_description") is not False


def selected_flags(runtime_configuration: dict[str, Any] | None) -> tuple[bool, bool]:
    """Return (want_title, want_description) from runtime configuration."""
    if not isinstance(runtime_configuration, dict):
        return False, False
    ai_gen = runtime_configuration.get("ai_listing_generation") or {}
    if not isinstance(ai_gen, dict):
        return False, False
    return bool(ai_gen.get("generate_title_on_import")), bool(ai_gen.get("generate_description_on_import"))


def recognize_size_chart(
    *,
    gateway: Any,
    connection: Any,
    image_path: Path | str,
) -> "SizeChartResult":
    """Ask the vision model to read a size-chart image and return the size list
    (e.g. S/M/L/XL or 均码). Best-effort: never raises; returns an error result
    on failure so the caller can surface a clear message."""
    image = _read_image(Path(image_path))
    if image is None:
        return SizeChartResult(sizes=[], error="无法读取尺码表图片")
    request = GenerationRequest(
        system=(
            "You read a clothing size-chart image for a Southeast Asia cross-border "
            "store. Return STRICTLY valid JSON with one key and nothing else — no "
            "markdown fences:\n"
            '{"sizes": ["S", "M", "L", "XL"]}\n'
            "Rules:\n"
            "- List every distinct size shown on the chart, in the order they appear "
            "(top to bottom / left to right).\n"
            "- For one-size / free-size items return {\"sizes\": [\"均码\"]}.\n"
            "- Keep numeric sizes as printed (e.g. \"160/84A\", \"27\", \"6-12M\").\n"
            "- Do not invent sizes not present in the image."
        ),
        user="List the sizes on this size-chart image.",
        max_tokens=8192,
        temperature=0.4,
        images=(image,),
        timeout_override_seconds=180,
    )
    generated = None
    last_error = ""
    for _attempt in range(2):  # one retry: empty replies are intermittent
        try:
            generated = gateway.generate(connection, request)
            break
        except Exception as exc:  # noqa: BLE001 - vision is best-effort
            last_error = f"AI 调用失败: {exc}"
    if generated is None:
        return SizeChartResult(sizes=[], error=last_error or "AI 服务无响应")
    try:
        sizes = _parse_size_chart_result(str(generated.text or ""))
    except (ValueError, TypeError) as exc:
        return SizeChartResult(sizes=[], error=str(exc))
    if not sizes:
        return SizeChartResult(sizes=[], error="AI 未从尺码表识别出任何尺码")
    return SizeChartResult(sizes=sizes)


@dataclass(frozen=True)
class SizeChartResult:
    sizes: list[str]
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.sizes)


def _parse_size_chart_result(text: str) -> list[str]:
    """Extract the sizes array from a model reply that may include prose or fences."""
    match = re.search(r"\{\s*\"sizes\"\s*:\s*\[(.*?)\]\s*\}", text, re.S)
    if not match:
        raise ValueError("AI 未返回有效的尺码 JSON")
    try:
        payload = json.loads("{" + f'"sizes": [{match.group(1)}]' + "}")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"AI 尺码 JSON 解析失败: {exc}") from exc
    raw = payload.get("sizes") or []
    result: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if value and value not in result:
            result.append(value)
    return result
