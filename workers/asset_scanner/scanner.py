from __future__ import annotations

import hashlib
import os
import re
import struct
import uuid
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
SOURCE_EXTENSIONS = {".psd", ".ai", ".cdr", ".pdf"}
KNOWN_ASSET_DIRS = {"main", "variants", "variant", "video", "videos", "detail", "details"}
# 中文颜色根词 → 规范英文名（取值和 ENGLISH_COLOR_WORDS 是同一套名字）。
CHINESE_COLOR_ROOTS = {
    "白": "White",
    "黑": "Black",
    "灰": "Gray",
    "粉": "Pink",
    "紫": "Purple",
    "红": "Red",
    "蓝": "Blue",
    "绿": "Green",
    "黄": "Yellow",
    "米": "Beige",
    "棕": "Brown",
    "杏": "Apricot",
    "橙": "Orange",
    "咖啡": "Brown",
    "藏青": "Navy",
    "卡其": "Khaki",
    "金": "Gold",
    "银": "Silver",
}
# 中文深浅前缀：和英文一样合成 CamelCase 规范名（深蓝色 → DarkBlue、浅蓝 → LightBlue），
# 免得同一个颜色在中文写法和英文写法下变成两个变体键。
CHINESE_COLOR_MODIFIERS = {
    "深": "Dark",
    "浅": "Light",
    "淡": "Pale",
    "暗": "Deep",
    "亮": "Bright",
}


def _build_chinese_color_phrases() -> tuple[tuple[str, str], ...]:
    """每个中文根词的显式颜色写法（`棕色`、`深蓝`、`深蓝色`…），长的在前，
    这样「深蓝色」不会被「蓝色」抢先命中。"""
    phrases: dict[str, str] = {}
    for root, color in CHINESE_COLOR_ROOTS.items():
        phrases[f"{root}色"] = color
        for modifier, prefix in CHINESE_COLOR_MODIFIERS.items():
            phrases[f"{modifier}{root}"] = f"{prefix}{color}"
            phrases[f"{modifier}{root}色"] = f"{prefix}{color}"
    return tuple(sorted(phrases.items(), key=lambda item: len(item[0]), reverse=True))


_CHINESE_COLOR_PHRASES = _build_chinese_color_phrases()
COLOR_WORDS = dict(_CHINESE_COLOR_PHRASES)
ENGLISH_COLOR_WORDS = {
    "white": "White",
    "black": "Black",
    "gray": "Gray",
    "grey": "Gray",
    "pink": "Pink",
    "purple": "Purple",
    "red": "Red",
    "blue": "Blue",
    "green": "Green",
    "yellow": "Yellow",
    "beige": "Beige",
    "brown": "Brown",
    "apricot": "Apricot",
    "orange": "Orange",
    "navy": "Navy",
    "khaki": "Khaki",
    "cream": "Cream",
    "ivory": "Ivory",
    "wine": "Wine",
    "gold": "Gold",
    "silver": "Silver",
    "teal": "Teal",
    "coral": "Coral",
    "olive": "Olive",
    "maroon": "Maroon",
    "mint": "Mint",
    "rose": "Rose",
    "lavender": "Lavender",
}
# 深浅前缀与颜色词连写是供应商文件名的常见写法（darkgreen/lightblue）。合成
# CamelCase 规范名，和店铺里已有的 PureWhite/DarkGreen 写法保持一致。
COLOR_MODIFIERS = {
    "dark": "Dark",
    "light": "Light",
    "deep": "Deep",
    "pale": "Pale",
    "bright": "Bright",
}
# 尺码表与源图是上架辅助素材：尺码表走 size_chart_image，源图只留档做参考，
# 两者都不是商品图。AI 生图工具按 <SKU>-sheet.png / <SKU>-source.png 命名，
# 没有中文也没有 "size" 字样，旧规则会落到默认的 main，于是被当成商品图甚至主图。
SIZE_CHART_MARKERS = ("sheet", "sizechart", "码表")
SOURCE_IMAGE_MARKERS = ("source", "源图", "源文件")
# 生产用印刷图：前/后印花、透明印花层（前胸透明图 / 袖口透明图 / 后背透明图…）。
# 它们只用于打样和生产，不是商品照片：不进商品图、不当主图、也不做颜色图。
# 文件名里的颜色词指的是印花色而不是衣服颜色（``后印花 (黑色衣服).png`` 的
# "黑色"），所以这类文件的 sku_option 也清空，不参与颜色识别。
PRINT_ARTWORK_MARKERS = ("印花", "透明图", "print")
# 商品照例外：AI 生图工具把整批商品照命名为「T恤印花改图任务」及编号版本
# （``T恤印花改图任务 (1).png``…）。名字里带「印花」但它们是衣服商品照，
# 不是前/后印花或透明图那样的生产素材，不能因为关键词被排除出商品图和颜色识别。
# 例外只认这一种已确认的命名，不放开「印花」关键词。
PRODUCT_PHOTO_PRINT_NAME_PATTERN = re.compile(
    r"^T恤印花改图任务(?:\s*[(（]\d+[)）]|\s+\d+)?$"
)


def _is_product_photo_with_print_in_name(stem: str) -> bool:
    """True when the file name is the confirmed product-photo naming that contains 印花."""
    return bool(PRODUCT_PHOTO_PRINT_NAME_PATTERN.match(str(stem or "").strip()))


def scan_asset_root(root: str | Path, category_override: str = "", max_files: int = 3000, mode: str = "auto") -> dict[str, Any]:
    root_path = Path(root).expanduser()
    if not root_path.exists():
        return {"root": str(root_path), "assets": [], "count": 0, "errors": [f"Root not found: {root_path}"]}
    if mode not in {"auto", "groups", "recursive"}:
        mode = "auto"

    assets: list[dict[str, Any]] = []
    errors: list[str] = []
    default_group_id = _detect_default_group_id(root_path)
    scanned_files = 0
    if mode == "groups" or (mode == "auto" and not default_group_id):
        group_dirs = _find_group_dirs(root_path, limit=200)
        if group_dirs:
            for group_dir in group_dirs:
                group_id = _detect_default_group_id(group_dir)
                for file_path in _iter_limited_files(group_dir, max_depth=2):
                    if file_path.suffix.lower() not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | SOURCE_EXTENSIONS:
                        continue
                    try:
                        assets.append(_asset_from_path(root_path, file_path, group_id, category_override))
                        scanned_files += 1
                        if max_files and scanned_files >= max_files:
                            errors.append(f"Reached max_files={max_files}, scan stopped early")
                            break
                    except Exception as exc:
                        errors.append(f"{file_path}: {exc}")
                if max_files and scanned_files >= max_files:
                    break
            _apply_unnamed_variant_set_policy(assets)
            assets.sort(key=lambda item: (item["category"], item["group_id"], item["brand"], item["asset_type"], item["file_name"]))
            summary = summarize_assets(assets)
            return {"root": str(root_path), "assets": assets, "count": len(assets), "summary": summary, "errors": errors}

    for file_path in root_path.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.name.startswith("."):
            continue
        if file_path.suffix.lower() not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | SOURCE_EXTENSIONS:
            continue
        try:
            assets.append(_asset_from_path(root_path, file_path, default_group_id, category_override))
            scanned_files += 1
            if max_files and scanned_files >= max_files:
                errors.append(f"Reached max_files={max_files}, scan stopped early")
                break
        except Exception as exc:  # pragma: no cover - scanner should continue
            errors.append(f"{file_path}: {exc}")

    # 一个根目录可能扫到多个商品（上级目录递归扫描），策略按商品分组各自判断。
    for group_assets in _assets_by_group(assets).values():
        _apply_unnamed_variant_set_policy(group_assets)
    assets.sort(key=lambda item: (item["category"], item["group_id"], item["brand"], item["asset_type"], item["file_name"]))
    summary = summarize_assets(assets)
    return {"root": str(root_path), "assets": assets, "count": len(assets), "summary": summary, "errors": errors}


def _assets_by_group(assets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        grouped[str(asset.get("group_id") or "")].append(asset)
    return grouped


def _apply_unnamed_variant_set_policy(assets: list[dict[str, Any]]) -> None:
    """一整套没有任何角色线索的图片按「未命名颜色图」处理。

    AI 生图工具导出的商品文件夹用 ``1 (1).png``…``1 (10).png`` /
    ``服装布面颜色更换 (1).png`` 这类下载文件名，既没有 ``主图-`` 也没有颜色词，
    旧规则会把它们全部当成商品图：颜色图/款式为空，10 张还会被商品图 9 张上限截断。
    这类文件夹整体就是一套改色图，整组标成未命名 variant，交给视觉识别命名
    （AI 不可用时由导入侧兜底成 款式A/B/C），颜色才落得到颜色图上。

    文件夹里只要出现显式主图（``主图-``/``main-``）或已识别的颜色词，就保持原判定：
    那些无信息文件是多角度商品图，不是颜色图。"""
    mains = [asset for asset in assets if (asset.get("asset_type") or "").lower() == "main"]
    if len(mains) < 2:
        return
    if any(_has_explicit_main_hint(Path(str(asset.get("file_name") or "")).stem) for asset in mains):
        return
    if any((asset.get("asset_type") or "").lower() == "variant" for asset in assets):
        return
    for asset in mains:
        asset["asset_type"] = "variant"


def _has_explicit_main_hint(stem: str) -> bool:
    """文件名是否自己声明了主图身份（``主图-正面.jpg`` / ``Main-01.jpg`` / ``主图1.jpg``）。"""
    value = str(stem or "").strip()
    return _is_main_filename(value) or "\u4e3b\u56fe" in value or value.lower().startswith("main")


def _find_group_dirs(root: Path, limit: int = 200) -> list[Path]:
    result: list[Path] = []
    queue = deque([(root, 0)])
    while queue and len(result) < limit:
        current, depth = queue.popleft()
        if depth > 3:
            continue
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                child = Path(entry.path)
                if _extract_group_token(entry.name.upper()):
                    result.append(child)
                    if len(result) >= limit:
                        break
                    continue
                if depth < 3:
                    queue.append((child, depth + 1))
    return result


def _iter_limited_files(root: Path, max_depth: int = 2):
    queue = deque([(root, 0)])
    while queue:
        current, depth = queue.popleft()
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        yield Path(entry.path)
                    elif entry.is_dir(follow_symlinks=False) and depth < max_depth:
                        queue.append((Path(entry.path), depth + 1))
                except OSError:
                    continue


def summarize_assets(assets: list[dict[str, Any]]) -> dict[str, Any]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        by_group[asset.get("group_id") or "unknown"].append(asset)

    groups = {}
    for group_id, group_assets in by_group.items():
        counts = Counter(asset.get("asset_type") or "unknown" for asset in group_assets)
        colors = sorted({asset.get("sku_option") for asset in group_assets if asset.get("sku_option")})
        missing = []
        # 主图按素材角色回退（模特图 > 颜色图 > 详情图，见 storage._pick_main_asset）：
        # 印刷图（print）不再算 main，只有印刷图的文件夹主图会落到颜色图上，不该报缺主图。
        has_main_fallback = any(counts.get(name, 0) for name in ("model", "variant", "detail"))
        if counts.get("main", 0) < 1 and not has_main_fallback:
            missing.append("main")
        if counts.get("variant", 0) < 1:
            missing.append("variant")
        warnings = []
        if counts.get("model", 0) < 1:
            warnings.append("model")
        if counts.get("size_chart", 0) < 1:
            warnings.append("size_chart")
        if counts.get("video", 0) < 1:
            warnings.append("video")
        groups[group_id] = {
            "counts": dict(counts),
            "colors": colors,
            "missing_required": missing,
            "missing_recommended": warnings,
            "ready": not missing,
        }

    return {
        "group_count": len(groups),
        "groups": groups,
    }


def _asset_from_path(root: Path, file_path: Path, default_group_id: str = "", category_override: str = "") -> dict[str, Any]:
    rel = file_path.relative_to(root)
    parts = rel.parts
    stat = file_path.stat()
    category = category_override or _infer_category(root, parts, default_group_id)
    group_id = default_group_id or _detect_group_id(parts, file_path.stem)
    group_root = _detect_group_root(root, file_path, group_id)
    brand = _detect_brand(parts)
    asset_type = _detect_asset_type(parts, file_path.suffix)
    print_artwork = _is_print_artwork(Path(file_path.name).stem)
    width, height = _image_size(file_path) if file_path.suffix.lower() in IMAGE_EXTENSIONS else (None, None)
    status = "ready"
    if file_path.suffix.lower() in IMAGE_EXTENSIONS and (not width or not height):
        status = "invalid_size"

    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, _fast_file_fingerprint(file_path, stat))),
        "category": category,
        "group_id": group_id,
        "brand": brand,
        "sku_option": "" if print_artwork else _detect_sku_option(file_path.name),
        "asset_type": asset_type,
        "group_root": str(group_root),
        "file_path": str(file_path),
        "file_name": file_path.name,
        "file_hash": _fast_file_fingerprint(file_path, stat),
        "width": width,
        "height": height,
        "size_bytes": stat.st_size,
        "status": status,
    }


def _detect_group_root(root: Path, file_path: Path, group_id: str) -> Path:
    if not group_id:
        return file_path.parent
    current = file_path.parent
    while current != root.parent:
        if group_id.upper() in current.name.upper():
            return current
        if current == current.parent:
            break
        current = current.parent
    return root


def _detect_default_group_id(root: Path) -> str:
    current = root
    for _ in range(4):
        matched = _extract_group_token(current.name.upper())
        if matched:
            return matched
        if current.parent == current:
            break
        current = current.parent
    return ""


def _infer_category(root: Path, parts: tuple[str, ...], default_group_id: str) -> str:
    if default_group_id:
        # When scanning a single product folder, the first relative path part is
        # usually a file name, not a category. Use the parent material folder.
        parent = root.parent.name.strip()
        if parent and _extract_group_token(parent.upper()) == default_group_id:
            return root.parent.parent.name or "unknown"
        return parent or "unknown"
    return parts[0] if len(parts) >= 1 else "unknown"


def _detect_group_id(parts: tuple[str, ...], fallback: str) -> str:
    for part in parts:
        normalized = part.upper()
        matched = _extract_group_token(normalized)
        if matched:
            return matched
        if len(normalized) >= 4 and any(ch.isdigit() for ch in normalized):
            if normalized[:2].isalpha() or normalized[:1].isalpha():
                return normalized
    matched = _extract_group_token(fallback.upper())
    if matched:
        return matched
    return fallback.split("-")[0].split("_")[0].upper()


def _extract_group_token(value: str) -> str:
    token = []
    started = False
    for ch in value:
        if ch.isascii() and ch.isalpha():
            token.append(ch)
            started = True
            continue
        if started and ch.isdigit():
            token.append(ch)
            continue
        if started and token and any(c.isdigit() for c in token):
            break
        if started and not ch.isascii():
            break
        if started and not ch.isalnum():
            if any(c.isdigit() for c in token):
                break
            token = []
            started = False
    raw = "".join(token)
    if len(raw) >= 4 and any(ch.isdigit() for ch in raw):
        return raw
    return ""


def _detect_brand(parts: tuple[str, ...]) -> str:
    for part in parts:
        lower = part.lower()
        if lower in KNOWN_ASSET_DIRS:
            continue
        if lower in {"samsung", "xiaomi", "huawei", "oppo", "vivo", "apple", "honor", "lenovo"}:
            return lower
    if len(parts) >= 3:
        return parts[2].lower()
    return ""


def _detect_asset_type(parts: tuple[str, ...], suffix: str) -> str:
    lower_parts = [part.lower() for part in parts]
    file_name = parts[-1] if parts else ""
    stem = Path(file_name).stem
    suffix = suffix.lower()
    if suffix in SOURCE_EXTENSIONS:
        return "source"
    if suffix.lower() in VIDEO_EXTENSIONS:
        return "video"
    if _matches_filename_marker(stem, SOURCE_IMAGE_MARKERS):
        return "source"
    if _is_main_filename(stem):
        return "main"
    # 显式主图（主图-/main-）仍然优先；其余带「印花 / 透明图 / print」的文件是生产
    # 用印刷图，不能落到默认的 main 里当商品图。
    if _is_print_artwork(stem):
        return "print"
    if _is_sku_filename(stem):
        return "variant"
    if _sku_color_candidate(stem):
        return "variant"
    if "variants" in lower_parts or "variant" in lower_parts:
        return "variant"
    if "detail" in lower_parts or "details" in lower_parts:
        return "detail"
    if "detail" in stem.lower() or "product" in stem.lower() or "\u8be6\u60c5" in stem or "\u4ea7\u54c1" in stem:
        return "detail"
    if "\u5c3a\u7801" in stem or "size" in stem.lower() or _matches_filename_marker(stem, SIZE_CHART_MARKERS):
        return "size_chart"
    if "\u6a21\u7279" in stem or "model" in stem.lower():
        return "model"
    if "\u4e3b\u56fe" in stem or stem.lower().startswith("main"):
        return "main"
    if _detect_chinese_color_option(stem) or _detect_english_color_option(stem):
        return "variant"
    return "main"


def _matches_filename_marker(stem: str, markers: tuple[str, ...]) -> bool:
    """True when the file name carries one of ``markers``.

    英文标记按 ``-``/``_``/空格切出的整段比较（``AL0474-source.png`` 命中，
    ``resources.png`` 不命中）；中文标记在文件名里常与前后缀连写
    （``DL0001源图.png``），所以按子串判断。"""
    value = str(stem or "").lower()
    segments = {segment for segment in re.split(r"[^0-9a-z\u4e00-\u9fff]+", value) if segment}
    for marker in markers:
        normalized = str(marker).lower()
        if not normalized.isascii():
            if normalized in value:
                return True
        elif normalized in segments:
            return True
    return False


def _is_print_artwork(stem: str) -> bool:
    """文件名是不是生产用印刷图（前/后印花、透明印花层）。"""
    if _is_product_photo_with_print_in_name(stem):
        return False
    return _matches_filename_marker(stem, PRINT_ARTWORK_MARKERS)


def _detect_sku_option(file_name: str) -> str:
    stem = Path(file_name).stem
    candidate = _sku_color_candidate(stem)
    if candidate:
        chinese = _detect_chinese_color_option(candidate)
        if chinese:
            return chinese
        english = _detect_english_color_option(candidate)
        if english:
            # 英文颜色名首字母大写（ENGLISH_COLOR_WORDS 值已是 White/Black…），
            # 不要 lower——上架到马帮的规格名/展示都用首字母大写。
            return english
        return candidate
    chinese = _detect_chinese_color_option(stem)
    if chinese:
        return chinese
    english = _detect_english_color_option(stem)
    if english:
        return english
    return ""


def _is_main_filename(stem: str) -> bool:
    value = str(stem or "").strip().lower()
    return value.startswith(("main-", "main_", "主图-", "主图_"))


def _sku_color_candidate(stem: str) -> str:
    for segment in _sku_segments(stem):
        if segment.isdigit():
            continue
        if _detect_chinese_color_option(segment) or _detect_english_color_option(segment):
            return segment
        # Preserve an unfamiliar Chinese color name for the vision model to translate,
        # while avoiding filename descriptors such as "dress only".
        if any("\u4e00" <= char <= "\u9fff" for char in segment) and any(
            marker in segment for marker in ("色", "白", "黑", "红", "蓝", "绿", "黄", "紫", "灰", "棕", "粉", "米", "杏", "驼")
        ):
            return segment
    return ""


def _is_sku_filename(stem: str) -> bool:
    value = str(stem or "").strip().lower()
    return value.startswith(("sku-", "sku_"))


def _sku_segments(stem: str) -> list[str]:
    value = str(stem or "").strip()
    if not _is_sku_filename(value):
        return []
    return [segment.strip() for segment in re.split(r"[-_]", value[4:]) if segment.strip()]


def _detect_chinese_color_option(value: str) -> str:
    """中文颜色名 → 规范英文名，没有颜色词时返回 ""。

    `棕色`/`杏色`/`深蓝色` 这类显式写法按子串识别（长写法优先），所以「深蓝色」
    拿到 DarkBlue 而不是 蓝色 的 Blue。裸颜色字（白/蓝/杏）只在它自成一段时才认：
    「奶油白」「米白」这类自造色名要保持原样交给视觉模型翻译，不能被拆成 White。"""
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    for phrase, color in _CHINESE_COLOR_PHRASES:
        if phrase in lowered:
            return color
    segments = {segment for segment in re.split(r"[^0-9a-z\u4e00-\u9fff]+", lowered) if segment}
    for root, color in CHINESE_COLOR_ROOTS.items():
        if root in segments:
            return color
    return ""


def _detect_english_color_option(stem: str) -> str:
    return canonical_color_name(stem)


def canonical_color_name(value: str) -> str:
    """Canonical English color name for one filename token / variant label, or ""
    when the value carries no color word.

    Spaced, hyphenated and glued spellings collapse onto one name so a supplier
    filename and a hand-typed label never become two variants for one color:
    ``darkgreen`` / ``dark-green`` / ``dark green`` / ``DarkGreen`` → ``DarkGreen``.
    Unknown words return "" — the scanner never invents a color."""
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or ""))
    tokens = [token for token in normalized.split() if token]
    if not tokens:
        return ""
    candidates: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        # 深浅前缀可能单独成词（"dark green"）→ 与紧随的颜色词合成一个候选。
        if token in COLOR_MODIFIERS and index + 1 < len(tokens) and tokens[index + 1] in ENGLISH_COLOR_WORDS:
            candidates.append(token + tokens[index + 1])
            index += 2
            continue
        candidates.append(token)
        index += 1
    for candidate in candidates:
        color = _resolve_color_token(candidate)
        if color:
            return color
    return ""


def _resolve_color_token(token: str) -> str:
    base = ENGLISH_COLOR_WORDS.get(token)
    if base:
        return base
    for modifier, prefix in COLOR_MODIFIERS.items():
        if token.startswith(modifier):
            base = ENGLISH_COLOR_WORDS.get(token[len(modifier):])
            if base:
                return f"{prefix}{base}"
    return ""


def _fast_file_fingerprint(file_path: Path, stat: os.stat_result | None = None) -> str:
    stat = stat or file_path.stat()
    raw = f"{file_path}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _image_size(file_path: Path) -> tuple[int | None, int | None]:
    kind = _image_kind(file_path)
    if kind == "png":
        with file_path.open("rb") as f:
            f.seek(16)
            width, height = struct.unpack(">II", f.read(8))
        return width, height
    if kind == "jpeg":
        return _jpeg_size(file_path)
    return None, None


def _image_kind(file_path: Path) -> str | None:
    with file_path.open("rb") as f:
        head = f.read(12)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    return None


def _jpeg_size(file_path: Path) -> tuple[int | None, int | None]:
    with file_path.open("rb") as f:
        if f.read(2) != b"\xff\xd8":
            return None, None
        while True:
            marker_prefix = f.read(1)
            if marker_prefix != b"\xff":
                return None, None
            marker = f.read(1)
            while marker == b"\xff":
                marker = f.read(1)
            if marker in [b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"]:
                f.read(3)
                height, width = struct.unpack(">HH", f.read(4))
                return width, height
            segment_length_raw = f.read(2)
            if len(segment_length_raw) != 2:
                return None, None
            segment_length = struct.unpack(">H", segment_length_raw)[0]
            f.seek(segment_length - 2, 1)
