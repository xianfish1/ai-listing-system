from __future__ import annotations

import json
import hashlib
import base64
import binascii
import math
import os
import re
import sys
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Iterator

from services.api import settings
from services.api.runtime_configuration import (
    merge_configuration,
    public_runtime_configuration,
    update_runtime_configuration,
)
from services.api.credential_crypto import CredentialCrypto, redact_connection
from services.api.category_rules import (
    build_profile_snapshot,
    hash_profile_snapshot,
    validate_attribute_values,
)
from services.api.category_profile_service import (
    CategoryProfileConflict,
    CategoryProfileNotFound,
    complete_collection_run,
    create_collection_run,
    publish_version,
    reject_version,
)
from services.api.db_store import PostgresJsonStore
from services.api.marketplace_category_catalog import (
    complete_catalog_run,
    create_catalog_run,
    sanitize_mabang_urls_in_store,
    search_catalog,
)
from services.api.mabang_page_import import (
    MabangPageImportError,
    confirm_page_import,
    record_page_import_error,
    stage_page_import,
)
from services.api.store_collection_scheduler import store_collection_key
from services.api.store_promotions import replace_promotion_catalog
from services.api.store_watermarks import list_watermarks
from services.api.store_size_chart_templates import list_size_chart_templates
from services.api.promotion_resolution import promotion_state
from services.api.size_chart_uploads import resolve_image_upload, resolve_size_chart
from services.api.video_assets import validate_video


_LOCK = threading.Lock()
_DB_STORE: PostgresJsonStore | None = None

# Protocols a model connection can use. `chat_completions` is OpenAI-style but
# treats the configured base_url as the literal /chat/completions endpoint
# (no /v1 insertion), for providers that hand out the full endpoint URL.
AI_MODEL_PROTOCOLS = frozenset({"anthropic_messages", "openai_chat_completions", "chat_completions"})


class ActiveAutomationJobExists(RuntimeError):
    pass


AUTOMATION_WARNING_FIELDS = frozenset({
    "description_profile_version_id",
    "category_profile_version_expired",
    "promotion_expired",
    "promotion_unresolved",
    "promotion_unconfigured",
})


class MabangPageImportNotFound(RuntimeError):
    pass

_ATOMIC_REPLACE_ATTEMPTS = 8
_ATOMIC_REPLACE_BACKOFF_SECONDS = 0.05

LEGACY_STORE_KEYS = {"legacy_queue", "legacy_bundles", "legacy_logs", "legacy_nodes"}
PRODUCT_IMAGE_LIMIT = 9
# 能当商品图/主图的素材角色：尺码表、源图、视频、印刷图（前/后印花、透明印花层）
# 只做上架辅助，永远不进商品图。
PHOTO_ASSET_TYPES = frozenset({"main", "model", "variant", "detail"})
# 默认商品图的角色顺序：主图 → 模特图 → 颜色图（2026-10-09 起颜色图也进默认顺序，
# 现场每件都要手工把 A白色.jpg 这类颜色图加进产品图，直接排好省掉这一步）。
DEFAULT_PRODUCT_IMAGE_ROLE_ORDER = {"main": 0, "model": 1, "variant": 2}
DEFAULT_PRODUCT_IMAGE_TYPES = frozenset(DEFAULT_PRODUCT_IMAGE_ROLE_ORDER)

PRODUCT_TEMPLATES: dict[str, dict[str, Any]] = {
    "POD 男装T恤": {
        "template_id": "pod-mens-tee-v1",
        "target_audience": "men",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "attribute_brand": "Generic",
        "attribute_gender": "Men",
        "attribute_material": "Cotton",
        "attribute_sleeve_length": "Short Sleeves",
        "attribute_collar": "Round Neck",
        "weight_kg": "0.25",
        "length_cm": "28",
        "width_cm": "22",
        "height_cm": "2",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Anime Graphic Cotton T-Shirt Men Casual Short Sleeve Tee",
        "description": "POD 测试商品。用于验证产品维护和马帮导出流程。",
        "price": {"normal": 89, "sale": 69, "large_normal": 95, "large_sale": 75, "cost": 28, "large_cost": 31, "shipping_cost": 12},
    },
    "POD 女装T恤": {
        "template_id": "pod-womens-tee-v1",
        "target_audience": "women",
        "marketplace_category_path": "Women Clothes > Tops > T-shirts",
        "attribute_brand": "Generic",
        "attribute_gender": "Women",
        "attribute_material": "Cotton",
        "attribute_sleeve_length": "Short Sleeves",
        "attribute_collar": "Round Neck",
        "weight_kg": "0.25",
        "length_cm": "28",
        "width_cm": "22",
        "height_cm": "2",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Anime Graphic Cotton T-Shirt Women Casual Short Sleeve Tee",
        "description": "POD 测试商品。用于验证产品维护和马帮导出流程。",
        "price": {"normal": 89, "sale": 69, "large_normal": 95, "large_sale": 75, "cost": 28, "large_cost": 31, "shipping_cost": 12},
    },
    "POD 男童T恤": {
        "template_id": "pod-boys-tee-v1",
        "target_audience": "boy",
        "marketplace_category_path": "Baby & Kids Fashion > Boy Clothes > Tops > T-shirts",
        "attribute_brand": "Generic",
        "attribute_gender": "Boy",
        "attribute_material": "Cotton",
        "attribute_sleeve_length": "Short Sleeves",
        "attribute_collar": "Round Neck",
        "weight_kg": "0.25",
        "length_cm": "28",
        "width_cm": "22",
        "height_cm": "2",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Kids Cotton T-Shirt Boy Casual Short Sleeve Tee",
        "description": "POD 测试商品。用于验证产品维护和马帮导出流程。",
        "price": {"normal": 89, "sale": 69, "large_normal": 95, "large_sale": 75, "cost": 28, "large_cost": 31, "shipping_cost": 12},
    },
    "POD 女童T恤": {
        "template_id": "pod-girls-tee-v1",
        "target_audience": "girl",
        "marketplace_category_path": "Baby & Kids Fashion > Girl Clothes > Tops > T-shirts",
        "attribute_brand": "Generic",
        "attribute_gender": "Girl",
        "attribute_material": "Cotton",
        "attribute_sleeve_length": "Short Sleeves",
        "attribute_collar": "Round Neck",
        "weight_kg": "0.25",
        "length_cm": "28",
        "width_cm": "22",
        "height_cm": "2",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Kids Cotton T-Shirt Girl Casual Short Sleeve Tee",
        "description": "POD 测试商品。用于验证产品维护和马帮导出流程。",
        "price": {"normal": 89, "sale": 69, "large_normal": 95, "large_sale": 75, "cost": 28, "large_cost": 31, "shipping_cost": 12},
    },
    "POD 童装T恤": {
        "template_id": "pod-kids-tee-v1",
        "target_audience": "kids_unisex",
        "marketplace_category_path": "",
        "attribute_brand": "Generic",
        "attribute_gender": "",
        "attribute_material": "Cotton",
        "attribute_sleeve_length": "Short Sleeves",
        "attribute_collar": "Round Neck",
        "weight_kg": "0.25",
        "length_cm": "28",
        "width_cm": "22",
        "height_cm": "2",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Kids Cotton T-Shirt Casual Short Sleeve Tee",
        "description": "POD 测试商品。用于验证产品维护和马帮导出流程。",
        "price": {"normal": 89, "sale": 69, "large_normal": 95, "large_sale": 75, "cost": 28, "large_cost": 31, "shipping_cost": 12},
    },
}

DEFAULT_CATEGORY_ATTRIBUTES: dict[str, Any] = {
    "brand": "NoBrand",
    "pattern": "Print",
    "material": "Cotton",
    "sleeve_length": "Short Sleeves",
    "plus_size": "Yes",
    "display": {
        "brand": "NoBrand",
        "pattern": "Print",
        "material": "Cotton",
        "sleeve_length": "Short Sleeves",
        "plus_size": "Yes",
    },
}

DEFAULT_VARIATION: dict[str, Any] = {
    "type": "variant",
    "axes": ["color", "size"],
}

CATEGORY_PROFILES: dict[str, dict[str, Any]] = {
    "pod-ph-mens-tee": {
        "profile_id": "pod-ph-mens-tee",
        "display_name": "POD菲律宾 男装 > 上衣 > T恤",
        "product_line": "POD Mens T-Shirt",
        "template_id": "pod-mens-tee-v1",
        "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
        "mabang_category_id": "100244",
        "country": "PH",
        "currency": "PHP",
        "normal_price": 89,
        "sale_price": 69,
        "discount_label": "40%",
        "mabang_discount_id": "928232107737088",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Cotton T-Shirt Men Casual Tee",
        "description": "POD Philippines Shopee mens T-shirt draft.",
        "required_attributes": DEFAULT_CATEGORY_ATTRIBUTES,
        "variation": DEFAULT_VARIATION,
        "default_size_chart_path": "templates/pod-ph-mens-tee-size-chart.jpg",
        "status": "ready",
    },
    "pod-ph-womens-tee": {
        "profile_id": "pod-ph-womens-tee",
        "display_name": "POD菲律宾 女装 > 上衣 > T恤",
        "product_line": "POD Womens T-Shirt",
        "template_id": "pod-womens-tee-v1",
        "marketplace_category_path": "Women Clothes > Tops > T-Shirts",
        "mabang_category_id": "",
        "country": "PH",
        "currency": "PHP",
        "normal_price": 89,
        "sale_price": 69,
        "discount_label": "40%",
        "mabang_discount_id": "928232107737088",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Cotton T-Shirt Women Casual Tee",
        "description": "POD Philippines Shopee womens T-shirt draft.",
        "required_attributes": DEFAULT_CATEGORY_ATTRIBUTES,
        "variation": DEFAULT_VARIATION,
        "default_size_chart_path": "",
        "status": "needs_manual",
    },
    "pod-ph-boys-tee": {
        "profile_id": "pod-ph-boys-tee",
        "display_name": "POD菲律宾 男童 > 上衣 > T恤",
        "product_line": "POD Boys T-Shirt",
        "template_id": "pod-boys-tee-v1",
        "marketplace_category_path": "Baby & Kids Fashion > Boy Clothes > Tops > T-Shirts",
        "mabang_category_id": "",
        "country": "PH",
        "currency": "PHP",
        "normal_price": 89,
        "sale_price": 69,
        "discount_label": "40%",
        "mabang_discount_id": "928232107737088",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Kids Cotton T-Shirt Boy Casual Tee",
        "description": "POD Philippines Shopee boys T-shirt draft.",
        "required_attributes": DEFAULT_CATEGORY_ATTRIBUTES,
        "variation": DEFAULT_VARIATION,
        "default_size_chart_path": "",
        "status": "needs_manual",
    },
    "pod-ph-girls-tee": {
        "profile_id": "pod-ph-girls-tee",
        "display_name": "POD菲律宾 女童 > 上衣 > T恤",
        "product_line": "POD Girls T-Shirt",
        "template_id": "pod-girls-tee-v1",
        "marketplace_category_path": "Baby & Kids Fashion > Girl Clothes > Tops > T-Shirts",
        "mabang_category_id": "",
        "country": "PH",
        "currency": "PHP",
        "normal_price": 89,
        "sale_price": 69,
        "discount_label": "40%",
        "mabang_discount_id": "928232107737088",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Kids Cotton T-Shirt Girl Casual Tee",
        "description": "POD Philippines Shopee girls T-shirt draft.",
        "required_attributes": DEFAULT_CATEGORY_ATTRIBUTES,
        "variation": DEFAULT_VARIATION,
        "default_size_chart_path": "",
        "status": "needs_manual",
    },
    "pod-ph-kids-tee": {
        "profile_id": "pod-ph-kids-tee",
        "display_name": "POD菲律宾 童装 > 上衣 > T恤",
        "product_line": "POD Kids T-Shirt",
        "template_id": "pod-kids-tee-v1",
        "marketplace_category_path": "Baby & Kids Fashion > Tops > T-Shirts",
        "mabang_category_id": "",
        "country": "PH",
        "currency": "PHP",
        "normal_price": 89,
        "sale_price": 69,
        "discount_label": "40%",
        "mabang_discount_id": "928232107737088",
        "sizes": ["S", "M", "L", "XL", "2XL"],
        "title_suffix": "Kids Cotton T-Shirt Casual Tee",
        "description": "POD Philippines Shopee kids T-shirt draft.",
        "required_attributes": DEFAULT_CATEGORY_ATTRIBUTES,
        "variation": DEFAULT_VARIATION,
        "default_size_chart_path": "",
        "status": "needs_manual",
    },
}


def _atomic_replace(
    tmp: Path,
    target: Path,
    *,
    attempts: int = _ATOMIC_REPLACE_ATTEMPTS,
    backoff_seconds: float = _ATOMIC_REPLACE_BACKOFF_SECONDS,
) -> None:
    """Atomically replace ``target`` with retry for transient Windows locks."""

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            os.replace(tmp, target)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(backoff_seconds * (attempt + 1))
    raise OSError(f"atomic replace {tmp} -> {target} failed after {attempts} attempts") from last_exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_BASE_PRICE_SITES = ("TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR")


def _empty_store() -> dict[str, list[dict[str, Any]]]:
    return {
        "assets": [],
        "products": [],
        "skus": [],
        "prices": [],
        "base_products": [],
        "store_templates": [],
        "mabang_accounts": [],
        "mabang_store_options": [],
        "store_listing_copies": [],
        "listing_skus": [],
        "listing_prices": [],
        "automation_jobs": [],
        # 任务的执行快照按任务 id 单独存（每个约 214KB，占任务文件 17.3MB/17.6MB）。
        # 拆出去以后领取/心跳/进度/任务列表只碰约 300KB 的任务文件。
        "automation_job_snapshots": {},
        "category_profiles": [],
        "category_profile_versions": [],
        "category_collection_runs": [],
        "marketplace_categories": [],
        "marketplace_category_catalog_runs": [],
        "category_quick_templates": [],
        "ai_outputs": [],
        "ai_model_connections": [],
        "description_generation_profiles": [],
        "title_skills": [],
        "hot_words": [],
        # 标题工作台的生成历史（2026-11-03 拆出主库，见 _SPLIT_STORE_KEYS）。
        "title_generation_history": [],
        "store_template_versions": [],
        "store_promotion_activities": [],
        "store_watermark_templates": [],
        # 每店一条「采集水印目录」的结果（时间/条数/失败原因），见 store_watermarks.record_catalog_run。
        "store_watermark_catalog_runs": [],
        "store_size_chart_templates": [],
        "store_promotion_catalog_runs": [],
        "store_collection_runs": [],
        "mabang_page_import_runs": [],
        "listing_copy_revisions": [],
        "runtime_configuration": merge_configuration({}),
        "runtime_configuration_version": 0,
        "runtime_configuration_audit": [],
    }


def default_store() -> dict[str, Any]:
    return deepcopy(_empty_store())


def ensure_dirs() -> None:
    for path in [settings.DATA_DIR, settings.STORAGE_DIR, settings.EXPORT_DIR]:
        path.mkdir(parents=True, exist_ok=True)
    if _using_postgres():
        _postgres_store().ensure_schema()


# In-process cache for the JSON store. `load_store()` returns the cached dict
# by reference when the file's (mtime, size) hasn't changed since the last read,
# so read-heavy API endpoints don't repeatedly parse the 4MB+ store.json.
#
# Contract: callers of `load_store()` MUST NOT mutate the returned dict or its
# contents. Anywhere that needs a mutable copy must go through `locked_store()`,
# which forces a fresh disk read (bypassing the cache) and shares the write lock.
_STORE_CACHE: dict[str, Any] = {"key": None, "data": None}

# 多个客户端的首屏/轮询会同时撞上“缓存刚失效”：如果每个线程都重新解析
# 17MB 的任务文件，CPU 和 GIL 会一起被占满，所有页面都卡。这里只让一个线程
# 重建，其余线程等它完成后直接复用结果（single-flight）。
_STORE_REBUILD_LOCK = threading.Lock()

# 存储写入后的通知（例如清掉 API 的 GET 响应缓存）。监听器必须轻量且不能
# 反过来调用存储写路径；异常一律吞掉，绝不影响保存本身。
_CHANGE_LISTENERS: list[Callable[[], None]] = []
_CHANGE_LISTENERS_LOCK = threading.Lock()


def register_change_listener(listener: Callable[[], None]) -> None:
    """注册一个“存储已变化”回调（进程内、幂等由调用方自己保证）。

    目前用于让 API 的短 TTL GET 缓存在后台 Worker 写入后立刻失效，避免多台
    电脑看到一两秒的旧任务状态。
    """

    if not callable(listener):
        return
    with _CHANGE_LISTENERS_LOCK:
        if listener not in _CHANGE_LISTENERS:
            _CHANGE_LISTENERS.append(listener)


def _notify_change_listeners() -> None:
    with _CHANGE_LISTENERS_LOCK:
        listeners = list(_CHANGE_LISTENERS)
    for listener in listeners:
        try:
            listener()
        except Exception:  # noqa: BLE001 - 缓存清理失败不能影响数据落盘
            pass

# Large, low-frequency top-level collections that used to live inside store.json.
# Splitting them out shrinks the main file from ~4MB to ~800KB, so ordinary
# save_store writes rewrite far less data, and each of these files is only
# rewritten when *its own* collection actually changes. Callers still see a
# merged dict — the split is transparent to every existing `.get("ai_outputs", ...)`
# style access. Adding a key here also requires the loader to know how to
# initialise it to a list (or dict, per _empty_store() default) if missing.
_SPLIT_STORE_KEYS: tuple[str, ...] = (
    "ai_outputs",
    "listing_copy_revisions",
    "mabang_page_import_runs",
    # 18.5MB（81 个任务的执行快照）——每次任务心跳/进度/建任务都会改它。留在主库里
    # 就等于每 30 秒重写整份 59MB 的 store.json；拆出来以后只重写这 18.5MB。
    "automation_jobs",
    # 13.2MB 的类目目录（只在采集类目时变），拆出来让主库从 ~40MB 降到 ~27MB。
    "marketplace_categories",
    # 17.3MB 的执行快照（按任务 id）：只有建任务/重排队/删除会动它，
    # 心跳与进度完全不碰，这样执行期的每一次写入都不用背着 17MB。
    "automation_job_snapshots",
    # 2026-11-03 主库瘦身第二批：listing_skus 13MB + listing_prices 14.5MB +
    # store_listing_copies 4.7MB + assets 4.2MB + title_generation_history 2.5MB +
    # base_products 0.9MB。保存一个基础商品只改 base_products 这 0.9MB，但主库
    # 55.7MB 意味着每次保存都要 json.loads(~500ms) + json.dumps(~380ms) + 重写整份；
    # 拆出来以后 update_base_product 走 load_main=False 快路径（~40ms），
    # 其它操作的保存也只重写各自变了的文件。
    "listing_skus",
    "listing_prices",
    "store_listing_copies",
    "assets",
    "title_generation_history",
    "base_products",
    "title_skills",
)

# 主文件上次写出的字节。save_store 用它跳过「主库根本没变」的写入（只动 split 集合的
# 保存，例如任务心跳/进度，不必每次重写几十 MB 的主库）。与 _SPLIT_LAST_BYTES 同一套
# 判断：单实例部署下，内存里的主库没变就说明盘上那份也是我们写的。
_MAIN_LAST_BYTES: dict[str, bytes] = {}

# Snapshot of the last known bytes written for each split file, keyed by
# collection. save_store uses this to skip rewriting a file whose serialised
# content is byte-identical to what's already on disk — an in-place edit that
# didn't touch ai_outputs shouldn't cost a 900KB rewrite.
_SPLIT_LAST_BYTES: dict[str, bytes] = {}

# 按"文件"粒度的解析缓存：绝对路径 -> ((mtime_ns, size), 解析结果)。
#
# 现场问题：原来 save_store 会把整个读缓存（_STORE_CACHE）清空，于是"每次写入
# 之后"的第一个读请求都要把主库(13MB)+任务(18MB)+类目(13MB)全部重新 json.loads
# （实测 ~430ms，而且 json.loads 期间一直占着 GIL，界面就卡）。三个 Worker 每 3 秒
# 轮询 + 执行期高频进度上报，会不停触发这种"整库重解析"。
#
# 有了按文件缓存：一次任务心跳只让 automation_jobs.json 失效，读请求只重解析
# 那一个文件，其余文件直接复用上次的解析结果。只用于"读路径"（load_store）；
# 写路径（locked_store）永远重新解析，绝不把缓存里的对象交给调用方原地修改。
_FILE_PARSE_CACHE: dict[str, tuple[tuple[int, int], Any]] = {}

# 已经做过 URL 清洗的文件版本：path -> (mtime_ns, size)。
# sanitize_mabang_urls_in_store 是整库深遍历（实测 165ms/次，占整库重建开销的一大半），
# 但它只在**新字节**上才可能改东西；同一份字节遍历过一次之后再遍历就是纯浪费。
# 按文件记录「这份字节已经洗过」，重建时只遍历变过的文件（新写入的内容照常遍历）。
_SANITIZED_FILE_STATES: dict[str, tuple[int, int]] = {}


def _json_file_stat_key(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _read_parsed_json_cached(path: Path) -> tuple[bool, Any]:
    """读路径专用：文件 (mtime_ns, size) 没变就不读盘、不解析。

    返回 (hit, value)；hit=False 表示缓存未命中（调用方需要自己解析）。
    """

    key = _json_file_stat_key(path)
    if key == (0, 0):
        return (False, None)
    entry = _FILE_PARSE_CACHE.get(str(path))
    if entry is None or entry[0] != key:
        return (False, None)
    return (True, entry[1])


def _remember_parsed_json(path: Path, value: Any) -> None:
    _FILE_PARSE_CACHE[str(path)] = (_json_file_stat_key(path), value)


def _split_store_path(key: str) -> Path:
    """Split 集合跟着 STORE_FILE 走（不是 DATA_DIR）。

    生产里两者是同一个目录（`STORE_FILE = DATA_DIR/store.json`），但**测试/临时库**只把
    STORE_FILE 指到 tmp 时，绝不会再写到真实 `data/` 下的真实集合。2026-10-05 现场就是
    跑一次全量 pytest（`test_save_store_*` 用隔离的 STORE_FILE、但 DATA_DIR 还是真实目录）
    把 `data/mabang_page_import_runs.json`、`data/ai_outputs.json` 覆盖成了 `[]`——
    和 last-good 快照同一个教训：隔离路径各管各的。
    """

    return settings.STORE_FILE.parent / f"{key}.json"


def _legacy_split_store_path(key: str) -> Path:
    """旧布局：split 集合曾经放在 DATA_DIR 下。只在读不到新位置时兜底。"""

    return settings.DATA_DIR / f"{key}.json"


def _split_file_has_content(path: Path) -> bool:
    """文件里是不是真的有东西（缺失/空集合/读不出来都算没有）。"""

    try:
        raw = path.read_bytes()
    except OSError:
        return False
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return bool(value)


def _active_split_store_path(key: str) -> Path:
    """当前该读哪个文件：新位置优先，没有就看旧布局（迁移期兜底）。

    新位置存在但**是空的**、旧位置还有内容时，旧数据优先——空文件绝不能把真实集合
    遮成「没有数据」（store.json 缺失时先落了一份空库，就是这么把旧布局遮住的）。
    """

    path = _split_store_path(key)
    legacy = _legacy_split_store_path(key)
    if legacy != path and legacy.exists() and not _split_file_has_content(path):
        return legacy
    return path


def _read_split_key(key: str, *, use_cache: bool = False) -> Any:
    """读一个 split 集合。

    use_cache=True（只读路径）时按文件 (mtime_ns, size) 复用上次解析结果：
    没变的文件不读盘、不 json.loads。写路径必须用默认的 False，拿到的是全新对象。
    """

    path = _active_split_store_path(key)
    if not path.exists():
        return None
    if use_cache:
        hit, cached = _read_parsed_json_cached(path)
        if hit:
            return cached
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    _SPLIT_LAST_BYTES[key] = raw
    if use_cache:
        _remember_parsed_json(path, value)
    return value


def _backup_split_key_before_empty_write(path: Path) -> None:
    """空集合覆盖已有文件之前先留一份，别把内容直接抹掉。

    清空可能是误操作：读失败被当成空集合、隔离路径没生效、脚本写错库。备份文件
    （`<name>.emptied-<时间>.bak`）让现场永远能捞回来。
    """

    if not path.exists():
        return
    try:
        raw = path.read_bytes()
    except OSError:
        return
    try:
        existing = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        existing = None  # 坏文件更要留一份
    if existing == [] or existing == {}:
        return
    backup = path.with_name(f"{path.name}.emptied-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    try:
        backup.write_bytes(raw)
    except OSError:
        return
    print(
        f"[store] {path.name} 的内容被空集合覆盖，已留备份 {backup.name}",
        file=sys.stderr,
    )


def _write_split_key(key: str, value: Any) -> None:
    path = _split_store_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    # split 文件是机器自己管的（主库 store.json 仍然保持 indent=2 方便人看）：
    # 任务集合每次心跳/进度都要重写它，紧凑格式省下约 30% 的序列化与写盘量。
    serialised = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if _SPLIT_LAST_BYTES.get(key) == serialised and path.exists():
        return  # nothing changed on this collection — skip the write
    if not value:
        _backup_split_key_before_empty_write(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(serialised)
    _atomic_replace(tmp, path)
    _SPLIT_LAST_BYTES[key] = serialised


def _migrate_split_keys_out(data: dict[str, Any]) -> bool:
    # First-run migration: if the main store.json still carries any split key,
    # move it to its own file. Returns True if the main dict was mutated so the
    # caller knows to persist the shrunken store.json.
    moved = False
    for key in _SPLIT_STORE_KEYS:
        if key in data and data[key]:  # non-empty value inside main store
            if not _split_store_path(key).exists():
                _write_split_key(key, data[key])
            data.pop(key, None)
            moved = True
        elif key in data:
            # Empty collection carried in the main file — drop it so subsequent
            # saves don't keep re-serialising empty containers alongside the split.
            data.pop(key, None)
            moved = True
    return moved


def _read_main_store_json(*, use_cache: bool) -> Any:
    """解析主库 store.json。

    读路径（use_cache=True）按 (mtime_ns, size) 复用解析结果；返回的对象**只读**，
    调用方必须先做浅拷贝再改（_read_store_from_disk 就是这么用的）。
    """

    path = settings.STORE_FILE
    if use_cache:
        hit, cached = _read_parsed_json_cached(path)
        if hit:
            return cached
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(".broken.json")
        if path.exists():
            _atomic_replace(path, backup)
        if _restore_last_good_store():
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = _empty_store()
            save_store(value)
    if use_cache:
        _remember_parsed_json(path, value)
    return value


def _split_keys_to_skip(split_keys: tuple[str, ...] | None) -> tuple[str, ...]:
    """把「只加载这几个拆分集合」翻译成「跳过哪几个」。None = 全都要加载。"""

    if split_keys is None:
        return ()
    wanted = {str(key) for key in split_keys}
    return tuple(key for key in _SPLIT_STORE_KEYS if key not in wanted)


def _validated_write_keys(
    write_keys: tuple[str, ...] | None,
    loaded: tuple[str, ...],
) -> tuple[str, ...]:
    """校验 write_keys ⊆ 本次加载的拆分集合，并去重；None = 全部加载的集合都可写。

    只加载不写的集合（例如建任务时读副本/SKU/价格算就绪度）不进 write_keys，
    保存时就不用为它们付「序列化后比对字节」的成本。
    """

    if write_keys is None:
        return tuple(dict.fromkeys(str(key) for key in loaded))
    writable = tuple(dict.fromkeys(str(key) for key in write_keys))
    unknown = [key for key in writable if key not in loaded or key not in _SPLIT_STORE_KEYS]
    if unknown:
        raise ValueError(
            f"write_keys 只能是本次 locked_store 已加载的拆分集合，越界: {unknown}"
            f"（loaded={loaded}）"
        )
    return writable


def _read_store_from_disk(
    *,
    use_cache: bool = False,
    skip_split_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    ensure_dirs()
    if not settings.STORE_FILE.exists():
        if not _restore_last_good_store():
            print("[store] store.json 缺失且没有 last-good 快照：新建空库（请立刻确认是否丢数据）")
            save_store(_empty_store())

    main_data = _read_main_store_json(use_cache=use_cache)
    # 浅拷贝：缓存里的解析结果绝不能被下面的合并与迁移原地改动。
    data = dict(main_data) if isinstance(main_data, dict) else _empty_store()

    # Move split collections out of the main file if this is the first run after
    # deploying the split layout. save_store below will persist the shrunken
    # main file (and the split files individually).
    needs_resave = _migrate_split_keys_out(data)

    # Merge split collections back in so callers see one big dict. Each split
    # file is only parsed if it exists; otherwise fall back to _empty_store().
    empty = _empty_store()
    skipped = set(skip_split_keys)
    for key in _SPLIT_STORE_KEYS:
        if key in skipped:
            # 不读、也不塞空占位：save_store 只写 data 里有的键，跳过就等于不碰文件。
            continue
        value = _read_split_key(key, use_cache=use_cache)
        if value is None:
            value = empty.get(key, [])
        data[key] = value

    for key, value in empty.items():
        if key in skipped:
            continue
        data.setdefault(key, value)
    if _normalize_legacy_store_cached(data, skip_split_keys=skip_split_keys):
        needs_resave = True
    if needs_resave:
        save_store(data)
        _mark_sanitized_sources(data, skip_split_keys=skip_split_keys)
    return data


_LAST_GOOD_NAME = "store.json.last-good"
_LAST_GOOD_MIN_INTERVAL_SECONDS = 300.0
_LAST_GOOD_WRITTEN_AT = 0.0
# 这些集合里任何一个非空，就说明这是一份"有业务数据"的库。
_STORE_DATA_KEYS = (
    "base_products",
    "store_templates",
    "store_listing_copies",
    "listing_skus",
    "listing_prices",
    "automation_jobs",
    "automation_job_snapshots",
    "category_profiles",
    "category_profile_versions",
)


def _last_good_store_path() -> Path:
    """last-good 快照跟着 STORE_FILE 走（不是 DATA_DIR）：隔离路径/临时库各管各的，
    否则一个测试路径的保存会被另一个库的快照误判成"清库"。"""

    return settings.STORE_FILE.with_name(f"{settings.STORE_FILE.name}.last-good")


def _store_has_business_data(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    return any(data.get(key) for key in _STORE_DATA_KEYS)


def _last_good_has_business_data() -> bool:
    path = _last_good_store_path()
    if not path.is_file():
        return False
    try:
        return _store_has_business_data(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return False


def _remember_last_good(data: dict[str, Any]) -> None:
    """把整库快照留一份 last-good（最多 5 分钟一次，best-effort）。

    事故当天没有任何可用备份，只能退回十几小时前的人工备份；这份快照就是为此加的。
    """

    global _LAST_GOOD_WRITTEN_AT
    if not _store_has_business_data(data):
        return
    now = time.monotonic()
    if now - _LAST_GOOD_WRITTEN_AT < _LAST_GOOD_MIN_INTERVAL_SECONDS:
        return
    try:
        tmp = _last_good_store_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _atomic_replace(tmp, _last_good_store_path())
        _LAST_GOOD_WRITTEN_AT = now
    except OSError:
        pass


def _restore_last_good_store() -> bool:
    """store.json 不见了/坏了：能用 last-good 救回来就绝不建空库。"""

    path = _last_good_store_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not _store_has_business_data(data):
        return False
    try:
        save_store(data)
    except OSError:
        return False
    print(f"[store] store.json 缺失或损坏，已从 {_last_good_store_path().name} 自动恢复；请核对是否缺少最新改动")
    return True


def load_store() -> dict[str, Any]:
    if _using_postgres():
        data = _postgres_store().load_store()
        if _normalize_legacy_store(data):
            save_store(data)
        return data

    # File-based backend: reuse the parsed dict while both the main store.json
    # AND all split files are unchanged since the cached parse. The cache key
    # is a tuple of (mtime_ns, size) for every file we merged from.
    ensure_dirs()
    if not settings.STORE_FILE.exists():
        # 关键护栏：绝不能因为"文件不见了"就把整库当空的建出来。
        if not _restore_last_good_store():
            print("[store] store.json 缺失且没有 last-good 快照：新建空库（请立刻确认是否丢数据）")
            save_store(_empty_store())
    cache_key = _current_store_cache_key()
    cached = _STORE_CACHE.get("data")
    if cached is not None and _STORE_CACHE.get("key") == cache_key:
        return cached

    # 双重检查 + single-flight：拿到锁之后可能已经有别的线程重建完了。
    with _STORE_REBUILD_LOCK:
        latest_key = _current_store_cache_key()
        cached = _STORE_CACHE.get("data")
        if cached is not None and _STORE_CACHE.get("key") == latest_key:
            return cached
        data = _read_store_from_disk(use_cache=True)
        # `_read_store_from_disk` may have called save_store to fix legacy shape or
        # migrate split keys; restat before caching.
        _STORE_CACHE["key"] = _current_store_cache_key()
        _STORE_CACHE["data"] = data
        return data


def _current_store_cache_key() -> tuple:
    parts = []
    try:
        stat = settings.STORE_FILE.stat()
        parts.append((str(settings.STORE_FILE), stat.st_mtime_ns, stat.st_size))
    except OSError:
        parts.append((str(settings.STORE_FILE), 0, 0))
    for key in _SPLIT_STORE_KEYS:
        # 跟随实际会被读的那个文件（新位置或旧布局），否则改了旧文件缓存不会失效。
        path = _active_split_store_path(key)
        try:
            stat = path.stat()
            parts.append((key, stat.st_mtime_ns, stat.st_size))
        except OSError:
            parts.append((key, 0, 0))
    return tuple(parts)


def _remember_written_parse(key: str, value: Any, *, sanitized: bool = False) -> None:
    """把刚写出的集合登记进按文件解析缓存。

    执行期的进度/心跳每几秒就写一次 17.6MB 的任务文件，写完之后第一个读请求原来还要
    把这份文件重新 json.loads 一遍（实测 177ms），之后还要再深遍历一遍做 URL 清洗
    （实测 78ms）。写入方手里就有刚序列化出去的那份数据；若写之前那份字节已经洗过、
    而且这次改动只是阶段/状态/租约/日志（不新增 url/page_url/source_url 键），
    连清洗标记也可以一起顺延，下一个读请求就只剩一次字典合并。
    """

    path = _split_store_path(key)
    try:
        if not path.is_file():
            return
        state = _json_file_stat_key(path)
        _FILE_PARSE_CACHE[str(path)] = (state, value)
        if sanitized and state != (0, 0):
            _SANITIZED_FILE_STATES[str(path)] = state
    except OSError:
        return


def _skipped_split_files_have_content(skip_split_keys: tuple[str, ...]) -> bool:
    """跳过的拆分文件里是不是本来就有数据（只 stat，不解析）。

    空集合的紧凑 JSON 是 2 字节（[] / {}），比这大就说明有内容。
    """

    for key in skip_split_keys:
        path = _active_split_store_path(key)
        try:
            if path.is_file() and path.stat().st_size > 2:
                return True
        except OSError:
            continue
    return False


def save_store(data: dict[str, Any], *, skip_split_keys: tuple[str, ...] = ()) -> None:
    if _using_postgres():
        _postgres_store().save_store(data)
        _notify_change_listeners()
        return

    ensure_dirs()
    # 护栏：skip_split_keys 声明「没加载、也不碰」的拆分集合绝不能出现在待保存数据里。
    # 出现了就说明调用方在部分加载的 data 上动了被跳过的集合（setdefault / 整体替换），
    # 继续写等于把这批改动静默吞掉——宁可当场报错点名 key。
    stray = [key for key in _SPLIT_STORE_KEYS if key in set(skip_split_keys) and data.get(key)]
    if stray:
        raise RuntimeError(
            "save_store(skip_split_keys=...) 的待保存数据里出现了被跳过的拆分集合 "
            f"{stray}：调用方没有加载它们却试图保存，继续写会丢数据；"
            "请把这些 key 加进 locked_store(split_keys=...)"
        )
    # Split off the large collections into their own files. The main store.json
    # never carries them, so a save that only changed store_templates rewrites
    # ~800KB instead of ~4MB.
    main_data = {key: value for key, value in data.items() if key not in _SPLIT_STORE_KEYS}
    settings.STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    main_bytes = json.dumps(main_data, ensure_ascii=False, indent=2).encode("utf-8")
    # 主库内容没变就不重写（与 _write_split_key 的「字节相同就跳过」同一套判断）。
    # 任务心跳/执行进度这类只动 split 集合的写入，原来每次都要重写整份主库。
    if _MAIN_LAST_BYTES.get("main") != main_bytes or not settings.STORE_FILE.exists():
        tmp = settings.STORE_FILE.with_suffix(".tmp")
        tmp.write_bytes(main_bytes)
        _atomic_replace(tmp, settings.STORE_FILE)
        _MAIN_LAST_BYTES["main"] = main_bytes
    skip = set(skip_split_keys)
    for key in _SPLIT_STORE_KEYS:
        if key in skip:
            continue
        if key in data:
            _write_split_key(key, data[key])
    # 这里刻意**不清空**读缓存：新写出的文件 mtime 变了，_current_store_cache_key()
    # 自然对不上，下一次 load_store() 会走重建路径；而重建只重解析真正变了的那几个
    # 文件（按文件缓存），没变的文件（例如只动任务时 13MB 的主库和类目）继续复用。
    # 如果这次保存其实一个字节都没改（心跳重写同样内容），缓存键不变，读请求直接命中。
    if (
        not _store_has_business_data(data)
        and not _skipped_split_files_have_content(skip_split_keys)
        and _last_good_has_business_data()
    ):
        # 空库覆盖非空库 = 数据全丢（现场就是这么丢过一次）。宁可直接报错。
        # 部分保存（skip_split_keys）时，被跳过的文件本身可能就有业务数据，
        # 而我们并没有动它们，所以不能据此判定「清库」。
        raise RuntimeError(
            "refusing to overwrite a non-empty store with an empty one; "
            f"备选：先备份 {settings.STORE_FILE} 再确认是否真的要清库"
        )
    if not skip_split_keys:
        # last-good 是整库快照（灾难恢复用）。部分保存手里没有被跳过的集合，
        # 写进去会把它们从快照里抹掉，所以只有整库保存才刷新它。
        _remember_last_good(data)
    _notify_change_listeners()


def _using_postgres() -> bool:
    return settings.STORAGE_BACKEND in {"postgres", "postgresql", "db"}


def _postgres_store() -> PostgresJsonStore:
    global _DB_STORE
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required when STORAGE_BACKEND=postgres")
    if _DB_STORE is None:
        _DB_STORE = PostgresJsonStore(settings.DATABASE_URL)
    return _DB_STORE


def storage_backend_info() -> dict[str, Any]:
    backend = "postgres" if _using_postgres() else "json"
    return {
        "backend": backend,
        "database_configured": bool(settings.DATABASE_URL),
        "store_file": str(settings.STORE_FILE),
        "shared_storage": backend == "postgres",
    }


@contextmanager
def locked_store(
    *,
    split_keys: tuple[str, ...] | None = None,
    main_readonly: bool = False,
    load_main: bool = True,
    write_keys: tuple[str, ...] | None = None,
) -> Iterator[tuple[dict[str, Any], Callable[[dict[str, Any]], None]]]:
    """整库读写锁。

    ``split_keys=None``（默认）加载**全部**拆分集合，行为与以前完全一致。
    传元组时只加载列出的拆分集合，其余不解析、保存时也不碰它们的文件：

    - 复制店铺模板 / 生成副本只动主库里的模板与副本，可以 ``split_keys=()``；
    - 生成自动化任务只额外需要 automation_jobs / automation_job_snapshots。

    这几个操作原来每次都要把 17.6MB 的任务、13.1MB 的类目一起解析（读 ~510ms），
    并在保存时对每个拆分集合重新序列化一遍（写 ~340ms），实测合计 0.9~1.3s。

    ``main_readonly=True``（只往拆分集合里追加的操作用，例如建任务）连「把主库序列化
    一遍看看有没有变」都不做 —— save_store 那一步在 13MB 主库上要 85ms，而这类操作
    一个字节都不会改主库。
    **契约**：这个模式下 with 块里绝不能修改主库（含嵌套字段）。下面只拦得住
    「整个集合对象被替换」，原地改要调用方自己保证（有测试盯着建任务不动主库）。
    ``write_keys``：main_readonly 模式下实际会写的拆分集合（默认=split_keys）。
    建任务要**读**副本/SKU/价格/素材来算就绪度，但只**写**任务与快照——分开声明
    可以避免每次保存都对 30MB 的未改动集合做「序列化后比对字节」。

    ``load_main=False``（快路径）：**连主库 store.json 都不读**，只加载 ``split_keys``
    点名的拆分集合，保存也只写这几个文件。适合只动已拆分集合、不需要任何主库数据的
    操作（例如 ``update_base_product``：0.9MB 的 base_products + 6KB 的 title_skills，
    原来却要为它解析+重写整份 55.7MB 主库）。
    **契约**：这个模式下 with 块里只能改 ``split_keys`` 里的集合——data 里根本没有
    主库数据，别把主库键 setdefault 进来再保存。
    """

    skipped = _split_keys_to_skip(split_keys)
    if _using_postgres():
        with _postgres_store().locked_store() as (data, save_locked):
            if _normalize_legacy_store(data):
                save_locked(data)
            yield data, save_locked
        return

    with _LOCK:
        if load_main is False:
            if not split_keys:
                raise ValueError("load_main=False 需要显式指定 split_keys（快路径只加载点名的拆分集合）")
            wanted = tuple(dict.fromkeys(str(key) for key in split_keys))
            unknown = [key for key in wanted if key not in _SPLIT_STORE_KEYS]
            if unknown:
                raise ValueError(
                    f"load_main=False 只能用于已拆分集合 {unknown}；主库键请走默认路径"
                )
            writable = _validated_write_keys(write_keys, wanted)
            if any(not _active_split_store_path(key).exists() for key in wanted):
                # 还没迁移（首启/旧库）：先走一次整库读，把拆分文件落出来，否则下面会
                # 把「文件不存在」当成空集合、把改动写到一份只有本次内容的文件里。
                _read_store_from_disk()
            empty = _empty_store()
            loaded: dict[str, Any] = {}
            for key in wanted:
                value = _read_split_key(key, use_cache=False)
                if value is None:
                    value = empty.get(key, [])
                # 与整库路径同一套 URL 清洗约定：新字节在写出去之前先洗一遍。
                view = {key: value}
                _sanitize_view_once(_active_split_store_path(key), view)
                loaded[key] = view[key]

            def save_loaded_only(
                updated: dict[str, Any],
                _keys: tuple[str, ...] = writable,
            ) -> None:
                for key in _keys:
                    if key in updated:
                        _write_split_key(key, updated[key])
                # 只把这次写过的文件标记成「已清洗」；主库一个字节没动，不要碰它的标记。
                for key in _keys:
                    if key not in updated:
                        continue
                    path = _active_split_store_path(key)
                    state = _json_file_stat_key(path)
                    if state != (0, 0):
                        _SANITIZED_FILE_STATES[str(path)] = state
                _notify_change_listeners()

            yield loaded, save_loaded_only
            return

        # Bypass the read-cache: writers need a fresh mutable snapshot they can
        # modify in place before calling `save_store`. If we returned the cached
        # dict here, in-place edits would pollute the cache visible to readers.
        data = _read_store_from_disk(skip_split_keys=skipped)
        if not skipped:
            yield data, save_store
            return

        if main_readonly:
            main_ids = {key: id(value) for key, value in data.items() if key not in _SPLIT_STORE_KEYS}
            writes = _validated_write_keys(write_keys, tuple(split_keys or ()))

            def save_split_only(
                updated: dict[str, Any],
                _writes: tuple[str, ...] = writes,
                _ids: dict[str, int] = main_ids,
            ) -> None:
                current = {key: id(value) for key, value in updated.items() if key not in _SPLIT_STORE_KEYS}
                if current != _ids:
                    # 主库被替换过（说明调用方动了它）：退回完整保存，别把改动吞掉。
                    save_store(updated)
                    return
                for key in _writes:
                    if key in updated:
                        _write_split_key(key, updated[key])
                _notify_change_listeners()

            yield data, save_split_only
            return

        def save_partial(updated: dict[str, Any], _skip: tuple[str, ...] = skipped) -> None:
            save_store(updated, skip_split_keys=_skip)

        yield data, save_partial


@contextmanager
def locked_automation_jobs() -> Iterator[tuple[list[dict[str, Any]], Callable[[list[dict[str, Any]]], None]]]:
    """只读写 automation_jobs 一个集合的写路径。

    Worker 的领取/心跳/进度只动任务集合，但原来的 locked_store() 每次都要把
    主库(13MB)+类目(13MB)+任务(18MB) 整库解析——执行期进度事件每秒好几条，
    每次都占着 GIL 解析几百毫秒，界面跟着一起卡。

    这里只解析任务文件、也只写任务文件；没变的其它集合一个字节都不碰。
    automation_jobs 已经拆成独立文件，所以这与整库保存等价（save_store 本来
    也只在任务文件变化时才重写它）。
    """

    if _using_postgres():
        with _postgres_store().locked_store() as (data, save_locked):
            jobs = data.setdefault("automation_jobs", [])

            def save_jobs(updated: list[dict[str, Any]]) -> None:
                data["automation_jobs"] = updated
                save_locked(data)
                _notify_change_listeners()

            yield jobs, save_jobs
        return

    if not _active_split_store_path("automation_jobs").exists():
        # 还没迁移出主库（首启/旧库）：退回整库路径，让 _read_store_from_disk
        # 完成迁移落到 automation_jobs.json，下一次就走快路径。
        with locked_store() as (data, save):
            jobs = data.setdefault("automation_jobs", [])

            def save_full(updated: list[dict[str, Any]]) -> None:
                data["automation_jobs"] = updated
                save(data)

            yield jobs, save_full
        return

    with _LOCK:
        jobs = _read_split_key("automation_jobs", use_cache=False)
        if not isinstance(jobs, list):
            # 文件缺失或坏掉：宁可从合并视图拷一份，也绝不写空集合把任务抹了。
            jobs = deepcopy([item for item in (load_store().get("automation_jobs") or []) if isinstance(item, dict)])

        def save_jobs_only(updated: list[dict[str, Any]]) -> None:
            # 写之前那份字节洗过 => 这次改动（阶段/状态/租约/日志）没引入 URL 键，
            # 清洗标记可以顺延到新内容上。
            jobs_path = _split_store_path("automation_jobs")
            was_sanitized = _SANITIZED_FILE_STATES.get(str(jobs_path)) == _json_file_stat_key(jobs_path)
            _write_split_key("automation_jobs", updated)
            _remember_written_parse("automation_jobs", updated, sanitized=was_sanitized)
            _notify_change_listeners()

        yield jobs, save_jobs_only


def _migrate_mabang_accounts(data: dict[str, Any]) -> bool:
    """把单账号时代的配置迁移成账号实体，并给店铺回填账号归属。

    幂等：只在缺少账号或归属时改写；返回是否真的变了，调用方据此决定是否落盘。
    第二个账号出现前，所有存量店铺都属于当前绑定的那一个账号。
    """

    changed = False
    accounts = data.get("mabang_accounts")
    if not isinstance(accounts, list):
        accounts = []
        data["mabang_accounts"] = accounts
        changed = True
    if not accounts:
        mabang = (data.get("runtime_configuration") or {}).get("mabang") or {}
        login_account = str(mabang.get("login_account") or "").strip()
        if login_account:
            account = _normalize_mabang_account(
                {"name": login_account, "login_account": login_account},
                now=utc_now(),
            )
            # 旧密码是同一把主密钥加密的，原样搬过来，不重新加密。
            account["encrypted_password"] = str(mabang.get("login_password") or "")
            accounts.append(account)
            changed = True
    # 保住现网登录态：单账号时代浏览器用的是 storage/browser-use-profile，里面就有
    # 已经在线的马帮会话。与旧配置同名的那个账号继续用它——若改按账号 id 派生新目录，
    # 等于把登录态丢掉，Worker 又要重新撞滑块验证码。
    # 这条规则同时覆盖「首次播种」和「已经迁移过、只是当时还没写 profile_dir」的库。
    legacy_login = str(
        ((data.get("runtime_configuration") or {}).get("mabang") or {}).get("login_account") or ""
    ).strip()
    legacy_profile = _normalized_profile_dir(str(settings.STORAGE_DIR / "browser-use-profile"))
    if legacy_login and legacy_profile:
        claimed = {
            _normalized_profile_dir(item.get("profile_dir") or "").lower()
            for item in accounts
            if isinstance(item, dict)
        }
        for account in accounts:
            if not isinstance(account, dict):
                continue
            if str(account.get("login_account") or "").strip() != legacy_login:
                continue
            if str(account.get("profile_dir") or "").strip():
                break
            if legacy_profile.lower() in claimed:
                break
            account["profile_dir"] = str(settings.STORAGE_DIR / "browser-use-profile")
            changed = True
            break

    # 端口由系统分配：账号实体刚播种或多账号化之前建的账号可能还没有端口。
    for account in accounts:
        if isinstance(account, dict) and not int(account.get("cdp_port") or 0):
            account["cdp_port"] = _next_free_cdp_port(accounts)
            changed = True
    default_id = _default_mabang_account_id(data)
    if default_id:
        for option in data.get("mabang_store_options") or []:
            if isinstance(option, dict) and not str(option.get("account_id") or "").strip():
                option["account_id"] = default_id
                changed = True
    return changed


def _migrate_automation_job_accounts(data: dict[str, Any]) -> bool:
    """给没有 account_id 的历史自动化任务补上店铺归属账号。

    只在账号实体存在时补。补不出来的（店铺没采过 / 命中多个账号）保持空——多账号下固定
    账号的 Worker 用 account_id 过滤领取，空值的任务不会被任何人领走。宁可搁置等人确认，
    也绝不把任务填进错误的账号。
    """

    if not (data.get("mabang_accounts") or []):
        return False
    copies = {
        str(item.get("id") or ""): item
        for item in data.get("store_listing_copies") or []
        if isinstance(item, dict)
    }
    changed = False
    for job in data.get("automation_jobs") or []:
        if not isinstance(job, dict) or str(job.get("account_id") or "").strip():
            continue
        copy = copies.get(str(job.get("listing_copy_id") or ""))
        if not copy:
            continue
        account_id = _account_id_for_listing_copy_in_store(data, copy)
        if account_id:
            job["account_id"] = account_id
            changed = True
    return changed


def _dedupe_snapshot_rows(snapshot: Any) -> bool:
    """执行快照里的 skus/prices 只留一份（顶层那份，执行器要的网格顺序）。

    browser_inputs 里原来也带着同一份（_build_browser_inputs 的返回值），顶层又
    deepcopy 了一份：最大一档快照 272KB 里这 117KB 是纯重复（占 43%）。执行器
    （scripts/browser_use_runner.py:357）读 browser_inputs.skus 时会回落到
    payload.skus —— 就是顶层这份，所以删掉不改变执行行为。
    """

    if not isinstance(snapshot, dict):
        return False
    inputs = snapshot.get("browser_inputs")
    if not isinstance(inputs, dict):
        return False
    changed = False
    for key in ("skus", "prices"):
        if key in inputs and snapshot.get(key):
            inputs.pop(key)
            changed = True
    return changed


def _migrate_automation_job_snapshot_rows(data: dict[str, Any]) -> bool:
    """旧快照去重（并在去重后重算 execution_snapshot_hash，否则执行前自校验会报变过）。"""

    snapshots = data.get("automation_job_snapshots")
    if not isinstance(snapshots, dict):
        return False
    new_hashes: dict[str, str] = {}
    for job_id, snapshot in snapshots.items():
        if _dedupe_snapshot_rows(snapshot):
            new_hashes[str(job_id)] = _stable_hash(snapshot)
    if not new_hashes:
        return False
    for job in data.get("automation_jobs") or []:
        job_id = str(job.get("id") or "")
        if job_id in new_hashes:
            job["execution_snapshot_hash"] = new_hashes[job_id]
    return True


def _migrate_automation_job_snapshots_out(data: dict[str, Any]) -> bool:
    """把任务里内联的 execution_snapshot 搬到独立的 automation_job_snapshots 集合。

    任务文件 17.6MB 里快照占 17.3MB；搬出去之后领取/心跳/进度/任务列表只碰约 300KB。
    只对**内联**的旧数据生效；没有 id 的任务不 pop，宁可留着也不丢数据。
    """

    snapshots = data.get("automation_job_snapshots")
    if not isinstance(snapshots, dict):
        snapshots = {}
        data["automation_job_snapshots"] = snapshots
    changed = False
    for job in data.get("automation_jobs") or []:
        if not isinstance(job, dict) or "execution_snapshot" not in job:
            continue
        job_id = str(job.get("id") or "")
        if not job_id:
            continue
        snapshots.setdefault(job_id, job.pop("execution_snapshot"))
        changed = True
    return changed


def _normalize_legacy_store_shape(data: dict[str, Any], *, migrate_job_snapshots: bool = True) -> bool:
    """旧键清理与账号/任务归属迁移（不包含整库 URL 遍历）。"""

    changed = False
    for key in list(LEGACY_STORE_KEYS):
        if key in data:
            data.pop(key, None)
            changed = True
    for product in data.get("products") or []:
        if isinstance(product, dict) and product.get("status") == "task_created":
            product["status"] = "ready" if _has_basic_product_data(product) else "needs_check"
            changed = True
    if _migrate_mabang_accounts(data):
        changed = True
    if _migrate_store_template_accounts(data):
        changed = True
    if _migrate_automation_job_accounts(data):
        changed = True
    if migrate_job_snapshots and _migrate_automation_job_snapshots_out(data):
        changed = True
    if migrate_job_snapshots and _migrate_automation_job_snapshot_rows(data):
        changed = True
    return changed


def _sanitize_view_once(path: Path, view: dict[str, Any]) -> bool:
    """对一份文件视图做 URL 清洗；同一份字节只遍历一次。"""

    state = _json_file_stat_key(path)
    if state != (0, 0) and _SANITIZED_FILE_STATES.get(str(path)) == state:
        return False
    changed = sanitize_mabang_urls_in_store(view)
    if state != (0, 0):
        _SANITIZED_FILE_STATES[str(path)] = state
    return changed


def _sanitize_store_sources(data: dict[str, Any], *, skip_split_keys: tuple[str, ...] = ()) -> bool:
    """按文件粒度做 URL 清洗：主库一份视图，每个已加载的拆分集合一份视图。

    视图里放的是与 data 相同的对象引用，所以清洗改动的就是 data 本身。
    """

    changed = False
    main_view = {key: value for key, value in data.items() if key not in _SPLIT_STORE_KEYS}
    if main_view and _sanitize_view_once(settings.STORE_FILE, main_view):
        changed = True
    skipped = set(skip_split_keys)
    for key in _SPLIT_STORE_KEYS:
        if key in skipped or not data.get(key):
            # 空集合/没加载的集合没什么可洗的，连遍历都省掉。
            continue
        if _sanitize_view_once(_active_split_store_path(key), {key: data[key]}):
            changed = True
    return changed


def _mark_sanitized_sources(data: dict[str, Any], *, skip_split_keys: tuple[str, ...] = ()) -> None:
    """把当前各文件的版本标记成「已清洗」。

    只在刚写完盘之后调用：写出去的就是我们刚洗过的那份内容（旧标记会因为 mtime 变化
    失效，不补一次的话下次重建又会白白深遍历一遍）。
    """

    main_state = _json_file_stat_key(settings.STORE_FILE)
    if main_state != (0, 0):
        _SANITIZED_FILE_STATES[str(settings.STORE_FILE)] = main_state
    skipped = set(skip_split_keys)
    for key in _SPLIT_STORE_KEYS:
        if key in skipped or not data.get(key):
            continue
        path = _active_split_store_path(key)
        state = _json_file_stat_key(path)
        if state != (0, 0):
            _SANITIZED_FILE_STATES[str(path)] = state


def _normalize_legacy_store(data: dict[str, Any]) -> bool:
    changed = sanitize_mabang_urls_in_store(data)
    if _normalize_legacy_store_shape(data):
        changed = True
    return changed


def _normalize_legacy_store_cached(data: dict[str, Any], *, skip_split_keys: tuple[str, ...] = ()) -> bool:
    """文件后端的整库规整：URL 清洗按文件缓存，跳过已经洗过的版本。"""

    changed = _sanitize_store_sources(data, skip_split_keys=skip_split_keys)
    # 只在快照集合确实被加载时才搬家：部分加载（领取/心跳）里没有它的位置，
    # 这时候 pop 掉内联快照就等于把 17MB 数据直接删了。
    shape_changed = _normalize_legacy_store_shape(
        data,
        migrate_job_snapshots="automation_job_snapshots" not in set(skip_split_keys),
    )
    if shape_changed:
        changed = True
    return changed


def deployment_readiness() -> dict[str, Any]:
    data = load_store()
    backend = storage_backend_info()
    products = data.get("products") or []
    base_products = data.get("base_products") or []
    store_listing_copies = data.get("store_listing_copies") or []
    assets = data.get("assets") or []
    skus = data.get("skus") or []
    prices = data.get("prices") or []
    outputs = data.get("ai_outputs") or []
    template_path = Path(settings.MABANG_TEMPLATE_PATH)
    local_template = settings.PROJECT_ROOT / "storage" / "samples" / "mabang-template.xlsx"
    template_available = _safe_is_file(template_path) or _safe_is_file(local_template)
    # 一次建索引、整批算完：原来每个商品都单独整表扫描（91 个商品 618ms，首屏每次都付）。
    readiness_by_id = _readiness_by_product(data, products)
    product_readiness = [
        readiness_by_id[str(product.get("id") or "")]
        for product in products
        if str(product.get("id") or "") in readiness_by_id
    ]
    exportable_count = sum(1 for item in product_readiness if item.get("ready"))
    blocked_count = len(product_readiness) - exportable_count
    audit_count = len([item for item in outputs if item.get("output_type") == "mabang_export_audit"])

    checks = [
        {
            "code": "storage_available",
            "label": "存储可用",
            "ok": True,
            "severity": "info",
            "message": f"当前使用 {backend['backend']} 存储。",
            "evidence": backend,
        },
        {
            "code": "template_available",
            "label": "马帮模板",
            "ok": template_available,
            "severity": "error",
            "message": "马帮模板可用。" if template_available else "马帮模板不可用，请配置 MABANG_TEMPLATE_PATH 或补齐本地 fallback。",
            "evidence": {"configured_path": str(template_path), "local_fallback": str(local_template)},
        },
        {
            "code": "product_library",
            "label": "商品资料",
            "ok": bool(products),
            "severity": "warning",
            "message": f"已有 {len(products)} 个商品，{len(assets)} 个素材，{len(skus)} 个 SKU，{len(prices)} 条价格。",
            "evidence": {"products": len(products), "assets": len(assets), "skus": len(skus), "prices": len(prices)},
        },
        {
            "code": "exportable_products",
            "label": "可导出商品",
            "ok": exportable_count > 0,
            "severity": "warning",
            "message": f"当前 {exportable_count} 个商品可直接导出，{blocked_count} 个商品需要补资料。",
            "evidence": {"exportable": exportable_count, "blocked": blocked_count},
        },
        {
            "code": "mabang_audit",
            "label": "导出审计",
            "ok": True,
            "severity": "info",
            "message": f"已记录 {audit_count} 条马帮导出审计。",
            "evidence": {"mabang_export_audit": audit_count},
        },
    ]
    blocking = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    warnings = [check for check in checks if not check["ok"] and check["severity"] == "warning"]
    status = "blocked" if blocking else ("needs_data" if warnings else "ready")
    return {
        "target": "mabang_export",
        "status": status,
        "summary": _deployment_summary(status, warnings),
        "ready": status == "ready",
        "storage": backend,
        "counts": {
            "assets": len(assets),
            "products": len(products),
            "legacy_products": len(products),
            "base_products": len(base_products),
            "store_listing_copies": len(store_listing_copies),
            "skus": len(skus),
            "prices": len(prices),
            "exportable_products": exportable_count,
            "blocked_products": blocked_count,
            "ai_outputs": len(outputs),
        },
        "checks": checks,
        "next_actions": _deployment_next_actions(status, checks),
    }


def list_items(collection: str) -> list[dict[str, Any]]:
    with _LOCK:
        return list(load_store().get(collection, []))


def get_item(collection: str, item_id: str) -> dict[str, Any] | None:
    with _LOCK:
        for item in load_store().get(collection, []):
            if isinstance(item, dict) and item.get("id") == item_id:
                return dict(item)
    return None


def create_ai_output(payload: dict[str, Any]) -> dict[str, Any]:
    output = {
        "id": str(uuid.uuid4()),
        "task_id": payload.get("task_id") or "",
        "product_id": payload.get("product_id") or "",
        "output_type": payload.get("output_type") or "suggestion",
        "prompt": payload.get("prompt") or "",
        "response": payload.get("response") or "",
        "model": payload.get("model") or "",
        "tokens": int(payload.get("tokens") or 0),
        "status": payload.get("status") or "suggested",
        "payload_json": payload.get("payload_json") or {},
        "created_at": utc_now(),
    }
    # 只往 ai_outputs 追加：不解析类目 / 任务 / 快照，也不重写主库（原 754ms → 约 90ms）。
    with locked_store(split_keys=("ai_outputs",), main_readonly=True) as (data, save):
        data.setdefault("ai_outputs", [])
        data["ai_outputs"].append(output)
        save(data)
    return output


def list_ai_outputs_for_product(product_id: str, output_type: str = "") -> list[dict[str, Any]]:
    with _LOCK:
        rows = [
            item
            for item in load_store().get("ai_outputs", [])
            if item.get("product_id") == product_id and (not output_type or item.get("output_type") == output_type)
        ]
    return sorted(rows, key=lambda item: item.get("created_at") or "", reverse=True)


def record_listing_description_generation(
    listing_copy_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    now = utc_now()
    output = {
        "id": str(uuid.uuid4()),
        "task_id": "",
        "product_id": str(listing_copy_id or "").strip(),
        "listing_copy_id": str(listing_copy_id or "").strip(),
        "output_type": "listing_description",
        "description": str(result.get("description") or ""),
        "profile_version_id": str(result.get("profile_version_id") or ""),
        "connection_id": str(result.get("connection_id") or ""),
        "model_id": str(result.get("model_id") or ""),
        "status": str(result.get("status") or "pending"),
        "error": str(result.get("error") or ""),
        "usage": deepcopy(result.get("usage") or {}),
        "created_at": now,
    }
    with locked_store(split_keys=("ai_outputs",), main_readonly=True) as (data, save):
        data.setdefault("ai_outputs", []).append(output)
        save(data)
    return deepcopy(output)


def find_product_by_group(group_id: str) -> dict[str, Any] | None:
    normalized = (group_id or "").strip().upper()
    if not normalized:
        return None
    with _LOCK:
        for product in load_store().get("products", []):
            if (product.get("group_id") or "").strip().upper() == normalized:
                return dict(product)
    return None


def replace_assets(assets: list[dict[str, Any]]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    now = utc_now()
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        item = dict(asset)
        item["id"] = item.get("id") or str(uuid.uuid4())
        item["group_id"] = item.get("group_id") or _derive_group_from_path(item.get("file_path") or item.get("group_root") or "")
        item["status"] = item.get("status") or "ready"
        item["updated_at"] = item.get("updated_at") or now
        normalized.append(item)
    with locked_store() as (data, save):
        data["assets"] = normalized
        save(data)
    return {"count": len(normalized)}


def get_asset(asset_id: str) -> dict[str, Any] | None:
    return get_item("assets", asset_id)


def assets_for_group(group_id: str) -> list[dict[str, Any]]:
    normalized = (group_id or "").strip().upper()
    if not normalized:
        return []
    with _LOCK:
        assets = [
            dict(asset)
            for asset in load_store().get("assets", [])
            if (asset.get("group_id") or "").strip().upper() == normalized
        ]
    return sorted(assets, key=lambda item: (item.get("asset_type") or "", item.get("file_name") or item.get("file_path") or ""))


def _scope_assets_to_asset_root(assets: list[dict[str, Any]], asset_root: str) -> list[dict[str, Any]]:
    """只保留「这次导入目录」下的素材。

    同一个 SKU 文件夹重新导入会换一个上传目录，但同名文件还都留在素材库里（HN0244
    两次导入都有「服装颜色替换生成图片 (1).png」，名字一样、图不一样）。只按 group_id
    取素材再做按文件名查路径，就会一排全落到旧那批图上——副本/执行器上传的图因此
    和商品维护里看到的不一样。操作员自上传的商品图（origin=manual_upload）不在素材
    目录里，按来源标记保留；一张都匹配不上时退回整组，避免路径写法差异把图片全藏掉。
    """
    root = str(asset_root or "").strip()
    if not root:
        return assets
    normalized = os.path.normcase(os.path.normpath(root))
    within = [asset for asset in assets if _path_within_dir(str(asset.get("file_path") or ""), normalized)]
    if not within:
        # 一个都对不上说明素材路径和 asset_root 写法不一致（换过盘符/移动过目录）：
        # 退回整组，宁可可能混进旧图，也不能把这个商品的图全藏掉。
        return assets
    # 操作员自上传的图不在素材目录里，按来源标记保留；其余只认这次导入目录。
    keep = {id(asset) for asset in within}
    keep |= {id(asset) for asset in assets if str(asset.get("origin") or "") == MANUAL_UPLOAD_ORIGIN}
    return [asset for asset in assets if id(asset) in keep]


def assets_for_base_product(base_product: dict[str, Any]) -> list[dict[str, Any]]:
    """只取该商品自己那次导入的素材。

    同一个 SKU 文件夹重新导入会拿到新的上传目录，上一批素材却仍留在素材库里，
    只按 group_id 取素材会把两次导入混在一起（商品图选择器、AI 颜色识别都会读到旧图）。
    有 asset_root 时按它过滤；操作员自上传的商品图（``origin="manual_upload"``）
    不在素材目录里，按来源标记保留。过滤结果为空则退回整个分组的素材，
    避免路径写法不一致时把商品的素材全部藏掉。"""
    payload = base_product or {}
    assets = assets_for_group(str(payload.get("group_id") or ""))
    return _scope_assets_to_asset_root(assets, str(payload.get("asset_root") or ""))


def priority_thumbnail_asset_ids() -> list[str]:
    """列表/卡片真正会显示的主图素材 id（基础商品与刊登副本的主图），按出现顺序。

    现场预热发现：按素材入库顺序倒着预热，和操作员当前在看的列表对不上（91 张被引用的
    主图只覆盖到 33 张）。预热队列按这个优先级排，第一次打开商品/副本列表才是真的不用等。
    """

    with _LOCK:
        data = load_store()
        assets = data.get("assets") or []
        products = data.get("base_products") or []
        copies = data.get("store_listing_copies") or []
    index: dict[tuple[str, str], str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        key = (str(asset.get("group_id") or "").strip().upper(), str(asset.get("file_name") or ""))
        index.setdefault(key, str(asset.get("id") or ""))
    ids: list[str] = []
    seen: set[str] = set()

    def add(group_id: Any, name: Any) -> None:
        asset_id = index.get((str(group_id or "").strip().upper(), str(name or "")))
        if asset_id and asset_id not in seen:
            seen.add(asset_id)
            ids.append(asset_id)

    base_by_id = {str(item.get("id") or ""): item for item in products if isinstance(item, dict)}
    for product in products:
        if not isinstance(product, dict):
            continue
        images = product.get("product_images") or []
        add(product.get("group_id"), product.get("main_image") or (images[0] if images else ""))
    for copy in copies:
        if not isinstance(copy, dict):
            continue
        base = base_by_id.get(str(copy.get("base_product_id") or "")) or {}
        images = base.get("product_images") or []
        name = copy.get("main_image") or base.get("main_image") or (images[0] if images else "")
        add(base.get("group_id"), name)
    return ids


def _path_within_dir(path: str, normalized_dir: str) -> bool:
    if not path:
        return False
    candidate = os.path.normcase(os.path.normpath(path))
    return candidate == normalized_dir or candidate.startswith(normalized_dir + os.sep)


def list_products_by_ids(product_ids: list[str]) -> list[dict[str, Any]]:
    wanted = [str(item or "").strip() for item in product_ids if str(item or "").strip()]
    with _LOCK:
        products = {item.get("id"): dict(item) for item in load_store().get("products", []) if item.get("id") in wanted}
    return [products[item] for item in wanted if item in products]


def list_ai_model_connections() -> list[dict[str, Any]]:
    return [redact_connection(deepcopy(item)) for item in load_store().get("ai_model_connections", [])]


def get_ai_model_connection_private(connection_id: str) -> dict[str, Any] | None:
    item = next((row for row in load_store().get("ai_model_connections", []) if row.get("id") == connection_id), None)
    return deepcopy(item) if item else None


def get_ai_connection_for_purpose(purpose: str = "default") -> dict[str, Any] | None:
    """按用途取 AI 连接。

    现场需求：颜色识别想单独用一个 AI（例如换成更便宜/更稳的视觉模型），
    所以 ``runtime_configuration.ai.color_connection_id`` 优先；
    没配置、或配置的连接已被删除时，回落到默认连接。
    """

    wanted = str(purpose or "default").strip().lower()
    if wanted in {"colors", "color", "color_recognition"}:
        runtime = get_runtime_configuration() or {}
        color_id = str((runtime.get("ai") or {}).get("color_connection_id") or "").strip()
        if color_id:
            connection = get_ai_model_connection_private(color_id)
            if connection:
                return connection
    return get_default_ai_model_connection_private()


def get_default_ai_model_connection_private() -> dict[str, Any] | None:
    item = next((row for row in load_store().get("ai_model_connections", []) if row.get("is_default")), None)
    return deepcopy(item) if item else None


def create_ai_model_connection(payload: dict[str, Any]) -> dict[str, Any]:
    required = ("name", "protocol", "base_url", "model_id", "api_key")
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise ValueError("缺少模型连接字段：" + "、".join(missing))
    protocol = str(payload.get("protocol") or "").strip()
    if protocol not in AI_MODEL_PROTOCOLS:
        raise ValueError("仅支持 Anthropic Messages 或 OpenAI Chat Completions 协议")
    crypto = CredentialCrypto(settings.get_ai_credentials_master_key())
    now = utc_now()
    item = {
        "id": str(uuid.uuid4()),
        "name": str(payload["name"]).strip(),
        "protocol": protocol,
        "base_url": str(payload["base_url"]).strip().rstrip("/"),
        "model_id": str(payload["model_id"]).strip(),
        "api_version": str(payload.get("api_version") or ("2023-06-01" if protocol == "anthropic_messages" else "")).strip(),
        "timeout_seconds": int(payload.get("timeout_seconds") or 30),
        "encrypted_api_key": crypto.encrypt(str(payload["api_key"])),
        "is_default": bool(payload.get("is_default")),
        "created_at": now,
        "updated_at": now,
    }
    with locked_store() as (data, save):
        if item["is_default"] or not any(row.get("is_default") for row in data.get("ai_model_connections", [])):
            for row in data.get("ai_model_connections", []):
                row["is_default"] = False
            item["is_default"] = True
        data.setdefault("ai_model_connections", []).append(item)
        save(data)
    return redact_connection(deepcopy(item))


def set_default_ai_model_connection(connection_id: str) -> dict[str, Any] | None:
    with locked_store() as (data, save):
        selected = None
        for item in data.get("ai_model_connections", []):
            item["is_default"] = item.get("id") == connection_id
            if item["is_default"]:
                selected = item
        if not selected:
            return None
        selected["updated_at"] = utc_now()
        save(data)
        return redact_connection(deepcopy(selected))


def rotate_ai_model_connection_key(connection_id: str, api_key: str) -> dict[str, Any] | None:
    if not str(api_key or "").strip():
        raise ValueError("API Key 不能为空")
    crypto = CredentialCrypto(settings.get_ai_credentials_master_key())
    encrypted = crypto.encrypt(str(api_key or ""))
    with locked_store() as (data, save):
        item = next((row for row in data.get("ai_model_connections", []) if row.get("id") == connection_id), None)
        if not item:
            return None
        item["encrypted_api_key"] = encrypted
        item["updated_at"] = utc_now()
        save(data)
        return redact_connection(deepcopy(item))


def update_ai_model_connection(connection_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    editable = {"name", "protocol", "base_url", "model_id", "api_version", "timeout_seconds"}
    unknown = set(payload) - editable
    if unknown:
        raise ValueError("不支持修改模型连接字段：" + sorted(unknown)[0])
    protocol = str(payload.get("protocol") or "").strip()
    if protocol and protocol not in AI_MODEL_PROTOCOLS:
        raise ValueError("仅支持 Anthropic Messages 或 OpenAI Chat Completions 协议")
    with locked_store() as (data, save):
        item = next((row for row in data.get("ai_model_connections", []) if row.get("id") == connection_id), None)
        if not item:
            return None
        for key in editable:
            if key not in payload:
                continue
            value = payload[key]
            if key in {"name", "base_url", "model_id", "api_version", "protocol"}:
                value = str(value or "").strip()
            if key == "base_url":
                value = value.rstrip("/")
            if key == "timeout_seconds":
                value = int(value or 0)
                if value <= 0:
                    raise ValueError("timeout_seconds 必须大于 0")
            if key in {"name", "base_url", "model_id"} and not value:
                raise ValueError(f"{key} 不能为空")
            item[key] = value
        if item.get("protocol") == "anthropic_messages" and not item.get("api_version"):
            item["api_version"] = "2023-06-01"
        if item.get("protocol") in {"openai_chat_completions", "chat_completions"}:
            item["api_version"] = ""
        item["updated_at"] = utc_now()
        save(data)
        return redact_connection(deepcopy(item))


def record_ai_model_connection_balance(connection_id: str, balance: dict[str, Any]) -> dict[str, Any] | None:
    """Cache the latest balance result on the connection row so the UI can render
    it without re-hitting the provider on every page load. `balance` is expected
    to be the dict form of ai_balance.BalanceResult."""
    now = utc_now()
    with locked_store() as (data, save):
        rows = data.get("ai_model_connections", [])
        item = next((row for row in rows if row.get("id") == connection_id), None)
        if not item:
            return None
        item["last_balance"] = deepcopy(balance) if isinstance(balance, dict) else None
        item["last_balance_fetched_at"] = now
        item["updated_at"] = now
        save(data)
        return redact_connection(deepcopy(item))


def delete_ai_model_connection(connection_id: str) -> bool | None:
    with locked_store() as (data, save):
        rows = data.get("ai_model_connections", [])
        item = next((row for row in rows if row.get("id") == connection_id), None)
        if not item:
            return None
        if item.get("is_default"):
            raise ValueError("默认模型连接不能删除，请先设定其他默认连接")
        data["ai_model_connections"] = [row for row in rows if row.get("id") != connection_id]
        save(data)
        return True


def list_description_profiles() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in load_store().get("description_generation_profiles", [])]


def get_description_profile(profile_id: str) -> dict[str, Any] | None:
    item = next((row for row in load_store().get("description_generation_profiles", []) if row.get("id") == profile_id), None)
    return deepcopy(item) if item else None


def get_default_description_profile() -> dict[str, Any] | None:
    rows = load_store().get("description_generation_profiles", [])
    item = next((row for row in rows if row.get("is_default") and row.get("enabled", True)), None)
    if item is None:
        item = next((row for row in rows if row.get("enabled", True)), None)
    return deepcopy(item) if item else None


def create_description_profile(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in {"combined", "full", "sectioned"} or not str(payload.get("name") or "").strip():
        raise ValueError("描述方案必须包含名称，模式只能是 combined、full 或 sectioned")
    if mode == "sectioned" and "sections" in payload and not isinstance(payload.get("sections"), list):
        raise ValueError("分区方案的 sections 必须是列表")
    now = utc_now()
    item = {
        **deepcopy(payload),
        "id": str(uuid.uuid4()),
        "mode": mode,
        "enabled": bool(payload.get("enabled", True)),
        "is_default": bool(payload.get("is_default")),
        "mabang_store_id": str(payload.get("mabang_store_id") or "").strip(),
        "store_name": str(payload.get("store_name") or "").strip(),
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    item["current_version_id"] = str(uuid.uuid4())
    with locked_store() as (data, save):
        profiles = data.setdefault("description_generation_profiles", [])
        if item["is_default"] or not any(row.get("is_default") for row in profiles):
            for row in profiles:
                row["is_default"] = False
            item["is_default"] = True
        profiles.append(item)
        data.setdefault("description_generation_profile_versions", []).append(
            {"id": item["current_version_id"], "profile_id": item["id"], "version": 1, "snapshot": deepcopy(item), "created_at": now}
        )
        save(data)
    return deepcopy(item)


def update_description_profile(profile_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with locked_store() as (data, save):
        item = next((row for row in data.get("description_generation_profiles", []) if row.get("id") == profile_id), None)
        if not item:
            return None
        updated = {**item, **deepcopy(payload)}
        if str(updated.get("mode") or "") not in {"combined", "full", "sectioned"}:
            raise ValueError("描述方案模式只能是 combined、full 或 sectioned")
        if str(updated.get("mode") or "") == "sectioned" and "sections" in updated and not isinstance(updated.get("sections"), list):
            raise ValueError("分区方案的 sections 必须是列表")
        if "mabang_store_id" in payload:
            updated["mabang_store_id"] = str(payload.get("mabang_store_id") or "").strip()
            updated["store_name"] = str(payload.get("store_name") or "").strip()
        updated["version"] = int(item.get("version") or 1) + 1
        updated["current_version_id"] = str(uuid.uuid4())
        updated["updated_at"] = utc_now()
        item.clear(); item.update(updated)
        data.setdefault("description_generation_profile_versions", []).append(
            {"id": updated["current_version_id"], "profile_id": profile_id, "version": updated["version"], "snapshot": deepcopy(updated), "created_at": updated["updated_at"]}
        )
        save(data)
        return deepcopy(item)


def set_default_description_profile(profile_id: str) -> dict[str, Any] | None:
    with locked_store() as (data, save):
        selected = next((row for row in data.get("description_generation_profiles", []) if row.get("id") == profile_id), None)
        if not selected:
            return None
        if not selected.get("enabled", True):
            raise ValueError("停用的描述方案不能设为默认")
        for row in data.get("description_generation_profiles", []):
            row["is_default"] = row is selected
        selected["updated_at"] = utc_now()
        save(data)
        return deepcopy(selected)


def delete_description_profile(profile_id: str) -> dict[str, Any] | None:
    """Remove a description profile and its version history. Copies keep their
    already-generated descriptions; the deleted profile id simply dangles there
    and the editor shows「当前描述方案」until another profile is picked."""
    profile_id = str(profile_id or "").strip()
    if not profile_id:
        return None
    with locked_store() as (data, save):
        profiles = data.get("description_generation_profiles", [])
        profile = next((row for row in profiles if row.get("id") == profile_id), None)
        if not profile:
            return None
        was_default = bool(profile.get("is_default"))
        profiles.remove(profile)
        data["description_generation_profile_versions"] = [
            row for row in data.get("description_generation_profile_versions", [])
            if row.get("profile_id") != profile_id
        ]
        if was_default and profiles:
            profiles[0]["is_default"] = True
        save(data)
        return {"id": profile_id, "deleted": True}


# ---------------------------------------------------------------------------
# 标题 Skill（Shopee 标题生成配置）与热词词库
# ---------------------------------------------------------------------------

# 工作台采用项目运营规则的 100 字符上限（包含空格）。
TITLE_SKILL_ABSOLUTE_MAX_CHARS = 100
TITLE_SKILL_HOT_WORD_POLICIES = ("off", "reference", "prefer")
TITLE_CATEGORIES = frozenset({"tshirt", "set", "hoodie", "dress", "other", "all"})
# 2026-11-02 起人群主口径为 boys/girls/men（男童/女童/男装）；kids/women/adult
# 是历史数据与旧规则仍在用的兼容值，读取/保存都继续接受，不再出现在新下拉里。
TITLE_AUDIENCES = frozenset({"boys", "girls", "men", "kids", "women", "adult", "all", "unknown"})
TITLE_STRUCTURE = ("theme", "core", "attributes", "audience")

_TITLE_SKILL_FIELDS = (
    "name",
    "enabled",
    "is_default",
    "instructions",
    "title_min_chars",
    "title_max_chars",
    "hot_word_policy",
    "market", "category", "audience", "language", "structure", "ranking",
    "library_id", "max_age_days", "fixed_terms", "banned_terms", "examples",
    "operator_allowances",
    "generation_mode", "operator_template",
)

TITLE_GENERATION_MODES = frozenset({"standard", "operator_template"})


def _title_enum(value: Any, allowed: Any, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label}无效")
    return value


def _title_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{label}必须是整数")
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label}必须是整数") from None


def _title_terms(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(term, str) for term in value):
        raise ValueError(f"{label}必须是文本列表")
    normalized: list[str] = []
    seen: set[str] = set()
    for term in value:
        term = " ".join(term.split())
        if len(term) > TITLE_SKILL_ABSOLUTE_MAX_CHARS:
            raise ValueError(f"{label}中的词语不能超过 100 字符")
        if term and term.casefold() not in seen:
            normalized.append(term)
            seen.add(term.casefold())
    return normalized


def _title_template_lines(value: Any, label: str, *, max_count: int, max_chars: int) -> list[str]:
    """模板配置里的多行文本（附加规则等）：不去重、逐条限长。"""
    if value in (None, []):
        return []
    if not isinstance(value, list) or any(not isinstance(line, str) for line in value):
        raise ValueError(f"{label}必须是文本列表")
    lines = [line.strip() for line in value if line.strip()]
    if len(lines) > max_count:
        raise ValueError(f"{label}最多 {max_count} 条")
    if any(len(line) > max_chars for line in lines):
        raise ValueError(f"{label}每条不能超过 {max_chars} 个字符")
    return lines


def _normalize_operator_template(value: Any) -> dict[str, Any] | None:
    """归一校验仿运营模板配置；空配置返回 None，由调用方决定是否允许。"""
    if value in (None, {}, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError("仿运营模板配置必须是对象")
    core_term = " ".join(str(value.get("core_term") or "").split())
    if not core_term:
        raise ValueError("仿运营模板必须提供核心词")
    if len(core_term) > TITLE_SKILL_ABSOLUTE_MAX_CHARS:
        raise ValueError("核心词不能超过 100 字符")
    template = {
        "core_term": core_term,
        "style_words": _title_terms(value.get("style_words", []), "风格词")[:20],
        "companion_words": _title_terms(value.get("companion_words", []), "搭配词")[:20],
        "patterns": _title_terms(value.get("patterns", []), "排列方式")[:10],
        "trim_order": _title_terms(value.get("trim_order", []), "删词顺序")[:20],
        "reference_terms": _title_terms(value.get("reference_terms", []), "待确认参考词")[:20],
        "rules": _title_template_lines(value.get("rules", []), "模板附加规则", max_count=20, max_chars=300),
        "source": str(value.get("source") or "").strip()[:200],
    }
    if not template["patterns"]:
        raise ValueError("仿运营模板至少要提供一个排列方式")
    try:
        template["version"] = int(value.get("version") or 1)
    except (TypeError, ValueError):
        raise ValueError("模板版本必须是整数") from None
    if not 1 <= template["version"] <= 999:
        raise ValueError("模板版本需在 1–999 之间")
    return template


def _normalize_title_skill(item: dict[str, Any]) -> dict[str, Any]:
    """按白名单归一并校验一个标题 Skill（create 前补默认值、update 前先合并原值）。"""
    name = str(item.get("name") or "").strip()
    if not name:
        raise ValueError("标题 Skill 必须有名称")
    min_chars = _title_integer(item.get("title_min_chars", 40), "最少字数")
    max_chars = _title_integer(item.get("title_max_chars", 100), "最多字数")
    if min_chars > max_chars:
        raise ValueError("标题最少字数不能大于最多字数")
    if not 0 <= min_chars <= TITLE_SKILL_ABSOLUTE_MAX_CHARS:
        raise ValueError(f"标题最少字数需在 0–{TITLE_SKILL_ABSOLUTE_MAX_CHARS} 之间")
    if not 10 <= max_chars <= TITLE_SKILL_ABSOLUTE_MAX_CHARS:
        raise ValueError(f"标题最多字数需在 10–{TITLE_SKILL_ABSOLUTE_MAX_CHARS} 之间")
    policy = str(item.get("hot_word_policy") or "reference").strip().lower()
    if policy not in TITLE_SKILL_HOT_WORD_POLICIES:
        raise ValueError("热词策略只能是不使用（off）、参考融入（reference）或尽量包含（prefer）")
    for flag in ("enabled", "is_default"):
        if flag in item and not isinstance(item[flag], bool):
            raise ValueError("启用及默认状态必须是布尔值")
    structure = item.get("structure", list(TITLE_STRUCTURE))
    if not isinstance(structure, list) or len(structure) != 4 or any(not isinstance(slot, str) for slot in structure) or set(structure) != set(TITLE_STRUCTURE):
        raise ValueError("标题结构必须包含主题、核心词、属性、人群各一次")
    max_age_days = _title_integer(item.get("max_age_days", 30), "提醒周期")
    if not 1 <= max_age_days <= 365:
        raise ValueError("提醒周期需在 1–365 天之间")
    generation_mode = str(item.get("generation_mode") or "standard").strip().lower()
    if generation_mode not in TITLE_GENERATION_MODES:
        raise ValueError("生成模式只能是现有生成方式（standard）或仿运营模板（operator_template）")
    operator_template = None
    if generation_mode == "operator_template":
        operator_template = _normalize_operator_template(item.get("operator_template"))
        if not operator_template:
            raise ValueError("仿运营模板模式必须提供模板配置（核心词、风格词、排列方式）")
    return {
        "name": name,
        "enabled": bool(item.get("enabled", True)),
        "is_default": bool(item.get("is_default")),
        "instructions": str(item.get("instructions") or "").strip(),
        "title_min_chars": min_chars,
        "title_max_chars": max_chars,
        "hot_word_policy": policy,
        "market": _title_enum(item.get("market", "PH"), {"PH"}, "站点"),
        "category": _title_enum(item.get("category", "all"), TITLE_CATEGORIES, "品类"),
        "audience": _title_enum(item.get("audience", "all"), TITLE_AUDIENCES, "人群"),
        "language": _title_enum(item.get("language", "en"), {"en", "taglish"}, "语言"),
        "ranking": _title_enum(item.get("ranking", "volume"), {"volume", "growth"}, "选词偏好"),
        "structure": list(structure),
        "library_id": str(item.get("library_id") or "").strip(),
        "max_age_days": max_age_days,
        "fixed_terms": _title_terms(item.get("fixed_terms", []), "固定词"),
        "banned_terms": _title_terms(item.get("banned_terms", []), "禁用词"),
        "operator_allowances": _title_terms(item.get("operator_allowances", []), "运营例外"),
        "examples": str(item.get("examples") or "").strip(),
        "generation_mode": generation_mode,
        "operator_template": operator_template,
    }


def _title_scope(item: dict[str, Any]) -> tuple[str, str, str]:
    return (str(item.get("market") or "PH"), str(item.get("category") or "all"), str(item.get("audience") or "all"))


def _repair_title_defaults(skills: list[dict[str, Any]], *, preferred_id: str = "", skip_version_id: str = "") -> None:
    """Exactly one enabled default per populated scope; disabled-only scopes have none."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for rule in skills:
        groups.setdefault(_title_scope(rule), []).append(rule)
    for group in groups.values():
        enabled = [rule for rule in group if rule.get("enabled", True)]
        selected = next((rule for rule in enabled if rule.get("id") == preferred_id), None)
        selected = selected or next((rule for rule in enabled if rule.get("is_default")), None)
        selected = selected or (enabled[0] if enabled else None)
        for rule in group:
            desired = rule is selected
            if bool(rule.get("is_default")) != desired:
                rule["is_default"] = desired
                if rule.get("id") != skip_version_id:
                    rule["version"] = int(rule.get("version") or 1) + 1
                    rule["updated_at"] = utc_now()


def list_title_skills() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in load_store().get("title_skills", [])]


def get_title_skill(skill_id: str) -> dict[str, Any] | None:
    item = next((row for row in load_store().get("title_skills", []) if row.get("id") == skill_id), None)
    return deepcopy(item) if item else None


def get_active_title_skill() -> dict[str, Any] | None:
    """生成标题时实际生效的 Skill：默认且启用的优先，其次首个启用项，没有则 None
    （走内置 prompt，行为与没有 Skill 功能时完全一致）。"""
    rows = load_store().get("title_skills", [])
    item = next((row for row in rows if row.get("is_default") and row.get("enabled", True)), None)
    if item is None:
        item = next((row for row in rows if row.get("enabled", True)), None)
    return deepcopy(item) if item else None


def create_title_skill(payload: dict[str, Any]) -> dict[str, Any]:
    # 只带操作员显式提交的字段：省略的字段（如字数、策略）走默认值，而不是 None。
    normalized = _normalize_title_skill({
        key: payload[key] for key in _TITLE_SKILL_FIELDS if payload.get(key) is not None
    })
    now = utc_now()
    item = {
        **normalized,
        "id": str(uuid.uuid4()),
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    with locked_store() as (data, save):
        skills = data.setdefault("title_skills", [])
        skills.append(item)
        _repair_title_defaults(skills, preferred_id=item["id"] if item["is_default"] else "", skip_version_id=item["id"])
        save(data)
    return deepcopy(item)


def update_title_skill(skill_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with locked_store() as (data, save):
        item = next((row for row in data.get("title_skills", []) if row.get("id") == skill_id), None)
        if not item:
            return None
        merged = {**item, **{key: payload[key] for key in _TITLE_SKILL_FIELDS if key in payload}}
        normalized = _normalize_title_skill(merged)
        item.update(normalized)
        _repair_title_defaults(data.get("title_skills", []), preferred_id=item["id"] if item["is_default"] else "", skip_version_id=item["id"])
        item["version"] = int(item.get("version") or 1) + 1
        item["updated_at"] = utc_now()
        save(data)
        return deepcopy(item)


def set_default_title_skill(skill_id: str) -> dict[str, Any] | None:
    with locked_store() as (data, save):
        selected = next((row for row in data.get("title_skills", []) if row.get("id") == skill_id), None)
        if not selected:
            return None
        if not selected.get("enabled", True):
            raise ValueError("停用的标题 Skill 不能设为默认")
        _repair_title_defaults(data.get("title_skills", []), preferred_id=selected["id"])
        selected["updated_at"] = utc_now()
        save(data)
        return deepcopy(selected)


def delete_title_skill(skill_id: str) -> dict[str, Any] | None:
    skill_id = str(skill_id or "").strip()
    if not skill_id:
        return None
    with locked_store() as (data, save):
        skills = data.get("title_skills", [])
        skill = next((row for row in skills if row.get("id") == skill_id), None)
        if not skill:
            return None
        skills.remove(skill)
        _repair_title_defaults(skills)
        save(data)
        return {"id": skill_id, "deleted": True}


def _normalize_hot_word(item: dict[str, Any]) -> dict[str, Any]:
    word = str(item.get("word") or "").strip()
    if not word:
        raise ValueError("热词不能为空")
    if len(word) > 80:
        raise ValueError("单个热词最多 80 个字符")
    return {
        "word": word,
        "tag": str(item.get("tag") or "").strip()[:40],
        "enabled": bool(item.get("enabled", True)),
    }


def list_hot_words() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in load_store().get("hot_words", [])]


def list_enabled_hot_words() -> list[str]:
    """按创建顺序返回启用的热词文本，供标题 prompt 注入。"""
    return [
        str(row.get("word") or "").strip()
        for row in load_store().get("hot_words", [])
        if row.get("enabled", True) and str(row.get("word") or "").strip()
    ]


def create_hot_word(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_hot_word(payload)
    with locked_store() as (data, save):
        words = data.setdefault("hot_words", [])
        if any(str(row.get("word") or "").strip().lower() == normalized["word"].lower() for row in words):
            raise ValueError(f"热词已存在：{normalized['word']}")
        item = {"id": str(uuid.uuid4()), **normalized, "created_at": utc_now()}
        words.append(item)
        save(data)
        return deepcopy(item)


def create_hot_words_batch(words: list[Any], tag: str = "") -> dict[str, int]:
    """批量添加热词：空行跳过、按小写去重（含与库存重复），返回添加/跳过数量。"""
    with locked_store() as (data, save):
        rows = data.setdefault("hot_words", [])
        existing = {str(row.get("word") or "").strip().lower() for row in rows}
        normalized_tag = str(tag or "").strip()[:40]
        total = 0
        created = 0
        now = utc_now()
        for raw in words if isinstance(words, list) else []:
            total += 1
            word = str(raw or "").strip()
            key = word.lower()
            if not word or len(word) > 80 or key in existing:
                continue
            existing.add(key)
            rows.append({"id": str(uuid.uuid4()), "word": word, "tag": normalized_tag, "enabled": True, "created_at": now})
            created += 1
        if created:
            save(data)
        return {"created": created, "skipped": total - created}


def update_hot_word(word_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with locked_store() as (data, save):
        item = next((row for row in data.get("hot_words", []) if row.get("id") == word_id), None)
        if not item:
            return None
        merged = {
            "word": payload.get("word", item.get("word")),
            "tag": payload.get("tag", item.get("tag")),
            "enabled": payload.get("enabled", item.get("enabled", True)),
        }
        normalized = _normalize_hot_word(merged)
        if normalized["word"].lower() != str(item.get("word") or "").strip().lower():
            if any(
                row is not item and str(row.get("word") or "").strip().lower() == normalized["word"].lower()
                for row in data.get("hot_words", [])
            ):
                raise ValueError(f"热词已存在：{normalized['word']}")
        item.update(normalized)
        save(data)
        return deepcopy(item)


def delete_hot_word(word_id: str) -> dict[str, Any] | None:
    with locked_store() as (data, save):
        rows = data.get("hot_words", [])
        item = next((row for row in rows if row.get("id") == word_id), None)
        if not item:
            return None
        rows.remove(item)
        save(data)
        return {"id": word_id, "deleted": True}


def delete_all_hot_words() -> dict[str, Any]:
    with locked_store() as (data, save):
        removed = len(data.get("hot_words", []))
        data["hot_words"] = []
        save(data)
        return {"deleted": removed}


def list_store_templates(account_id: str = "") -> list[dict[str, Any]]:
    wanted_account = str(account_id or "").strip()
    with _LOCK:
        templates = [
            dict(item)
            for item in load_store().get("store_templates", [])
            if str(item.get("status") or "active").strip().lower() != "archived"
        ]
    if wanted_account:
        # 模板按马帮账号隔离：切账号只看得到该账号的模板。
        templates = [
            item for item in templates if str(item.get("account_id") or "").strip() == wanted_account
        ]
    for template in templates:
        template["variation_rule"] = str(template.get("variation_rule") or "color_size").strip()
        template["sku_template"] = str(template.get("sku_template") or "{main_sku}_{color}||{size}").strip()
    return sorted(templates, key=lambda item: (item.get("store_name") or "", item.get("template_name") or "", item.get("created_at") or ""))


def delete_store_template(template_id: str) -> dict[str, Any] | None:
    template_id = str(template_id or "").strip()
    if not template_id:
        return None
    now = utc_now()
    with locked_store() as (data, save):
        templates = data.get("store_templates", [])
        template = next((item for item in templates if item.get("id") == template_id), None)
        if not template:
            return None
        referenced = any(
            item.get("template_id") == template_id
            for item in data.get("store_listing_copies", [])
        )
        if referenced:
            template["status"] = "archived"
            template["archived_at"] = now
            template["updated_at"] = now
            save(data)
            return {"deleted": False, "archived": True, "id": template_id}
        templates.remove(template)
        data["store_template_versions"] = [
            item
            for item in data.get("store_template_versions", [])
            if item.get("template_id") != template_id
        ]
        save(data)
        return {"deleted": True, "archived": False, "id": template_id}


def list_store_promotions(store_id: str, *, available_only: bool = False) -> list[dict[str, Any]]:
    wanted = str(store_id or "").strip()
    with _LOCK:
        items = [
            deepcopy(item)
            for item in load_store().get("store_promotion_activities", [])
            if str(item.get("store_id") or "").strip() == wanted
        ]
    if available_only:
        items = [item for item in items if item.get("status") == "available"]
    return sorted(items, key=lambda item: (item.get("status") != "available", item.get("name") or ""))


def list_store_watermarks(store_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        return list_watermarks(load_store(), store_id)


def get_store_watermark_catalog_status(store_id: str) -> dict[str, Any] | None:
    """店铺水印目录的采集状态（未采集过 → None）。"""
    from services.api.store_watermarks import catalog_status as _status
    with _LOCK:
        return _status(load_store(), store_id)


def record_store_watermark_collect_failure(store_id: str, message: str) -> dict[str, Any] | None:
    """采集水印失败时留痕（不动目录本身）。目录为空/未采集时返回 None。"""
    from services.api.store_watermarks import record_catalog_run as _record
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        return None
    now = utc_now()
    with locked_store() as (data, save):
        run = _record(
            data,
            store_id=wanted_store,
            status="failed",
            count=len(list_watermarks(data, wanted_store)),
            error=message,
            catalog_loaded=False,
            now=now,
        )
        save(data)
    return run


def replace_store_watermark_catalog(
    store_id: str,
    raw_items: list[dict[str, Any]],
    *,
    catalog_loaded: bool = True,
) -> list[dict[str, Any]]:
    from services.api.store_watermarks import record_catalog_run as _record
    from services.api.store_watermarks import replace_watermark_catalog as _replace
    now = utc_now()
    with locked_store() as (data, save):
        normalized = _replace(
            data,
            store_id=store_id,
            raw_items=raw_items or [],
            now=now,
            catalog_loaded=catalog_loaded,
        )
        # 采集成功也要记一笔状态：界面要显示「目录 N 条 · 采集于 …」。
        _record(
            data,
            store_id=store_id,
            status="ok",
            count=len(normalized),
            error="",
            catalog_loaded=True,
            now=now,
        )
        save(data)
    return normalized


def list_store_size_chart_templates(store_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        return list_size_chart_templates(load_store(), store_id)


def replace_store_promotion_catalog(
    store_id: str,
    raw_items: list[dict[str, Any]],
    *,
    now: str | None = None,
    catalog_loaded: bool = True,
) -> list[dict[str, Any]]:
    from services.api.store_promotions import record_catalog_run as _record
    timestamp = now or utc_now()
    with locked_store() as (data, save):
        items = replace_promotion_catalog(
            data,
            store_id=store_id,
            raw_items=raw_items,
            now=timestamp,
            catalog_loaded=catalog_loaded,
        )
        # 采集成功也记一笔状态：界面要能显示「目录 N 条 · 采集于 …」。
        if catalog_loaded:
            _record(
                data,
                store_id=store_id,
                status="ok",
                count=len(items),
                error="",
                catalog_loaded=True,
                now=timestamp,
            )
        save(data)
        return items


def get_store_promotion_catalog_status(store_id: str) -> dict[str, Any] | None:
    """店铺活动目录的采集状态（未采集过 → None）。"""
    from services.api.store_promotions import catalog_status as _status
    with _LOCK:
        return _status(load_store(), store_id)


def record_store_promotion_collect_failure(store_id: str, message: str) -> dict[str, Any] | None:
    """采集店铺活动失败时留痕（不动目录本身）。"""
    from services.api.store_promotions import catalog_status as _status
    from services.api.store_promotions import record_catalog_run as _record
    wanted_store = str(store_id or "").strip()
    if not wanted_store:
        return None
    now = utc_now()
    with locked_store() as (data, save):
        current = _status(data, wanted_store) or {}
        run = _record(
            data,
            store_id=wanted_store,
            status="failed",
            count=int(current.get("count") or 0),
            error=message,
            catalog_loaded=False,
            now=now,
        )
        save(data)
    return run


def create_mabang_page_import(payload: dict[str, Any]) -> dict[str, Any]:
    platform = str(payload.get("platform") or "").strip().lower()
    country = str(payload.get("country") or "").strip().upper()
    conflict_action = str(payload.get("conflict_action") or "update").strip()
    if not platform or not country:
        raise MabangPageImportError("platform and country are required")
    if conflict_action not in {"update", "save_as_new"}:
        raise MabangPageImportError("conflict_action must be update or save_as_new")
    account_id = str(payload.get("account_id") or "").strip()
    if account_id and not get_mabang_account(account_id):
        raise MabangPageImportError("所选马帮账号不存在，请重新选择")
    now = utc_now()
    run = {
        "id": str(uuid.uuid4()),
        "source": "mabang_current_page",
        "status": "pending",
        "platform": platform,
        "country": country,
        "conflict_action": conflict_action,
        # 采集要落到正确的马帮账号：这个 run 用哪个账号的浏览器打开与采集，
        # 全程按它取 CDP 端口与 Chrome Profile（cookie 隔离）。
        "account_id": account_id,
        # 页面导入用的是「1 号槽位」那只浏览器（账号自己的可见窗口）。多槽位并行时
        # 只有这个槽位的 Worker 需要让路，其他槽位各用各的浏览器，不受影响。
        "slot": _normalized_slot(payload.get("slot")),
        "expected_store_id": str(payload.get("expected_store_id") or "").strip(),
        "created_at": now,
        "updated_at": now,
    }
    with locked_store() as (data, save):
        data.setdefault("mabang_page_import_runs", []).append(run)
        save(data)
    return deepcopy(run)


def get_mabang_page_import(run_id: str) -> dict[str, Any] | None:
    data = load_store()
    run = next((item for item in data.get("mabang_page_import_runs", []) if item.get("id") == run_id), None)
    return deepcopy(run) if run else None


# 「从马帮当前页导入」进行中的判定窗口：这个时间内的未完成导入任务算「操作员正在
# 用采集浏览器」，Worker 必须让路（不能重启浏览器、不能领任务抢页面）。超过这个
# 时间还没有动静（弹窗被关掉、采集失败后没人管）就算放弃，不再一直挡着 Worker。
PAGE_IMPORT_SESSION_TTL_SECONDS = 30 * 60
PAGE_IMPORT_ACTIVE_STATUSES = ("pending", "collected", "collected_with_warnings")


def active_mabang_page_import(
    account_id: str | None = None,
    *,
    slot: int | None = None,
    now: str | None = None,
    ttl_seconds: int = PAGE_IMPORT_SESSION_TTL_SECONDS,
) -> dict[str, Any] | None:
    """该账号下仍在进行中的「从马帮当前页导入」任务，没有则返回 None。

    导入期间采集浏览器就是操作员的可见窗口：Worker 的浏览器恢复会按设置里的
    「无头」把它杀掉重起（窗口凭空消失），执行器也会顶掉操作员正在填的页面。
    所以调用方（Worker 预检、自动登录）必须先问这里。

    - 只认 `PAGE_IMPORT_ACTIVE_STATUSES`；已确认/失败/取消的都不算。
    - 按账号过滤：指定账号时，空 account_id 的历史任务视为任意账号（单账号时代）。
    - 指定 `slot` 时只认那一个槽位的导入（页面导入固定落在 1 号槽位的窗口上）；
      不指定则任意槽位都算（老调用方的语义）。
    - 超过 `ttl_seconds` 没有更新的任务视为已放弃，避免一个没人管的弹窗长期挡住 Worker。
    """

    wanted = str(account_id or "").strip()
    wanted_slot = None if slot is None else _normalized_slot(slot)
    current = _parse_utc(now) if now else datetime.now(timezone.utc)
    latest: dict[str, Any] | None = None
    latest_stamp = ""
    for run in load_store().get("mabang_page_import_runs", []) or []:
        if not isinstance(run, dict):
            continue
        if str(run.get("status") or "").strip() not in PAGE_IMPORT_ACTIVE_STATUSES:
            continue
        run_account = str(run.get("account_id") or "").strip()
        if wanted and run_account and run_account != wanted:
            continue
        if wanted_slot is not None and _normalized_slot(run.get("slot")) != wanted_slot:
            continue
        stamp = str(run.get("updated_at") or run.get("created_at") or "").strip()
        if not stamp:
            continue
        try:
            age_seconds = (current - _parse_utc(stamp)).total_seconds()
        except ValueError:
            continue
        if age_seconds > ttl_seconds:
            continue
        if latest is None or stamp > latest_stamp:
            latest = run
            latest_stamp = stamp
    return deepcopy(latest) if latest else None


def stage_mabang_page_import(run_id: str, raw_capture: dict[str, Any]) -> dict[str, Any]:
    with locked_store() as (data, save):
        run = next((item for item in data.get("mabang_page_import_runs", []) if item.get("id") == run_id), None)
        if not run:
            raise MabangPageImportNotFound(f"Mabang page import not found: {run_id}")
        result = stage_page_import(data, run_id, raw_capture, now=utc_now())
        save(data)
        return deepcopy(result)


def complete_mabang_page_import(run_id: str, raw_capture: dict[str, Any]) -> dict[str, Any]:
    """Compatibility endpoint: completion now means snapshot staging only."""

    return stage_mabang_page_import(run_id, raw_capture)


def record_mabang_page_import_error(run_id: str, message: str) -> dict[str, Any]:
    with locked_store() as (data, save):
        run = next((item for item in data.get("mabang_page_import_runs", []) if item.get("id") == run_id), None)
        if not run:
            raise MabangPageImportNotFound(f"Mabang page import not found: {run_id}")
        result = record_page_import_error(data, run_id, message, now=utc_now())
        save(data)
        return deepcopy(result)


def confirm_mabang_page_import(run_id: str) -> dict[str, Any]:
    with locked_store() as (data, save):
        run = next((item for item in data.get("mabang_page_import_runs", []) if item.get("id") == run_id), None)
        if not run:
            raise MabangPageImportNotFound(f"Mabang page import not found: {run_id}")
        result = confirm_page_import(
            data,
            run_id,
            now=utc_now(),
            # 导入用的是 run 上记录的账号（采集时选的），没有才退回默认账号；
            # 模板按账号隔离，这里不能一律写默认账号。
            account_id=str(run.get("account_id") or "").strip() or _default_mabang_account_id(data),
        )
        if not result["run"].get("already_confirmed"):
            save(data)
        return deepcopy(result)


def list_category_profiles() -> list[dict[str, Any]]:
    data = load_store()
    persisted = data.get("category_profiles") or []
    if not persisted:
        return [_export_category_profile(profile) for profile in CATEGORY_PROFILES.values()]
    items = [_enrich_category_profile(profile, data) for profile in persisted]
    persisted_ids = {item.get("id") for item in persisted}
    items.extend(
        _export_category_profile(profile)
        for profile_id, profile in CATEGORY_PROFILES.items()
        if profile_id not in persisted_ids
    )
    return items


def get_category_profile(profile_id: str) -> dict[str, Any]:
    normalized = str(profile_id or "").strip()
    data = load_store()
    persisted = next((item for item in data.get("category_profiles", []) if item.get("id") == normalized), None)
    if persisted:
        return _enrich_category_profile(persisted, data)
    if normalized in CATEGORY_PROFILES:
        return _export_category_profile(CATEGORY_PROFILES[normalized])
    return {}


def list_category_profile_versions(profile_id: str) -> list[dict[str, Any]]:
    data = load_store()
    return sorted(
        [deepcopy(item) for item in data.get("category_profile_versions", []) if item.get("profile_id") == profile_id],
        key=lambda item: int(item.get("version") or 0),
        reverse=True,
    )


def get_category_profile_collection_run(run_id: str) -> dict[str, Any] | None:
    data = load_store()
    item = next((entry for entry in data.get("category_collection_runs", []) if entry.get("id") == run_id), None)
    return deepcopy(item) if item else None


def list_marketplace_categories(
    *,
    platform: str = "",
    country: str = "",
    store_id: str = "",
    query: str = "",
    parent_id: str | None = None,
    leaf_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    data = load_store()
    return search_catalog(
        data.get("marketplace_categories", []),
        platform=platform,
        country=country,
        store_id=store_id,
        query=query,
        parent_id=parent_id,
        leaf_only=leaf_only,
        limit=limit,
    )


def list_merged_marketplace_categories(query: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """Return leaf categories across every store, de-duplicated by path.

    The per-store catalog is isolated by store_id, but a base product carries a
    single cross-store "expected category" (no store binding). Collapse nodes with
    the same localized path into one option; the representative keeps the first
    store's category_id so a later copy-to-store can match by path.
    """
    data = load_store()
    needle = str(query or "").strip().casefold()
    by_path: dict[str, dict[str, Any]] = {}
    for item in data.get("marketplace_categories", []):
        if not item.get("active", True):
            continue
        if not item.get("is_leaf"):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("category_id", "name", "name_zh", "name_en", "path", "path_zh", "path_en")
        ).casefold()
        if needle and needle not in haystack:
            continue
        key = str(item.get("path_zh") or "").strip() or path
        key = key.casefold()
        existing = by_path.get(key)
        if existing is None:
            by_path[key] = deepcopy(item)
            continue
        # Same path from another store: prefer the one that already surfaced a
        # localized name/path (richer for display), keep the first as representative.
        if not str(existing.get("path_zh") or "").strip() and str(item.get("path_zh") or "").strip():
            by_path[key] = deepcopy(item)
    items = sorted(by_path.values(), key=lambda entry: (str(entry.get("path_zh") or entry.get("path") or "").casefold(), str(entry.get("category_id") or "")))
    bounded_limit = max(1, min(int(limit or 500), 5000))
    return items[:bounded_limit]


def _merged_category_catalog() -> dict[str, dict[str, Any]]:
    """Merge every store's catalog into one cross-store node map keyed by category_id.

    Mabang reuses the same global category ids across stores, so the same node
    appears once per store. The merged view keeps the richest localized names,
    treats a node as a leaf only when no store has children under it, and always
    exposes parent links so a cascading picker can walk the union tree.
    """
    data = load_store()
    merged: dict[str, dict[str, Any]] = {}
    child_ids: dict[str, set[str]] = {}
    for item in data.get("marketplace_categories", []):
        if not item.get("active", True):
            continue
        cid = str(item.get("category_id") or "").strip()
        if not cid:
            continue
        parent_id = str(item.get("parent_id") or "").strip()
        node = merged.get(cid)
        if node is None:
            node = deepcopy(item)
            node["category_id"] = cid
            node["parent_id"] = parent_id
            node["_leaf_count"] = 0
            merged[cid] = node
        # Prefer richer localized names (first non-empty wins; later stores may
        # fill gaps with data earlier stores lacked).
        if not str(node.get("name_zh") or "").strip() and str(item.get("name_zh") or "").strip():
            node["name_zh"] = item.get("name_zh")
        if not str(node.get("path_zh") or "").strip() and str(item.get("path_zh") or "").strip():
            node["path_zh"] = item.get("path_zh")
        if not str(node.get("name_en") or "").strip() and str(item.get("name_en") or "").strip():
            node["name_en"] = item.get("name_en")
        if not str(node.get("path") or "").strip() and str(item.get("path") or "").strip():
            node["path"] = item.get("path")
        if parent_id:
            child_ids.setdefault(parent_id, set()).add(cid)
    # Recompute leaf/children on the union tree.
    for node in merged.values():
        cid = str(node.get("category_id") or "")
        has_children = bool(child_ids.get(cid))
        node["has_children"] = has_children
        node["is_leaf"] = not has_children
    return merged


def list_merged_category_children(parent_id: str = "", query: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """Direct children of a parent in the cross-store union catalog (for a
    cascading three-column category picker). parent_id "" / "0" means roots.

    When ``query`` is given the search spans the whole union catalog (any depth),
    so typing a keyword surfaces both leaves and branch nodes the user can then
    drill into.
    """
    merged = _merged_category_catalog()
    wanted_parent = str(parent_id or "").strip()
    if wanted_parent in {"", "0"}:
        wanted_parent = ""
    needle = str(query or "").strip().casefold()
    children = []
    for node in merged.values():
        node_parent = str(node.get("parent_id") or "").strip()
        if needle:
            haystack = " ".join(
                str(node.get(key) or "")
                for key in ("category_id", "name", "name_zh", "name_en", "path", "path_zh")
            ).casefold()
            if needle not in haystack:
                continue
        else:
            if wanted_parent and node_parent != wanted_parent:
                continue
            if not wanted_parent and node_parent not in {"", "0"}:
                continue
        clean = {k: v for k, v in node.items() if not k.startswith("_")}
        children.append(clean)
    children.sort(
        key=lambda item: (
            # Search ranking: exact category-id hits first, then exact name
            # matches, then shallower (upper-level) paths — so the branch the
            # user typed surfaces before its many descendants.
            0 if needle and str(item.get("category_id") or "").casefold() == needle else 1,
            0 if needle and str(item.get("name_zh") or item.get("name") or "").casefold() == needle else 1,
            0 if needle and str(item.get("name_zh") or item.get("name") or "").casefold().startswith(needle) else 1,
            (str(item.get("path") or "").count(">") if needle else 0),
            str(item.get("name_zh") or item.get("name") or "").casefold(),
            str(item.get("category_id") or ""),
        )
    )
    bounded_limit = max(1, min(int(limit or 500), 1000))
    return children[:bounded_limit]


class CategoryQuickTemplateError(ValueError):
    """Raised when a quick category template payload is invalid."""


class CategoryQuickTemplateNotFound(ValueError):
    """Raised when a quick category template id does not exist."""


def list_category_quick_templates() -> list[dict[str, Any]]:
    data = load_store()
    items = data.get("category_quick_templates", [])
    result = []
    for item in items:
        entry = deepcopy(item)
        # 老模板只有类目：统一补空字典，前端不必区分 undefined。
        if not isinstance(entry.get("attribute_values"), dict):
            entry["attribute_values"] = {}
        result.append(entry)
    return sorted(
        result,
        key=lambda entry: str(entry.get("name") or "").casefold(),
    )


def _normalize_quick_template_attribute_values(value: Any) -> dict[str, Any]:
    """Keep a quick template's category attribute values small and JSON-safe.

    The shape mirrors base-product 'attribute_values' (text, boolean and
    multi-select lists). Prices, sizes and nested objects never belong here.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if isinstance(raw_value, dict):
            # 嵌套结构不属于类目属性值，跳过而不是转成字符串。
            continue
        if isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, (int, float)):
            result[key] = str(raw_value)
        elif isinstance(raw_value, (list, tuple)):
            items = [str(item).strip() for item in raw_value if str(item or "").strip()]
            if items:
                result[key] = items[:50]
        else:
            text = str(raw_value if raw_value is not None else "").strip()
            if text:
                result[key] = text
        if len(result) >= 200:
            break
    return result


def save_category_quick_template(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert a named quick template: category + its attribute values, no prices.

    Selecting the template later replays both the category and the saved
    attribute values; prices/sizes stay on the base product.
    """
    name = str(payload.get("name") or "").strip()
    if not name:
        raise CategoryQuickTemplateError("name is required")
    category = _normalize_product_category(payload.get("category"))
    if not category.get("path"):
        raise CategoryQuickTemplateError("category with a path is required")
    raw_values = payload.get("attribute_values")
    if raw_values is None:
        raw_values = payload.get("category_attributes")
    attribute_values = _normalize_quick_template_attribute_values(raw_values)
    now = utc_now()
    with locked_store() as (data, save):
        templates = data.setdefault("category_quick_templates", [])
        existing = next((item for item in templates if str(item.get("name") or "").strip().casefold() == name.casefold()), None)
        if existing:
            existing["category"] = category
            existing["attribute_values"] = attribute_values
            existing["updated_at"] = now
            result = deepcopy(existing)
        else:
            item = {
                "id": str(uuid.uuid4()),
                "name": name,
                "category": category,
                "attribute_values": attribute_values,
                "created_at": now,
                "updated_at": now,
            }
            templates.append(item)
            result = deepcopy(item)
        save(data)
        return result


def delete_category_quick_template(template_id: str) -> dict[str, Any] | None:
    template_id = str(template_id or "").strip()
    if not template_id:
        return None
    with locked_store() as (data, save):
        templates = data.setdefault("category_quick_templates", [])
        index = next((idx for idx, item in enumerate(templates) if item.get("id") == template_id), None)
        if index is None:
            return None
        removed = templates.pop(index)
        save(data)
        return removed


def get_marketplace_category_catalog_run(run_id: str) -> dict[str, Any] | None:
    data = load_store()
    item = next(
        (entry for entry in data.get("marketplace_category_catalog_runs", []) if entry.get("id") == run_id),
        None,
    )
    return deepcopy(item) if item else None


def create_marketplace_category_catalog_run(payload: dict[str, Any]) -> dict[str, Any]:
    with locked_store() as (data, save):
        run = create_catalog_run(data, payload, now=utc_now())
        save(data)
        return deepcopy(run)


def complete_marketplace_category_catalog_run(run_id: str, raw_capture: dict[str, Any]) -> dict[str, Any]:
    with locked_store() as (data, save):
        result = complete_catalog_run(data, run_id, raw_capture, now=utc_now())
        save(data)
        return deepcopy(result)


def create_category_profile_collection_run(payload: dict[str, Any]) -> dict[str, Any]:
    with locked_store() as (data, save):
        run = create_collection_run(data, payload, now=utc_now())
        save(data)
        return deepcopy(run)


def complete_category_profile_collection_run(run_id: str, raw_capture: dict[str, Any]) -> dict[str, Any]:
    with locked_store() as (data, save):
        result = complete_collection_run(data, run_id, raw_capture, now=utc_now())
        save(data)
        return deepcopy(result)


def publish_category_profile_version(version_id: str, reviewer: str, note: str) -> dict[str, Any]:
    with locked_store() as (data, save):
        result = publish_version(data, version_id, reviewer=reviewer, note=note, now=utc_now())
        save(data)
        return deepcopy(result)


def reject_category_profile_version(version_id: str, reviewer: str, reason: str) -> dict[str, Any]:
    with locked_store() as (data, save):
        result = reject_version(data, version_id, reviewer=reviewer, reason=reason, now=utc_now())
        save(data)
        return deepcopy(result)


def validate_category_profile_values(version_id: str, values: dict[str, Any]) -> dict[str, Any]:
    data = load_store()
    version = next((item for item in data.get("category_profile_versions", []) if item.get("id") == version_id), None)
    if not version:
        raise CategoryProfileNotFound(f"Category profile version not found: {version_id}")
    return validate_attribute_values(version, values).as_dict()


def _enrich_category_profile(profile: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(profile.get("id") or "")
    item = _export_category_profile(CATEGORY_PROFILES[profile_id]) if profile_id in CATEGORY_PROFILES else {}
    item.update(deepcopy(profile))
    version = next(
        (
            candidate
            for candidate in data.get("category_profile_versions", [])
            if candidate.get("id") == (profile.get("active_version_id") or profile.get("published_version_id"))
        ),
        None,
    )
    if version:
        item["active_version"] = deepcopy(version)
        item["published_version"] = deepcopy(version)
        item["active_version_id"] = version.get("id") or ""
        item["published_version_id"] = version.get("id") or ""
        defaults = {
            field.get("field_id"): deepcopy(field.get("default_value"))
            for field in version.get("fields") or []
            if field.get("field_id")
        }
        item["required_attributes"] = defaults
        item["category_attributes"] = deepcopy(defaults)
        variation = deepcopy(version.get("variation") or {})
        variation.setdefault("type", "variant")
        item["variation"] = variation
    return item


def create_store_template(payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    template = _normalize_store_template_payload(payload, now=now)
    template["version"] = 1
    template["current_version_id"] = str(uuid.uuid4())
    with locked_store() as (data, save):
        _validate_cnsc_store_template(template, allow_missing_activity=True)
        template["account_id"] = _resolve_store_template_account(data, template, fallback_default=True)
        if template.get("listing_mode") == "cnsc_global":
            template["missing_configuration"] = _cnsc_configuration_problems(template)
            template["status"] = "needs_configuration" if template["missing_configuration"] else "active"
        _validate_store_template_category_binding(data, template)
        data.setdefault("store_templates", [])
        data.setdefault("store_template_versions", [])
        data["store_templates"].append(template)
        data["store_template_versions"].append(_store_template_version(template, now=now))
        save(data)
    return deepcopy(template)


def copy_store_template(template_id: str, target: dict[str, Any]) -> dict[str, Any]:
    source_id = str(template_id or "").strip()
    target_store_id = str(target.get("mabang_store_id") or "").strip()
    template_name = str(target.get("template_name") or "").strip()
    if not source_id:
        raise ValueError("源店铺模板不能为空")
    if not target_store_id:
        raise ValueError("请选择目标店铺")
    if not template_name:
        raise ValueError("模板名不能为空")

    now = utc_now()
    # 复制模板只动主库里的 store_templates / store_template_versions：不解析 17.6MB 的任务、
    # 13.1MB 的类目，保存时也不重新序列化它们。
    with locked_store(split_keys=()) as (data, save):
        source = next(
            (
                item for item in data.get("store_templates", [])
                if item.get("id") == source_id
                and str(item.get("status") or "active").strip().lower() != "archived"
            ),
            None,
        )
        if not source:
            raise LookupError("Store template not found")
        target_account = str(target.get("account_id") or "").strip()
        candidates = [
            item for item in data.get("mabang_store_options", [])
            if str(item.get("mabang_store_id") or "") == target_store_id
        ]
        if target_account:
            candidates = [
                item for item in candidates
                if str(item.get("account_id") or "") == target_account
            ]
        elif len(candidates) > 1:
            wanted_default = _default_mabang_account_id(data)
            preferred = [
                item for item in candidates
                if str(item.get("account_id") or "") == wanted_default
            ]
            if len(preferred) == 1:
                candidates = preferred
        if len(candidates) > 1:
            raise ValueError("该店铺 id 在多个马帮账号下存在，请指定账号")
        target_store = candidates[0] if candidates else None
        if not target_store:
            raise ValueError("目标店铺尚未从马帮采集")

        platform = str(target_store.get("platform") or "").strip().lower()
        country = str(target_store.get("country") or target_store.get("site") or "").strip().upper()
        same_site = (
            platform == str(source.get("platform") or "").strip().lower()
            and country == str(source.get("country") or "").strip().upper()
        )
        copied = _copy_store_template_payload(
            source,
            target_store=target_store,
            template_name=template_name,
            same_site=same_site,
            now=now,
        )
        data.setdefault("store_templates", []).append(copied)
        data.setdefault("store_template_versions", []).append(_store_template_version(copied, now=now))
        save(data)
        return deepcopy(copied)


def _copy_store_template_payload(
    source: dict[str, Any],
    *,
    target_store: dict[str, Any],
    template_name: str,
    same_site: bool,
    now: str,
) -> dict[str, Any]:
    reusable_fields = (
        "sizes",
        "variation_rule",
        "variation",
        "sku_template",
        "default_sku",
        "sku_strip_parentheses",
        "stock",
        "weight_kg",
        "package_length_cm",
        "package_width_cm",
        "package_height_cm",
        "transaction_info",
        "description_profile_version_id",
        "default_size_chart_path",
        "size_chart_asset_id",
        "size_chart_preview_url",
        "size_chart_source",
        "mabang_size_chart_template_id",
        "mabang_size_chart_template_name",
    )
    same_site_fields = (
        "category_profile_id",
        "category_profile_version_id",
        "product_line",
        "marketplace_category_path",
        "mabang_category_id",
        "category_attributes",
        "attribute_values",
        "normal_price",
        "sale_price",
        "size_price_overrides",
    )
    copied = {field: deepcopy(source.get(field)) for field in reusable_fields}
    if same_site:
        copied.update({field: deepcopy(source.get(field)) for field in same_site_fields})
    else:
        copied.update({
            "category_profile_id": "",
            "category_profile_version_id": "",
            "product_line": "",
            "marketplace_category_path": "",
            "mabang_category_id": "",
            "category_attributes": {},
            "attribute_values": {},
            "normal_price": "",
            "sale_price": "",
            "size_price_overrides": {},
        })
    copied.update({
        "id": str(uuid.uuid4()),
        "store_name": str(target_store.get("store_name") or "").strip(),
        "platform": str(target_store.get("platform") or source.get("platform") or "shopee").strip().lower(),
        "country": str(target_store.get("country") or target_store.get("site") or "").strip().upper(),
        "currency": str(target_store.get("currency") or "").strip(),
        "mabang_store_id": str(target_store.get("mabang_store_id") or "").strip(),
        # 另存模板也换账号：副本跟着目标店铺的账号走（跨账号搬模板因此不需要额外配置）。
        "account_id": str(target_store.get("account_id") or "").strip(),
        "template_name": template_name,
        "promotion_activity_id": "",
        "mabang_discount_id": "",
        "discount_id": "",
        "discount_label": "",
        "main_image_watermark": {},
        "product_image_watermark": {},
        "status": "active" if same_site else "needs_mabang_import",
        "version": 1,
        "current_version_id": str(uuid.uuid4()),
        "created_at": now,
        "updated_at": now,
    })
    return copied


def duplicate_store_template(template_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy one store template into a new template of the same store.

    模板卡片上的「复制」走这条路径：目标店铺就是源模板的店铺，所以促销活动、水印、
    类目属性、价格、描述模板和 CNSC 档位都照抄，只换 id / 名称 / 版本和时间戳。
    换店铺的「另存为模板」仍走 :func:`copy_store_template`，那里才会清空店铺绑定值。
    """
    source_id = str(template_id or "").strip()
    if not source_id:
        raise ValueError("源店铺模板不能为空")
    requested_name = str((payload or {}).get("template_name") or "").strip()
    now = utc_now()
    # 同 copy_store_template：只动主库里的 store_templates / store_template_versions，
    # 不解析任务与类目集合，保存时也不重新序列化它们。
    with locked_store(split_keys=()) as (data, save):
        templates = data.setdefault("store_templates", [])
        source = next(
            (
                item for item in templates
                if item.get("id") == source_id
                and str(item.get("status") or "active").strip().lower() != "archived"
            ),
            None,
        )
        if not source:
            raise LookupError("Store template not found")
        copied = deepcopy(source)
        copied["id"] = str(uuid.uuid4())
        copied["current_version_id"] = str(uuid.uuid4())
        copied["template_name"] = requested_name or _duplicate_template_name(templates, source)
        copied["version"] = 1
        copied["created_at"] = now
        copied["updated_at"] = now
        # 同店复制：账号跟着源模板（老模板还没记账号时按店铺兜底）。
        copied["account_id"] = _resolve_store_template_account(
            data, copied, previous=source, fallback_default=True
        )
        if str(copied.get("listing_mode") or "site").strip().lower() == "cnsc_global":
            # 副本按自己的配置重算：缺配置的副本仍旧是 needs_configuration，
            # 不能因为复制就被当成已就绪。
            copied["missing_configuration"] = _cnsc_configuration_problems(copied)
            copied["status"] = "needs_configuration" if copied["missing_configuration"] else "active"
        else:
            copied["status"] = "active"
        templates.append(copied)
        data.setdefault("store_template_versions", []).append(_store_template_version(copied, now=now))
        save(data)
        return deepcopy(copied)


def _duplicate_template_name(templates: list[dict[str, Any]], source: dict[str, Any]) -> str:
    """Name a same-store duplicate "<原模板名> - 副本"；同店重名时依次加序号。"""

    base = f"{str(source.get('template_name') or '').strip() or '店铺模板'} - 副本"
    store_id = str(source.get("mabang_store_id") or "")
    used = {
        str(item.get("template_name") or "").strip()
        for item in templates
        if str(item.get("mabang_store_id") or "") == store_id
        and str(item.get("status") or "active").strip().lower() != "archived"
    }
    if base not in used:
        return base
    index = 2
    while f"{base} ({index})" in used:
        index += 1
    return f"{base} ({index})"


def _resolve_store_template_account(
    data: dict[str, Any],
    template: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    fallback_default: bool = False,
) -> str:
    """店铺模板归属的马帮账号。

    显式账号优先（并校验该账号真的拥有这个店铺），其次按店铺唯一命中；店铺还没采集过、
    或者历史上没记账号时回落到 ``previous`` / 默认账号。命中多个账号且没有显式账号时
    宁可报错，也不把模板挂到别人的账号下。
    """

    explicit = str(template.get("account_id") or "").strip()
    store_id = str(template.get("mabang_store_id") or "").strip()
    platform = str(template.get("platform") or "shopee").strip().lower()
    owners: list[str] = []
    if store_id:
        for option in data.get("mabang_store_options") or []:
            if str(option.get("mabang_store_id") or "").strip() != store_id:
                continue
            if str(option.get("platform") or "").strip().lower() != platform:
                continue
            account_id = str(option.get("account_id") or "").strip()
            if account_id and account_id not in owners:
                owners.append(account_id)
    if explicit:
        if owners and explicit not in owners:
            raise ValueError("该店铺属于其它马帮账号，请重新选择店铺")
        return explicit
    if len(owners) == 1:
        return owners[0]
    previous_account = str((previous or {}).get("account_id") or "").strip()
    if previous_account and (not owners or previous_account in owners):
        return previous_account
    if len(owners) > 1:
        raise ValueError("该店铺在多个马帮账号下存在，请先指定马帮账号")
    return _default_mabang_account_id(data) if fallback_default else ""


def _migrate_store_template_accounts(data: dict[str, Any]) -> bool:
    """给没有 account_id 的历史模板补上归属账号。

    归档模板不补（列表里本来就不显示，补了也没人看）；按店铺唯一命中写回，命不中就写
    默认账号——这里和任务的保守策略不同：模板是操作员可见可改的配置，落到默认账号比
    留空更好收拾。
    """

    if not (data.get("mabang_accounts") or []):
        return False
    changed = False
    for template in data.get("store_templates") or []:
        if not isinstance(template, dict) or str(template.get("account_id") or "").strip():
            continue
        if str(template.get("status") or "active").strip().lower() == "archived":
            continue
        try:
            account_id = _resolve_store_template_account(data, template, fallback_default=True)
        except ValueError:
            continue
        if account_id:
            template["account_id"] = account_id
            changed = True
    return changed


def _store_template_version(template: dict[str, Any], *, now: str) -> dict[str, Any]:
    return {
        "id": str(template.get("current_version_id") or uuid.uuid4()),
        "template_id": str(template.get("id") or ""),
        "version": int(template.get("version") or 1),
        "snapshot": deepcopy(template),
        "created_at": now,
    }


def get_runtime_configuration() -> dict[str, Any]:
    data = load_store()
    result = public_runtime_configuration(data.get("runtime_configuration"))
    result.setdefault("mabang", {})["login_configured"] = _mabang_login_bound(data)
    result.setdefault("updates", {})["token_configured"] = _github_update_token_configured(data)
    result["version"] = int(data.get("runtime_configuration_version") or 0)
    return result


def save_runtime_configuration(patch: dict[str, Any]) -> dict[str, Any]:
    patch = _prepare_runtime_configuration_patch(patch)
    # 运行时配置全在主库里：类目 / 任务 / 快照 / 修订都不用解析（原 690ms → 约 240ms）。
    with locked_store(split_keys=()) as (data, save):
        updated = update_runtime_configuration(data.get("runtime_configuration"), patch)
        data["runtime_configuration"] = updated
        version = int(data.get("runtime_configuration_version") or 0) + 1
        data["runtime_configuration_version"] = version
        data.setdefault("runtime_configuration_audit", []).append({
            "id": str(uuid.uuid4()),
            "version": version,
            "at": utc_now(),
            "groups": sorted(str(group) for group in patch),
            "changes": _redacted_runtime_configuration_patch(patch),
        })
        save(data)
        result = public_runtime_configuration(updated)
        result["mabang"]["login_configured"] = _mabang_login_bound(data)
        result["updates"]["token_configured"] = _github_update_token_configured(data)
        result["version"] = version
        return result


def _prepare_runtime_configuration_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Encrypt credentials before they reach the store.

    A blank ``mabang.login_password`` / ``updates.token`` means "keep the current
    one" and is dropped; a non-empty one is Fernet-encrypted with the shared AI
    credentials master key.
    """

    if not isinstance(patch, dict):
        return patch
    prepared = dict(patch)

    mabang = prepared.get("mabang")
    if isinstance(mabang, dict):
        normalized_mabang = dict(mabang)
        if "login_account" in normalized_mabang:
            normalized_mabang["login_account"] = str(normalized_mabang.get("login_account") or "").strip()
        password = normalized_mabang.get("login_password")
        if isinstance(password, str):
            if not password.strip():
                normalized_mabang.pop("login_password", None)
            else:
                crypto = CredentialCrypto(settings.get_ai_credentials_master_key())
                normalized_mabang["login_password"] = crypto.encrypt(password)
        prepared["mabang"] = normalized_mabang

    updates = prepared.get("updates")
    if isinstance(updates, dict):
        normalized_updates = dict(updates)
        token = normalized_updates.get("token")
        if isinstance(token, str):
            if not token.strip():
                normalized_updates.pop("token", None)
            else:
                crypto = CredentialCrypto(settings.get_ai_credentials_master_key())
                normalized_updates["token"] = crypto.encrypt(token.strip())
        prepared["updates"] = normalized_updates

    return prepared


def _mabang_login_bound(data: dict[str, Any]) -> bool:
    for account in data.get("mabang_accounts") or []:
        if (
            str(account.get("login_account") or "").strip()
            and str(account.get("encrypted_password") or "").strip()
        ):
            return True
    config = (data.get("runtime_configuration") or {}).get("mabang") or {}
    return bool(str(config.get("login_account") or "").strip() and str(config.get("login_password") or "").strip())


def _github_update_token_configured(data: dict[str, Any]) -> bool:
    config = (data.get("runtime_configuration") or {}).get("updates") or {}
    return bool(str(config.get("token") or "").strip())


def get_github_update_token() -> str:
    """Return the decrypted GitHub token for release publishing / update checks.

    The token is stored Fernet-encrypted; it must never be returned by the settings
    API, logged, or put on a command line. Callers pass it to GitHub through an
    Authorization header (or a git env-based credential) only.
    """

    data = load_store()
    config = (data.get("runtime_configuration") or {}).get("updates") or {}
    encrypted = str(config.get("token") or "").strip()
    if not encrypted:
        return ""
    return _decrypt_mabang_password(encrypted) or ""


def get_mabang_login_credentials(account_id: str | None = None) -> tuple[str | None, str | None]:
    """Return the bound Mabang auto-login ``(account, password)`` or ``(None, None)``.

    ``account_id`` selects the 马帮账号; omitted means the default account (the one the
    legacy runtime configuration points at, else the first enabled one). When no
    account entity exists yet, the legacy single-account configuration is used.

    The password is decrypted in-process; callers must only pass it to the
    auto-login child process via environment variables, never argv or logs.
    """

    data = load_store()
    wanted = str(account_id or "").strip()
    if wanted:
        account = next(
            (item for item in data.get("mabang_accounts") or [] if str(item.get("id") or "") == wanted),
            None,
        )
        if account is None:
            return None, None
        login_account = str(account.get("login_account") or "").strip()
        password = _decrypt_mabang_password(str(account.get("encrypted_password") or ""))
        return (login_account, password) if login_account and password else (None, None)

    # 未指定账号：单账号时代的运行时配置仍然是权威来源（它也是账号实体被播种的
    # 来源），账号实体只作为回退。这样迁移不会改变现有绑定的行为；设置页接管账号
    # 管理后，调用方改为显式传 account_id。
    config = (data.get("runtime_configuration") or {}).get("mabang") or {}
    legacy_account = str(config.get("login_account") or "").strip()
    legacy_token = str(config.get("login_password") or "")
    if legacy_account and legacy_token:
        password = _decrypt_mabang_password(legacy_token)
        if password:
            return legacy_account, password

    default_id = _default_mabang_account_id(data)
    if default_id:
        account = next(
            (item for item in data.get("mabang_accounts") or [] if str(item.get("id") or "") == default_id),
            None,
        )
        if account is not None:
            login_account = str(account.get("login_account") or "").strip()
            password = _decrypt_mabang_password(str(account.get("encrypted_password") or ""))
            if login_account and password:
                return login_account, password
    return None, None


def list_runtime_configuration_audit(limit: int = 100) -> list[dict[str, Any]]:
    count = max(1, min(int(limit or 100), 500))
    rows = load_store().get("runtime_configuration_audit") or []
    return [deepcopy(row) for row in rows[-count:]][::-1]


def _redacted_runtime_configuration_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return {}
    hidden_fragments = ("api_key", "password", "secret", "token", "cookie")

    def sanitize(key: str, value: Any) -> Any:
        lowered = key.lower()
        if any(fragment in lowered for fragment in hidden_fragments):
            return "[redacted]"
        if isinstance(value, str) and lowered.endswith(("_url", "url")):
            return value.split("?", 1)[0]
        if isinstance(value, dict):
            return {str(child_key): sanitize(str(child_key), child_value) for child_key, child_value in value.items()}
        if isinstance(value, list):
            return [sanitize(key, item) for item in value]
        return deepcopy(value)

    return {str(group): sanitize(str(group), fields) for group, fields in patch.items()}


# ---------------------------------------------------------------------------
# 马帮账号
#
# 各账号结构相同：一份凭据、一个 CDP 端口、一个 Chrome Profile、一批自己采集到
# 的店铺、一条自己的自动化队列。主账号能看到子账号店铺只是马帮侧的权限表现，
# 系统不为它建模，也不区分主/子角色。
# 见 docs/adr/0002-mabang-account-owns-shops.md。
# ---------------------------------------------------------------------------

MABANG_ACCOUNT_MUTABLE_FIELDS = (
    "name",
    "login_account",
    "cdp_port",
    "profile_dir",
    "enabled",
    "session_status",
    "last_tested_at",
)


def _encrypt_mabang_password(value: str) -> str:
    crypto = CredentialCrypto(settings.get_ai_credentials_master_key())
    return crypto.encrypt(value)


def _decrypt_mabang_password(token: str) -> str | None:
    if not str(token or "").strip():
        return None
    try:
        return CredentialCrypto(settings.get_ai_credentials_master_key()).decrypt(str(token))
    except ValueError:
        return None


# 采集浏览器端口从这里开始按账号自动分配（9223、9224、9225…），操作员不需要、
# 也不应该自己配：端口错了会顶掉另一个账号的登录态。
MABANG_CDP_PORT_BASE = 9223
# 同一个账号的并行槽位（slot）整体挪到下一个百位段：2 号槽位 = 9223+100、3 号 = +200。
# 账号端口本身是 9223 起按账号顺延，槽位 stride 取 100 就保证「账号若干 × 槽位若干」
# 不会互相撞端口（最多 4 槽位 ⇒ 只用到 9223~9524）。
MABANG_SLOT_PORT_STRIDE = 100


def _normalized_slot(value: Any) -> int:
    """槽位号规整：任何脏值都按 0（账号自己的那只浏览器）处理。"""

    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _slot_profile_dir(profile_dir: Path, slot: int) -> Path:
    """一个账号第 slot 号槽位的 Chrome Profile 目录（1 号槽位就是账号自己的目录）。

    Chrome 一个 `--user-data-dir` 只允许一个实例，所以同账号要再开一只浏览器必须
    有独立目录；独立目录 = 独立 cookie jar，新槽位要自己走一次自动登录。
    """

    if slot <= 0:
        return Path(profile_dir)
    return Path(f"{profile_dir}-slot{slot + 1}")


def _next_free_cdp_port(accounts: list[dict[str, Any]]) -> int:
    used = {int(item.get("cdp_port") or 0) for item in accounts}
    port = MABANG_CDP_PORT_BASE
    while port in used:
        port += 1
    return port


def _normalize_mabang_account(payload: dict[str, Any], *, now: str) -> dict[str, Any]:
    login_account = str(payload.get("login_account") or "").strip()
    if not login_account:
        raise ValueError("马帮登录账号不能为空")
    # 端口由系统分配（见 _next_free_cdp_port）；显式传入只作为内部/测试用途，
    # 非法值不报错、退回自动分配——操作员界面上根本没有这个字段。
    cdp_port = 0
    raw_port = payload.get("cdp_port")
    if raw_port is not None and str(raw_port).strip():
        try:
            candidate = int(str(raw_port).strip())
        except (TypeError, ValueError):
            candidate = 0
        if 1 <= candidate <= 65535:
            cdp_port = candidate
    return {
        "id": str(payload.get("id") or uuid.uuid4()),
        "name": str(payload.get("name") or "").strip() or login_account,
        "login_account": login_account,
        "profile_dir": str(payload.get("profile_dir") or "").strip(),
        "cdp_port": cdp_port,
        "session_status": str(payload.get("session_status") or "unknown").strip(),
        "last_tested_at": str(payload.get("last_tested_at") or "").strip(),
        "enabled": bool(payload.get("enabled", True)),
        "encrypted_password": str(payload.get("encrypted_password") or ""),
        "created_at": payload.get("created_at") or now,
        "updated_at": now,
    }


def _public_mabang_account(account: dict[str, Any]) -> dict[str, Any]:
    item = {key: deepcopy(value) for key, value in account.items() if key != "encrypted_password"}
    item["has_password"] = bool(str(account.get("encrypted_password") or "").strip())
    return item


def _default_mabang_account_id(data: dict[str, Any]) -> str:
    """旧配置指向的账号优先，否则第一个启用的账号。

    账号实体尚不存在时返回 ""，调用方回退到单账号时代的默认桶。
    """
    accounts = [item for item in data.get("mabang_accounts") or [] if isinstance(item, dict)]
    if not accounts:
        return ""
    legacy = str(((data.get("runtime_configuration") or {}).get("mabang") or {}).get("login_account") or "").strip()
    if legacy:
        matched = next(
            (item for item in accounts if str(item.get("login_account") or "").strip() == legacy),
            None,
        )
        if matched:
            return str(matched.get("id") or "")
    enabled = [item for item in accounts if item.get("enabled", True)]
    return str((enabled or accounts)[0].get("id") or "")


def default_mabang_account_id() -> str:
    with _LOCK:
        return _default_mabang_account_id(load_store())


def list_mabang_accounts() -> list[dict[str, Any]]:
    with _LOCK:
        accounts = [dict(item) for item in load_store().get("mabang_accounts", [])]
    return [_public_mabang_account(item) for item in accounts]


def get_mabang_account(account_id: str) -> dict[str, Any] | None:
    wanted = str(account_id or "").strip()
    if not wanted:
        return None
    with _LOCK:
        account = next(
            (item for item in load_store().get("mabang_accounts", []) if str(item.get("id") or "") == wanted),
            None,
        )
        return _public_mabang_account(account) if account else None


def create_mabang_account(payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    password = payload.get("login_password")
    account = _normalize_mabang_account(payload, now=now)
    if isinstance(password, str) and password.strip():
        account["encrypted_password"] = _encrypt_mabang_password(password)
    with locked_store() as (data, save):
        accounts = data.setdefault("mabang_accounts", [])
        if any(str(item.get("login_account") or "").strip() == account["login_account"] for item in accounts):
            raise ValueError("该马帮登录账号已存在")
        if account["cdp_port"] and any(int(item.get("cdp_port") or 0) == account["cdp_port"] for item in accounts):
            raise ValueError("该 CDP 端口已被其他账号占用")
        wanted_profile = _normalized_profile_dir(account["profile_dir"])
        if wanted_profile and any(
            _normalized_profile_dir(item.get("profile_dir") or "").lower() == wanted_profile.lower()
            for item in accounts
        ):
            raise ValueError("该浏览器数据目录已被其他账号占用；每个账号必须独立 Profile 目录以保证 cookie 隔离")
        if not account["cdp_port"]:
            account["cdp_port"] = _next_free_cdp_port(accounts)
        accounts.append(account)
        saved = deepcopy(account)
        save(data)
    return _public_mabang_account(saved)


def update_mabang_account(account_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    wanted = str(account_id or "").strip()
    if not wanted:
        raise ValueError("账号 id 不能为空")
    now = utc_now()
    password = payload.get("login_password")
    with locked_store() as (data, save):
        accounts = data.setdefault("mabang_accounts", [])
        account = next((item for item in accounts if str(item.get("id") or "") == wanted), None)
        if account is None:
            return None
        merged = {
            **account,
            **{key: payload[key] for key in MABANG_ACCOUNT_MUTABLE_FIELDS if key in payload},
            "id": wanted,
        }
        normalized = _normalize_mabang_account(merged, now=now)
        normalized["created_at"] = account.get("created_at") or normalized["created_at"]
        normalized["encrypted_password"] = str(account.get("encrypted_password") or "")
        if isinstance(password, str) and password.strip():
            normalized["encrypted_password"] = _encrypt_mabang_password(password)
        others = [item for item in accounts if str(item.get("id") or "") != wanted]
        if any(str(item.get("login_account") or "").strip() == normalized["login_account"] for item in others):
            raise ValueError("该马帮登录账号已存在")
        if normalized["cdp_port"] and any(int(item.get("cdp_port") or 0) == normalized["cdp_port"] for item in others):
            raise ValueError("该 CDP 端口已被其他账号占用")
        wanted_profile = _normalized_profile_dir(normalized["profile_dir"])
        if wanted_profile and any(
            _normalized_profile_dir(item.get("profile_dir") or "").lower() == wanted_profile.lower()
            for item in others
        ):
            raise ValueError("该浏览器数据目录已被其他账号占用；每个账号必须独立 Profile 目录以保证 cookie 隔离")
        if not normalized["cdp_port"]:
            normalized["cdp_port"] = _next_free_cdp_port(others)
        account.clear()
        account.update(normalized)
        saved = deepcopy(account)
        save(data)
    return _public_mabang_account(saved)


def delete_mabang_account(account_id: str) -> dict[str, Any] | None:
    wanted = str(account_id or "").strip()
    if not wanted:
        raise ValueError("账号 id 不能为空")
    with locked_store() as (data, save):
        accounts = data.setdefault("mabang_accounts", [])
        account = next((item for item in accounts if str(item.get("id") or "") == wanted), None)
        if account is None:
            return None
        owned = [
            item for item in data.get("mabang_store_options", [])
            if str(item.get("account_id") or "") == wanted
        ]
        if owned:
            raise ValueError(f"该账号名下仍有 {len(owned)} 个店铺，请先转移归属再删除")
        accounts.remove(account)
        removed = deepcopy(account)
        save(data)
    return _public_mabang_account(removed)


def _mabang_account_profile_dir(account: dict[str, Any]) -> Path:
    """账号的 Chrome Profile 目录。

    Cookie 隔离 = Profile 隔离：不同 `--user-data-dir` 的 Chrome 各自持有独立的
    cookie jar，所以每个账号必须落在唯一的目录里。没显式配置时按账号 id 派生，
    绝不允许回落到共用目录——两个账号共用一个 Profile 会互相顶掉登录态。
    """

    explicit = str(account.get("profile_dir") or "").strip()
    if explicit:
        return Path(explicit)
    return settings.STORAGE_DIR / "mabang-profiles" / str(account.get("id") or "")


def _normalized_profile_dir(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except OSError:
        return text


def resolve_mabang_account_runtime(
    account_id: str | None = None,
    *,
    slot: int = 0,
) -> dict[str, Any]:
    """解析一个马帮账号的采集/执行运行时：CDP 地址、Chrome Profile 目录、登录账号。

    指定账号时用账号自己的端口与目录；账号实体还不存在时回退到单账号时代的
    `worker.cdp_url` + `storage/browser-use-profile`，保证迁移期行为不变。

    `slot` 是同一账号的并行槽位（0 = 账号自己的那只浏览器，老行为）。同账号要同时
    跑多单就得多开浏览器，而 Chrome 的端口与 `--user-data-dir` 都不能共用，所以
    槽位 2 起用独立 Profile 目录（`<账号目录>-slot2`）与独立端口（+100）。
    """

    wanted_slot = _normalized_slot(slot)
    data = load_store()
    wanted = str(account_id or "").strip() or _default_mabang_account_id(data)
    runtime_config = data.get("runtime_configuration") or {}
    worker_config = runtime_config.get("worker") or {}
    mabang_config = runtime_config.get("mabang") or {}
    fallback_cdp = str(worker_config.get("cdp_url") or settings.WORKER_CDP_URL)
    headless = bool(mabang_config.get("headless"))
    account = (
        next(
            (item for item in data.get("mabang_accounts") or [] if str(item.get("id") or "") == wanted),
            None,
        )
        if wanted
        else None
    )
    if account is None:
        # 没有账号实体（旧单账号库）：槽位没有立足点，保持老的单浏览器运行时。
        return {
            "account_id": "",
            "login_account": str(mabang_config.get("login_account") or "").strip(),
            "cdp_url": fallback_cdp,
            "profile_dir": str(settings.STORAGE_DIR / "browser-use-profile"),
            "headless": headless,
            "has_credentials": _mabang_login_bound(data),
            "slot": 0,
        }
    port = int(account.get("cdp_port") or 0)
    profile_dir = _slot_profile_dir(_mabang_account_profile_dir(account), wanted_slot)
    return {
        "account_id": str(account.get("id") or ""),
        "login_account": str(account.get("login_account") or "").strip(),
        "cdp_url": (
            f"http://127.0.0.1:{port + wanted_slot * MABANG_SLOT_PORT_STRIDE}" if port else fallback_cdp
        ),
        "profile_dir": str(profile_dir),
        "headless": headless,
        "has_credentials": bool(str(account.get("encrypted_password") or "").strip()),
        "slot": wanted_slot,
    }


def record_store_collection_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """持久化采集运行记录，并给成功的运行写回 `last_collected_at`（按账号+店铺记账）。"""

    if not runs:
        return {"ok": True, "saved": 0}
    now = utc_now()
    with locked_store() as (data, save):
        stored = data.setdefault("store_collection_runs", [])
        collected = (
            data.setdefault("runtime_configuration", {})
            .setdefault("store_collection", {})
            .setdefault("last_collected_at", {})
        )
        for run in runs:
            row = dict(run)
            row["recorded_at"] = now
            stored.append(row)
            if str(row.get("status") or "") != "succeeded":
                continue
            account_id = str(row.get("account_id") or "").strip()
            store_id = str(row.get("store_id") or "").strip()
            if store_id:
                collected[store_collection_key(account_id, store_id)] = str(
                    row.get("finished_at") or now
                )
        save(data)
    return {"ok": True, "saved": len(runs)}


def list_store_collection_runs(
    *,
    limit: int = 50,
    account_id: str = "",
    store_id: str = "",
) -> list[dict[str, Any]]:
    wanted_account = str(account_id or "").strip()
    wanted_store = str(store_id or "").strip()
    rows = [dict(item) for item in load_store().get("store_collection_runs", [])]
    if wanted_account:
        rows = [row for row in rows if str(row.get("account_id") or "") == wanted_account]
    if wanted_store:
        rows = [row for row in rows if str(row.get("store_id") or "") == wanted_store]
    bounded = max(1, min(int(limit or 50), 500))
    return list(reversed(rows[-bounded:]))


def store_collection_targets() -> dict[str, dict[str, Any]]:
    """给采集调度器的店铺配置：每个已采集店铺带自己的账号。

    没有账号归属（旧数据、账号实体还没有）的店铺按 `""` 归到同一组，保持旧行为。
    """

    targets: dict[str, dict[str, Any]] = {}
    for option in load_store().get("mabang_store_options", []):
        store_id = str(option.get("mabang_store_id") or "").strip()
        if not store_id or str(option.get("status") or "active").strip().lower() != "active":
            continue
        targets[store_id] = {
            "enabled": True,
            "account_id": str(option.get("account_id") or "").strip(),
            "platform": str(option.get("platform") or "").strip(),
            "country": str(option.get("country") or "").strip(),
            "store_name": str(option.get("store_name") or "").strip(),
        }
    return targets


def _account_id_for_listing_copy_in_store(data: dict[str, Any], listing_copy: dict[str, Any]) -> str:
    """从店铺推导刊登副本所属的马帮账号。

    店铺按账号归属且互不重叠，所以按 (platform, mabang_store_id) 唯一命中一个账号；
    命中不唯一时返回 ""，绝不猜——宁可让任务停在需要人工确认。
    """

    store_id = str(listing_copy.get("mabang_store_id") or "").strip()
    platform = str(listing_copy.get("platform") or "shopee").strip().lower()
    if not store_id:
        return ""
    matches = [
        item
        for item in data.get("mabang_store_options") or []
        if str(item.get("mabang_store_id") or "").strip() == store_id
        and str(item.get("platform") or "").strip().lower() == platform
    ]
    # 命中不唯一（同一个店铺 id 出现在多个账号下）时不猜：宁可让任务停在
    # 需要人工确认，也不能把 A 账号的店铺填进 B 账号的登录态里。
    if len(matches) != 1:
        return ""
    return str(matches[0].get("account_id") or "")


def _store_option_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("account_id") or "").strip(),
        str(item.get("platform") or "").strip().lower(),
        str(item.get("mabang_store_id") or "").strip(),
    )


def list_mabang_store_options(platform: str = "", account_id: str | None = None) -> list[dict[str, Any]]:
    wanted_platform = str(platform or "").strip().lower()
    wanted_account = str(account_id or "").strip()
    with _LOCK:
        options = [dict(item) for item in load_store().get("mabang_store_options", [])]
    if wanted_platform:
        options = [item for item in options if str(item.get("platform") or "").strip().lower() == wanted_platform]
    if wanted_account:
        options = [item for item in options if str(item.get("account_id") or "").strip() == wanted_account]
    return sorted(
        options,
        key=lambda item: (
            item.get("account_id") or "",
            item.get("platform") or "",
            item.get("store_name") or "",
        ),
    )


def upsert_mabang_store_options(
    items: list[dict[str, Any]] | dict[str, Any],
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    if isinstance(items, dict):
        items = [items]
    now = utc_now()
    saved = []
    with locked_store() as (data, save):
        fallback_account = str(account_id or "").strip() or _default_mabang_account_id(data)
        options = data.setdefault("mabang_store_options", [])
        by_key = {_store_option_key(item): item for item in options}
        normalized = []
        for item in items:
            payload = dict(item)
            if not str(payload.get("account_id") or "").strip():
                payload["account_id"] = fallback_account
            option = _normalize_mabang_store_option(payload, now=now)
            if option.get("store_name") and option.get("mabang_store_id"):
                normalized.append(option)
        for option in normalized:
            key = _store_option_key(option)
            existing = by_key.get(key)
            if existing:
                option["created_at"] = existing.get("created_at") or option["created_at"]
                existing.clear()
                existing.update(option)
                saved.append(dict(existing))
                continue
            options.append(option)
            by_key[key] = option
            saved.append(dict(option))
        if normalized:
            save(data)
    return {"ok": True, "items": saved, "saved_count": len(saved)}


_AUTOMATION_READINESS_MUTABLE_STATUSES = {"ready", "needs_manual", "needs_regeneration"}

# 可以原地重排（自动化列表的「再次尝试」）的终止状态：任务已停止、且没有取得保存
# 证据，所以重跑不会产生重复草稿。needs_confirmation（可能已保存）与 saved 一律排除。
AUTOMATION_RETRYABLE_STATUSES = frozenset({"blocked", "failed", "needs_manual", "needs_regeneration"})
_ACTIVE_AUTOMATION_STATUSES = frozenset({"ready", "claimed", "running", "needs_confirmation"})


def _sync_automation_job_readiness(
    job: dict[str, Any],
    listing_copy: dict[str, Any],
    readiness: dict[str, Any],
    *,
    now: str,
) -> None:
    """Refresh task metadata without rewriting an in-flight or terminal state.

    Readiness is authoritative only before a task has been claimed. Once a
    worker has claimed a task, or a terminal outcome has been recorded, a
    listing/template edit must not make that task claimable again or erase its
    lifecycle result.
    """

    job["store_name"] = listing_copy.get("store_name") or ""
    job["platform"] = listing_copy.get("platform") or "shopee"
    job["country"] = listing_copy.get("country") or ""
    job["profile_snapshot_hash"] = listing_copy.get("profile_snapshot_hash") or ""
    if str(job.get("status") or "").strip().lower() in _AUTOMATION_READINESS_MUTABLE_STATUSES:
        job["status"] = readiness["status"]
        job["missing_fields"] = readiness["missing_fields"]
    job["updated_at"] = now


def _sync_default_sku_to_store_copies(
    data: dict[str, Any],
    template: dict[str, Any],
    *,
    now: str,
    regenerate_rows: bool,
) -> None:
    """把模板的默认 SKU(白T恤) 配置同步到已有**本土**副本。

    模板是这两项的唯一来源：默认 SKU 配置本身，以及 SKU 编码规则（去括号）。
    ``regenerate_rows`` 为真时外科式重建默认 SKU 的 SKU/价格行——其他颜色的行
    （含操作员手工改价）保持不动；为假时只刷新 SKU 编码，不碰价格。

    跨境模板/副本一律跳过，跨境链路行为与改动前完全一致。
    """
    if str(template.get("listing_mode") or "site").strip().lower() == "cnsc_global":
        return
    template_id = str(template.get("id") or "")
    if not template_id:
        return
    bases = {str(item.get("id") or ""): item for item in data.get("base_products", [])}
    for listing_copy in data.get("store_listing_copies", []):
        if str(listing_copy.get("template_id") or "") != template_id:
            continue
        if str(listing_copy.get("listing_mode") or "site").strip().lower() == "cnsc_global":
            continue
        listing_copy["default_sku"] = deepcopy(template.get("default_sku") or _normalize_default_sku(None))
        listing_copy["sku_strip_parentheses"] = template.get("sku_strip_parentheses") is not False
        copy_id = str(listing_copy.get("id") or "")
        base = bases.get(str(listing_copy.get("base_product_id") or ""))
        skus = [item for item in data.get("listing_skus", []) if item.get("listing_copy_id") == copy_id]
        prices = [item for item in data.get("listing_prices", []) if item.get("listing_copy_id") == copy_id]
        if regenerate_rows and base:
            stale_skus = [row for row in skus if str(row.get("sku_source") or "") == "default_sku"]
            stale_ids = {str(row.get("id") or "") for row in stale_skus}
            if stale_skus:
                data["listing_skus"] = [
                    row for row in data.get("listing_skus", [])
                    if str(row.get("id") or "") not in stale_ids
                ]
                data["listing_prices"] = [
                    row for row in data.get("listing_prices", [])
                    if str(row.get("sku_id") or "") not in stale_ids
                ]
            new_rows = _default_sku_rows(
                base, template, listing_copy, _listing_sizes(base, template), now=now
            )
            if new_rows:
                new_prices = _generate_listing_prices(
                    template, listing_copy, new_rows, base_price_tiers=base.get("price_tiers") or []
                )
                data.setdefault("listing_skus", []).extend(new_rows)
                data.setdefault("listing_prices", []).extend(new_prices)
                skus = [*[row for row in skus if str(row.get("id") or "") not in stale_ids], *new_rows]
                prices = [*[row for row in prices if str(row.get("sku_id") or "") not in stale_ids], *new_prices]
        else:
            _sync_listing_sku_codes(skus, prices, base or listing_copy, template, now=now)
        listing_copy["updated_at"] = now
        readiness = _check_automation_readiness(listing_copy, skus=skus, prices=prices)
        _apply_automation_warning_policy(readiness)
        for job in data.get("automation_jobs", []):
            if job.get("listing_copy_id") != copy_id:
                continue
            _sync_automation_job_readiness(job, listing_copy, readiness, now=now)


def _price_pair_matches(value: Any, expected: Any) -> bool:
    left = _float_or_empty(value)
    right = _float_or_empty(expected)
    if left == "" or right == "":
        return left == right
    return abs(float(left) - float(right)) < 0.005


def _sync_site_template_prices_to_store_copies(
    data: dict[str, Any],
    previous_template: dict[str, Any],
    template: dict[str, Any],
    *,
    now: str,
) -> None:
    """把模板「交易信息」的原价/六折售价改动同步到已有**本土**副本。

    只改写"当前值仍等于旧模板算出来的价"的行：操作员在副本价格表里手工改过的
    行保持不动。默认 SKU(白T恤) 有自己的价格来源、跨境副本走价目表，两者都跳过。
    """
    if str(template.get("listing_mode") or "site").strip().lower() == "cnsc_global":
        return
    template_id = str(template.get("id") or "")
    if not template_id:
        return
    skus_by_id = {str(row.get("id") or ""): row for row in data.get("listing_skus", [])}
    for listing_copy in data.get("store_listing_copies", []):
        if str(listing_copy.get("template_id") or "") != template_id:
            continue
        if str(listing_copy.get("listing_mode") or "site").strip().lower() == "cnsc_global":
            continue
        copy_id = str(listing_copy.get("id") or "")
        changed = False
        for price in data.get("listing_prices", []):
            if str(price.get("listing_copy_id") or "") != copy_id:
                continue
            sku = skus_by_id.get(str(price.get("sku_id") or "")) or {}
            if str(sku.get("sku_source") or "") == "default_sku":
                continue
            previous = _site_row_prices(previous_template, sku.get("size"))
            updated = _site_row_prices(template, sku.get("size"))
            if previous == updated:
                continue
            if not (_price_pair_matches(price.get("normal_price"), previous[0]) and _price_pair_matches(price.get("sale_price"), previous[1])):
                continue
            price["normal_price"] = updated[0]
            price["sale_price"] = updated[1]
            price["updated_at"] = now
            changed = True
        if not changed:
            continue
        listing_copy["updated_at"] = now
        skus = [row for row in data.get("listing_skus", []) if str(row.get("listing_copy_id") or "") == copy_id]
        prices = [row for row in data.get("listing_prices", []) if str(row.get("listing_copy_id") or "") == copy_id]
        readiness = _check_automation_readiness(listing_copy, skus=skus, prices=prices)
        _apply_automation_warning_policy(readiness)
        for job in data.get("automation_jobs", []):
            if job.get("listing_copy_id") != copy_id:
                continue
            _sync_automation_job_readiness(job, listing_copy, readiness, now=now)


def update_store_template(template_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    template_id = str(template_id or "").strip()
    if not template_id:
        return None
    now = utc_now()
    refresh_profile_snapshot = bool(payload.get("refresh_profile_snapshot"))
    sync_fields = [
        "store_name",
        "platform",
        "country",
        "currency",
        "category_profile_id",
        "category_profile_version_id",
        "product_line",
        "marketplace_category_path",
        "mabang_store_id",
        "mabang_category_id",
        "mabang_discount_id",
        "discount_id",
        "discount_label",
        "category_attributes",
        "attribute_values",
        "variation",
        "default_size_chart_path",
        "default_video_id",
        "variation_rule",
        "sku_template",
        "listing_mode",
        "cnsc_store",
        "target_site_stores",
        "site_price_order",
        "default_site_discount_percent",
        "price_tiers",
        "warehouse_stock_defaults",
    ]
    price_sync_fields = [
        "store_name",
        "platform",
        "country",
        "currency",
        "normal_price",
        "sale_price",
        "mabang_discount_id",
        "discount_id",
        "discount_label",
        "listing_mode",
        "default_site_discount_percent",
        "price_tiers",
        "warehouse_stock_defaults",
    ]
    # 保存模板只动主库（模板）+ 已拆分的副本/SKU/价格/基础商品 + 任务就绪度：
    # 不解析 13MB 类目、17.7MB 执行快照。实测整库路径 894ms → 约 320ms。
    with locked_store(split_keys=("automation_jobs", "store_listing_copies", "listing_skus", "listing_prices", "base_products")) as (data, save):
        for template in data.get("store_templates", []):
            if template.get("id") != template_id:
                continue
            normalize_payload = {**template, **payload, "id": template_id}
            # 换店铺时账号必须跟着新店铺走：沿用旧账号会把模板留在原来的账号下
            # （显式传了 account_id 才以传入的为准）。
            next_store_id = str(payload.get("mabang_store_id") or "").strip()
            if (
                next_store_id
                and next_store_id != str(template.get("mabang_store_id") or "").strip()
                and not str(payload.get("account_id") or "").strip()
            ):
                normalize_payload["account_id"] = ""
            if "category_profile_id" in payload:
                profile_owned_fields = {
                    "country",
                    "currency",
                    "product_line",
                    "marketplace_category_path",
                    "mabang_category_id",
                    "mabang_discount_id",
                    "discount_id",
                    "discount_label",
                    "normal_price",
                    "sale_price",
                    "sizes",
                    "title_suffix",
                    "description",
                    "category_attributes",
                    "attribute_values",
                    "variation",
                    "default_size_chart_path",
                    "template_product_id",
                }
                for field in profile_owned_fields:
                    if field not in payload:
                        normalize_payload[field] = ""
            updated = _normalize_store_template_payload(normalize_payload, now=template.get("created_at") or now)
            # 账号隔离：换店铺时账号跟着新店铺走；老模板没记账号就按店铺/默认账号兜底。
            updated["account_id"] = _resolve_store_template_account(
                data, updated, previous=template, fallback_default=True
            )
            updated["created_at"] = template.get("created_at") or now
            updated["updated_at"] = now
            updated["version"] = int(template.get("version") or 1) + 1
            updated["current_version_id"] = str(uuid.uuid4())
            _validate_cnsc_store_template(updated, allow_missing_activity=True)
            if updated.get("listing_mode") == "cnsc_global":
                updated["missing_configuration"] = _cnsc_configuration_problems(updated)
                updated["status"] = "needs_configuration" if updated["missing_configuration"] else "active"
            _validate_store_template_category_binding(data, updated)
            # 归一化后再比较：老模板的存量配置缺少后来新增的子字段（如
            # size_price_overrides），直接比较会把"没改设置"误判成"配置变了"，
            # 从而无谓地重建副本的默认 SKU 行。
            previous_default_sku = _normalize_default_sku(template.get("default_sku"))
            previous_sku_template = str(template.get("sku_template") or "")
            # 价格同步要拿"改之前"的原价/六折售价算旧值，必须在 template 就地更新前抓下来。
            previous_price_fields = {
                "normal_price": _float_or_empty(template.get("normal_price")),
                "sale_price": _float_or_empty(template.get("sale_price")),
                "size_price_overrides": _normalize_size_price_overrides(template.get("size_price_overrides")),
            }
            template.clear()
            template.update(updated)
            data.setdefault("store_template_versions", []).append(
                _store_template_version(updated, now=now)
            )
            # 默认 SKU(白T恤) / SKU 编码规则**真正变化**时，同步到该模板下已有的本土
            # 副本，否则存量副本要等到重新复制才会生效。跨境模板与跨境副本一律跳过。
            default_sku_changed = updated.get("default_sku") != previous_default_sku
            sku_rule_changed = str(updated.get("sku_template") or "") != previous_sku_template
            if updated.get("listing_mode") != "cnsc_global" and (default_sku_changed or sku_rule_changed):
                _sync_default_sku_to_store_copies(
                    data,
                    updated,
                    now=now,
                    regenerate_rows=default_sku_changed,
                )
            # 「交易信息」的原价/六折售价改动同样要落到已有本土副本的价格行上，
            # 否则自动化仍按旧价填写，除非重新复制一份。
            price_changed = (
                previous_price_fields["normal_price"] != updated.get("normal_price")
                or previous_price_fields["sale_price"] != updated.get("sale_price")
                or previous_price_fields["size_price_overrides"] != _normalize_size_price_overrides(updated.get("size_price_overrides"))
            )
            if updated.get("listing_mode") != "cnsc_global" and price_changed:
                _sync_site_template_prices_to_store_copies(
                    data,
                    {**previous_price_fields, "id": template_id},
                    updated,
                    now=now,
                )
            # 默认视频 ID 是模板-owned：模板一改就同步到该模板下全部副本
            # （旧的通用 sync_fields 块在下方 return 之后，不可达——勿往那里加字段）。
            if "default_video_id" in payload:
                for listing_copy in data.get("store_listing_copies", []):
                    if listing_copy.get("template_id") != template_id:
                        continue
                    listing_copy["default_video_id"] = str(updated.get("default_video_id") or "").strip()
                    listing_copy["updated_at"] = now
            save(data)
            return deepcopy(template)
            changed_sync_fields = {field for field in sync_fields if field in payload}
            if "category_profile_id" in payload:
                changed_sync_fields.update(sync_fields)
                changed_sync_fields.update(["normal_price", "sale_price"])
            if changed_sync_fields:
                for listing_copy in data.get("store_listing_copies", []):
                    if listing_copy.get("template_id") != template_id:
                        continue
                    effective_sync_fields = set(changed_sync_fields)
                    if listing_copy.get("category_profile_snapshot") and not refresh_profile_snapshot:
                        effective_sync_fields.difference_update(
                            {
                                "category_profile_id",
                                "category_profile_version_id",
                                "product_line",
                                "marketplace_category_path",
                                "mabang_category_id",
                                "category_attributes",
                                "attribute_values",
                                "variation",
                            }
                        )
                    changed_sync = {field: deepcopy(updated.get(field)) for field in sync_fields if field in effective_sync_fields}
                    listing_copy.update(changed_sync)
                    if refresh_profile_snapshot:
                        snapshot = _profile_snapshot_for_template(updated, data)
                        if snapshot:
                            listing_copy["category_profile_snapshot"] = snapshot
                            listing_copy["profile_snapshot_hash"] = hash_profile_snapshot(snapshot)
                    if "default_size_chart_path" in effective_sync_fields:
                        size_chart = _size_chart_fields_from_template(updated)
                        listing_copy["size_chart_image"] = size_chart["size_chart_image"]
                        listing_copy["size_chart_image_path"] = size_chart["size_chart_image_path"]
                    listing_copy["updated_at"] = now
                    skus = [item for item in data.get("listing_skus", []) if item.get("listing_copy_id") == listing_copy.get("id")]
                    prices = [item for item in data.get("listing_prices", []) if item.get("listing_copy_id") == listing_copy.get("id")]
                    if "sku_template" in effective_sync_fields or "category_profile_id" in effective_sync_fields:
                        base = next((item for item in data.get("base_products", []) if item.get("id") == listing_copy.get("base_product_id")), listing_copy)
                        _sync_listing_sku_codes(skus, prices, base, updated, now=now)
                    if any(field in effective_sync_fields for field in price_sync_fields) or "category_profile_id" in effective_sync_fields:
                        _sync_listing_prices_from_template(prices, updated, now=now)
                    readiness = _check_automation_readiness(listing_copy, skus=skus, prices=prices)
                    for job in data.get("automation_jobs", []):
                        if job.get("listing_copy_id") != listing_copy.get("id"):
                            continue
                        _sync_automation_job_readiness(job, listing_copy, readiness, now=now)
            save(data)
            return dict(template)
    return None


def list_base_products_with_summary() -> list[dict[str, Any]]:
    with _LOCK:
        data = load_store()
        base_products = [dict(item) for item in data.get("base_products", [])]
        asset_counts = Counter((asset.get("group_id") or "").strip().upper() for asset in data.get("assets", []))
        copies_by_base: dict[str, list[dict[str, Any]]] = {}
        for copy in data.get("store_listing_copies", []):
            copies_by_base.setdefault(str(copy.get("base_product_id") or ""), []).append(copy)
        jobs_by_copy: dict[str, list[dict[str, Any]]] = {}
        for job in data.get("automation_jobs", []):
            jobs_by_copy.setdefault(str(job.get("listing_copy_id") or ""), []).append(job)
    for product in base_products:
        product["main_sku"] = product.get("main_sku") or product.get("group_id") or ""
        product["asset_count"] = int(asset_counts.get((product.get("group_id") or "").strip().upper(), 0))
        product_id = str(product.get("id") or "")
        product_copies = copies_by_base.get(product_id, [])
        product["listing_copy_count"] = len(product_copies)
        product["automation_status_summary"] = _summarize_automation_for_base(product_copies, jobs_by_copy)
        product["automation_glyph"] = _derive_automation_glyph(product["automation_status_summary"])
        product["lifecycle_stage"] = _derive_lifecycle_stage(product, product_copies)
    return sorted(base_products, key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)


def _derive_lifecycle_stage(base_product: dict[str, Any], listing_copies: list[dict[str, Any]]) -> str:
    """商品维护列表的四段分类：首次导入 / 未复制 / 待执行 / 已完成。

    - 没复制过、也没编辑过 → ``imported``（首次导入）
    - 编辑过的商品一律按 ``uncopied``（未复制）：维护里的改动不会同步进已有副本，
      所以复制之后再编辑等于副本过期，要重新复制一次
    - 有副本、自动化还没全部保存 → ``pending``（待执行）
    - 所有副本都已保存（保存任务会级联把商品置为 completed）→ ``completed``（已完成）
    """

    edited_at = str(base_product.get("last_edited_at") or "").strip()
    if not listing_copies:
        return "uncopied" if edited_at else "imported"
    last_copy_at = max(str(copy.get("created_at") or copy.get("updated_at") or "") for copy in listing_copies)
    if edited_at and edited_at > last_copy_at:
        return "uncopied"
    if str(base_product.get("status") or "").strip().lower() == "completed":
        return "completed"
    return "pending"


_AUTOMATION_SAVED_STATUSES = {"saved", "saved_with_warnings"}
_AUTOMATION_FAILED_STATUSES = {"failed", "blocked"}
_AUTOMATION_ATTENTION_STATUSES = {"needs_confirmation", "needs_manual", "needs_regeneration"}
_AUTOMATION_RUNNING_STATUSES = {"claimed", "running", "ready"}


def _latest_job_for_copy(copy_id: str, jobs_by_copy: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    jobs = jobs_by_copy.get(copy_id) or []
    if not jobs:
        return None
    return max(jobs, key=lambda job: str(job.get("updated_at") or job.get("created_at") or ""))


def _summarize_automation_for_base(
    listing_copies: list[dict[str, Any]],
    jobs_by_copy: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    summary = {"done": 0, "running": 0, "failed": 0, "attention": 0, "not_started": 0, "total_copies": len(listing_copies)}
    for copy in listing_copies:
        copy_id = str(copy.get("id") or "")
        job = _latest_job_for_copy(copy_id, jobs_by_copy)
        if job is None:
            summary["not_started"] += 1
            continue
        status = str(job.get("status") or "").lower()
        if status in _AUTOMATION_SAVED_STATUSES:
            summary["done"] += 1
        elif status in _AUTOMATION_FAILED_STATUSES:
            summary["failed"] += 1
        elif status in _AUTOMATION_ATTENTION_STATUSES:
            summary["attention"] += 1
        elif status in _AUTOMATION_RUNNING_STATUSES:
            summary["running"] += 1
        else:
            summary["not_started"] += 1
    return summary


def _derive_automation_glyph(summary: dict[str, int]) -> str:
    total = int(summary.get("total_copies") or 0)
    done = int(summary.get("done") or 0)
    failed = int(summary.get("failed") or 0)
    running = int(summary.get("running") or 0)
    attention = int(summary.get("attention") or 0)
    if total == 0:
        return "idle"
    if failed > 0:
        return "alert"
    if running > 0 or attention > 0:
        return "watch"
    if done == total and total > 0:
        return "done"
    return "idle"


def _delete_base_product_from_store(data: dict, base_product_id: str) -> dict[str, Any] | None:
    """Remove one base product plus its regenerable listing data inside an already
    locked store. Terminal automation jobs are kept for audit; non-terminal jobs are
    dropped alongside the product. Returns per-product delete stats or None when the
    product is missing (the caller decides whether that is an error)."""
    terminal_job_statuses = {"saved", "saved_with_warnings", "failed", "blocked", "needs_confirmation", "canceled"}
    base_product = next((item for item in data.get("base_products", []) if item.get("id") == base_product_id), None)
    if not base_product:
        return None
    listing_copy_ids = {
        str(item.get("id") or "")
        for item in data.get("store_listing_copies", [])
        if item.get("base_product_id") == base_product_id
    }
    linked_skus = [item for item in data.get("listing_skus", []) if item.get("listing_copy_id") in listing_copy_ids]
    linked_prices = [item for item in data.get("listing_prices", []) if item.get("listing_copy_id") in listing_copy_ids]
    linked_jobs = [item for item in data.get("automation_jobs", []) if item.get("listing_copy_id") in listing_copy_ids]
    retained_jobs = [item for item in linked_jobs if str(item.get("status") or "").lower() in terminal_job_statuses]
    retained_job_ids = {str(item.get("id") or "") for item in retained_jobs}
    deleted_job_ids = {str(item.get("id") or "") for item in linked_jobs if str(item.get("id") or "") not in retained_job_ids}

    data["base_products"] = [item for item in data.get("base_products", []) if item.get("id") != base_product_id]
    data["store_listing_copies"] = [item for item in data.get("store_listing_copies", []) if item.get("id") not in listing_copy_ids]
    data["listing_skus"] = [item for item in data.get("listing_skus", []) if item.get("listing_copy_id") not in listing_copy_ids]
    data["listing_prices"] = [item for item in data.get("listing_prices", []) if item.get("listing_copy_id") not in listing_copy_ids]
    data["automation_jobs"] = [item for item in data.get("automation_jobs", []) if item.get("id") not in deleted_job_ids]
    # 任务没了，执行快照也要一起清掉（不然独立集合里会堆孤儿）。
    snapshots = data.get("automation_job_snapshots")
    if isinstance(snapshots, dict):
        for job_id in deleted_job_ids:
            snapshots.pop(job_id, None)
    return {
        "id": base_product_id,
        "deleted_listing_copy_count": len(listing_copy_ids),
        "deleted_listing_sku_count": len(linked_skus),
        "deleted_listing_price_count": len(linked_prices),
        "deleted_automation_job_count": len(deleted_job_ids),
        "retained_automation_job_count": len(retained_jobs),
    }


def delete_base_product(base_product_id: str) -> dict[str, Any] | None:
    """Delete a base product and regenerable listing data while keeping terminal job audit records."""
    base_product_id = str(base_product_id or "").strip()
    if not base_product_id:
        return None
    with locked_store(split_keys=("automation_jobs", "automation_job_snapshots", "base_products", "store_listing_copies", "listing_skus", "listing_prices")) as (data, save):
        result = _delete_base_product_from_store(data, base_product_id)
        if result is None:
            return None
        save(data)
        return result


def delete_base_products(base_product_ids: list[str]) -> dict[str, Any]:
    """Delete multiple base products in one locked write. Missing ids are reported
    per-product rather than aborting the batch, so a stale selection can never block
    the rest. Returns aggregate counts plus per-product results keyed by requested id."""
    requested = [str(item or "").strip() for item in (base_product_ids or []) if str(item or "").strip()]
    with locked_store(split_keys=("automation_jobs", "automation_job_snapshots", "base_products", "store_listing_copies", "listing_skus", "listing_prices")) as (data, save):
        results: dict[str, Any] = {}
        for base_product_id in requested:
            result = _delete_base_product_from_store(data, base_product_id)
            if result is not None:
                results[base_product_id] = result
        if results:
            save(data)
        deleted = list(results.values())
        return {
            "ok": True,
            "deleted_count": len(deleted),
            "requested_count": len(requested),
            "deleted_listing_copy_count": sum(item.get("deleted_listing_copy_count") or 0 for item in deleted),
            "deleted_listing_sku_count": sum(item.get("deleted_listing_sku_count") or 0 for item in deleted),
            "deleted_listing_price_count": sum(item.get("deleted_listing_price_count") or 0 for item in deleted),
            "deleted_automation_job_count": sum(item.get("deleted_automation_job_count") or 0 for item in deleted),
            "retained_automation_job_count": sum(item.get("retained_automation_job_count") or 0 for item in deleted),
            "deleted_ids": [item.get("id") for item in deleted],
            "not_found_ids": [item for item in requested if item not in results],
            "results": deleted,
        }


# 副本上有这些状态的任务时不能删：worker 正拿着它跑，删掉就是把执行状态抽走。
_LIVE_LISTING_COPY_JOB_STATUSES = frozenset({"claimed", "running"})
# 终态任务留作审计，只有没跑完的才跟着副本一起清掉。
_TERMINAL_LISTING_COPY_JOB_STATUSES = frozenset(
    {"saved", "saved_with_warnings", "failed", "blocked", "needs_confirmation", "canceled"}
)


def _purge_store_listing_copy(data: dict[str, Any], listing_copy_id: str) -> dict[str, Any]:
    """从整库快照里删掉一个副本及其可再生成数据（SKU / 价格 / 未完成任务 / 任务快照）。

    只改内存里的 ``data``，不落盘；调用方决定 save。返回 ``skipped`` 说明没动它的原因：
    ``not_found``（副本不存在）、``running_job``（有 claimed/running 的任务）。"""
    copy = next((item for item in data.get("store_listing_copies", []) if item.get("id") == listing_copy_id), None)
    if not copy:
        return {"id": listing_copy_id, "skipped": "not_found"}
    linked_jobs = [item for item in data.get("automation_jobs", []) if item.get("listing_copy_id") == listing_copy_id]
    if any(str(job.get("status") or "").lower() in _LIVE_LISTING_COPY_JOB_STATUSES for job in linked_jobs):
        return {"id": listing_copy_id, "skipped": "running_job", "linked_automation_job_count": len(linked_jobs)}
    linked_skus = [item for item in data.get("listing_skus", []) if item.get("listing_copy_id") == listing_copy_id]
    linked_prices = [item for item in data.get("listing_prices", []) if item.get("listing_copy_id") == listing_copy_id]
    retained_jobs = [
        item for item in linked_jobs
        if str(item.get("status") or "").lower() in _TERMINAL_LISTING_COPY_JOB_STATUSES
    ]
    retained_job_ids = {str(item.get("id") or "") for item in retained_jobs}
    deleted_job_ids = {str(item.get("id") or "") for item in linked_jobs if str(item.get("id") or "") not in retained_job_ids}

    data["store_listing_copies"] = [item for item in data.get("store_listing_copies", []) if item.get("id") != listing_copy_id]
    data["listing_skus"] = [item for item in data.get("listing_skus", []) if item.get("listing_copy_id") != listing_copy_id]
    data["listing_prices"] = [item for item in data.get("listing_prices", []) if item.get("listing_copy_id") != listing_copy_id]
    data["automation_jobs"] = [item for item in data.get("automation_jobs", []) if item.get("id") not in deleted_job_ids]
    snapshots = data.get("automation_job_snapshots")
    if isinstance(snapshots, dict):
        for job_id in deleted_job_ids:
            snapshots.pop(job_id, None)
    return {
        "id": listing_copy_id,
        "skipped": "",
        "base_product_id": str(copy.get("base_product_id") or ""),
        "store_name": str(copy.get("store_name") or ""),
        "deleted_listing_sku_count": len(linked_skus),
        "deleted_listing_price_count": len(linked_prices),
        "deleted_automation_job_count": len(deleted_job_ids),
        "retained_automation_job_count": len(retained_jobs),
    }


def delete_store_listing_copy(listing_copy_id: str) -> dict[str, Any] | None:
    """Delete one store listing copy plus its regenerable data. Terminal automation
    jobs are kept for audit; non-terminal jobs are dropped alongside the copy. Refuses
    if a job is currently claimed or running to avoid pulling state out from under a
    live worker."""
    listing_copy_id = str(listing_copy_id or "").strip()
    if not listing_copy_id:
        return None
    with locked_store(split_keys=("automation_jobs", "automation_job_snapshots", "store_listing_copies", "listing_skus", "listing_prices")) as (data, save):
        result = _purge_store_listing_copy(data, listing_copy_id)
        if result.get("skipped") == "not_found":
            return None
        if result.get("skipped") == "running_job":
            raise ValueError("有正在执行的自动化任务，请先取消后再删除副本")
        save(data)
        return {key: value for key, value in result.items() if key != "skipped"}


def delete_store_listing_copies(listing_copy_ids: list[str]) -> dict[str, Any]:
    """批量删除副本：整库只加载/保存一次（逐条删每回都要重写 51MB 主库，现场就是卡）。

    正在执行（claimed/running）或已不存在的副本按条跳过并回报，不阻断同批其它副本。"""
    requested = [str(item or "").strip() for item in (listing_copy_ids or []) if str(item or "").strip()]
    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with locked_store(split_keys=("automation_jobs", "automation_job_snapshots", "store_listing_copies", "listing_skus", "listing_prices")) as (data, save):
        for listing_copy_id in requested:
            outcome = _purge_store_listing_copy(data, listing_copy_id)
            if outcome.get("skipped"):
                skipped.append({"id": listing_copy_id, "reason": outcome["skipped"]})
                continue
            deleted.append({key: value for key, value in outcome.items() if key != "skipped"})
        if deleted:
            save(data)
    return {
        "ok": True,
        "requested_count": len(requested),
        "deleted_count": len(deleted),
        "deleted_listing_sku_count": sum(item.get("deleted_listing_sku_count") or 0 for item in deleted),
        "deleted_listing_price_count": sum(item.get("deleted_listing_price_count") or 0 for item in deleted),
        "deleted_automation_job_count": sum(item.get("deleted_automation_job_count") or 0 for item in deleted),
        "retained_automation_job_count": sum(item.get("retained_automation_job_count") or 0 for item in deleted),
        "deleted_ids": [item.get("id") for item in deleted],
        "not_found_ids": [item["id"] for item in skipped if item["reason"] == "not_found"],
        "running_job_ids": [item["id"] for item in skipped if item["reason"] == "running_job"],
        "skipped": skipped,
        "results": deleted,
    }


def create_base_product_from_folder(payload: dict[str, Any]) -> dict[str, Any] | None:
    from workers.asset_scanner.scanner import scan_asset_root

    root_path = Path(payload.get("root_path") or payload.get("asset_root") or "").expanduser()
    category = str(payload.get("category") or payload.get("product_line") or "").strip()
    result = scan_asset_root(root_path, category_override=category, mode="recursive")
    assets = [dict(asset) for asset in result.get("assets") or [] if isinstance(asset, dict)]
    if not assets:
        return None

    now = utc_now()
    group_id = _most_common([asset.get("group_id") for asset in assets]) or _derive_group_from_path(str(root_path))
    # Folder name wins: the operator names folders by SKU (AL0301男语言月 → AL0301),
    # so a derivable folder SKU is the main SKU even when auto-assign is enabled.
    # Auto-assign only kicks in as a fallback when the folder name carries no
    # SKU-like token (pure Chinese names, year-first names, etc.).
    derived = _derive_main_sku_from_folder_name(str(root_path))
    main_sku = str(payload.get("main_sku") or "").strip() or derived or group_id
    if _auto_assign_sku_enabled(payload) and not derived:
        assigned = _allocate_auto_main_sku()
        if assigned:
            main_sku = assigned
    if not group_id:
        group_id = main_sku
    main = _pick_main_asset(assets)
    size_chart = _pick_size_chart_asset(assets)
    video_asset = next((asset for asset in assets if str(asset.get("asset_type") or "").lower() == "video"), None)
    video = {}
    if video_asset and video_asset.get("file_path"):
        video = {
            "asset_id": str(video_asset.get("id") or ""),
            "file_name": str(video_asset.get("file_name") or ""),
            "path": str(video_asset.get("file_path") or ""),
            **validate_video(Path(str(video_asset.get("file_path")))),
            "validated_at": now,
        }
    product_images = _default_product_images(assets, main)
    recognized_colors = sorted({str(asset.get("sku_option") or "").strip() for asset in assets if str(asset.get("sku_option") or "").strip()})
    variant_image_map = _default_variant_image_map(assets)
    base_name = _derive_product_name_from_folder_name(str(root_path), main_sku) or _derive_product_name(group_id, assets)
    counts = Counter(asset.get("asset_type") or "unknown" for asset in assets)
    status = "base_ready" if main and recognized_colors else "needs_check"
    product_category = _normalize_product_category(payload.get("product_category"))
    base_product = {
        "id": str(uuid.uuid4()),
        "listing_kind": "base_product",
        "category": category or _most_common([asset.get("category") for asset in assets]),
        "product_category": product_category,
        "attribute_values": {},
        "group_id": group_id,
        "main_sku": main_sku,
        "product_name_cn": base_name,
        "base_name": base_name,
        "asset_root": str(root_path),
        "main_image": main.get("file_name") if main else "",
        "product_images": product_images,
        "size_chart_image": size_chart.get("file_name") if size_chart else "",
        "video": video,
        "recognized_colors": recognized_colors,
        "recognized_sizes": [],
        "selected_sizes": [],
        "variant_image_map": variant_image_map,
        # 导入时默认联动：颜色顺序跟随「产品图顺序」（操作员可以在颜色图区关掉）。
        "color_order_follows_images": True,
        "variant_count": int(counts.get("variant", 0)),
        "price_tiers": [],
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    with locked_store() as (data, save):
        data.setdefault("assets", [])
        data.setdefault("base_products", [])
        existing_asset_ids = {asset.get("id") for asset in assets if asset.get("id")}
        data["assets"] = [asset for asset in data["assets"] if asset.get("id") not in existing_asset_ids] + assets
        data["base_products"] = [
            item
            for item in data["base_products"]
            if (item.get("group_id") or "").strip().upper() != group_id.upper()
        ]
        data["base_products"].append(base_product)
        save(data)
    return base_product


def _editable_snapshot(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


# 改了也不算「人工编辑过」的字段：它们只是维护界面上的显示/交互偏好，不影响副本内容。
# 「颜色图顺序跟随产品图顺序」开关就属于这类——单为它点一次保存，不该把商品从
# 「已完成」打回「未复制 / 待执行」。
_EDIT_DETECTION_EXEMPT_FIELDS = frozenset({"color_order_follows_images"})


def update_base_product(
    base_product_id: str,
    payload: dict[str, Any],
    *,
    mark_edited: bool = False,
) -> dict[str, Any] | None:
    """``mark_edited`` 只在操作员保存维护内容的路径上传 True：内容真的变了才记
    ``last_edited_at``（商品维护列表用它区分「首次导入」和「未复制」）。导入时的
    AI 识别复用同一个函数，但不算人工编辑，所以默认不记。"""

    base_product_id = str(base_product_id or "").strip()
    if not base_product_id:
        return None
    now = utc_now()
    editable_fields = {
        "main_image",
        "product_images",
        "size_chart_image",
        "variant_image_map",
        "recognized_colors",
        # 「颜色图顺序跟随产品图顺序」开关（商品维护 → 颜色图）。开关只影响前端排序
        # 交互，颜色顺序最终还是落到 recognized_colors；旧数据没有这个字段时默认打开。
        "color_order_follows_images",
        "recognized_sizes",
        "selected_sizes",
        "recognized_attributes",
        "product_name_cn",
        "base_name",
        "main_sku",
        "listing_title",
        "description",
        "status",
        "price_tiers",
        "product_category",
        # 类目必填属性的值（商品维度）：复制到店铺模板/副本时随商品类目带上
        # （见 _build_store_listing_copy），执行器据此填马帮表单。
        "attribute_values",
        "ai_generation_error",
        "title_skill_id",
        "title_category",
        "title_audience",
    }
    # 保存基础商品快路径：base_products（0.9MB）+ title_skills（6KB）都是拆分集合，
    # load_main=False 连 55MB 主库都不解析、不重写（实测 1.3~2.2s → ~40ms）。
    # 副本/SKU/价格的派生同步早已不在这条路径上（见下方历史注释），需要时由
    # update_store_listing_copy / 复制链路自己算。
    with locked_store(split_keys=("base_products", "title_skills"), load_main=False) as (data, save):
        for base in data.get("base_products", []):
            if base.get("id") != base_product_id:
                continue
            if "title_skill_id" in payload:
                selected_rule = str(payload.get("title_skill_id") or "").strip()
                if selected_rule and not any(rule.get("id") == selected_rule for rule in data.get("title_skills", [])):
                    raise ValueError("指定的标题规则不存在")
            if "title_category" in payload and payload.get("title_category"):
                _title_enum(payload["title_category"], TITLE_CATEGORIES, "标题品类")
            if "title_audience" in payload and payload.get("title_audience"):
                _title_enum(payload["title_audience"], TITLE_AUDIENCES, "标题人群")
            before = {
                key: _editable_snapshot(base.get(key))
                for key in editable_fields
                if key in payload and key not in _EDIT_DETECTION_EXEMPT_FIELDS
            }
            for key in editable_fields:
                if key not in payload:
                    continue
                if key == "product_images":
                    base[key] = _normalize_product_images(payload.get(key))
                elif key == "variant_image_map":
                    base[key] = _normalize_variant_image_map(payload.get(key))
                elif key == "recognized_colors":
                    base[key] = _normalize_recognized_colors(payload.get(key))
                elif key == "color_order_follows_images":
                    base[key] = _normalize_flag(payload.get(key), default=True)
                elif key == "recognized_sizes":
                    base[key] = _normalize_recognized_sizes(payload.get(key))
                elif key == "selected_sizes":
                    base[key] = _normalize_recognized_sizes(payload.get(key))
                elif key == "recognized_attributes":
                    base[key] = _normalize_recognized_attributes(payload.get(key))
                elif key == "price_tiers":
                    base[key] = _normalize_base_price_tiers(payload.get(key))
                elif key == "product_category":
                    base[key] = _normalize_product_category(payload.get(key))
                elif key == "attribute_values":
                    raw = payload.get(key)
                    base[key] = (
                        {
                            str(field_id): value
                            for field_id, value in raw.items()
                            if str(field_id or "").strip() and value not in ("", None)
                        }
                        if isinstance(raw, dict)
                        else {}
                    )
                else:
                    base[key] = str(payload.get(key) or "").strip()
            if "variant_image_map" in payload or "recognized_colors" in payload:
                base["variant_image_map"] = _variant_image_map_for_colors(
                    base.get("variant_image_map") or {},
                    base.get("recognized_colors") or [],
                )
            if not base.get("main_image") and base.get("product_images"):
                base["main_image"] = base["product_images"][0]
            if mark_edited and any(_editable_snapshot(base.get(key)) != snapshot for key, snapshot in before.items()):
                base["last_edited_at"] = now
            base["updated_at"] = now
            save(data)
            return dict(base)
    return None


def save_listing_copy_video(listing_copy_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    return _save_entity_video("store_listing_copies", listing_copy_id, payload)


def save_base_product_video(base_product_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    return _save_entity_video("base_products", base_product_id, payload)


MANUAL_UPLOAD_ORIGIN = "manual_upload"


def add_base_product_product_image(base_product_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """操作员自上传商品图：落盘 → 注册素材 → 追加到该商品的商品图列表。

    上传的图片不在商品素材目录（``asset_root``）里，素材记录用
    ``origin="manual_upload"`` 标注，取素材时和本次导入目录一起带出，
    这样商品图选择器、颜色图、导出都能用上它。素材角色按商品图处理（``main``），
    图片顺序不变——新图追加在末尾，主图仍是原来的第一张。"""
    from services.api.size_chart_uploads import store_image_upload

    base_product_id = str(base_product_id or "").strip()
    if not base_product_id:
        return None
    # 上传商品图只动 base_products + assets（两者都是拆分集合）：主库不读也不写。
    with locked_store(split_keys=("base_products", "assets")) as (data, save):
        base = next((row for row in data.get("base_products", []) if row.get("id") == base_product_id), None)
        if not base:
            return None
        group_id = str(base.get("group_id") or "").strip()
        info, path = store_image_upload(payload, storage_dir=settings.STORAGE_DIR, kind="product_image")
        taken = {
            str(asset.get("file_name") or "").strip().lower()
            for asset in data.get("assets", [])
            if str(asset.get("group_id") or "").strip().upper() == group_id.upper()
        }
        file_name = _unique_asset_file_name(str(payload.get("file_name") or info.get("file_name") or ""), path.suffix, taken)
        now = utc_now()
        stat = path.stat()
        width, height = _asset_image_dimensions(path)
        asset = {
            "id": str(uuid.uuid4()),
            "category": str(base.get("category") or ""),
            "group_id": group_id,
            "brand": "",
            "sku_option": "",
            "asset_type": "main",
            "group_root": str(base.get("asset_root") or ""),
            "file_path": str(path),
            "file_name": file_name,
            "file_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            "width": width,
            "height": height,
            "size_bytes": stat.st_size,
            "status": "ready",
            "origin": MANUAL_UPLOAD_ORIGIN,
            "created_at": now,
        }
        data.setdefault("assets", []).append(asset)
        base["product_images"] = _normalize_product_images([*(base.get("product_images") or []), file_name])
        if not str(base.get("main_image") or "").strip() and base["product_images"]:
            base["main_image"] = base["product_images"][0]
        base["updated_at"] = now
        save(data)
        return {"product": deepcopy(base), "asset": deepcopy(asset)}


def _unique_asset_file_name(file_name: str, suffix: str, taken: set[str]) -> str:
    """上传文件在商品内重名时补 ``-2``/``-3`` 后缀，避免同一素材名指向两张图。"""
    name = Path(str(file_name or "")).name.strip() or f"product-image{suffix.lower()}"
    stem = Path(name).stem or "product-image"
    extension = Path(name).suffix or suffix.lower()
    candidate = f"{stem}{extension}"
    index = 1
    while candidate.lower() in taken:
        index += 1
        candidate = f"{stem}-{index}{extension}"
    return candidate


def _asset_image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """素材库里的尺寸字段：Pillow 可选，读不出来就留空。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow 是可选依赖
        return None, None
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:  # noqa: BLE001 - 坏图不影响登记素材
        return None, None


def _save_entity_video(collection: str, entity_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    file_name = Path(str(payload.get("file_name") or "")).name
    content_type = str(payload.get("content_type") or "").strip().lower()
    if Path(file_name).suffix.lower() != ".mp4" or content_type not in {"video/mp4", "application/mp4"}:
        raise ValueError("仅支持 MP4 视频")
    try:
        content = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("视频内容无效") from exc
    if len(content) > 30 * 1024 * 1024:
        raise ValueError("视频文件大小不能超过 30 MB")

    directory = settings.STORAGE_DIR / "videos" / str(entity_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.mp4"
    path.write_bytes(content)
    validation = validate_video(path)
    video = {
        "asset_id": str(uuid.uuid4()),
        "file_name": file_name,
        "path": str(path),
        **validation,
        "validated_at": utc_now(),
    }
    # 视频只挂在实体上（base_products / store_listing_copies 都是拆分集合）。
    with locked_store(split_keys=(str(collection),)) as (data, save):
        item = next((row for row in data.get(collection, []) if row.get("id") == entity_id), None)
        if not item:
            path.unlink(missing_ok=True)
            return None
        item["video"] = video
        item["updated_at"] = utc_now()
        save(data)
        return deepcopy(item)


def remove_listing_copy_video(listing_copy_id: str) -> dict[str, Any] | None:
    with locked_store(split_keys=("store_listing_copies",)) as (data, save):
        item = next((row for row in data.get("store_listing_copies", []) if row.get("id") == listing_copy_id), None)
        if not item:
            return None
        item["video"] = {"validation_status": "missing", "validation_errors": ["video_missing"]}
        item["updated_at"] = utc_now()
        save(data)
        return deepcopy(item)


def list_store_listing_copies() -> list[dict[str, Any]]:
    with _LOCK:
        data = load_store()
        copies = [dict(item) for item in data.get("store_listing_copies", [])]
        sku_counts = Counter(sku.get("listing_copy_id") for sku in data.get("listing_skus", []))
        price_counts = Counter(price.get("listing_copy_id") for price in data.get("listing_prices", []))
    for copy in copies:
        copy["sku_count"] = int(sku_counts.get(copy.get("id") or "", 0))
        copy["price_count"] = int(price_counts.get(copy.get("id") or "", 0))
    return sorted(copies, key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)


def get_store_listing_copy_detail(listing_copy_id: str) -> dict[str, Any] | None:
    with _LOCK:
        data = load_store()
        item = next((row for row in data.get("store_listing_copies", []) if row.get("id") == listing_copy_id), None)
        if not item:
            return None
        return _store_listing_copy_detail(data, item)


def update_store_listing_copy(listing_copy_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    editable = {
        "title", "description", "description_status", "normal_price", "sale_price", "sizes",
        "stock", "weight_kg", "package_length_cm", "package_width_cm", "package_height_cm",
        "size_chart_asset_id", "size_chart_preview_url", "size_chart_image", "size_chart_image_path",
        "mabang_size_chart_template_id", "mabang_size_chart_template_name",
        # 本土副本可以单独关掉/打开「产品图最后一张放尺码表」（模板快照是默认值）。
        "size_chart_as_last_product_image",
        "description_profile_version_id", "promotion_activity_id", "mabang_discount_id", "discount_label",
        "product_images", "main_image", "attribute_values",
        # Decision B: the copy editor may edit the full CNSC configuration, not just
        # per-SKU warehouse stock. These stay permissive on save (partial progress is
        # allowed); the automation readiness gate stays fail-closed at job creation.
        # (Copy-level warehouse defaults are not edited here — per-SKU warehouse_stocks
        # flows through sku_updates below.)
        "target_site_stores", "price_tiers", "default_site_discount_percent", "cnsc_store",
        "cnsc_binding_mode",
        # 类目方案绑定（含快照）：副本的生效类目换成基础商品类目时，绑定与快照
        # 必须能跟着换（2026-09-26 修存量 FL0067 属性错配走的同一条路）。
        "category_profile_id", "category_profile_version_id", "category_profile_snapshot",
    }
    # 保存副本只动副本/SKU/价格/基础商品 + 修订集合：不解析 13MB 类目、17.7MB 执行快照。
    with locked_store(split_keys=("listing_copy_revisions", "store_listing_copies", "listing_skus", "listing_prices", "base_products")) as (data, save):
        item = next((row for row in data.get("store_listing_copies", []) if row.get("id") == listing_copy_id), None)
        if not item:
            return None
        if "cnsc_binding_mode" in payload:
            mode = str(payload.get("cnsc_binding_mode") or "").strip().lower()
            if mode not in {"colors", "sizes"}:
                raise ValueError("cnsc_binding_mode must be colors or sizes")
            payload = {**payload, "cnsc_binding_mode": mode}
        sku_updates = _validated_listing_row_updates(
            data.get("listing_skus", []),
            listing_copy_id,
            payload.get("skus"),
            row_label="SKU",
        )
        price_updates = _validated_listing_row_updates(
            data.get("listing_prices", []),
            listing_copy_id,
            payload.get("prices"),
            row_label="价格",
        )
        changed = {key: deepcopy(value) for key, value in payload.items() if key in editable}
        if "title" in changed:
            changed["listing_title"] = changed.pop("title")
        # Normalize (never raise) the CNSC config so partial editor progress can be
        # saved; the readiness gate recomputes problems from the normalized shape.
        if "target_site_stores" in changed:
            changed["target_site_stores"] = _normalize_cnsc_target_sites(changed["target_site_stores"])
        if "site_price_order" in changed:
            changed["site_price_order"] = [
                site for site in (
                    str(item or "").strip().upper() for item in (changed["site_price_order"] or [])
                )
                if site in _BASE_PRICE_SITES
            ]
        if "price_tiers" in changed:
            changed["price_tiers"] = _normalize_cnsc_price_tiers(changed["price_tiers"])
        if "default_site_discount_percent" in changed:
            changed["default_site_discount_percent"] = _float_or_empty(changed["default_site_discount_percent"])
        if "cnsc_store" in changed and not isinstance(changed["cnsc_store"], dict):
            changed.pop("cnsc_store")
        if "category_profile_id" in changed:
            changed["category_profile_id"] = str(changed.get("category_profile_id") or "").strip()
        if "category_profile_version_id" in changed:
            changed["category_profile_version_id"] = str(changed.get("category_profile_version_id") or "").strip()
        if "category_profile_snapshot" in changed and not isinstance(changed["category_profile_snapshot"], dict):
            changed.pop("category_profile_snapshot")
        for field, label in {
            "weight_kg": "重量",
            "package_length_cm": "包裹长度",
            "package_width_cm": "包裹宽度",
            "package_height_cm": "包裹高度",
            "normal_price": "原价",
            "sale_price": "折后价",
        }.items():
            if field in changed:
                changed[field] = _validated_non_negative_number(changed[field], label)
        if "stock" in changed:
            changed["stock"] = _validated_non_negative_number(changed["stock"], "库存", integer=True)
        if "product_images" in changed:
            changed["product_images"] = _normalize_product_images(changed["product_images"])
            changed["main_image"] = changed["product_images"][0] if changed["product_images"] else ""
        elif "main_image" in changed:
            main_image = str(changed.get("main_image") or "").strip()
            current_images = _normalize_product_images(item.get("product_images") or [])
            changed["product_images"] = _normalize_product_images([main_image, *current_images]) if main_image else current_images
            changed["main_image"] = main_image
        if "size_chart_as_last_product_image" in changed:
            # 开关只服务本土：跨境副本保存即视为关闭，免得 UI 之外的调用写进半支持的状态。
            changed["size_chart_as_last_product_image"] = bool(
                _size_chart_last_product_image_enabled(changed)
                and str(item.get("listing_mode") or "site") != "cnsc_global"
            )
        item.update(changed)
        if "category_profile_snapshot" in changed:
            snapshot = item.get("category_profile_snapshot") or {}
            item["profile_snapshot_hash"] = hash_profile_snapshot(snapshot) if snapshot else ""
        now = utc_now()
        for row, update in sku_updates:
            if "stock" in update:
                row["stock"] = _validated_non_negative_number(update.get("stock"), "SKU 库存", integer=True)
            if "warehouse_stocks" in update:
                if item.get("listing_mode") != "cnsc_global":
                    raise ValueError("只有 CNSC 全球商品可以覆盖多仓库存")
                row["warehouse_stocks"] = _validated_cnsc_warehouse_stocks(update.get("warehouse_stocks"))
            row["updated_at"] = now
        for row, update in price_updates:
            if "sku_id" in update and str(update.get("sku_id") or "") != str(row.get("sku_id") or ""):
                raise ValueError("价格对应的 SKU 不属于当前价格记录")
            if "normal_price" in update:
                row["normal_price"] = _validated_non_negative_number(update.get("normal_price"), "原价")
            if "sale_price" in update:
                row["sale_price"] = _validated_non_negative_number(update.get("sale_price"), "折后价")
            row["updated_at"] = now
        # 维护入口（不在编辑器 UI 里）：按当前商品 × 当前模板重生成 SKU/价格行。
        # 用途是把"复制时按旧规则生成的尺码"（如商品识别字母码覆盖了模板 cm 码）
        # 对齐到模板现在的尺码轴，省掉"删副本再重复制"。
        if bool(payload.get("rebuild_skus")):
            base = next(
                (row for row in data.get("base_products", []) if row.get("id") == item.get("base_product_id")),
                None,
            )
            template = next(
                (row for row in data.get("store_templates", []) if row.get("id") == item.get("template_id")),
                None,
            ) or item
            if base is None:
                raise ValueError("基础商品不存在，无法重建 SKU 行")
            _replace_listing_skus_and_prices(data, base, template, item, now=now)
        # After an editor tier edit, re-derive each SKU row's CNSC price fields by
        # (color,size)→tier match so the per-SKU global price / site matrix stay
        # consistent with the copy-level price_tiers (mirrors _generate_listing_skus).
        if item.get("listing_mode") == "cnsc_global" and "price_tiers" in changed:
            tiers = item.get("price_tiers") or []
            base = next(
                (row for row in data.get("base_products", []) if row.get("id") == item.get("base_product_id")),
                None,
            )
            template = next(
                (row for row in data.get("store_templates", []) if row.get("id") == item.get("template_id")),
                None,
            )
            if base is not None:
                # 编辑器 colors 模式重建颜色档时不带 global（界面不展示该列），
                # 按颜色从基础商品价格档成本价补齐，避免保存即报「待补全全球价」。
                _fill_cnsc_color_tier_globals(tiers, base, template or {})
            default_off = float(item.get("default_site_discount_percent") or 40)
            site_discounts = {
                str(s.get("site_code") or "").strip().upper(): float(s.get("discount_percent") or default_off)
                for s in (item.get("target_site_stores") or [])
                if str(s.get("site_code") or "").strip()
            }
            sku_color_size: dict[str, tuple[str, str]] = {}
            for row in data.get("listing_skus", []):
                if row.get("listing_copy_id") != listing_copy_id:
                    continue
                size = str(row.get("size") or "").strip()
                color = str(row.get("color_code") or row.get("color_cn") or "").strip()
                sku_color_size[str(row.get("id") or "")] = (color, size)
                tier = next((item_tier for item_tier in tiers if _tier_matches(item_tier, color, size)), None)
                matrix = deepcopy(tier.get("site_prices") or {}) if tier else {}
                derived = {}
                for site_code, entry in matrix.items():
                    site_code = str(site_code or "").strip().upper()
                    if not isinstance(entry, dict):
                        continue
                    original = _float_or_empty(entry.get("original_price"))
                    if original == "" or float(original) <= 0:
                        continue
                    off = site_discounts.get(site_code, default_off)
                    sale = _cnsc_sale_price(original, entry.get("sale_price"), off)
                    derived[site_code] = {"original_price": float(original), "sale_price": sale}
                row["price_tier_id"] = tier.get("id") if tier else ""
                row["global_price_cny"] = tier.get("global_price_cny") if tier else ""
                row["site_price_matrix"] = derived
                row["updated_at"] = now
            if base is not None:
                # 价格行成本价以基础商品价格表为唯一来源（readiness
                # base_product_cost_price）：保存档位后按 (color,size) 重取，
                # 颜色大小写漂移不再导致成本价丢失。
                base_tiers = _normalize_base_price_tiers(base.get("price_tiers") or [])
                for row in data.get("listing_prices", []):
                    if row.get("listing_copy_id") != listing_copy_id:
                        continue
                    color, size = sku_color_size.get(str(row.get("sku_id") or ""), ("", ""))
                    base_tier = next((bt for bt in base_tiers if _tier_matches(bt, color, size)), None)
                    cost = (base_tier or {}).get("cost_price")
                    if cost not in ("", None) and float(cost) > 0:
                        row["cost_price"] = float(cost)
                        row["updated_at"] = now
        item["revision"] = int(item.get("revision") or 1) + 1
        item["updated_at"] = now
        detail = _store_listing_copy_detail(data, item)
        data.setdefault("listing_copy_revisions", []).append(
            {
                "id": str(uuid.uuid4()),
                "listing_copy_id": listing_copy_id,
                "revision": item["revision"],
                "snapshot": deepcopy(item),
                "skus": deepcopy(detail["skus"]),
                "prices": deepcopy(detail["prices"]),
                "created_at": item["updated_at"],
            }
        )
        save(data)
        return detail


def _validated_listing_row_updates(
    rows: list[dict[str, Any]],
    listing_copy_id: str,
    updates: Any,
    *,
    row_label: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if updates is None:
        return []
    if not isinstance(updates, list):
        raise ValueError(f"{row_label} 明细必须是数组")
    by_id = {
        str(row.get("id") or ""): row
        for row in rows
        if row.get("listing_copy_id") == listing_copy_id and row.get("id")
    }
    validated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError(f"{row_label} 明细格式无效")
        row_id = str(update.get("id") or "").strip()
        row = by_id.get(row_id)
        if not row:
            if row_label == "SKU":
                raise ValueError("SKU 不属于当前店铺刊登副本")
            raise ValueError("价格记录不属于当前店铺刊登副本")
        validated.append((row, update))
    return validated


def _validated_non_negative_number(value: Any, label: str, *, integer: bool = False) -> int | float | str:
    if value is None or value == "":
        return ""
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是有效数字") from exc
    if number < 0:
        raise ValueError(f"{label}不能小于 0")
    return number


def _store_listing_copy_detail(data: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    listing_copy_id = item.get("id") or ""
    skus = [deepcopy(row) for row in data.get("listing_skus", []) if row.get("listing_copy_id") == listing_copy_id]
    # 默认 SKU(白T恤) 固定排在最后，与马帮 SKU 表的颜色顺序一致。
    # 尺码顺序：本土按模板 chip 顺序排（与生成 SKU 的尺码轴一致）。旧的
    # _size_sort_value 只认 S/M/L，cm 类尺码全部落到 999 后按 sku_code 字母序，
    # 90cm 会排到 100~160cm 后面，副本编辑器的 SKU 行与模板顺序对不上。跨境不变。
    size_rank = _listing_size_order_rank(item, [row.get("size") for row in skus], data)
    skus.sort(key=lambda row: (
        str(row.get("sku_source") or "") == "default_sku",
        row.get("color_code") or "",
        _ordered_size_sort_key(row.get("size"), size_rank),
        row.get("sku_code") or "",
    ))
    prices = [deepcopy(row) for row in data.get("listing_prices", []) if row.get("listing_copy_id") == listing_copy_id]
    prices.sort(key=lambda row: row.get("sku_code") or "")
    prices_by_sku = {row.get("sku_id"): row for row in prices if row.get("sku_id")}
    for sku in skus:
        sku["price"] = deepcopy(prices_by_sku.get(sku.get("id")) or {})
    readiness = _check_automation_readiness(item, skus=skus, prices=prices)
    for field in _runtime_configuration_missing(data, item, now=utc_now()):
        if field not in readiness["missing_fields"]:
            readiness["missing_fields"].append(field)
    if item.get("description_status") == "pending" and "description_pending" not in readiness["missing_fields"]:
        readiness["missing_fields"].append("description_pending")
    _apply_automation_warning_policy(readiness)
    detail = deepcopy(item)
    detail["skus"] = skus
    detail["prices"] = prices
    detail["readiness"] = readiness
    return detail


def _apply_automation_warning_policy(readiness: dict[str, Any]) -> dict[str, Any]:
    fields = list(dict.fromkeys(readiness.get("missing_fields") or []))
    warnings = [field for field in fields if field in AUTOMATION_WARNING_FIELDS]
    blocking = [field for field in fields if field not in AUTOMATION_WARNING_FIELDS]
    readiness["warnings"] = list(dict.fromkeys([*(readiness.get("warnings") or []), *warnings]))
    readiness["missing_fields"] = blocking
    readiness["ready"] = not blocking
    readiness["status"] = "ready" if readiness["ready"] else "needs_manual"
    return readiness


def list_listing_skus(listing_copy_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        data = load_store()
        rows = [dict(item) for item in data.get("listing_skus", []) if item.get("listing_copy_id") == listing_copy_id]
        copy = next((row for row in data.get("store_listing_copies", []) if row.get("id") == listing_copy_id), None) or {}
        size_rank = _listing_size_order_rank(copy, [row.get("size") for row in rows], data)
    return sorted(rows, key=lambda item: (item.get("color_code") or "", _ordered_size_sort_key(item.get("size"), size_rank), item.get("sku_code") or ""))


def list_listing_prices(listing_copy_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [dict(item) for item in load_store().get("listing_prices", []) if item.get("listing_copy_id") == listing_copy_id]
    return sorted(rows, key=lambda item: item.get("sku_code") or "")


def _copy_base_products_to_templates_in_store(
    data: dict[str, Any],
    base_product_ids: list[str],
    wanted_template_ids: list[str],
    now: str,
) -> dict[str, Any]:
    """Copy one or more base products to one or more store templates inside an
    already locked store. Returns an aggregated result whose top-level keys match the
    legacy single-product contract (created/created_with_issues/skipped/errors plus
    counts) so old callers keep working unchanged, with per_base added for batch UI."""
    templates = {
        item.get("id"): item
        for item in data.get("store_templates", [])
        if item.get("id") in wanted_template_ids
        and str(item.get("status") or "active").strip().lower() != "archived"
    }
    existing_pairs = {
        (item.get("base_product_id"), item.get("template_id"))
        for item in data.get("store_listing_copies", [])
    }
    created: list[dict[str, Any]] = []
    created_with_issues: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    per_base: list[dict[str, Any]] = []
    data.setdefault("store_listing_copies", [])
    data.setdefault("listing_skus", [])
    data.setdefault("listing_prices", [])
    for base_product_id in base_product_ids:
        base = next((item for item in data.get("base_products", []) if item.get("id") == base_product_id), None)
        if not base:
            errors.append({"base_product_id": base_product_id, "reason": "base_product_not_found"})
            per_base.append({
                "base_product_id": base_product_id,
                "created": [], "created_with_issues": [], "skipped": [], "errors": [{"base_product_id": base_product_id, "reason": "base_product_not_found"}],
                "created_count": 0, "created_with_issues_count": 0, "skipped_count": 0, "error_count": 1,
            })
            continue
        base_created: list[dict[str, Any]] = []
        base_with_issues: list[dict[str, Any]] = []
        base_skipped: list[dict[str, Any]] = []
        base_errors: list[dict[str, Any]] = []
        for template_id in wanted_template_ids:
            template = templates.get(template_id)
            if not template:
                base_errors.append({"base_product_id": base_product_id, "template_id": template_id, "reason": "template_not_found"})
                continue
            if (base_product_id, template_id) in existing_pairs:
                base_skipped.append({"base_product_id": base_product_id, "template_id": template_id, "reason": "already_exists"})
                continue
            listing_copy = _build_store_listing_copy(base, template, now, data)
            skus = _generate_listing_skus(base, template, listing_copy)
            prices = _generate_listing_prices(template, listing_copy, skus, base_price_tiers=base.get("price_tiers") or [])
            data["store_listing_copies"].append(listing_copy)
            data["listing_skus"].extend(skus)
            data["listing_prices"].extend(prices)
            existing_pairs.add((base_product_id, template_id))
            base_created.append(listing_copy)
            # Decision A: always create the copy; surface any CNSC gaps so the user
            # finishes them in the editor. The job gate stays fail-closed later.
            if template.get("listing_mode") == "cnsc_global":
                sku_variants = [
                    (str(sku.get("color_code") or sku.get("color_cn") or "").strip(), str(sku.get("size") or "").strip())
                    for sku in skus
                ]
                problems = _cnsc_configuration_problems(listing_copy, selected_variants=sku_variants)
                if problems:
                    base_with_issues.append({
                        "base_product_id": base_product_id,
                        "template_id": template_id,
                        "listing_copy_id": listing_copy.get("id") or "",
                        "missing_configuration": problems,
                    })
        created.extend(base_created)
        created_with_issues.extend(base_with_issues)
        skipped.extend(base_skipped)
        errors.extend(base_errors)
        per_base.append({
            "base_product_id": base_product_id,
            "created": base_created,
            "created_with_issues": base_with_issues,
            "skipped": base_skipped,
            "errors": base_errors,
            "created_count": len(base_created),
            "created_with_issues_count": len(base_with_issues),
            "skipped_count": len(base_skipped),
            "error_count": len(base_errors),
        })
    # Category selection is now a plain either/or rule (base product wins when
    # set, store template is the default), so there is no mismatch to report.
    category_mismatches: list[dict[str, Any]] = []
    return {
        "ok": not errors,
        "created": created,
        "created_with_issues": created_with_issues,
        "skipped": skipped,
        "errors": errors,
        "category_mismatches": category_mismatches,
        "category_mismatch_count": len(category_mismatches),
        "created_count": len(created),
        "created_with_issues_count": len(created_with_issues),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "per_base": per_base,
        "requested_base_count": len(base_product_ids),
    }


def copy_base_product_to_store_templates(base_product_id: str, template_ids: list[str] | str) -> dict[str, Any]:
    """Legacy single-product entry point, kept so existing callers/tests work
    unchanged. For multi-product batch use copy_base_products_to_store_templates."""
    if isinstance(template_ids, str):
        template_ids = [item.strip() for item in template_ids.split(",") if item.strip()]
    wanted_template_ids = [str(item or "").strip() for item in template_ids if str(item or "").strip()]
    base_product_id = str(base_product_id or "").strip()
    if not base_product_id or not wanted_template_ids:
        return {"ok": False, "created": [], "skipped": [], "errors": [{"reason": "missing_base_or_templates"}], "created_count": 0, "per_base": []}
    now = utc_now()
    # 生成副本只动基础商品 + 副本/SKU/价格（都是拆分集合）：主库不读也不写。
    with locked_store(split_keys=("base_products", "store_listing_copies", "listing_skus", "listing_prices")) as (data, save):
        result = _copy_base_products_to_templates_in_store(data, [base_product_id], wanted_template_ids, now)
        if result["created"]:
            save(data)
        return result


def copy_base_products_to_store_templates(base_product_ids: list[str], template_ids: list[str] | str) -> dict[str, Any]:
    """Copy multiple base products to one or more store templates in one locked
    write. Missing ids are reported per-product rather than aborting the batch."""
    if isinstance(template_ids, str):
        template_ids = [item.strip() for item in template_ids.split(",") if item.strip()]
    wanted_template_ids = [str(item or "").strip() for item in template_ids if str(item or "").strip()]
    requested = [str(item or "").strip() for item in (base_product_ids or []) if str(item or "").strip()]
    if not requested or not wanted_template_ids:
        return {"ok": False, "created": [], "skipped": [], "errors": [{"reason": "missing_base_or_templates"}], "created_count": 0, "per_base": []}
    now = utc_now()
    # 同上：批量生成副本也只动基础商品 + 副本/SKU/价格。
    with locked_store(split_keys=("base_products", "store_listing_copies", "listing_skus", "listing_prices")) as (data, save):
        result = _copy_base_products_to_templates_in_store(data, requested, wanted_template_ids, now)
        if result["created"]:
            save(data)
        return result


def list_automation_jobs(*, include_snapshot: bool = False) -> list[dict[str, Any]]:
    """任务列表（默认**不带** `execution_snapshot`）。

    执行快照是任务里最大的一坨：81 个任务 18.5MB 里它占 17.7MB（每个 233KB，含整份
    副本/SKU/价格/图片）。列表页一个字段都用不到它（前端只读 店铺/状态/阶段/缺失字段/
    日志），可带着它 `/api/automation/jobs` 要吐 27.8MB —— 浏览器解析完就卡住了，
    这正是「用起来很卡」的主因之一。

    需要快照的调用方（构建执行 payload、重建任务）走 `get_item`/`build_automation_payload`
    单独取，不要从这里拿。
    """

    with _LOCK:
        data = load_store()
        jobs = [dict(item) for item in data.get("automation_jobs", [])]
        snapshots = data.get("automation_job_snapshots") or {}
    if include_snapshot:
        # 快照在独立集合里：需要时才挂回任务（迁移期内联的仍然直接用）。
        for job in jobs:
            if "execution_snapshot" not in job:
                snapshot = snapshots.get(str(job.get("id") or ""))
                if snapshot is not None:
                    job["execution_snapshot"] = snapshot
    else:
        for job in jobs:
            job.pop("execution_snapshot", None)
    return sorted(jobs, key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lease_is_expired(job: dict[str, Any], now: str) -> bool:
    expires_at = str(job.get("lease_expires_at") or "").strip()
    if not expires_at:
        return True
    try:
        return _parse_utc(expires_at) <= _parse_utc(now)
    except ValueError:
        return True


def _append_automation_job_event(job: dict[str, Any], event: str, *, now: str, **details: Any) -> None:
    job.setdefault("run_log", []).append({"event": event, "at": now, **deepcopy(details)})


def next_ready_automation_account() -> str:
    """最早一个 ready 任务归属的马帮账号（没有则 ""）。

    Worker 在**领取之前**用它决定该准备哪个账号的浏览器与登录，这样浏览器没就绪时
    任务仍然留在 ready、不消耗租约与重试次数。
    """

    with _LOCK:
        jobs = [dict(item) for item in load_store().get("automation_jobs", [])]
    ready = [item for item in jobs if str(item.get("status") or "").strip() == "ready"]
    if not ready:
        return ""
    earliest = min(ready, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
    return str(earliest.get("account_id") or "").strip()


def _automation_store_has_claimable_job(
    data: dict[str, Any],
    *,
    current: str,
    max_attempts: int,
    account_id: str = "",
) -> bool:
    """只读判断：这份库里有没有值得进写锁的任务。

    与 claim_next_automation_job 的候选条件保持一致：ready 且没超重试次数
    （可选限账号），或者有过期租约需要恢复/判失败。Worker 每 3 秒空轮询一次，
    没有可领的任务时连写锁都不该进、更不该整库解析。
    """

    for job in data.get("automation_jobs") or []:
        if not isinstance(job, dict):
            continue
        status = str(job.get("status") or "").strip()
        attempts = int(job.get("attempt_count") or 0)
        if status in {"claimed", "running"}:
            if _lease_is_expired(job, current):
                return True
            continue
        if status == "ready" and attempts < max_attempts:
            if account_id and str(job.get("account_id") or "").strip() != account_id:
                continue
            return True
    return False


def automation_worker_queue_snapshot(*, max_attempts: int = 2, now: str | None = None) -> dict[str, dict[str, Any]]:
    """按账号汇总「还有多少可领任务 + 最近一次任务活动时间」。

    只读（走读缓存），给「按需唤醒 Worker」和「空闲自动关闭」两件事共用：

    - ``claimable``：与 ``claim_next_automation_job`` 的候选条件一致（ready 且没超重试
      次数，或者租约已过期需要恢复/判失败）。>0 说明这个账号还有活要干。
    - ``in_flight``：正在被某个 Worker 执行/占着租约的任务数（不管租约过没过期），
      空闲关闭看到它就别关——那是在途的一单。
    - ``latest_activity_at``：该账号全部任务里最新的时间戳（建单/改状态/领取/完成），
      用来算「最后一单之后过了多久」，也就是空闲计时的起点。

    返回 ``{account_id: {...}}``，没绑定账号的任务落在空字符串键上。
    """

    current = now or utc_now()
    snapshot: dict[str, dict[str, Any]] = {}
    for job in load_store().get("automation_jobs") or []:
        if not isinstance(job, dict):
            continue
        account = str(job.get("account_id") or "").strip()
        row = snapshot.setdefault(
            account, {"claimable": 0, "in_flight": 0, "latest_activity_at": ""}
        )
        status = str(job.get("status") or "").strip()
        attempts = int(job.get("attempt_count") or 0)
        if status in {"claimed", "running"}:
            row["in_flight"] += 1
            if _lease_is_expired(job, current):
                row["claimable"] += 1
        elif status == "ready" and attempts < max_attempts:
            row["claimable"] += 1
        for key in ("updated_at", "created_at", "claimed_at", "finished_at", "completed_at"):
            value = str(job.get(key) or "").strip()
            if value > str(row["latest_activity_at"] or ""):
                row["latest_activity_at"] = value
    return snapshot


def claim_next_automation_job(
    worker_id: str,
    *,
    lease_seconds: int = 120,
    max_attempts: int = 2,
    now: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    normalized_worker = str(worker_id or "").strip()
    wanted_account = str(account_id or "").strip()
    if not normalized_worker:
        raise ValueError("worker_id is required")
    current = now or utc_now()
    expires_at = (_parse_utc(current) + timedelta(seconds=max(1, int(lease_seconds)))).isoformat()
    # 快路径：先用只读缓存判断有没有值得领的任务。没有就直接返回——不进写锁、
    # 不解析任何文件。三个 Worker 每 3 秒空轮询一次，原来每次都要整库解析
    # （约 300~430ms，且 json.loads 期间占着 GIL），这是界面卡顿的主要来源之一。
    if not _automation_store_has_claimable_job(
        load_store(), current=current, max_attempts=max_attempts, account_id=wanted_account
    ):
        return {"job": None, "lease_token": ""}
    with locked_automation_jobs() as (jobs, save):
        changed = False
        candidates = []
        for job in jobs:
            status = str(job.get("status") or "").strip()
            attempts = int(job.get("attempt_count") or 0)
            if status in {"claimed", "running"} and _lease_is_expired(job, current):
                changed = True
                if attempts >= max_attempts:
                    job.update(
                        {
                            "status": "failed",
                            "current_phase": "lease_expired",
                            "last_error": "Automation worker lease expired after maximum attempts",
                            "lease_token": "",
                            "lease_expires_at": "",
                            "updated_at": current,
                        }
                    )
                    _append_automation_job_event(job, "lease_exhausted", now=current, attempt_count=attempts)
                    continue
                job.update(
                    {
                        "status": "ready",
                        "worker_id": "",
                        "lease_token": "",
                        "lease_expires_at": "",
                        "current_phase": "lease_recovered",
                        "updated_at": current,
                    }
                )
                _append_automation_job_event(job, "lease_recovered", now=current, attempt_count=attempts)
                status = "ready"
            if status == "ready" and attempts < max_attempts:
                if wanted_account and str(job.get("account_id") or "").strip() != wanted_account:
                    continue
                candidates.append(job)

        if not candidates:
            if changed:
                save(jobs)
            return {"job": None, "lease_token": ""}

        job = min(candidates, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
        lease_token = str(uuid.uuid4())
        attempt_count = int(job.get("attempt_count") or 0) + 1
        job.update(
            {
                "status": "claimed",
                "worker_id": normalized_worker,
                "lease_token": lease_token,
                "lease_expires_at": expires_at,
                "attempt_count": attempt_count,
                "claimed_at": current,
                "current_phase": "claimed",
                "last_error": "",
                "updated_at": current,
            }
        )
        _append_automation_job_event(job, "claimed", now=current, worker_id=normalized_worker, attempt_count=attempt_count)
        save(jobs)
        return {"job": deepcopy(job), "lease_token": lease_token}


def _find_leased_automation_job(
    jobs: list[dict[str, Any]],
    job_id: str,
    worker_id: str,
    lease_token: str,
    *,
    now: str,
) -> dict[str, Any]:
    job = next((item for item in jobs if item.get("id") == job_id), None)
    if not job:
        raise ValueError("Automation job not found")
    if (
        str(job.get("worker_id") or "") != str(worker_id or "")
        or not lease_token
        or str(job.get("lease_token") or "") != str(lease_token)
    ):
        raise ValueError("Automation job lease does not match")
    if str(job.get("status") or "") not in {"claimed", "running"} or _lease_is_expired(job, now):
        raise ValueError("Automation job lease is not active")
    return job


def _require_automation_job_lease(
    data: dict[str, Any],
    job_id: str,
    worker_id: str,
    lease_token: str,
    *,
    now: str,
) -> dict[str, Any]:
    """整库视图版本的租约校验（finish 这类要同时改主库的调用方用）。"""

    return _find_leased_automation_job(
        data.get("automation_jobs", []), job_id, worker_id, lease_token, now=now
    )


def heartbeat_automation_job(
    job_id: str,
    worker_id: str,
    lease_token: str,
    *,
    phase: str,
    lease_seconds: int = 120,
    now: str | None = None,
) -> dict[str, Any]:
    current = now or utc_now()
    expires_at = (_parse_utc(current) + timedelta(seconds=max(1, int(lease_seconds)))).isoformat()
    with locked_automation_jobs() as (jobs, save):
        job = _find_leased_automation_job(jobs, job_id, worker_id, lease_token, now=current)
        job.update(
            {
                "status": "running",
                "current_phase": str(phase or "running").strip() or "running",
                "lease_expires_at": expires_at,
                "updated_at": current,
            }
        )
        _append_automation_job_event(job, "heartbeat", now=current, phase=job["current_phase"])
        save(jobs)
        return deepcopy(job)


LIVE_STEP_EVENTS = {
    "stage",
    "attempt",
    "attempt_retrying",
    "attempt_failed",
    "blocked",
    "saved",
    "save_clicked",
    "warning",
    "navigation",
}
LIVE_STEP_LIMIT = 60


def record_automation_job_progress(
    job_id: str,
    worker_id: str,
    lease_token: str,
    *,
    event: dict[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """把执行器的一条进度事件写进作业（worker 持租约调用）。

    现场需求：执行中的步骤要实时显示在自动化面板最上面。原来步骤只在执行器结束时由
    runner 解析，运行中没有任何细粒度信息（只有 30 秒一次的心跳相位）。这里把 stage
    事件按序累积到 ``result.live_steps``，供界面边跑边刷新。
    """

    current = now or utc_now()
    payload = event if isinstance(event, dict) else {}
    name = str(payload.get("event") or "").strip()
    with locked_automation_jobs() as (jobs, save):
        job = _find_leased_automation_job(jobs, job_id, worker_id, lease_token, now=current)
        # 有进度在流 = 正在执行（与 heartbeat 一致），界面据此判定"实时"并开始轮询。
        job["status"] = "running"
        job["updated_at"] = current
        if name in LIVE_STEP_EVENTS:
            step = {
                "event": name,
                "at": str(payload.get("at") or current),
                "stage": str(payload.get("stage") or ""),
                "ok": payload.get("ok") if isinstance(payload.get("ok"), bool) else None,
                "status": str(payload.get("status") or ""),
                "code": str(payload.get("code") or ""),
            }
            if step["stage"]:
                job["current_phase"] = step["stage"]
            result = job.setdefault("result", {})
            steps = result.setdefault("live_steps", [])
            steps.append({key: value for key, value in step.items() if value not in ("", None)})
            if len(steps) > LIVE_STEP_LIMIT:
                del steps[: len(steps) - LIVE_STEP_LIMIT]
            _append_automation_job_event(job, name, now=current, **{k: v for k, v in step.items() if k != "event"})
        save(jobs)
        return deepcopy(job)


def manual_finish_automation_job(
    job_id: str,
    *,
    status: str,
    note: str = "",
    operator: str = "",
    now: str | None = None,
) -> dict[str, Any]:
    """操作员人工确认这次执行的结果（saved / failed）。

    设计要点：**不伪造执行器证据**——``save_clicked`` / ``publish_clicked`` 保持原值，
    人工确认只写 ``result.manual_confirmation``（含原状态、时间、备注、操作员），
    界面据此显示"人工确认已保存"。执行器那条"无保存证据不得报 saved"的硬规则不变。
    """

    current = now or utc_now()
    target = str(status or "").strip().lower()
    if target not in {"saved", "failed"}:
        raise ValueError("人工标记只支持 saved / failed")
    # 人工确认只动任务本身：不解析类目 / 快照，也不重写主库。
    with locked_store(split_keys=("automation_jobs",), main_readonly=True) as (data, save):
        job = next((item for item in data.get("automation_jobs", []) if item.get("id") == job_id), None)
        if job is None:
            raise ValueError("Automation job not found")
        previous = str(job.get("status") or "")
        result = job.setdefault("result", {})
        result["manual_confirmation"] = {
            "status": target,
            "previous_status": previous,
            "note": str(note or "").strip()[:500],
            "operator": str(operator or "").strip()[:80],
            "at": current,
        }
        job.update(
            {
                "status": target,
                "current_phase": "manual_confirmed_saved" if target == "saved" else "manual_confirmed_failed",
                "finished_at": current,
                "updated_at": current,
                "lease_token": "",
                "lease_expires_at": "",
            }
        )
        if target == "failed" and not str(job.get("last_error") or "").strip():
            job["last_error"] = str(note or "操作员人工判定为失败").strip()[:500]
        _append_automation_job_event(job, "manual_confirmation", now=current, status=target, previous_status=previous)
        save(data)
        return deepcopy(job)


def finish_automation_job(
    job_id: str,
    worker_id: str,
    lease_token: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
    now: str | None = None,
) -> dict[str, Any]:
    final_status = str(status or "").strip().lower()
    saved_statuses = {"saved", "saved_with_warnings"}
    if final_status not in {*saved_statuses, "blocked", "failed", "needs_confirmation"}:
        raise ValueError("Automation job final status is invalid")
    outcome = deepcopy(result or {})
    if "stages" in outcome:
        outcome["stages"] = _normalize_execution_stages(outcome.get("stages"))
    if final_status in saved_statuses and (
        outcome.get("save_clicked") is not True or outcome.get("publish_clicked") is not False
    ):
        raise ValueError("Saved automation job requires save evidence and publish_clicked=false")
    current = now or utc_now()
    # 任务落终态会级联副本/基础商品状态（_cascade_completed_from_job）和模板默认视频，
    # 所以副本与基础商品两个拆分集合必须一起加载。
    with locked_store(split_keys=("automation_jobs", "store_listing_copies", "base_products")) as (data, save):
        existing = next((item for item in data.get("automation_jobs", []) if item.get("id") == job_id), None)
        if (
            existing
            and final_status in saved_statuses
            and existing.get("status") == final_status
            and str(existing.get("worker_id") or "") == str(worker_id or "")
            and existing.get("result") == outcome
        ):
            return deepcopy(existing)
        strict_saved_receipt = (
            final_status in saved_statuses
            and outcome.get("ok") is True
            and str(outcome.get("status") or "").lower() == "saved"
            and outcome.get("exit_code") == 0
            and outcome.get("save_clicked") is True
            and outcome.get("publish_clicked") is False
            and outcome.get("page_closed") is True
            and outcome.get("close_method") in {"success_dialog", "playwright_fallback"}
        )
        matching_unreclaimed_lease = (
            existing
            and str(existing.get("status") or "") in {"claimed", "running"}
            and str(existing.get("worker_id") or "") == str(worker_id or "")
            and bool(lease_token)
            and str(existing.get("lease_token") or "") == str(lease_token)
        )
        if strict_saved_receipt and matching_unreclaimed_lease:
            job = existing
        else:
            job = _require_automation_job_lease(data, job_id, worker_id, lease_token, now=current)
        job.update(
            {
                "status": final_status,
                "current_phase": final_status,
                "result": outcome,
                "last_error": str(error or "").strip(),
                "lease_token": "",
                "lease_expires_at": "",
                "finished_at": current,
                "updated_at": current,
            }
        )
        if final_status in saved_statuses:
            job["saved_at"] = current
            _cascade_completed_from_job(data, job, now=current)
            _record_default_video_id_from_result(data, job, outcome, now=current)
        _append_automation_job_event(job, final_status, now=current, error=job["last_error"], result=outcome)
        save(data)
        return deepcopy(job)


def _cascade_completed_from_job(data: dict[str, Any], job: dict[str, Any], *, now: str) -> None:
    """When an automation job saves, mark its listing copy completed and (if all copies of the base product are completed) mark the base product completed too."""

    listing_copy_id = str(job.get("listing_copy_id") or "").strip()
    if not listing_copy_id:
        return
    listing_copy = next(
        (item for item in data.get("store_listing_copies", []) if item.get("id") == listing_copy_id),
        None,
    )
    if not listing_copy:
        return
    if listing_copy.get("status") != "completed":
        listing_copy["status"] = "completed"
        listing_copy["completed_at"] = now
        listing_copy["updated_at"] = now
    base_product_id = str(listing_copy.get("base_product_id") or "").strip()
    if not base_product_id:
        return
    sibling_copies = [
        item
        for item in data.get("store_listing_copies", [])
        if str(item.get("base_product_id") or "") == base_product_id
    ]
    if not sibling_copies:
        return
    if not all(item.get("status") == "completed" for item in sibling_copies):
        return
    base_product = next(
        (item for item in data.get("base_products", []) if item.get("id") == base_product_id),
        None,
    )
    if not base_product:
        return
    if base_product.get("status") != "completed":
        base_product["status"] = "completed"
        base_product["completed_at"] = now
        base_product["updated_at"] = now


def _record_default_video_id_from_result(
    data: dict[str, Any],
    job: dict[str, Any],
    outcome: dict[str, Any],
    *,
    now: str,
) -> None:
    """本土首次执行成功后，把自动化上传的视频 videoId 记到店铺模板的
    ``default_video_id``（以及该模板下还没有记录的副本），之后各次执行直接按
    ID 选择该视频，不再重复上传。跨境(CNSC) 不记录——默认视频是本土专属；
    模板已有 ID 或本次视频阶段没回传 video_id 时不覆盖。"""
    stages = outcome.get("stages") if isinstance(outcome, dict) else None
    video_stage = stages.get("video_upload") if isinstance(stages, dict) else None
    video_id = str((video_stage or {}).get("video_id") or "").strip()
    if not video_id:
        return
    listing_copy_id = str(job.get("listing_copy_id") or "").strip()
    listing_copy = next(
        (item for item in data.get("store_listing_copies", []) if item.get("id") == listing_copy_id),
        None,
    )
    if not listing_copy or str(listing_copy.get("listing_mode") or "site").strip().lower() == "cnsc_global":
        return
    template_id = str(listing_copy.get("template_id") or "").strip()
    template = next(
        (item for item in data.get("store_templates", []) if item.get("id") == template_id),
        None,
    )
    if not template or str(template.get("default_video_id") or "").strip():
        return
    template["default_video_id"] = video_id
    template["updated_at"] = now
    for item in data.get("store_listing_copies", []):
        if item.get("template_id") == template_id and not str(item.get("default_video_id") or "").strip():
            item["default_video_id"] = video_id
            item["updated_at"] = now


def rollback_base_product(base_product_id: str) -> dict[str, Any] | None:
    """Move a completed base product back to base_ready and reopen its listing copies. Automation job history stays intact for audit."""

    base_product_id = str(base_product_id or "").strip()
    if not base_product_id:
        return None
    now = utc_now()
    # 回滚只动基础商品 + 副本状态（都是拆分集合）。
    with locked_store(split_keys=("base_products", "store_listing_copies")) as (data, save):
        base_product = next(
            (item for item in data.get("base_products", []) if item.get("id") == base_product_id),
            None,
        )
        if not base_product:
            return None
        if base_product.get("status") != "completed":
            raise ValueError("Base product is not completed and cannot be rolled back")
        base_product["status"] = "base_ready"
        base_product["completed_at"] = ""
        base_product["rolled_back_at"] = now
        base_product["updated_at"] = now
        reopened_copy_ids: list[str] = []
        for listing_copy in data.get("store_listing_copies", []):
            if str(listing_copy.get("base_product_id") or "") != base_product_id:
                continue
            if listing_copy.get("status") == "completed":
                listing_copy["status"] = "copy_ready"
                listing_copy["completed_at"] = ""
                listing_copy["rolled_back_at"] = now
                listing_copy["updated_at"] = now
                reopened_copy_ids.append(str(listing_copy.get("id") or ""))
        save(data)
        return {
            "id": base_product_id,
            "status": base_product["status"],
            "reopened_listing_copy_ids": reopened_copy_ids,
            "rolled_back_at": now,
        }


def apply_asset_pointer_repair(updates: list[dict[str, Any]], *, now: str = "") -> int:
    """把死链的本地资产记录改指到同组同名的现存文件（一次事务、一次保存）。

    ``updates`` 来自 ``asset_compaction.plan_asset_pointer_repair``。保留 asset id，
    因此前端按 id 取的缩略图/原图 URL 不变；只换 ``file_path`` 与文件元数据。
    返回真正改动的条数。
    """

    entries = [item for item in (updates or []) if isinstance(item, dict) and str(item.get("asset_id") or "")]
    if not entries:
        return 0
    changed = 0
    with locked_store(split_keys=("assets",)) as (data, save):
        by_id = {str(item.get("id") or ""): item for item in data.get("assets", []) or []}
        for entry in entries:
            asset = by_id.get(str(entry["asset_id"]))
            if asset is None:
                continue
            if str(asset.get("file_path") or "") == str(entry.get("file_path") or ""):
                continue
            asset["file_path"] = str(entry.get("file_path") or "")
            if entry.get("file_name"):
                asset["file_name"] = str(entry["file_name"])
            for field in ("size_bytes", "width", "height"):
                if entry.get(field) is not None:
                    asset[field] = int(entry[field])
            if entry.get("file_hash"):
                asset["file_hash"] = str(entry["file_hash"])
            changed += 1
        if changed:
            save(data)
    return changed


# --- 商品素材生命周期：压缩后的引用改写与清理留痕 -----------------------------------
#
# 设计见 docs/superpowers/specs/2026-10-19-listing-asset-lifecycle-design.md。
# 压缩把 storage/uploads/base-products/<upload_id>/<文件夹>/ 下的 PNG 转成同名 .jpg，
# 于是「按文件名引用图片」的各处记录必须跟着改，否则素材就成了断链。

_COMPACTED_NAME_FIELDS = ("main_image", "size_chart_image")
_COMPACTED_NAME_LIST_FIELDS = ("product_images",)
_COMPACTED_NAME_MAP_FIELDS = ("variant_image_map",)
# listing_skus 上保存的是颜色图文件名，压缩后同样要改。
_COMPACTED_SKU_FIELDS = ("variant_image",)


def _normcase_path(value: Any) -> str:
    return os.path.normcase(os.path.normpath(str(value or "")))


def _remap_name(value: Any, name_map: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    return name_map.get(value, value)


def _remap_name_list(value: Any, name_map: dict[str, str]) -> Any:
    if not isinstance(value, list):
        return value
    return [name_map.get(item, item) if isinstance(item, str) else item for item in value]


def _remap_name_map(value: Any, name_map: dict[str, str]) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: (name_map.get(item, item) if isinstance(item, str) else item) for key, item in value.items()}


def _apply_asset_names(record: dict[str, Any], name_map: dict[str, str]) -> int:
    """把记录里精确等于旧文件名的引用改成新文件名，返回改了几处。

    只做整值替换，不做子串替换：``A白色.png`` 命中，``A白色备用.png`` 不命中。
    """

    if not name_map:
        return 0
    changed = 0
    for key in (*_COMPACTED_NAME_FIELDS, *_COMPACTED_SKU_FIELDS):
        if key not in record:
            continue
        before = record.get(key)
        after = _remap_name(before, name_map)
        if after != before:
            record[key] = after
            changed += 1
    for key in _COMPACTED_NAME_LIST_FIELDS:
        if key not in record:
            continue
        before = record.get(key)
        after = _remap_name_list(before, name_map)
        if after != before:
            record[key] = after
            changed += 1
    for key in _COMPACTED_NAME_MAP_FIELDS:
        if key not in record:
            continue
        before = record.get(key)
        after = _remap_name_map(before, name_map)
        if after != before:
            record[key] = after
            changed += 1
    return changed


def apply_asset_name_map(
    base_product_id: str,
    updates: list[dict[str, Any]],
    *,
    saved_bytes: int = 0,
    now: str = "",
) -> dict[str, Any]:
    """压缩素材后，在同一个事务里改写所有引用该文件名的记录。

    ``updates`` 每项形如
    ``{"old_name","new_name","old_path","new_path","size_bytes","width","height","file_hash"}``。
    只有真的换了扩展名（PNG → JPEG）的才需要改名字；已是 JPEG 的就地重压名字不变，
    但 ``assets`` 的 ``file_hash`` / ``size_bytes`` / ``width`` / ``height`` 仍要更新。

    改写范围：``assets``（按 ``file_path`` 精确匹配，回退到「同目录同名」，并要求落在该商品的
    ``asset_root`` 之内）、``base_products``、该商品的 ``store_listing_copies`` 与
    ``listing_skus``。历史快照（``automation_job_snapshots`` / ``listing_copy_revisions``）
    是审计证据，刻意不回写；重排队会按当前副本重建 payload。

    刻意不写 ``base_product.updated_at``：压缩不是人工编辑，不该影响列表的「最近修改」排序，
    也不该把商品从「已完成」的观感里推出去。
    """

    base_product_id = str(base_product_id or "").strip()
    entries = [
        item
        for item in (updates or [])
        if isinstance(item, dict) and str(item.get("old_name") or "").strip()
    ]
    if not base_product_id or not entries:
        return {"updated_assets": 0, "renamed": 0, "records": 0}
    name_map = {
        str(item["old_name"]): str(item.get("new_name") or item["old_name"]) for item in entries
    }
    renamed = sum(1 for old, new in name_map.items() if old != new)
    by_path = {_normcase_path(item.get("old_path")): item for item in entries if str(item.get("old_path") or "")}
    by_dir_name = {
        (_normcase_path(os.path.dirname(str(item.get("old_path") or ""))), str(item["old_name"])): item
        for item in entries
        if str(item.get("old_path") or "")
    }
    now_value = now or utc_now()
    summary = {"updated_assets": 0, "renamed": renamed, "records": 0}
    with locked_store(split_keys=("assets", "base_products", "listing_skus", "store_listing_copies")) as (data, save):
        base = next(
            (item for item in data.get("base_products", []) if item.get("id") == base_product_id),
            None,
        )
        if not base:
            return summary
        asset_root = _normcase_path(base.get("asset_root"))
        for asset in data.get("assets", []) or []:
            raw_path = str(asset.get("file_path") or "")
            key = _normcase_path(raw_path)
            entry = by_path.get(key)
            if entry is None:
                entry = by_dir_name.get((_normcase_path(os.path.dirname(raw_path)), str(asset.get("file_name") or "")))
            if entry is None:
                continue
            if asset_root and not (key == asset_root or key.startswith(asset_root + os.sep)):
                # 同样的文件名但不在这个商品的素材目录里：不动。
                continue
            asset["file_name"] = str(entry.get("new_name") or asset.get("file_name") or "")
            if str(entry.get("new_path") or ""):
                asset["file_path"] = str(entry["new_path"])
            if entry.get("size_bytes") is not None:
                asset["size_bytes"] = int(entry["size_bytes"])
            for field in ("width", "height"):
                if entry.get(field) is not None:
                    asset[field] = int(entry[field])
            if str(entry.get("file_hash") or ""):
                asset["file_hash"] = str(entry["file_hash"])
            summary["updated_assets"] += 1
        changed = _apply_asset_names(base, name_map)
        for copy in data.get("store_listing_copies", []) or []:
            if str(copy.get("base_product_id") or "") != base_product_id:
                continue
            changed += _apply_asset_names(copy, name_map)
        for sku in data.get("listing_skus", []) or []:
            if str(sku.get("base_product_id") or "") != base_product_id:
                continue
            changed += _apply_asset_names(sku, name_map)
        base["assets_compacted_at"] = now_value
        base["assets_compacted_bytes"] = int(base.get("assets_compacted_bytes") or 0) + max(0, int(saved_bytes or 0))
        summary["records"] = changed
        save(data)
    return summary


def mark_assets_cleaned(base_product_id: str, deleted_bytes: int = 0, *, now: str = "") -> dict[str, Any] | None:
    """素材目录被清理后留痕（只写主库，不动其它集合）。

    留痕是给操作员和就绪检查看的：素材被删掉的商品再上款会在执行器里报
    ``upload_file_missing``（``scripts/mabang_playwright_executor.cjs``）。
    """

    base_product_id = str(base_product_id or "").strip()
    if not base_product_id:
        return None
    now_value = now or utc_now()
    with locked_store(split_keys=("base_products",)) as (data, save):
        base = next(
            (item for item in data.get("base_products", []) if item.get("id") == base_product_id),
            None,
        )
        if not base:
            return None
        base["assets_cleaned_at"] = now_value
        base["assets_cleaned_bytes"] = int(base.get("assets_cleaned_bytes") or 0) + max(0, int(deleted_bytes or 0))
        save(data)
        return {
            "id": base_product_id,
            "assets_cleaned_at": now_value,
            "assets_cleaned_bytes": base["assets_cleaned_bytes"],
        }


def _listing_skus_in_store(data: dict[str, Any], listing_copy_id: str) -> list[dict[str, Any]]:
    """与 list_listing_skus 同序，但只读已经加载好的 data（避免重复整库加载）。"""

    rows = [dict(item) for item in data.get("listing_skus", []) if item.get("listing_copy_id") == listing_copy_id]
    copy = next((row for row in data.get("store_listing_copies", []) if row.get("id") == listing_copy_id), None) or {}
    size_rank = _listing_size_order_rank(copy, [row.get("size") for row in rows], data)
    return sorted(rows, key=lambda item: (
        item.get("color_code") or "",
        _ordered_size_sort_key(item.get("size"), size_rank),
        item.get("sku_code") or "",
    ))


def _listing_prices_in_store(data: dict[str, Any], listing_copy_id: str) -> list[dict[str, Any]]:
    rows = [dict(item) for item in data.get("listing_prices", []) if item.get("listing_copy_id") == listing_copy_id]
    return sorted(rows, key=lambda item: item.get("sku_code") or "")


def _assets_for_group_in_store(
    data: dict[str, Any],
    group_id: str,
    asset_root: str = "",
) -> list[dict[str, Any]]:
    normalized = (group_id or "").strip().upper()
    if not normalized:
        return []
    assets = [
        dict(asset)
        for asset in data.get("assets", [])
        if (asset.get("group_id") or "").strip().upper() == normalized
    ]
    assets.sort(key=lambda item: (
        item.get("asset_type") or "",
        item.get("file_name") or item.get("file_path") or "",
    ))
    # 同名文件会跨导入重复：有 asset_root 时只认这次导入目录（见 _scope_assets_to_asset_root）。
    return _scope_assets_to_asset_root(assets, asset_root)


def _template_attribute_values_for_copy(
    data: dict[str, Any],
    listing_copy: dict[str, Any],
) -> dict[str, Any]:
    """副本模板里配好、且**该副本生效类目确实有**的类目属性值。

    只取副本快照里存在的字段：模板配的是它自己类目的属性，生效类目可能不同
    （现场踩过：拿错类目的字段去填，页面上根本没有那个表单项）。
    """

    template = next(
        (item for item in data.get("store_templates", []) if item.get("id") == listing_copy.get("template_id")),
        None,
    ) or {}
    values = template.get("category_attributes") or template.get("attribute_values") or {}
    snapshot_fields = {
        str(field.get("field_id"))
        for field in ((listing_copy.get("category_profile_snapshot") or {}).get("fields") or [])
        if field.get("field_id")
    }
    return {
        str(field_id): deepcopy(value)
        for field_id, value in values.items()
        if str(field_id) in snapshot_fields and value not in ("", None, [], {})
    }


def _missing_template_attribute_fields(data: dict[str, Any], listing_copy: dict[str, Any]) -> list[str]:
    """模板配了、副本没值的字段 id（就绪提醒 + 补全动作共用同一套判断）。"""

    current = listing_copy.get("attribute_values") or listing_copy.get("category_attributes") or {}
    return sorted(
        field_id
        for field_id, value in _template_attribute_values_for_copy(data, listing_copy).items()
        if current.get(field_id) in ("", None, [], {})
    )


def fill_listing_copy_missing_attributes(listing_copy_ids: list[str] | str | None = None) -> dict[str, Any]:
    """把模板里的类目属性值补进副本**缺失**的字段（已有值一律不动）。

    现场背景：模板里配了「型号」，但早于该配置创建的副本自己 attribute_values 里没有，
    模板→副本同步按设计又不会改已有快照副本的属性值，于是执行器没值可填、刊登上就是空。
    这个动作只补缺，操作员之后仍可在模板里改、或再单独调整。
    """

    if isinstance(listing_copy_ids, str):
        listing_copy_ids = [item.strip() for item in listing_copy_ids.split(",") if item.strip()]
    wanted = [str(item or "").strip() for item in (listing_copy_ids or []) if str(item or "").strip()]
    filled_copies: list[dict[str, Any]] = []
    # 补全属性只动副本集合（模板在主库）：类目 / 任务 / 快照都不必解析（原 448ms → 约 170ms）。
    with locked_store(split_keys=("store_listing_copies",)) as (data, save):
        copies = data.get("store_listing_copies", [])
        targets = [item for item in copies if item.get("id") in wanted] if wanted else list(copies)
        for listing_copy in targets:
            missing = _missing_template_attribute_fields(data, listing_copy)
            if not missing:
                continue
            template_values = _template_attribute_values_for_copy(data, listing_copy)
            values = dict(listing_copy.get("attribute_values") or listing_copy.get("category_attributes") or {})
            for field_id in missing:
                values[field_id] = deepcopy(template_values[field_id])
            listing_copy["attribute_values"] = values
            listing_copy["category_attributes"] = deepcopy(values)
            listing_copy["updated_at"] = utc_now()
            filled_copies.append({
                "id": listing_copy.get("id"),
                "group_id": listing_copy.get("group_id") or "",
                "listing_mode": listing_copy.get("listing_mode") or "site",
                "filled_fields": missing,
            })
        if filled_copies:
            save(data)
    return {
        "ok": True,
        "filled_count": len(filled_copies),
        "scanned_count": len(targets),
        "filled": filled_copies,
    }


def _build_automation_job_in_store(
    data: dict[str, Any],
    listing_copy_id: str,
    *,
    now: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在**已加载**的 store 上构造一个自动化任务（不读盘、不保存）。

    现场反馈「生成会有卡顿」：原来每建一个任务都要 get_item / _runtime_configuration_missing /
    list_listing_skus / list_listing_prices / assets_for_group 各整库加载一次，再 locked_store()
    加载 + 保存一次 —— 34MB 的库上就是每个副本 ~4 秒的阻塞。这里改成调用方加载一次、保存一次。
    """

    payload = payload or {}
    listing_copy = next(
        (item for item in data.get("store_listing_copies", []) if item.get("id") == listing_copy_id),
        None,
    )
    if not listing_copy:
        return {
            "id": "",
            "listing_copy_id": listing_copy_id,
            "status": "failed",
            "missing_fields": ["listing_copy"],
            "error": "Listing copy not found",
        }
    skus = _listing_skus_in_store(data, listing_copy_id)
    prices = _listing_prices_in_store(data, listing_copy_id)
    readiness = _check_automation_readiness(
        listing_copy,
        skus=skus,
        prices=prices,
        expected_snapshot_hash=listing_copy.get("profile_snapshot_hash") or "",
    )
    configuration_missing = _runtime_configuration_missing(data, listing_copy, now=now)
    readiness["missing_fields"].extend(item for item in configuration_missing if item not in readiness["missing_fields"])
    _apply_automation_warning_policy(readiness)
    # 模板配了、副本缺值的属性（如「型号」）：执行器没值可填，刊登上会是空 —— 提醒操作员
    # 用「从模板补全缺失属性」补一次，别静默漏填。
    missing_from_template = _missing_template_attribute_fields(data, listing_copy)
    if missing_from_template:
        readiness["warnings"] = list(dict.fromkeys([
            *(readiness.get("warnings") or []),
            *[f"template_attribute_not_copied.{field_id}" for field_id in missing_from_template],
        ]))
    assets = _assets_for_group_in_store(
        data,
        listing_copy.get("group_id") or "",
        listing_copy.get("asset_root") or "",
    )
    browser_inputs = _build_browser_inputs(listing_copy, skus, prices, assets, data)
    # 传入 skus/prices 时执行快照要沿用界面顺序（见原实现注释）：list_listing_skus 按 color_code
    # 排序，browser_inputs.variation.colors 也由它生成，顺序不一致会把默认 SKU 插到中间。
    execution_snapshot = {
        "listing_copy": deepcopy(listing_copy),
        "skus": deepcopy(browser_inputs.get("skus") or skus),
        "prices": deepcopy(browser_inputs.get("prices") or prices),
        "browser_inputs": browser_inputs,
    }
    _dedupe_snapshot_rows(execution_snapshot)
    jobs = data.setdefault("automation_jobs", [])
    existing = next(
        (
            item
            for item in jobs
            if item.get("listing_copy_id") == listing_copy_id
            and item.get("status") in _ACTIVE_AUTOMATION_STATUSES
        ),
        None,
    )
    if existing:
        raise ActiveAutomationJobExists("该店铺刊登副本已有活动任务")
    return {
        "id": str(uuid.uuid4()),
        "job_type": "mabang_browser_use",
        "listing_copy_id": listing_copy_id,
        "profile_snapshot_hash": listing_copy.get("profile_snapshot_hash") or "",
        "listing_copy_revision": int(listing_copy.get("revision") or 1),
        "execution_snapshot": execution_snapshot,
        "execution_snapshot_hash": _stable_hash(execution_snapshot),
        "store_name": listing_copy.get("store_name") or "",
        "platform": listing_copy.get("platform") or "shopee",
        "country": listing_copy.get("country") or "",
        # 任务归属的马帮账号：由店铺推导。Worker 执行前据此选择 CDP/Profile 与登录凭据；
        # 店铺不跨账号，所以「页面选中店铺 id == 任务店铺 id」同时锁定了账号。
        "account_id": _account_id_for_listing_copy_in_store(data, listing_copy),
        "status": "ready" if readiness["ready"] else "needs_manual",
        "missing_fields": readiness["missing_fields"],
        "warnings": readiness.get("warnings") or [],
        "notes": payload.get("notes") or "",
        "browser_session_id": payload.get("browser_session_id") or "",
        "last_error": "",
        "worker_id": "",
        "lease_token": "",
        "lease_expires_at": "",
        "attempt_count": 0,
        "current_phase": "ready" if readiness["ready"] else "needs_manual",
        "result": {},
        "run_log": [],
        "created_at": now,
        "updated_at": now,
    }


def create_automation_job_from_listing_copy(listing_copy_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    listing_copy_id = str(listing_copy_id or "").strip()
    snapshot = None
    with locked_store(
        # 建任务/重排队要读副本、SKU、价格、素材来算就绪度并冻结快照：
        # 这些都是拆分集合，必须点名加载，否则读到的全是空。
        # 写只落任务与快照两个文件（write_keys），读过的集合不付序列化比对的成本。
        split_keys=(
            "automation_jobs",
            "automation_job_snapshots",
            "store_listing_copies",
            "listing_skus",
            "listing_prices",
            "assets",
        ),
        write_keys=("automation_jobs", "automation_job_snapshots"),
        main_readonly=True,
    ) as (data, save):
        job = _build_automation_job_in_store(data, listing_copy_id, now=utc_now(), payload=payload)
        if not job.get("id"):
            return job
        # 快照进独立集合：任务文件从此只有约 300KB，领取/心跳/进度不背着 17MB。
        snapshot = job.pop("execution_snapshot", None)
        if snapshot is not None:
            data.setdefault("automation_job_snapshots", {})[str(job["id"])] = snapshot
        data.setdefault("automation_jobs", []).append(job)
        save(data)
    result = deepcopy(job)
    if snapshot is not None:
        # 调用方（与既有测试）仍按原来的形状拿到快照，只是不再落进任务文件。
        result["execution_snapshot"] = deepcopy(snapshot)
    return result


def create_automation_jobs_from_listing_copies(listing_copy_ids: list[str] | str) -> dict[str, Any]:
    """批量建任务：整库只加载/保存各一次（原来每个副本一轮，现场表现为长时间卡顿）。

    单个副本失败（副本不存在 / 已有活动任务）只记进 errors，不影响同批其他副本。
    """

    if isinstance(listing_copy_ids, str):
        listing_copy_ids = [item.strip() for item in listing_copy_ids.split(",") if item.strip()]
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    # 生成任务需要主库（副本/SKU/价格）+ 任务集合 + 快照集合：不解析 13.1MB 的类目，
    # 也不重写主库（main_readonly：省掉 85ms 的整库序列化比对）。
    with locked_store(
        # 建任务/重排队要读副本、SKU、价格、素材来算就绪度并冻结快照：
        # 这些都是拆分集合，必须点名加载，否则读到的全是空。
        # 写只落任务与快照两个文件（write_keys），读过的集合不付序列化比对的成本。
        split_keys=(
            "automation_jobs",
            "automation_job_snapshots",
            "store_listing_copies",
            "listing_skus",
            "listing_prices",
            "assets",
        ),
        write_keys=("automation_jobs", "automation_job_snapshots"),
        main_readonly=True,
    ) as (data, save):
        now = utc_now()
        for listing_copy_id in listing_copy_ids:
            try:
                job = _build_automation_job_in_store(data, str(listing_copy_id or "").strip(), now=now)
            except ActiveAutomationJobExists as exc:
                errors.append({
                    "id": "",
                    "listing_copy_id": str(listing_copy_id or "").strip(),
                    "status": "failed",
                    "error": str(exc),
                    "reason": "active_job_exists",
                })
                continue
            if not job.get("id"):
                errors.append(job)
                continue
            snapshot = job.pop("execution_snapshot", None)
            if snapshot is not None:
                data.setdefault("automation_job_snapshots", {})[str(job["id"])] = snapshot
            data.setdefault("automation_jobs", []).append(job)
            created_job = deepcopy(job)
            if snapshot is not None:
                created_job["execution_snapshot"] = deepcopy(snapshot)
            created.append(created_job)
        if created:
            save(data)
    return {"ok": not errors, "created": created, "errors": errors, "created_count": len(created), "error_count": len(errors)}


def requeue_automation_job(job_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """把已终止的任务原地重排（自动化列表的「再次尝试」按钮）。

    与 ``create_automation_job_from_listing_copy`` 同一套就绪度/快照重建逻辑，但写回**同一条**
    job，让操作员点一次「再次尝试」看到的是同一行从"失败"回到"待执行"，而不是每次多一行。
    执行快照会按当前副本重新冻结（店铺/模板在此期间被改过也能跟上）。

    ``attempt_count`` 归零——否则 worker 的领取预算（``claim_next_automation_job``
    只在 attempts < max_attempts 时领取）会被预先消耗，原任务再也领不出来；操作员
    主动重试计入 ``retry_count``，不会被自动重试预算吞掉。

    读取与写回都在同一次整库读写里完成（原来这里同样是每个副本 ~6 次整库加载）。
    """

    payload = payload or {}
    job_id = str(job_id or "").strip()
    if not job_id:
        raise LookupError("Automation job not found")
    # 重排队读主库（副本/SKU/价格）但只写任务与快照；原 1145ms → 约 220ms。
    with locked_store(
        # 建任务/重排队要读副本、SKU、价格、素材来算就绪度并冻结快照：
        # 这些都是拆分集合，必须点名加载，否则读到的全是空。
        # 写只落任务与快照两个文件（write_keys），读过的集合不付序列化比对的成本。
        split_keys=(
            "automation_jobs",
            "automation_job_snapshots",
            "store_listing_copies",
            "listing_skus",
            "listing_prices",
            "assets",
        ),
        write_keys=("automation_jobs", "automation_job_snapshots"),
        main_readonly=True,
    ) as (data, save):
        target = next((item for item in data.get("automation_jobs", []) if item.get("id") == job_id), None)
        if not target:
            raise LookupError("Automation job not found")
        status = str(target.get("status") or "").strip()
        if status not in AUTOMATION_RETRYABLE_STATUSES:
            raise ValueError(f"Only a terminated automation job can be requeued. Current status: {status}")
        listing_copy_id = str(target.get("listing_copy_id") or "").strip()
        if not listing_copy_id:
            raise ValueError("Automation job has no listing_copy_id, cannot requeue")
        listing_copy = next(
            (item for item in data.get("store_listing_copies", []) if item.get("id") == listing_copy_id),
            None,
        )
        if not listing_copy:
            return {
                "id": "",
                "job_id": job_id,
                "listing_copy_id": listing_copy_id,
                "status": "failed",
                "missing_fields": ["listing_copy"],
                "error": "Listing copy not found",
            }
        now = utc_now()
        skus = _listing_skus_in_store(data, listing_copy_id)
        prices = _listing_prices_in_store(data, listing_copy_id)
        readiness = _check_automation_readiness(
            listing_copy,
            skus=skus,
            prices=prices,
            expected_snapshot_hash=listing_copy.get("profile_snapshot_hash") or "",
        )
        configuration_missing = _runtime_configuration_missing(data, listing_copy, now=now)
        readiness["missing_fields"].extend(item for item in configuration_missing if item not in readiness["missing_fields"])
        _apply_automation_warning_policy(readiness)
        assets = _assets_for_group_in_store(
        data,
        listing_copy.get("group_id") or "",
        listing_copy.get("asset_root") or "",
    )
        browser_inputs = _build_browser_inputs(listing_copy, skus, prices, assets, data)
        execution_snapshot = {
            "listing_copy": deepcopy(listing_copy),
            "skus": deepcopy(browser_inputs.get("skus") or skus),
            "prices": deepcopy(browser_inputs.get("prices") or prices),
            "browser_inputs": browser_inputs,
        }
        _dedupe_snapshot_rows(execution_snapshot)
        # 同一个副本只允许一条活动任务：就地重排的前提是没有别的活动任务，
        # 否则会绕过"一条副本一条活动任务"的约束，产生重复草稿。
        conflict = next(
            (
                item
                for item in data.get("automation_jobs", [])
                if item is not target
                and item.get("listing_copy_id") == listing_copy_id
                and item.get("status") in _ACTIVE_AUTOMATION_STATUSES
            ),
            None,
        )
        if conflict:
            raise ActiveAutomationJobExists("该店铺刊登副本已有活动任务")
        previous_attempts = int(target.get("attempt_count") or 0)
        previous_error = str(target.get("last_error") or (target.get("result") or {}).get("error") or "")
        retry_count = int(target.get("retry_count") or 0) + 1
        target.update(
            {
                "profile_snapshot_hash": listing_copy.get("profile_snapshot_hash") or "",
                "listing_copy_revision": int(listing_copy.get("revision") or 1),
                "execution_snapshot_hash": _stable_hash(execution_snapshot),
                "store_name": listing_copy.get("store_name") or "",
                "platform": listing_copy.get("platform") or "shopee",
                "country": listing_copy.get("country") or "",
                "status": "ready" if readiness["ready"] else "needs_manual",
                "current_phase": "ready" if readiness["ready"] else "needs_manual",
                "missing_fields": readiness["missing_fields"],
                "warnings": readiness.get("warnings") or [],
                "last_error": "",
                "attempt_count": 0,
                "retry_count": retry_count,
                "worker_id": "",
                "lease_token": "",
                "lease_expires_at": "",
                "claimed_at": "",
                "finished_at": "",
                "result": {},
                "browser_session_id": payload.get("browser_session_id") or "",
                "updated_at": now,
            }
        )
        # 快照进独立集合（任务文件里不再背 17MB）。
        target.pop("execution_snapshot", None)
        data.setdefault("automation_job_snapshots", {})[job_id] = execution_snapshot
        if payload.get("notes"):
            target["notes"] = payload["notes"]
        # 历史不丢：把上一轮的错误写进事件里，操作员重试时仍能看到原因。
        _append_automation_job_event(
            target,
            "requeued",
            now=now,
            previous_status=status,
            attempt_count=previous_attempts,
            error=previous_error,
            retry_count=retry_count,
        )
        save(data)
        result = deepcopy(target)
        # 与建任务保持同一形状：调用方仍能拿到这次冻结的快照（只是不再存进任务文件）。
        result["execution_snapshot"] = deepcopy(execution_snapshot)
        return result


def cancel_automation_job(job_id: str, *, reason: str) -> dict[str, Any]:
    # 取消只动任务本身：不解析类目 / 快照，也不重写主库。
    with locked_store(split_keys=("automation_jobs",), main_readonly=True) as (data, save):
        job = next((item for item in data.get("automation_jobs", []) if item.get("id") == job_id), None)
        if not job:
            raise LookupError("Automation job not found")
        if job.get("status") not in {"ready", "claimed", "needs_manual", "needs_regeneration"}:
            raise ValueError("Only a non-running automation job can be canceled")
        now = utc_now()
        job.update(
            {
                "status": "canceled",
                "current_phase": "canceled",
                "canceled_reason": str(reason or "").strip(),
                "lease_token": "",
                "lease_expires_at": "",
                "updated_at": now,
            }
        )
        _append_automation_job_event(job, "canceled", now=now, error=job["canceled_reason"])
        save(data)
        return deepcopy(job)


def delete_automation_job(job_id: str) -> dict[str, Any] | None:
    job_id = str(job_id or "").strip()
    if not job_id:
        return None
    with locked_store(split_keys=("automation_jobs", "automation_job_snapshots")) as (data, save):
        jobs = data.get("automation_jobs", [])
        job = next((item for item in jobs if item.get("id") == job_id), None)
        if not job:
            return None
        if str(job.get("status") or "").lower() in {"claimed", "running"}:
            raise ValueError("Only a non-running automation job can be deleted")
        jobs.remove(job)
        snapshots = data.get("automation_job_snapshots")
        if isinstance(snapshots, dict):
            snapshots.pop(job_id, None)
        save(data)
        return {"id": job_id, "deleted": True}


def _normalize_execution_stages(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    allowed_statuses = {
        "succeeded", "skipped", "warning", "failed", "needs_confirmation", "applied", "uploaded",
        "attached",
    }
    allowed_fields = {
        "status", "started_at", "finished_at", "code", "message", "details", "evidence",
        "role", "template_id", "template_name", "image_positions", "file_name", "method",
        # 水印阶段回传：image_range 是「该作用在哪几张图」的意图（main=first / product=from_second…），
        # verified 是页面回读证据；丢了执行详情里就只剩模板名，看不出范围对不对。
        "image_range", "verified",
        # 视频阶段回传：video_id 是「首次上传后记录到模板 default_video_id」的数据来源，
        # 丢了就永远记不上；video_url/source/upload_url 供排查与展示。
        "video_id", "video_url", "source", "upload_url",
    }
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_stage in value.items():
        name = str(raw_name or "").strip()
        if not name or name == "warnings" or not isinstance(raw_stage, dict):
            continue
        status = str(raw_stage.get("status") or "").strip().lower()
        if status not in allowed_statuses:
            continue
        stage = {key: deepcopy(raw_stage[key]) for key in allowed_fields if key in raw_stage}
        stage["status"] = status
        for key in ("details", "evidence"):
            if key in stage:
                stage[key] = _sanitize_execution_stage_value(stage[key])
        normalized[name] = stage
    return normalized


def _sanitize_execution_stage_value(value: Any) -> Any:
    sensitive_fragments = ("token", "api_key", "apikey", "password", "secret", "traceback", "url")
    if isinstance(value, dict):
        return {
            str(key): _sanitize_execution_stage_value(item)
            for key, item in value.items()
            if not any(fragment in str(key).lower() for fragment in sensitive_fragments)
        }
    if isinstance(value, list):
        return [_sanitize_execution_stage_value(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:500]
    return value


def build_automation_payload(job_id: str) -> dict[str, Any]:
    job = get_item("automation_jobs", job_id)
    if not job:
        return {"ok": False, "error": "Automation job not found"}
    store = load_store()
    snapshots = store.get("automation_job_snapshots") or {}
    # 迁移期兼容：旧库里快照还内联在任务上。
    snapshot = deepcopy(job.get("execution_snapshot") or snapshots.get(str(job_id)) or {})
    if snapshot:
        missing_fields = list(job.get("missing_fields") or [])
        warnings = list(job.get("warnings") or [])
        expected_execution_hash = str(job.get("execution_snapshot_hash") or "").strip()
        if expected_execution_hash and _stable_hash(snapshot) != expected_execution_hash:
            missing_fields.append("execution_snapshot_changed")
        current_copy = get_item("store_listing_copies", job.get("listing_copy_id") or "")
        runtime_missing = _runtime_configuration_missing(store, snapshot.get("listing_copy") or {}, now=utc_now())
        for field in runtime_missing:
            if field in AUTOMATION_WARNING_FIELDS:
                if field not in warnings:
                    warnings.append(field)
            elif field not in missing_fields:
                missing_fields.append(field)
        expected_hash = str(job.get("profile_snapshot_hash") or "").strip()
        current_hash = str((current_copy or {}).get("profile_snapshot_hash") or "").strip()
        if expected_hash and current_hash != expected_hash and "profile_snapshot_changed" not in missing_fields:
            missing_fields.append("profile_snapshot_changed")
        browser_inputs = dict(snapshot.get("browser_inputs") or {})
        # 快照里去重后 browser_inputs 不再自带 skus/prices；执行器优先读它
        # （scripts/browser_use_runner.py:357），这里按去重前的形状补回去，
        # 内容就是顶层那份（网格顺序不变）。
        if snapshot.get("skus"):
            browser_inputs["skus"] = snapshot["skus"]
        if snapshot.get("prices"):
            browser_inputs["prices"] = snapshot["prices"]
        return {
            "ok": True,
            "job": job,
            "listing_copy": snapshot.get("listing_copy") or {},
            "skus": snapshot.get("skus") or [],
            "prices": snapshot.get("prices") or [],
            "assets": [],
            "readiness": {
                "ready": job.get("status") in {"ready", "claimed", "running", "saved"} and not missing_fields,
                "status": "configuration_expired" if missing_fields else job.get("status") or "",
                "missing_fields": missing_fields,
                "warnings": warnings,
            },
            "browser_inputs": browser_inputs,
            "execution_snapshot": snapshot,
            "execution_snapshot_hash": job.get("execution_snapshot_hash") or "",
        }
    listing_copy = get_item("store_listing_copies", job.get("listing_copy_id") or "")
    if not listing_copy:
        return {"ok": False, "error": "Listing copy not found", "job": job}
    skus = list_listing_skus(listing_copy.get("id") or "")
    prices = list_listing_prices(listing_copy.get("id") or "")
    # 只取这次导入目录的素材：同名文件在重复导入后会有两份，混着取会拿到旧图。
    assets = assets_for_base_product(listing_copy)
    readiness = _check_automation_readiness(
        listing_copy,
        skus=skus,
        prices=prices,
        expected_snapshot_hash=job.get("profile_snapshot_hash") or "",
    )
    _apply_automation_warning_policy(readiness)
    browser_inputs = _build_browser_inputs(listing_copy, skus, prices, assets)
    return {
        "ok": True,
        "job": job,
        "listing_copy": listing_copy,
        # 与 create_automation_job_from_listing_copy 同样：顶层这份必须是网格顺序，
        # 否则执行器按行号填写时会在默认 SKU 块处整块错行。
        "skus": browser_inputs.get("skus") or skus,
        "prices": browser_inputs.get("prices") or prices,
        "assets": assets,
        "readiness": readiness,
        "browser_inputs": browser_inputs,
    }


def create_product(payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    category = payload.get("category") or "POD 男装T恤"
    template = get_product_template(category)
    group_id = str(payload.get("group_id") or "").strip()
    base_name = _sanitize_product_name_candidate(payload.get("base_name") or "") or group_id
    product_name = _sanitize_product_name_candidate(payload.get("product_name_cn") or "") or base_name
    listing_title = _sanitize_listing_title_candidate(
        payload.get("listing_title") or payload.get("generated_title") or payload.get("source_title") or ""
    )
    if not listing_title:
        listing_title = _build_default_listing_title(template, product_name, group_id)
    category_path = _resolve_marketplace_category_path(payload, template)
    product = {
        "id": str(uuid.uuid4()),
        "category": category,
        "platform": payload.get("platform") or "shopee",
        "store": payload.get("store") or "",
        "group_id": group_id,
        "target_audience": payload.get("target_audience") or template.get("target_audience") or "",
        "marketplace_category_path": category_path,
        "marketplace_category_source": _resolve_marketplace_category_source(payload, template, category_path),
        "brand": payload.get("brand") or "",
        "attribute_brand": payload.get("attribute_brand") or template.get("attribute_brand") or "",
        "attribute_gender": payload.get("attribute_gender") or template.get("attribute_gender") or "",
        "attribute_material": payload.get("attribute_material") or template.get("attribute_material") or "",
        "attribute_sleeve_length": payload.get("attribute_sleeve_length") or template.get("attribute_sleeve_length") or "",
        "attribute_collar": payload.get("attribute_collar") or template.get("attribute_collar") or "",
        "base_name": base_name,
        "product_name_cn": product_name,
        "source_title": payload.get("source_title") or "",
        "generated_title": payload.get("generated_title") or "",
        "listing_title": listing_title,
        "description": payload.get("description") or template.get("description") or "",
        "template_id": payload.get("template_id") or template.get("template_id") or "",
        "material": payload.get("material") or template.get("attribute_material") or "",
        "gender": payload.get("gender") or template.get("attribute_gender") or "",
        "fit": payload.get("fit") or "",
        "weight_kg": _float_or_empty(payload.get("weight_kg") or template.get("weight_kg")),
        "length_cm": _float_or_empty(payload.get("length_cm") or template.get("length_cm")),
        "width_cm": _float_or_empty(payload.get("width_cm") or template.get("width_cm")),
        "height_cm": _float_or_empty(payload.get("height_cm") or template.get("height_cm")),
        "asset_root": payload.get("asset_root") or "",
        "main_image": payload.get("main_image") or "",
        "variant_count": int(payload.get("variant_count") or 0),
        "status": payload.get("status") or "draft",
        "created_at": now,
        "updated_at": now,
    }
    with locked_store() as (data, save):
        data["products"].append(product)
        save(data)
    return product


def create_product_from_assets(payload: dict[str, Any]) -> dict[str, Any] | None:
    group_id = (payload.get("group_id") or "").strip()
    if not group_id:
        return None
    existing = find_product_by_group(group_id)
    if existing:
        existing["already_exists"] = True
        return existing
    # 同一个 SKU 可能导入过多次：只按分组取素材会把多次导入的图片混成一个商品。
    assets = assets_for_base_product({"group_id": group_id, "asset_root": payload.get("asset_root")})
    if not assets:
        return None

    main = _pick_main_asset(assets)
    category = payload.get("category") or _most_common([asset.get("category") for asset in assets]) or "POD 男装T恤"
    template = get_product_template(category)
    product_name = payload.get("product_name_cn") or _derive_product_name(group_id, assets)
    main_sku = str(payload.get("main_sku") or "").strip()
    derived = _derive_main_sku_from_folder_name(str(payload.get("asset_root") or group_id))
    if not main_sku:
        main_sku = derived or group_id
    if _auto_assign_sku_enabled(payload) and not derived:
        assigned = _allocate_auto_main_sku()
        if assigned:
            main_sku = assigned
    product = create_product({
        **payload,
        "category": category,
        "group_id": group_id,
        "main_sku": main_sku,
        "product_name_cn": product_name,
        "base_name": product_name,
        "asset_root": payload.get("asset_root") or _common_asset_root(assets),
        "main_image": main.get("file_name") if main else "",
        "variant_count": len([asset for asset in assets if asset.get("asset_type") == "variant"]),
        "template_id": template.get("template_id") or "",
    })
    sizes = payload.get("sizes") or template.get("sizes") or ["S", "M", "L", "XL", "2XL"]
    if isinstance(sizes, str):
        sizes = [item.strip() for item in sizes.split(",") if item.strip()]
    if payload.get("auto_skus", True):
        replace_product_skus(product["id"], _generate_skus_from_assets(product, assets, list(sizes)))
    if payload.get("auto_prices", True):
        replace_product_prices(product["id"], _generate_prices_from_skus(product, list_skus(product["id"])))
    return {**product, "generated_sku_count": len(list_skus(product["id"])), "generated_price_count": len(list_prices_for_product(product["id"]))}


def create_products_from_asset_groups(payload: dict[str, Any]) -> dict[str, Any]:
    group_ids = payload.get("group_ids") or []
    if isinstance(group_ids, str):
        group_ids = [item.strip() for item in group_ids.split(",") if item.strip()]
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_group_id in group_ids:
        group_id = str(raw_group_id or "").strip()
        key = group_id.upper()
        if not key or key in seen:
            continue
        seen.add(key)
        existing = find_product_by_group(group_id)
        if existing:
            skipped.append({"group_id": group_id, "reason": "already_exists", "product_id": existing.get("id")})
            continue
        product = create_product_from_assets({**payload, "group_id": group_id})
        if product:
            created.append(product)
        else:
            errors.append({"group_id": group_id, "reason": "no_assets"})
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "created_count": len(created),
        "skipped_count": len(skipped),
        "error_count": len(errors),
    }


def update_product(product_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    editable_fields = {
        "category",
        "platform",
        "store",
        "group_id",
        "target_audience",
        "marketplace_category_path",
        "marketplace_category_source",
        "brand",
        "attribute_brand",
        "attribute_gender",
        "attribute_material",
        "attribute_sleeve_length",
        "attribute_collar",
        "base_name",
        "product_name_cn",
        "source_title",
        "generated_title",
        "listing_title",
        "description",
        "template_id",
        "material",
        "gender",
        "fit",
        "weight_kg",
        "length_cm",
        "width_cm",
        "height_cm",
        "asset_root",
        "main_image",
        "variant_count",
        "status",
    }
    with locked_store() as (data, save):
        for product in data["products"]:
            if product.get("id") != product_id:
                continue
            for key in editable_fields:
                if key not in payload:
                    continue
                if key in {"weight_kg", "length_cm", "width_cm", "height_cm"}:
                    product[key] = _float_or_empty(payload.get(key))
                elif key == "variant_count":
                    product[key] = int(payload.get(key) or 0)
                else:
                    product[key] = payload.get(key) or ""
            template = get_product_template(product.get("category") or "")
            _apply_template_defaults(product, template)
            product["updated_at"] = utc_now()
            save(data)
            return dict(product)
    return None


def list_skus(product_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        skus = [dict(sku) for sku in load_store()["skus"] if sku.get("product_id") == product_id]
    return sorted(skus, key=lambda sku: (sku.get("color_code") or "", _size_sort_value(sku.get("size") or ""), sku.get("sku_code") or ""))


def list_prices_for_product(product_id: str) -> list[dict[str, Any]]:
    skus = list_skus(product_id)
    sku_ids = {sku.get("id") for sku in skus if sku.get("id")}
    with _LOCK:
        prices = [
            dict(price)
            for price in load_store()["prices"]
            if price.get("product_id") == product_id or price.get("sku_id") in sku_ids
        ]
    return sorted(prices, key=lambda price: price.get("sku_code") or price.get("sku_id") or "")


def replace_product_skus(product_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    product = get_item("products", product_id)
    if not product:
        return {"ok": False, "error": "Product not found"}
    now = utc_now()
    normalized = []
    for row in rows:
        sku = {
            "id": row.get("id") or str(uuid.uuid4()),
            "product_id": product_id,
            "sku_code": row.get("sku_code") or "",
            "group_id": row.get("group_id") or product.get("group_id") or "",
            "color_code": row.get("color_code") or "",
            "color_cn": row.get("color_cn") or "",
            "size": row.get("size") or "",
            "variant_name": row.get("variant_name") or "",
            "stock": int(row.get("stock") or 0),
            "weight_kg": _float_or_empty(row.get("weight_kg") or product.get("weight_kg")),
            "length_cm": _float_or_empty(row.get("length_cm") or product.get("length_cm")),
            "width_cm": _float_or_empty(row.get("width_cm") or product.get("width_cm")),
            "height_cm": _float_or_empty(row.get("height_cm") or product.get("height_cm")),
            "variant_image": row.get("variant_image") or "",
            "status": row.get("status") or "active",
            "updated_at": now,
        }
        normalized.append(sku)
    with locked_store() as (data, save):
        data["skus"] = [sku for sku in data["skus"] if sku.get("product_id") != product_id] + normalized
        save(data)
    return {"ok": True, "items": normalized, "count": len(normalized)}


def replace_product_prices(product_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    product = get_item("products", product_id)
    if not product:
        return {"ok": False, "error": "Product not found"}
    sku_by_code = {sku.get("sku_code"): sku for sku in list_skus(product_id)}
    template = get_product_template(product.get("category") or "")
    price_rule = template.get("price") or {}
    now = utc_now()
    normalized = []
    for row in rows:
        sku_code = row.get("sku_code") or ""
        sku = sku_by_code.get(sku_code, {})
        is_large = _is_large_size(str(sku.get("size") or row.get("size") or ""))
        default_cost = price_rule.get("large_cost") if is_large else price_rule.get("cost")
        normalized.append({
            "id": row.get("id") or str(uuid.uuid4()),
            "product_id": product_id,
            "sku_id": row.get("sku_id") or sku.get("id") or "",
            "sku_code": sku_code,
            "platform": row.get("platform") or product.get("platform") or "shopee",
            "store": row.get("store") or product.get("store") or "",
            "country": row.get("country") or "MY",
            "currency": row.get("currency") or "MYR",
            "cost_price": _float_or_empty(row.get("cost_price") or default_cost),
            "shipping_cost": _float_or_empty(row.get("shipping_cost") or price_rule.get("shipping_cost") or 0),
            "fee_rate": _float_or_empty(row.get("fee_rate") or 0.08),
            "margin_rate": _float_or_empty(row.get("margin_rate") or 0.35),
            "normal_price": _float_or_empty(row.get("normal_price") or (price_rule.get("large_normal") if is_large else price_rule.get("normal"))),
            "sale_price": _float_or_empty(row.get("sale_price") or (price_rule.get("large_sale") if is_large else price_rule.get("sale"))),
            "promo_price": row.get("promo_price") or "",
            "status": row.get("status") or "active",
            "updated_at": now,
        })
    with locked_store() as (data, save):
        data["prices"] = [price for price in data["prices"] if price.get("product_id") != product_id] + normalized
        save(data)
    return {"ok": True, "items": normalized, "count": len(normalized)}


def list_products_with_summary() -> list[dict[str, Any]]:
    with _LOCK:
        data = load_store()
        products = [dict(product) for product in data.get("products", [])]
        sku_counts = Counter(sku.get("product_id") for sku in data.get("skus", []))
        price_counts = Counter(price.get("product_id") for price in data.get("prices", []))
        asset_counts = Counter((asset.get("group_id") or "").strip().upper() for asset in data.get("assets", []))
        readiness_by_id = _readiness_by_product(data, products)
    for product in products:
        product_id = product.get("id") or ""
        readiness = readiness_by_id.get(str(product_id)) or check_product_readiness(product_id)
        product["sku_count"] = int(sku_counts.get(product_id, 0))
        product["price_count"] = int(price_counts.get(product_id, 0))
        product["asset_count"] = int(asset_counts.get((product.get("group_id") or "").strip().upper(), 0))
        product["readiness"] = readiness
        product["business_status"] = _product_business_status(product, readiness)
    return sorted(products, key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)


def _product_readiness_checks(
    product: dict[str, Any],
    *,
    has_main_asset: bool,
    skus: list[dict[str, Any]],
    prices: list[dict[str, Any]],
) -> dict[str, Any]:
    """就绪判定的共用实现：调用方负责把 assets/skus/prices 备好。"""

    has_cost = any(_value_present(price.get("cost_price")) for price in prices)
    has_weight = _value_present(product.get("weight_kg")) or any(_value_present(sku.get("weight_kg")) for sku in skus)
    has_dimensions = all(_value_present(product.get(key)) for key in ["length_cm", "width_cm", "height_cm"]) or any(
        all(_value_present(sku.get(key)) for key in ["length_cm", "width_cm", "height_cm"]) for sku in skus
    )
    category = str(product.get("category") or "").strip()
    checks = [
        _readiness_check("product_identity", "商品基础信息", bool(product.get("group_id") and (product.get("product_name_cn") or product.get("base_name"))), "缺少 group_id 或商品名称。"),
        _readiness_check("title", "标题", bool(str(product.get("listing_title") or product.get("generated_title") or "").strip()), "缺少商品标题。"),
        _readiness_check("category", "类目", category in PRODUCT_TEMPLATES, "商品类目不在当前马帮模板映射中。"),
        _readiness_check("marketplace_category", "平台分类", bool(str(product.get("marketplace_category_path") or "").strip()), "缺少平台分类路径。", severity="warning"),
        _readiness_check("main_image", "主图", has_main_asset, "缺少主图文件名或主图素材。"),
        _readiness_check("sku_rows", "SKU", bool(skus), "缺少 SKU。"),
        _readiness_check("cost_price", "成本价", has_cost, "缺少 SKU 成本价。"),
        _readiness_check("weight", "重量", has_weight, "缺少重量。"),
        _readiness_check("dimensions", "尺寸", has_dimensions, "缺少长宽高。"),
    ]
    blockers = [check["code"] for check in checks if not check["ok"] and check["severity"] == "blocking"]
    warnings = [check["code"] for check in checks if not check["ok"] and check["severity"] == "warning"]
    return {
        "ready": not blockers,
        "status": "ready" if not blockers else "needs_check",
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }


def check_product_readiness(product_id: str) -> dict[str, Any]:
    product = get_item("products", product_id)
    if not product:
        return {"ready": False, "status": "missing", "checks": [], "blockers": ["product_not_found"], "warnings": []}
    assets = assets_for_base_product(product)
    main_image = str(product.get("main_image") or "").strip()
    has_main_asset = bool(main_image) or any((asset.get("asset_type") or "").lower() == "main" for asset in assets)
    return _product_readiness_checks(
        product,
        has_main_asset=has_main_asset,
        skus=list_skus(product_id),
        prices=list_prices_for_product(product_id),
    )


def _readiness_indexes(
    data: dict[str, Any],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    """把素材 / SKU / 价格按归属各建一次索引。

    现场问题：deployment_readiness 与商品列表摘要都对每个商品单独调一次
    check_product_readiness，而它每次都把 2549 个素材、5118 个 SKU、5118 条价格
    整表扫一遍 —— 91 个商品就是上百万次字典比较（实测 618ms，而且是首屏每次都付）。
    索引只建一次，之后每个商品只取自己那几行。
    """

    assets_by_group: dict[str, list[dict[str, Any]]] = {}
    for asset in data.get("assets") or []:
        if isinstance(asset, dict):
            assets_by_group.setdefault((asset.get("group_id") or "").strip().upper(), []).append(asset)
    skus_by_product: dict[str, list[dict[str, Any]]] = {}
    for sku in data.get("skus") or []:
        if isinstance(sku, dict):
            skus_by_product.setdefault(str(sku.get("product_id") or ""), []).append(sku)
    prices_by_product: dict[str, list[dict[str, Any]]] = {}
    prices_by_sku: dict[str, list[dict[str, Any]]] = {}
    for price in data.get("prices") or []:
        if isinstance(price, dict):
            prices_by_product.setdefault(str(price.get("product_id") or ""), []).append(price)
            sku_id = str(price.get("sku_id") or "")
            if sku_id:
                prices_by_sku.setdefault(sku_id, []).append(price)
    return assets_by_group, skus_by_product, prices_by_product, prices_by_sku


def _has_main_asset_from_index(
    product: dict[str, Any],
    assets_by_group: dict[str, list[dict[str, Any]]],
) -> bool:
    """与 assets_for_base_product + 「有没有 main 素材」同口径，但不复制整个分组。"""

    if str(product.get("main_image") or "").strip():
        return True
    assets = assets_by_group.get((product.get("group_id") or "").strip().upper()) or []
    root = str(product.get("asset_root") or "").strip()
    if root:
        normalized = os.path.normcase(os.path.normpath(root))
        scoped = [
            asset for asset in assets
            if _path_within_dir(str(asset.get("file_path") or ""), normalized)
            or str(asset.get("origin") or "") == MANUAL_UPLOAD_ORIGIN
        ]
        assets = scoped or assets
    return any((asset.get("asset_type") or "").lower() == "main" for asset in assets)


def _product_prices_from_index(
    product_id: str,
    skus: list[dict[str, Any]],
    prices_by_product: dict[str, list[dict[str, Any]]],
    prices_by_sku: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """与 list_prices_for_product 同口径：product_id 命中，或价格挂在该商品的 SKU 上。"""

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for price in prices_by_product.get(product_id, []):
        seen.add(id(price))
        rows.append(price)
    for sku in skus:
        sku_id = str(sku.get("id") or "")
        if not sku_id:
            continue
        for price in prices_by_sku.get(sku_id, []):
            if id(price) in seen:
                continue
            seen.add(id(price))
            rows.append(price)
    return rows


def _readiness_by_product(
    data: dict[str, Any],
    products: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """批量算就绪：与 check_product_readiness 同口径，但整库只扫一遍。"""

    assets_by_group, skus_by_product, prices_by_product, prices_by_sku = _readiness_indexes(data)
    result: dict[str, dict[str, Any]] = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        product_id = str(product.get("id") or "")
        if not product_id:
            continue
        skus = skus_by_product.get(product_id, [])
        result[product_id] = _product_readiness_checks(
            product,
            has_main_asset=_has_main_asset_from_index(product, assets_by_group),
            skus=skus,
            prices=_product_prices_from_index(product_id, skus, prices_by_product, prices_by_sku),
        )
    return result


def preview_product_name_cleanup(group_id: str = "") -> dict[str, Any]:
    target = (group_id or "").strip().upper()
    products = list_products_with_summary()
    items = []
    for product in products:
        if target and (product.get("group_id") or "").strip().upper() != target:
            continue
        preview = _build_product_name_cleanup_preview(product)
        if preview:
            items.append(preview)
    return {"ok": True, "group_id": target, "items": items, "count": len(items)}


def apply_product_name_cleanup(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    dry_run = bool(payload.get("dry_run"))
    target = (payload.get("group_id") or "").strip().upper()
    preview = preview_product_name_cleanup(target)
    updated: list[dict[str, Any]] = []
    if dry_run:
        return {"ok": True, "dry_run": True, "group_id": target, "updated": preview["items"], "updated_count": len(preview["items"])}
    suggestions = {item["product_id"]: item["suggested"] for item in preview["items"]}
    with locked_store() as (data, save):
        for product in data["products"]:
            suggestion = suggestions.get(product.get("id") or "")
            if not suggestion:
                continue
            before = {key: product.get(key) or "" for key in ["base_name", "product_name_cn", "listing_title"]}
            product.update({key: value for key, value in suggestion.items() if value})
            product["updated_at"] = utc_now()
            updated.append({"product_id": product.get("id") or "", "group_id": product.get("group_id") or "", "before": before, "after": suggestion})
        if updated:
            save(data)
    return {"ok": True, "dry_run": False, "group_id": target, "updated": updated, "updated_count": len(updated)}


def preview_product_dedup(group_id: str = "") -> dict[str, Any]:
    products = list_items("products")
    skus = list_items("skus")
    prices = list_items("prices")
    sku_counts = Counter(sku.get("product_id") for sku in skus)
    price_counts = Counter(price.get("product_id") for price in prices)
    target = (group_id or "").strip().upper()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for product in products:
        key = (product.get("group_id") or "").strip().upper()
        if not key or (target and key != target):
            continue
        grouped.setdefault(key, []).append(product)
    duplicate_groups = []
    for key, rows in sorted(grouped.items()):
        if len(rows) <= 1:
            continue
        ranked = sorted(rows, key=lambda item: (_has_basic_product_data(item), sku_counts.get(item.get("id"), 0), price_counts.get(item.get("id"), 0), item.get("updated_at") or ""), reverse=True)
        duplicate_groups.append({
            "group_id": key,
            "product_count": len(ranked),
            "keep_product_id": ranked[0].get("id") or "",
            "keep_reason": "资料较完整",
            "merged_sku_count": sum(sku_counts.get(item.get("id"), 0) for item in ranked),
            "merged_price_count": sum(price_counts.get(item.get("id"), 0) for item in ranked),
            "products": [
                {
                    "id": item.get("id") or "",
                    "created_at": item.get("created_at") or "",
                    "updated_at": item.get("updated_at") or "",
                    "category": item.get("category") or "",
                    "store": item.get("store") or "",
                    "product_name_cn": item.get("product_name_cn") or item.get("base_name") or "",
                    "listing_title": item.get("listing_title") or item.get("generated_title") or "",
                    "main_image": item.get("main_image") or "",
                    "status": item.get("status") or "",
                    "sku_count": sku_counts.get(item.get("id"), 0),
                    "price_count": price_counts.get(item.get("id"), 0),
                }
                for item in ranked
            ],
        })
    return {
        "groups": duplicate_groups,
        "group_count": len(duplicate_groups),
        "duplicate_product_count": sum(item["product_count"] for item in duplicate_groups),
    }


def dedup_products(group_id: str = "", keep_product_id: str = "") -> dict[str, Any]:
    preview = preview_product_dedup(group_id)
    if not preview["groups"]:
        return {"ok": True, "groups": [], "group_count": 0, "backup_path": ""}
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = settings.DATA_DIR / f"store.backup.dedup.{timestamp}.json"
    backup_path_value = ""
    if not _using_postgres() and settings.STORE_FILE.exists():
        backup_path.write_text(settings.STORE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        backup_path_value = str(backup_path)

    results = []
    with locked_store() as (data, save):
        for group in preview["groups"]:
            keep_id = (keep_product_id or "").strip() or group["keep_product_id"]
            remove_ids = [item["id"] for item in group["products"] if item["id"] != keep_id]
            if not remove_ids:
                continue
            data["products"] = [item for item in data["products"] if item.get("id") not in remove_ids]
            for collection in ["skus", "prices"]:
                for item in data.get(collection, []):
                    if item.get("product_id") in remove_ids:
                        item["product_id"] = keep_id
            results.append({"group_id": group["group_id"], "keep_product_id": keep_id, "removed_product_ids": remove_ids, "removed_count": len(remove_ids)})
        save(data)
    return {"ok": True, "groups": results, "group_count": len(results), "backup_path": backup_path_value}


def list_product_templates() -> list[dict[str, Any]]:
    return [_export_product_template(category, template) for category, template in PRODUCT_TEMPLATES.items()]


def get_product_template(category: str) -> dict[str, Any]:
    normalized = (category or "").strip()
    return dict(PRODUCT_TEMPLATES.get(normalized) or PRODUCT_TEMPLATES["POD 男装T恤"])


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _deployment_summary(status: str, warnings: list[dict[str, Any]]) -> str:
    if status == "blocked":
        return "马帮导出暂不可用，请先修复阻断项。"
    if status == "needs_data":
        return f"马帮导出链路可运行，但还有 {len(warnings)} 个数据项需要补齐。"
    return "马帮导出链路已就绪。"


# 文件存在性判断的 TTL 缓存。配置的马帮模板在国内是网络盘（UNC）路径，实测首次
# stat 要 **508ms**（SMB 目录缓存过期后每次重新付）；而 /api/deployment/readiness
# 每次首屏都会走一遍这个判断。缓存 60 秒，重复请求零成本，模板真的变了最多 60 秒
# 后才反映在「马帮模板可用」这条提示上。
_FILE_PROBE_TTL_SECONDS = 60.0
_FILE_PROBE_CACHE: dict[str, tuple[float, bool]] = {}


def _safe_is_file(path: Path) -> bool:
    key = str(path)
    now = time.monotonic()
    cached = _FILE_PROBE_CACHE.get(key)
    if cached is not None and now - cached[0] < _FILE_PROBE_TTL_SECONDS:
        return cached[1]
    try:
        value = path.is_file()
    except OSError:
        value = False
    _FILE_PROBE_CACHE[key] = (now, value)
    return value


def _deployment_next_actions(status: str, checks: list[dict[str, Any]]) -> list[str]:
    failed = {check["code"] for check in checks if not check["ok"]}
    actions: list[str] = []
    if "template_available" in failed:
        actions.append("配置 MABANG_TEMPLATE_PATH，或补齐 storage/samples/mabang-template.xlsx。")
    if "product_library" in failed:
        actions.append("先扫描 NAS 素材并创建商品资料。")
    if "exportable_products" in failed:
        actions.append("补齐商品主图、SKU、价格、重量和尺寸后再导出。")
    if not actions and status == "ready":
        actions.append("可以在自动化页面检查店铺刊登副本，并下载马帮 Excel。")
    return actions


def _product_business_status(product: dict[str, Any], readiness: dict[str, Any]) -> dict[str, str]:
    if readiness.get("ready"):
        return {"code": "ready", "label": "可导出"}
    if _has_basic_product_data(product):
        return {"code": "needs_check", "label": "需补资料"}
    return {"code": "draft", "label": "草稿"}


def _has_basic_product_data(product: dict[str, Any]) -> bool:
    return bool((product.get("group_id") or "").strip() and (product.get("product_name_cn") or product.get("base_name") or "").strip())


def _readiness_check(code: str, label: str, ok: bool, missing_message: str, *, severity: str = "blocking") -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "ok": bool(ok),
        "severity": severity,
        "message": "已填写。" if ok else missing_message,
    }


def _pick_main_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    main_assets = [asset for asset in assets if (asset.get("asset_type") or "").lower() == "main"]
    if main_assets:
        main_assets.sort(key=_product_image_sort_key)
        return main_assets[0]
    # 没有 main 素材时按素材角色优先级回退（见 docs/POD_ASSET_RULES.md 的推荐图片顺序）：
    # 模特图 > 颜色图 > 详情图。尺码表/源图/视频不是商品图，不能只因为它们排在最前面
    # （素材按 asset_type 排序，size_chart/source 都早于 variant）就被当成主图。
    for asset_type in ("model", "variant", "detail"):
        photos = [asset for asset in assets if (asset.get("asset_type") or "").lower() == asset_type]
        if photos:
            photos.sort(key=_product_image_sort_key)
            return photos[0]
    return None


def _pick_size_chart_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    for asset in assets:
        if (asset.get("asset_type") or "").lower() == "size_chart":
            return asset
    return None


def _default_product_images(assets: list[dict[str, Any]], main: dict[str, Any] | None = None) -> list[str]:
    """默认商品图：主图 → 模特图 → 颜色图，各自按文件名自然序，最多 9 张。

    尺码表/源图/视频/印刷图（print）永远不是商品图。颜色图排在模特图后面补位，
    现场产品文件夹（`A白色.jpg` 这类颜色图 + `前印花.png` 这类印刷图）导入后
    直接就是一套可上架的图片顺序，不用每件手工挑。"""
    main_type = str((main or {}).get("asset_type") or "").lower()
    main_name = str((main or {}).get("file_name") or "").strip() if main_type in PHOTO_ASSET_TYPES else ""
    product_assets = [
        asset
        for asset in assets
        if (asset.get("asset_type") or "").lower() in DEFAULT_PRODUCT_IMAGE_TYPES
        and str(asset.get("file_name") or "").strip()
        and str(asset.get("file_name") or "").strip() != main_name
    ]
    product_assets.sort(key=_product_image_sort_key)
    names = [asset.get("file_name") or "" for asset in product_assets]
    if main_name:
        names = [main_name, *[name for name in names if name != main_name]]
    return _normalize_product_images(names)


def _natural_parts(stem: str) -> list[tuple[str, Any]]:
    """按数字段切分文件名。每段带类型标签（"0" = 文本 / "1" = 数字），这样混排的
    文件名（"01-main.jpg" + "main-01.jpg"）不会在同一位置拿 int 和 str 比大小
    （Python 的 < 只能比同类型），同时数字段仍然按数值排：model2 < model10。"""
    parts: list[tuple[str, Any]] = []
    for part in re.split(r"(\d+)", str(stem or "").lower()):
        if part.isdigit():
            parts.append(("1", len(part), int(part)))
        elif part:
            parts.append(("0", part))
    return parts


def natural_name_key(value: Any) -> tuple:
    """文件名自然序：``1 (2).png`` 排在 ``1 (10).png`` 前。

    字典序会把 ``1 (10).png`` 排到第二位，而人和视觉模型都按数字读文件名；
    两边顺序不一致时，提示里的第 N 个文件名会配到第 N 张（其实不同的）图上，
    整批颜色图错位（2026-09-19 修「红色配到别的颜色」的根因之一）。"""
    text = str(value or "")
    return (_natural_parts(Path(text).stem), text.lower())


def _product_image_sort_key(asset: dict[str, Any]) -> tuple[int, int, list[tuple[str, Any]], str]:
    name = str(asset.get("file_name") or asset.get("file_path") or "")
    stem = Path(name).stem.lower()
    role = DEFAULT_PRODUCT_IMAGE_ROLE_ORDER.get(str(asset.get("asset_type") or "").lower(), len(DEFAULT_PRODUCT_IMAGE_ROLE_ORDER))
    explicit_main = 0 if ("main" in stem or "\u4e3b\u56fe" in stem) else 1
    return (role, explicit_main, _natural_parts(stem), name.lower())


def _default_variant_image_map(assets: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    variant_assets = [
        asset
        for asset in assets
        if (asset.get("asset_type") or "").lower() == "variant"
        and str(asset.get("sku_option") or "").strip()
        and str(asset.get("file_name") or "").strip()
    ]
    variant_assets.sort(key=lambda asset: natural_name_key(asset.get("file_name")))
    for asset in variant_assets:
        color = str(asset.get("sku_option") or "").strip()
        result.setdefault(color, str(asset.get("file_name") or "").strip())
    return result


def _normalize_product_images(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in re.split(r"[\n,]+", value) if item.strip()]
    elif isinstance(value, list):
        items = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        items = []
    seen = set()
    result = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= PRODUCT_IMAGE_LIMIT:
            break
    return result


def _title_case_english(value: str) -> str:
    """English color names display title-cased (white → White, dark blue → Dark
    Blue) and composite colors follow the SKU spelling the scanner produces
    (darkgreen → DarkGreen, PureWhite and other camel-cased names are kept as
    typed). Non-ASCII (Chinese 颜色名) and mixed text are returned unchanged."""
    text = str(value or "").strip()
    if not text:
        return text
    if not any("a" <= ch.lower() <= "z" for ch in text):
        return text  # no latin letters (e.g. 纯中文)
    # Only ASCII letter/digit/hyphen/space tokens get canonicalized; anything else
    # (Chinese chars inside a mixed label) is left as-is.
    parts: list[str] = []
    for raw_token in re.split(r"(\s+)", text):
        token = raw_token
        if raw_token.strip() and all(
            ch.isascii() and (ch.isalnum() or ch in "-'") for ch in raw_token
        ):
            token = _canonical_english_token(raw_token)
        parts.append(token)
    return _glue_color_modifiers(parts)


def _glue_color_modifiers(parts: list[str]) -> str:
    """Join a modifier that was typed as its own word with the color word it
    qualifies ("dark green" → "DarkGreen") so it lands on the same variant key as
    the filename form "darkgreen". Two color words are never merged: "Black Gold"
    is one label, not BlackGold."""
    from workers.asset_scanner.scanner import COLOR_MODIFIERS, canonical_color_name

    merged: list[str] = []
    index = 0
    while index < len(parts):
        word = parts[index]
        separator = parts[index + 1] if index + 1 < len(parts) else ""
        following = parts[index + 2] if index + 2 < len(parts) else ""
        glued = canonical_color_name(f"{word}{following}") if following else ""
        if glued and not separator.strip() and word.lower() in COLOR_MODIFIERS:
            merged.append(glued)
            index += 3
            continue
        merged.append(word)
        index += 1
    return "".join(merged)


def _canonical_english_token(token: str) -> str:
    """One pure-ASCII color token: known colors get the scanner's canonical name
    (darkgreen → DarkGreen, WHITE → White), an already camel-cased name such as
    PureWhite is kept. Hyphenated labels (Black-White) are left capitalized only —
    the color vocabulary has no hyphenated entries, so splitting them would drop
    the second half."""
    if "-" not in token:
        from workers.asset_scanner.scanner import canonical_color_name

        color = canonical_color_name(token)
        if color:
            return color
    if any(ch.isupper() for ch in token[1:]):
        return token  # 已经是驼峰（PureWhite/DarkGreen）——保留操作者/文件名写法
    return token[0].upper() + token[1:].lower()


def normalize_color_label(value: Any) -> str:
    """颜色名的规范写法（white → White、dark green → DarkGreen、中文原样）。

    颜色列表和颜色图映射的键必须用同一套写法：模型把复合色写成 ``Light Purple``
    而列表归一化成 ``LightPurple`` 时，映射会因为键对不上被静默丢掉，
    最后只剩颜色名没有图（2026-09-19 修 AL0507 少两个颜色图的根因）。"""
    return _title_case_english(str(value or ""))


def _color_match_key(value: Any) -> str:
    """颜色名比较用的键：忽略大小写与空格，兼容 ``LightPurple`` / ``Light Purple``。"""
    return re.sub(r"\s+", "", normalize_color_label(value)).lower()


def _normalize_flag(value: Any, *, default: bool = True) -> bool:
    """开关字段归一化：JSON 布尔优先，兼容 ``"false"`` / ``"0"`` / ``"off"`` 这类字符串。

    ``value`` 是 None（或空串）时用 ``default``——「颜色图跟随产品图顺序」对旧商品
    默认打开，所以读取端也不能把缺字段当成 False。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"", "none", "null"}:
        return default
    return text not in {"0", "false", "no", "off"}


def _normalize_recognized_colors(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in re.split(r"[\n,]+", value) if item.strip()]
    elif isinstance(value, list):
        items = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        items = []
    seen = set()
    result = []
    for item in items:
        # 英文颜色统一首字母大写（White/Black…），中文颜色名保持原样。
        item = _title_case_english(item)
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _normalize_recognized_sizes(value: Any) -> list[str]:
    """Normalize an AI/manual size list: split strings on commas/newlines, trim,
    de-dupe case-insensitively, keep order."""
    if isinstance(value, str):
        items = [item.strip() for item in re.split(r"[\n,]+", value) if item.strip()]
    elif isinstance(value, list):
        items = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        items = []
    seen = set()
    result = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _normalize_variant_image_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_color, raw_file_name in value.items():
        color = str(raw_color or "").strip()
        file_name = str(raw_file_name or "").strip()
        if color and file_name:
            result[color] = file_name
    return result


BASE_ATTRIBUTE_LIMIT = 12


def _normalize_recognized_attributes(value: Any) -> dict[str, str]:
    """Keep AI-recognized product attributes as an ordered {name: value} map; drop
    empty or non-string-ish entries so a bad patch can't pollute the stored product."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        val = str(raw_value if raw_value is not None else "").strip()
        if key and val:
            result[key] = val
        if len(result) >= BASE_ATTRIBUTE_LIMIT:
            break
    return result


def _normalize_product_category(value: Any) -> dict[str, Any]:
    """Normalize a base product's expected marketplace category.

    Accepts either an object {store_id?, category_id, path, path_zh?, name_zh?}
    or a bare path string. Empty input (None/""/{}) clears the category. The
    category is an expectation only — the store template remains authoritative
    when a listing copy is built.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        path = value.strip()
        return {"path": path} if path else {}
    if not isinstance(value, dict):
        return {}
    category_id = str(value.get("category_id") or "").strip()
    path = str(value.get("path") or value.get("path_zh") or "").strip()
    path_zh = str(value.get("path_zh") or "").strip()
    if not category_id and not path:
        return {}
    normalized = {
        "category_id": category_id,
        "path": path or path_zh,
        "path_zh": path_zh or path,
        "name_zh": str(value.get("name_zh") or "").strip(),
        "store_id": str(value.get("store_id") or "").strip(),
        "country": str(value.get("country") or "").strip(),
    }
    return {key: val for key, val in normalized.items() if val}


def _variant_image_map_for_colors(value: Any, colors: list[Any]) -> dict[str, str]:
    image_map = _normalize_variant_image_map(value)
    normalized_colors = _normalize_recognized_colors(colors)
    if not normalized_colors:
        return {}
    by_lower = {color.lower(): file_name for color, file_name in image_map.items()}
    # 归一化写法在同一颜色上不一致时（LightPurple / Light Purple / lightpurple）也要
    # 认得出是同一个颜色，否则历史数据里那些映射会在每次保存时被反复丢掉。
    by_key = {_color_match_key(color): file_name for color, file_name in image_map.items()}
    result: dict[str, str] = {}
    for color in normalized_colors:
        file_name = image_map.get(color) or by_lower.get(color.lower()) or by_key.get(_color_match_key(color)) or ""
        if file_name:
            result[color] = file_name
    return result


def _replace_listing_skus_and_prices(
    data: dict[str, Any],
    base: dict[str, Any],
    template: dict[str, Any],
    listing_copy: dict[str, Any],
    *,
    now: str,
) -> list[dict[str, Any]]:
    listing_copy_id = listing_copy.get("id") or ""
    data.setdefault("listing_skus", [])
    data.setdefault("listing_prices", [])
    skus = _generate_listing_skus(base, template, listing_copy)
    for sku in skus:
        sku["updated_at"] = now
    prices = _generate_listing_prices(template, listing_copy, skus, base_price_tiers=base.get("price_tiers") or [])
    for price in prices:
        price["updated_at"] = now
    data["listing_skus"] = [item for item in data["listing_skus"] if item.get("listing_copy_id") != listing_copy_id]
    data["listing_prices"] = [item for item in data["listing_prices"] if item.get("listing_copy_id") != listing_copy_id]
    data["listing_skus"].extend(skus)
    data["listing_prices"].extend(prices)
    return skus


def _normalize_category_attributes(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(DEFAULT_CATEGORY_ATTRIBUTES)
    result = dict(DEFAULT_CATEGORY_ATTRIBUTES)
    for key in ["brand", "pattern", "material", "sleeve_length", "plus_size"]:
        text = str(value.get(key) or "").strip()
        if text:
            result[key] = text
    display = dict(result.get("display") or {})
    if isinstance(value.get("display"), dict):
        for key, raw in value["display"].items():
            text = str(raw or "").strip()
            if text:
                display[key] = text
    result["display"] = display
    return result


def _normalize_variation(value: Any) -> dict[str, Any]:
    result = {"type": "variant", "axes": ["color", "size"]}
    if not isinstance(value, dict):
        return result
    variation_type = str(value.get("type") or "").strip()
    if variation_type:
        result["type"] = variation_type
    axes = value.get("axes")
    if isinstance(axes, str):
        axes = [item.strip() for item in axes.split(",") if item.strip()]
    elif isinstance(axes, list):
        axes = [str(item or "").strip() for item in axes if str(item or "").strip()]
    else:
        axes = []
    if axes:
        result["axes"] = axes
    return result


def _variant_image_for_color(base: dict[str, Any], color: Any) -> str:
    color_text = str(color or "").strip()
    if not color_text:
        return ""
    image_map = _normalize_variant_image_map(base.get("variant_image_map") or {})
    return image_map.get(color_text) or image_map.get(color_text.lower()) or ""


def _asset_by_name(assets: list[dict[str, Any]], file_name: str) -> dict[str, Any] | None:
    wanted = str(file_name or "").strip().lower()
    if not wanted:
        return None
    for asset in assets:
        if str(asset.get("file_name") or "").strip().lower() == wanted:
            return asset
    return None


def _asset_paths_for_names(assets: list[dict[str, Any]], file_names: list[str]) -> list[str]:
    paths = []
    for file_name in file_names:
        asset = _asset_by_name(assets, file_name)
        if asset and asset.get("file_path"):
            paths.append(asset["file_path"])
    return paths


def _asset_path_map_for_names(assets: list[dict[str, Any]], file_name_by_key: dict[str, str]) -> dict[str, str]:
    paths = {}
    for key, file_name in file_name_by_key.items():
        asset = _asset_by_name(assets, file_name)
        if asset and asset.get("file_path"):
            paths[key] = asset["file_path"]
    return paths


def _unique_sorted_sizes(skus: list[dict[str, Any]]) -> list[str]:
    """尺码轴顺序：S/M/L 等已知码按表排序，其余保持 SKU 行顺序。

    行顺序来自 list_listing_skus（按 sku_code 排序），执行器按行号往马帮变种表
    逐行填写；用 set 会让同权重的尺码（130cm/140cm… 全部映射到 999）顺序随机，
    变种轴与 SKU 表行序就会漂移。
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for sku in skus:
        size = str(sku.get("size") or "").strip()
        if size and size not in seen:
            seen.add(size)
            ordered.append(size)
    return sorted(ordered, key=_size_sort_value)


def _unique_sorted_colors(skus: list[dict[str, Any]], listing_copy: dict[str, Any]) -> list[str]:
    """颜色的字母序版本：仅跨境(cnsc_global)沿用。

    跨境 SKU 网格由执行器按行号填写，颜色轴换序就等于换行序，改动必须单独验证；
    本土走 :func:`_ordered_variation_colors`。"""
    colors = {str(sku.get("color_code") or sku.get("color_cn") or "").strip() for sku in skus if str(sku.get("color_code") or sku.get("color_cn") or "").strip()}
    if not colors:
        colors = {str(item or "").strip() for item in listing_copy.get("recognized_colors") or [] if str(item or "").strip()}
    return sorted(colors)


def _ordered_variation_colors(skus: list[dict[str, Any]], listing_copy: dict[str, Any]) -> list[str]:
    """颜色轴顺序 = 商品维护/颜色识别里操作员排的顺序（``recognized_colors``）。

    马帮按这份列表逐个「添加自定义」颜色值，颜色轴就按这个先后生成。而
    ``list_listing_skus`` 出库时按 ``color_code`` 排序，直接拿它建轴会得到
    Apricot/Black/Brown… 的字母序，与操作员排好的颜色顺序对不上（真机现象：
    马帮草稿上的颜色顺序不是商品维护里的顺序）。
    SKU 里多出来、快照里没有的颜色按名字补在后面，保证顺序稳定可复现。
    """
    recognized: list[str] = []
    for item in listing_copy.get("recognized_colors") or []:
        color = str(item or "").strip()
        if color and color not in recognized:
            recognized.append(color)
    present: list[str] = []
    for sku in skus:
        color = str(sku.get("color_code") or sku.get("color_cn") or "").strip()
        if color and color not in present:
            present.append(color)
    if not present:
        return recognized
    ordered = [color for color in recognized if color in present]
    return [*ordered, *sorted(set(present) - set(ordered))]


def _order_sku_rows_by_colors(
    skus: list[dict[str, Any]],
    prices: list[dict[str, Any]],
    colors: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把 SKU 行排成颜色轴顺序（同一颜色内保持原有行序），价格跟着同一顺序。

    马帮的 SKU 网格由颜色轴生成，行序 = 颜色轴顺序 × 尺码顺序。本土执行器按
    「颜色 + 尺码」匹配行，行序不影响写入结果，但快照里的行序与页面一致时，
    人工核对、日志排查和后续任何按行号的处理都不会错位。
    """
    if not skus:
        return list(skus), list(prices)
    position = {str(color): index for index, color in enumerate(colors or [])}
    fallback = len(position)
    ordered = sorted(
        skus,
        key=lambda sku: position.get(str(sku.get("color_code") or sku.get("color_cn") or "").strip(), fallback),
    )
    if not prices:
        return ordered, list(prices)
    price_by_sku = {str(price.get("sku_code") or ""): price for price in prices}
    aligned = [price_by_sku[str(sku.get("sku_code") or "")] for sku in ordered if str(sku.get("sku_code") or "") in price_by_sku]
    return ordered, (aligned if len(aligned) == len(ordered) else list(prices))


def _most_common(values: list[Any]) -> str:
    cleaned = [str(item or "").strip() for item in values if str(item or "").strip()]
    if not cleaned:
        return ""
    return Counter(cleaned).most_common(1)[0][0]


def _profile_defaults_for_payload(payload: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(payload.get("category_profile_id") or payload.get("profile_id") or "").strip()
    if not profile_id:
        return {}
    profile = get_category_profile(profile_id)
    if not profile:
        return {}
    defaults = profile
    published_version = defaults.get("published_version") or {}
    default_attributes = {
        field.get("field_id"): deepcopy(field.get("default_value"))
        for field in published_version.get("fields") or []
        if field.get("field_id")
    } or deepcopy(defaults.get("required_attributes") or {})
    return {
        "category_profile_id": defaults.get("id") or defaults.get("profile_id") or "",
        "category_profile_version_id": published_version.get("id") or defaults.get("published_version_id") or "",
        "product_line": defaults.get("product_line") or "",
        "marketplace_category_path": defaults.get("marketplace_category_path") or "",
        "mabang_category_id": defaults.get("mabang_category_id") or "",
        "country": defaults.get("country") or "",
        "currency": defaults.get("currency") or "",
        "normal_price": defaults.get("normal_price"),
        "sale_price": defaults.get("sale_price"),
        "discount_label": defaults.get("discount_label") or "",
        "mabang_discount_id": defaults.get("mabang_discount_id") or "",
        "discount_id": defaults.get("mabang_discount_id") or "",
        "sizes": defaults.get("sizes") or [],
        "title_suffix": defaults.get("title_suffix") or "",
        "description": defaults.get("description") or "",
        "attribute_values": default_attributes,
        "category_attributes": deepcopy(default_attributes),
        "variation": defaults.get("variation") or {},
        "default_size_chart_path": defaults.get("default_size_chart_path") or "",
        "template_product_id": defaults.get("template_id") or "",
    }


def _value_or_default(payload: dict[str, Any], defaults: dict[str, Any], key: str, fallback: Any = "") -> Any:
    value = payload.get(key)
    if value is None or value == "":
        return defaults.get(key, fallback)
    return value


def _normalize_cnsc_price_tiers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        tier_id = str(raw.get("id") or ("standard" if index == 0 else "large" if index == 1 else f"tier_{index + 1}")).strip()
        sizes = raw.get("sizes") or []
        if isinstance(sizes, str):
            sizes = [item.strip() for item in sizes.split(",") if item.strip()]
        sizes = [str(item or "").strip() for item in sizes if str(item or "").strip()]
        colors = raw.get("colors") or []
        if isinstance(colors, str):
            colors = [item.strip() for item in colors.split(",") if item.strip()]
        colors = [str(item or "").strip() for item in colors if str(item or "").strip()]
        site_prices = {}
        for code, price in (raw.get("site_prices") or {}).items() if isinstance(raw.get("site_prices"), dict) else []:
            if not isinstance(price, dict):
                continue
            sale = _float_or_empty(price.get("sale_price"))
            original = _float_or_empty(price.get("original_price"))
            # 站点价允许只填原价（sale 留空由目标店铺 %OFF 活动折扣自动推导）；
            # 完全空（两价皆空）的条目才丢弃。
            if sale != "" or original != "":
                site_prices[str(code or "").strip().upper()] = {"sale_price": sale, "original_price": original}
        result.append({
            "id": tier_id,
            "sizes": sizes,
            "colors": colors,
            "global_price_cny": _float_or_empty(raw.get("global_price_cny")),
            "cost_price": _float_or_empty(raw.get("cost_price")),
            "site_prices": site_prices,
        })
    return result


def _tier_matches(tier: dict[str, Any], color: str, size: str) -> bool:
    """Whether a price tier binds to the (color, size) SKU variant.

    A tier with neither ``sizes`` nor ``colors`` matches every SKU (uniform
    pricing); a tier with only ``sizes`` keeps the historical size-only
    binding (empty ``colors`` matches any color). Binding is case-insensitive:
    recognized colors get title-cased on later re-recognition while saved price
    tiers keep the casing of the session that filled them ("Wig And Ear" vs
    "Wig and ear"), and an exact-case compare silently decouples cost price
    from the copy's price rows."""
    sizes = {str(item or "").strip().casefold() for item in tier.get("sizes") or [] if str(item or "").strip()}
    colors = {str(item or "").strip().casefold() for item in tier.get("colors") or [] if str(item or "").strip()}
    size_ok = not sizes or str(size or "").strip().casefold() in sizes
    color_ok = not colors or str(color or "").strip().casefold() in colors
    return size_ok and color_ok


def _normalize_base_price_tiers(value: Any) -> list[dict[str, Any]]:
    """Base-product price tiers: same shape as CNSC template/copy tiers plus an
    optional ``cost_price`` per tier. Cost price applies to every SKU the tier
    binds (uniform tier = the whole product's cost)."""
    tiers = _normalize_cnsc_price_tiers(value)
    raw_by_id = {
        str(raw.get("id") or "").strip(): raw
        for raw in value
        if isinstance(raw, dict) and str(raw.get("id") or "").strip()
    }
    for tier in tiers:
        raw = raw_by_id.get(tier["id"]) or {}
        cost = _float_or_empty(raw.get("cost_price"))
        tier["cost_price"] = "" if cost in ("", None) else float(cost)
    return tiers


def _infer_cnsc_binding_mode(tiers: list[dict[str, Any]]) -> str:
    """回填副本价格绑定模式。

    旧副本没有 cnsc_binding_mode 字段：按档位形状推断 —— 任一档带 colors
    → colors（按颜色分行）；旧 uniform 单档（sizes/colors 全空）视为 colors
    （一个无约束档在按颜色分行渲染下等同「一价覆盖全部颜色」，见迁移）。
    """
    for tier in tiers or []:
        if tier.get("colors"):
            return "colors"
    for tier in tiers or []:
        if tier.get("sizes"):
            return "sizes"
    return "colors"


def _resolve_copy_category(
    base: dict[str, Any],
    template: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> tuple[str, str, bool]:
    """Decide the marketplace category for a listing copy built from a base product.

    Returns (mabang_category_id, marketplace_category_path, applied_from_base).
    If the base product carries a chosen product_category, that category is used
    verbatim (the store template's category is ignored). Otherwise the store
    template's category is the default.
    """
    base_category = _normalize_product_category(base.get("product_category"))
    base_category_id = str(base_category.get("category_id") or "").strip()
    base_path = str(base_category.get("path") or "").strip()
    template_id = str(template.get("mabang_category_id") or "").strip()
    template_path = str(template.get("marketplace_category_path") or "").strip()
    if not base_category_id or not base_path:
        return template_id, template_path, False
    return base_category_id, base_path, True


def _cnsc_copy_price_tiers(base: dict[str, Any], template: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """CNSC 复制时的副本价格档来源与绑定模式。

    返回 (binding_mode, tiers)：
    - 基础商品已配按颜色档（每颜色一档带站点价）→ colors，副本档直接采用它
      （与 base 颜色档同构：sizes 空、colors=[颜色]）；
    - 模板档带尺码/颜色绑定（standard/large 按尺码分档）→ sizes，副本档 =
      模板档原样保留（连同 sizes 绑定与站点价全量复制；SKU 是颜色×尺码，
      尺寸档可正常区分同色不同码定价）；
    - 模板档全无绑定（uniform 形态，一价覆盖全部 SKU）→ colors 模式下的单个
      无约束档（等价旧「全覆盖统一价」，渲染时按颜色展开成行）。
    """
    if template.get("listing_mode") != "cnsc_global":
        return "sizes", deepcopy(template.get("price_tiers") or [])
    base_tiers = _normalize_base_price_tiers(base.get("price_tiers") or [])
    color_tiers = [tier for tier in base_tiers if (tier.get("colors") or [])]
    if color_tiers:
        # colors 模式：每个颜色档的「全球商品价格」(CNY) 用该档成本价填充。
        # Mabang CNSC 的 SKU 表 *全球商品价格 列必填（真机 2026-09-04 实测：
        # 空值 → 每行「请输入价格」→ pre_save_validation_failed，保存从未发生），
        # 而颜色档来自 base、无模板档的 global 概念，成本价是该档唯一 RMB 值。
        filled = []
        for tier in color_tiers:
            tier = deepcopy(tier)
            cost = tier.get("cost_price")
            if cost in ("", None) or float(cost) <= 0:
                cost = _default_template_global_price(template)
            if cost not in ("", None) and float(cost) > 0:
                tier["global_price_cny"] = float(cost)
            filled.append(tier)
        return "colors", filled
    template_tiers = [tier for tier in (template.get("price_tiers") or []) if isinstance(tier, dict)]
    bound = any((tier.get("sizes") or []) or (tier.get("colors") or []) for tier in template_tiers)
    return ("colors" if not bound else "sizes"), deepcopy(template_tiers)


def _default_template_global_price(template: dict[str, Any]) -> Any:
    """First usable template-tier global price (CNY) as a fallback when a color
    tier has no cost. Template standard/large tiers both carry the same global in
    the CNSC templates today."""
    for tier in template.get("price_tiers") or []:
        if not isinstance(tier, dict):
            continue
        global_price = _float_or_empty(tier.get("global_price_cny"))
        if global_price != "" and float(global_price) > 0:
            return float(global_price)
    return ""


def _fill_cnsc_color_tier_globals(tiers: list[dict[str, Any]], base: dict[str, Any], template: dict[str, Any]) -> None:
    """Fill empty ``global_price_cny`` on color-bound tiers from the base product's
    price tiers (matched case-insensitively by color), falling back to the
    template's global price — mirrors ``_cnsc_copy_price_tiers`` where a color
    tier's 全球商品价格 is its cost price. The copy editor's colors mode rebuilds
    tiers without ``global_price_cny`` (the column is hidden there), so without
    this refill every editor save would re-flag ``price_tiers.<id>.global_price_cny``."""
    base_tiers = [
        tier
        for tier in _normalize_base_price_tiers(base.get("price_tiers") or [])
        if (tier.get("colors") or [])
    ]
    fallback = _default_template_global_price(template)
    for tier in tiers or []:
        colors = [str(item or "").strip() for item in tier.get("colors") or [] if str(item or "").strip()]
        if not colors:
            continue
        global_price = _float_or_empty(tier.get("global_price_cny"))
        if global_price != "" and float(global_price) > 0:
            continue
        base_tier = next((item_tier for item_tier in base_tiers if _tier_matches(item_tier, colors[0], "")), None)
        cost = (base_tier or {}).get("cost_price")
        if cost in ("", None) or float(cost) <= 0:
            cost = fallback
        if cost not in ("", None) and float(cost) > 0:
            tier["global_price_cny"] = float(cost)
def _normalize_cnsc_target_sites(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("site_code") or raw.get("country") or raw.get("site") or "").strip().upper()
        store_id = str(raw.get("store_id") or raw.get("mabang_store_id") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        catalog = []
        if raw.get("discount_catalog_loaded") is True:
            for activity in raw.get("discount_catalog") or []:
                if not isinstance(activity, dict):
                    continue
                activity_id = str(activity.get("mabang_discount_id") or activity.get("activity_id") or activity.get("id") or "").strip()
                name = str(activity.get("name") or activity.get("discount_name") or "").strip()
                if activity_id and name:
                    catalog.append({
                        "mabang_discount_id": activity_id,
                        "name": name,
                        "discount_percent": _float_or_empty(activity.get("discount_percent")),
                    })
        result.append({
            "site_code": code,
            "store_id": store_id,
            "store_name": str(raw.get("store_name") or raw.get("name") or "").strip(),
            "mabang_discount_id": str(raw.get("mabang_discount_id") or raw.get("discount_id") or "").strip(),
            "discount_name": str(raw.get("discount_name") or "").strip(),
            "discount_label": str(raw.get("discount_label") or "").strip(),
            "discount_percent": _float_or_empty(raw.get("discount_percent")),
            "discount_catalog_loaded": raw.get("discount_catalog_loaded") is True,
            "discount_catalog": catalog,
        })
    return result


def _validated_cnsc_warehouse_stocks(value: Any) -> dict[str, int]:
    required_codes = ("TH", "CN_GZ", "PH")
    if not isinstance(value, dict):
        raise ValueError("仓库库存必须包含 TH、CN_GZ、PH")
    normalized: dict[str, Any] = {}
    for raw_code, raw_value in value.items():
        code = str(raw_code or "").strip().upper()
        if code in normalized or code not in required_codes:
            raise ValueError("仓库库存必须包含 TH、CN_GZ、PH")
        normalized[code] = raw_value
    if set(normalized) != set(required_codes):
        raise ValueError("仓库库存必须包含 TH、CN_GZ、PH")

    result: dict[str, int] = {}
    for code in required_codes:
        raw_value = normalized[code]
        if isinstance(raw_value, bool):
            raise ValueError(f"{code} 仓库存必须是非负整数")
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{code} 仓库存必须是非负整数") from exc
        if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
            raise ValueError(f"{code} 仓库存必须是非负整数")
        result[code] = int(numeric)
    return result


def _normalize_cnsc_warehouse_stocks(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or not value:
        return {}
    return _validated_cnsc_warehouse_stocks(value)


def _cnsc_configuration_issues(
    config: dict[str, Any],
    *,
    selected_sizes: Any = None,
    selected_variants: Any = None,
) -> list[tuple[str, str]]:
    """Collect every CNSC configuration problem as ordered (field_key, message) pairs.

    A missing per-site discount activity is always reported; callers that tolerate
    it (template save with ``allow_missing_activity``) drop the ``.discount_activity``
    keys before deciding whether to raise. Field keys mirror the frontend
    ``cnscConfigurationLabel`` vocabulary so the UI can render them directly. The
    ordering matches the historical raise order so the first issue is the message
    ``_validate_cnsc_store_template`` would have raised.

    ``selected_variants`` carries the real (color, size) SKU combos; without it
    the check falls back to recognized colors × ``selected_sizes`` (templates
    carry no colors, so this degrades to the historical per-size check)."""
    issues: list[tuple[str, str]] = []

    def add(key: str, message: str) -> None:
        if any(existing_key == key for existing_key, _ in issues):
            return
        issues.append((key, message))

    store = config.get("cnsc_store") or {}
    if not str(store.get("id") or store.get("name") or "").strip():
        add("cnsc_store", "cnsc_store is required")

    targets = list(config.get("target_site_stores") or [])
    if not targets:
        add("target_site_stores", "target_site_stores is required")

    default_discount = _float_or_empty(config.get("default_site_discount_percent"))
    default_ok = default_discount != "" and 0 < float(default_discount) < 100
    if not default_ok:
        add(
            "default_site_discount_percent",
            "default_site_discount_percent must be greater than 0 and less than 100",
        )

    effective_discounts: dict[str, float] = {}
    for target in targets:
        code = str(target.get("site_code") or "UNKNOWN").strip().upper() or "UNKNOWN"
        if not str(target.get("store_id") or target.get("store_name") or "").strip():
            add(f"target_site_stores.{code}.store_identity", "each target site requires a store identity")
        if not str(target.get("mabang_discount_id") or "").strip():
            add(f"target_site_stores.{code}.discount_activity", "each target site requires a discount activity")
        override = _float_or_empty(target.get("discount_percent"))
        if override != "":
            effective_discount: float | None = float(override)
        elif default_ok:
            effective_discount = float(default_discount)
        else:
            effective_discount = None
        if effective_discount is None or not 0 < effective_discount < 100:
            add(
                f"target_site_stores.{code}.discount_percent",
                "discount_percent must be greater than 0 and less than 100",
            )
        if effective_discount is not None:
            effective_discounts[code] = effective_discount

    try:
        _validated_cnsc_warehouse_stocks(config.get("warehouse_stock_defaults"))
    except ValueError:
        add(
            "warehouse_stock_defaults",
            "warehouse_stock_defaults must contain valid TH, CN_GZ, and PH stocks",
        )
    if selected_sizes is None:
        selected_sizes = config.get("sizes")
    selected = {str(size or "").strip() for size in selected_sizes or [] if str(size or "").strip()}
    tiers = list(config.get("price_tiers") or [])
    if not tiers:
        add("price_tiers", "price_tiers is required")
    # 价格绑定模式：副本编辑器显式持久化（colors 按颜色分行 / sizes 按尺码分档）；
    # 模板无该字段时按档位形状推断（任一档带 colors → colors，旧 uniform 视为 colors）。
    binding_mode = str(config.get("cnsc_binding_mode") or "").strip().lower()
    if binding_mode not in {"colors", "sizes"}:
        binding_mode = _infer_cnsc_binding_mode(tiers)
    for tier in tiers:
        tier_id = str(tier.get("id") or "").strip() or "tier"
        global_price = _float_or_empty(tier.get("global_price_cny"))
        sizes0 = [str(s or "").strip() for s in tier.get("sizes") or [] if str(s or "").strip()]
        colors0 = [str(c or "").strip() for c in tier.get("colors") or [] if str(c or "").strip()]
        # 全球商品价格对每个档都必填：Mabang CNSC 的 SKU 表 *全球商品价格 列必填，
        # SKU 继承其档位值。颜色档（每颜色一档）复制时已用该档成本价填充
        # （见 _cnsc_copy_price_tiers），故此处不再豁免颜色档。
        if global_price == "" or float(global_price) <= 0:
            add(f"price_tiers.{tier_id}.global_price_cny", f"global_price_cny is required for {tier_id}")
        sizes = {str(size or "").strip() for size in tier.get("sizes") or [] if str(size or "").strip()}
        colors = {str(color or "").strip() for color in tier.get("colors") or [] if str(color or "").strip()}
        # 多个价格档的每一档都必须在对应绑定维度上真正绑定到变体：
        # colors 模式每档须有颜色（颜色档天然覆盖该颜色的全部尺码）；
        # sizes 模式每档须有尺码。单档无约束（uniform 形态）合法 = 一价覆盖全部 SKU。
        unbound_ok = len(tiers) == 1 and not sizes and not colors
        if not unbound_ok:
            if binding_mode == "colors" and not colors0:
                add(f"price_tiers.{tier_id}.colors", f"colors is required for {tier_id}")
            elif binding_mode == "sizes" and not sizes0:
                add(f"price_tiers.{tier_id}.sizes", f"sizes is required for {tier_id}")
        matrix = tier.get("site_prices") or {}
        for target in targets:
            site_code = str(target.get("site_code") or "").strip().upper()
            if not site_code or site_code == "SIP":
                continue
            prices = matrix.get(site_code) or {}
            sale = _float_or_empty(prices.get("sale_price"))
            original = _float_or_empty(prices.get("original_price"))
            # 原价必需；售价可留空（由目标店铺 %OFF 活动折扣自动推导），
            # 非空时须为有效正数且不超过原价。
            if (
                original == ""
                or float(original) <= 0
                or (sale != "" and (float(sale) <= 0 or float(sale) > float(original)))
            ):
                add(
                    f"site_price_matrix.{tier_id}.{site_code}",
                    f"site price matrix is incomplete for {tier_id}/{site_code}",
                )
                continue
    # Coverage: every (color, size) SKU variant must bind to exactly one tier
    # (0 = uncovered, >1 = overlapping), both under the same historical key.
    if selected_variants is None:
        variant_colors = {str(color or "").strip() for color in config.get("recognized_colors") or [] if str(color or "").strip()} or {""}
        combos = {(color, size) for color in variant_colors for size in selected}
    else:
        combos = {
            (str(variant[0] or "").strip(), str(variant[1] or "").strip())
            for variant in selected_variants
            if isinstance(variant, (list, tuple)) and len(variant) >= 2
        }
    for color, size in sorted(combos):
        matches = sum(1 for tier in tiers if _tier_matches(tier, color, size))
        if matches == 1:
            continue
        add(
            "cnsc_price_tier_size_coverage",
            "a size may belong to only one CNSC price tier" if matches > 1 else "CNSC price tiers must cover every selected size exactly once",
        )

    return issues


def _cnsc_configuration_problems(
    config: dict[str, Any],
    *,
    selected_sizes: Any = None,
    selected_variants: Any = None,
) -> list[str]:
    """Return ordered, de-duplicated CNSC configuration field keys (empty = ok)."""
    return [
        key
        for key, _ in _cnsc_configuration_issues(
            config, selected_sizes=selected_sizes, selected_variants=selected_variants
        )
    ]


def _validate_cnsc_store_template(template: dict[str, Any], *, allow_missing_activity: bool = False) -> None:
    """Fail closed when a CNSC template cannot later create a complete draft.

    Delegates detection to :func:`_cnsc_configuration_issues` so the strict gate,
    the copy step, and automation readiness all share one source of truth. When
    ``allow_missing_activity`` is set, a missing per-site discount activity is not
    a hard error (it is still reported via ``missing_configuration``)."""
    mode = str(template.get("listing_mode") or "site").strip().lower()
    if mode not in {"site", "cnsc_global"}:
        raise ValueError("listing_mode must be site or cnsc_global")
    if mode != "cnsc_global":
        return
    issues = _cnsc_configuration_issues(template)
    if allow_missing_activity:
        issues = [(key, message) for key, message in issues if not key.endswith(".discount_activity")]
    if issues:
        raise ValueError(issues[0][1])


def _normalize_default_sku_size_prices(value: Any) -> dict[str, dict[str, Any]]:
    """默认 SKU(白T恤) 的分尺码价格覆盖：``{尺码: {normal_price, sale_price}}``。

    只覆盖填了值的尺码（当前业务上是童码 90cm），其余尺码继续用默认 SKU 自己的
    售价/折扣价。半填（只填售价或只填折扣价）的尺码会被保留下来由
    ``_default_sku_configuration_problems`` 拦住，避免静默退回另一档价格。
    两个都空的条目直接丢弃，让"没设置覆盖"不产生噪音。
    """
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_size, raw_entry in value.items():
        size = str(raw_size or "").strip()
        if not size:
            continue
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        normal_price = _float_or_empty(entry.get("normal_price"))
        sale_price = _float_or_empty(entry.get("sale_price"))
        if normal_price == "" and sale_price == "":
            continue
        normalized[size] = {"normal_price": normal_price, "sale_price": sale_price}
    return normalized


def _default_sku_sale_price_at_ratio(default_sku: dict[str, Any], normal: float) -> float:
    """默认 SKU 分尺码覆盖只填原价时的折扣价：按默认 SKU 自己的 折扣价/售价 比例
    换算（与主表格「按尺码设置价格」的同一折扣比例规则一致）；默认 SKU 没填比例
    就按六折兜底。"""
    base_normal = _float_or_empty(default_sku.get("normal_price"))
    base_sale = _float_or_empty(default_sku.get("sale_price"))
    if base_normal != "" and float(base_normal) > 0 and base_sale != "" and float(base_sale) > 0:
        sale = Decimal(str(normal)) * Decimal(str(base_sale)) / Decimal(str(base_normal))
    else:
        sale = Decimal(str(normal)) * Decimal("0.6")
    return float(sale.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _default_sku_size_price(default_sku: dict[str, Any], size: Any) -> dict[str, Any] | None:
    """该尺码完整可用的覆盖价；没有可用覆盖时返回 ``None``（调用方退回共享价）。

    2026-09-26 起与主表格 size_price_overrides 同一套语义：只填原价＝折扣价按
    默认 SKU 自己的折扣比例换算；只填折扣价＝原价继承默认 SKU 的售价；两项都
    填按填的走。模板尺码普遍带括号补充值（``90cm(10-12kg)``），而设置里填的是
    ``90cm``：先按原值精确匹配，再按去括号后的尺码匹配（与 SKU 编码去括号同一
    套归一化）。
    """
    override = (default_sku or {}).get("size_price_overrides")
    if not isinstance(override, dict):
        return None
    wanted = str(size or "").strip()
    entry = override.get(wanted)
    if not isinstance(entry, dict):
        stripped = _strip_parenthetical(wanted)
        entry = next(
            (
                value
                for key, value in override.items()
                if stripped and _strip_parenthetical(key) == stripped
            ),
            None,
        )
    if not isinstance(entry, dict):
        return None
    override_normal = _float_or_empty(entry.get("normal_price"))
    override_sale = _float_or_empty(entry.get("sale_price"))
    if override_normal == "" and override_sale == "":
        return None
    normal_price = override_normal if override_normal != "" else _float_or_empty(default_sku.get("normal_price"))
    if normal_price == "":
        return None
    if override_sale != "":
        return {"normal_price": float(normal_price), "sale_price": float(override_sale)}
    return {
        "normal_price": float(normal_price),
        "sale_price": _default_sku_sale_price_at_ratio(default_sku, float(normal_price)),
    }


def _normalize_default_sku(value: Any) -> dict[str, Any]:
    """店铺模板「默认 SKU(白T恤)」配置。

    本土(site)店铺上架时在颜色轴**末尾**追加的一个额外颜色：图片、售价、折扣价、
    SKU 全部独立于其他 SKU（不参与 sku_template 生成）。跨境(CNSC)链路不消费它。
    ``size_price_overrides`` 让单个尺码（如童码 90cm）用独立的一对售价/折扣价，
    其余尺码沿用上面的售价/折扣价。
    """
    payload = value if isinstance(value, dict) else {}
    return {
        "enabled": payload.get("enabled") is True,
        "color_name": str(payload.get("color_name") or "").strip(),
        "sku": str(payload.get("sku") or "").strip(),
        "image_asset_id": str(payload.get("image_asset_id") or "").strip(),
        "image_file_name": str(payload.get("image_file_name") or "").strip(),
        "image_preview_url": str(payload.get("image_preview_url") or "").strip(),
        "image_path": str(payload.get("image_path") or "").strip(),
        "normal_price": _float_or_empty(payload.get("normal_price")),
        "sale_price": _float_or_empty(payload.get("sale_price")),
        "size_price_overrides": _normalize_default_sku_size_prices(payload.get("size_price_overrides")),
    }


def _active_default_sku(template: dict[str, Any], listing_copy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """生效的默认 SKU 配置；跨境模板恒为 None（跨境行为与改动前完全一致）。"""
    template = template or {}
    source = listing_copy or {}
    if str(source.get("listing_mode") or template.get("listing_mode") or "site").strip().lower() == "cnsc_global":
        return None
    copy_value = source.get("default_sku")
    payload = copy_value if isinstance(copy_value, dict) and copy_value else template.get("default_sku")
    config = _normalize_default_sku(payload)
    return config if config["enabled"] else None


def _sku_parentheses_stripped(template: dict[str, Any]) -> bool:
    """本土模板默认把尺码的括号补充值从 SKU 编码里去掉；跨境恒不处理。"""
    if str((template or {}).get("listing_mode") or "site").strip().lower() == "cnsc_global":
        return False
    return (template or {}).get("sku_strip_parentheses") not in (False, "false", 0, "0")


_PARENTHETICAL_RE = re.compile(r"[（(][^()（）]*[)）]")


def _strip_parenthetical(value: Any) -> str:
    """去掉括号补充值：``130cm(21-26kg)`` → ``130cm``。

    只作用于 SKU 编码；页面/类目展示的尺码保持原样。
    """
    text = str(value or "")
    if not text:
        return ""
    cleaned = _PARENTHETICAL_RE.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _default_sku_image_path(default_sku: dict[str, Any]) -> str:
    """解析默认 SKU 图片：手填路径优先，其次上传的 asset。均不存在时返回空串。"""
    raw_path = str((default_sku or {}).get("image_path") or "").strip()
    if raw_path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = settings.PROJECT_ROOT / candidate
        if candidate.is_file():
            return str(candidate)
    asset_id = str((default_sku or {}).get("image_asset_id") or "").strip()
    if asset_id:
        resolved = resolve_image_upload(asset_id, storage_dir=settings.STORAGE_DIR, kind="default_sku")
        if resolved:
            return str(resolved[0])
    return ""


def _default_sku_image_name(default_sku: dict[str, Any], image_path: str) -> str:
    return str((default_sku or {}).get("image_file_name") or "").strip() or (
        Path(image_path).name if str(image_path or "").strip() else ""
    )


def _format_default_sku_code(default_sku: dict[str, Any], size: Any) -> str:
    """默认 SKU 的固定编码：``AL9999-PureWhite`` + 尺码 → ``AL9999-PureWhite-150cm``。

    值里自带 ``{size}`` 占位时按占位展开（可自定义分隔符）；尺码已去括号。
    """
    value = str((default_sku or {}).get("sku") or "").strip()
    if not value:
        return ""
    stripped_size = _strip_parenthetical(size)
    if not stripped_size or stripped_size == "default":
        return value
    if "{size}" in value:
        try:
            return value.format_map(_SkuTemplateValues({"size": stripped_size}))
        except (KeyError, ValueError, IndexError):
            return value
    return f"{value}-{stripped_size}"


def _default_sku_configuration_problems(default_sku: dict[str, Any], *, colors: list[str] | None = None) -> list[str]:
    """启用后必填项与颜色重名检查（本土副本的自动化拦截）。"""
    if not default_sku or not default_sku.get("enabled"):
        return []
    problems: list[str] = []
    color_name = str(default_sku.get("color_name") or "").strip()
    if not color_name:
        problems.append("default_sku_color")
    if not str(default_sku.get("sku") or "").strip():
        problems.append("default_sku_code")
    if not _default_sku_image_path(default_sku):
        problems.append("default_sku_image")
    if default_sku.get("normal_price") in ("", None) or default_sku.get("sale_price") in ("", None):
        problems.append("default_sku_price")
    # 分尺码覆盖（size_price_overrides）与主表格同一套语义：只填原价＝折扣价按
    # 默认 SKU 比例换算，只填折扣价＝原价继承——半填不再是配置问题，不再拦截。
    if color_name and colors and color_name.lower() in {str(item or "").strip().lower() for item in colors}:
        # 颜色轴值重复会让马帮变种表少生成一整组行，执行器行数校验必然失败。
        problems.append("default_sku_color_conflict")
    return problems


def _normalize_store_template_payload(payload: dict[str, Any], *, now: str) -> dict[str, Any]:
    profile_defaults = _profile_defaults_for_payload(payload)
    sizes = _value_or_default(payload, profile_defaults, "sizes", [])
    if isinstance(sizes, str):
        sizes = [item.strip() for item in sizes.split(",") if item.strip()]
    elif isinstance(sizes, list):
        sizes = [str(item or "").strip() for item in sizes if str(item or "").strip()]
    else:
        sizes = []
    discount_id = str(_value_or_default(payload, profile_defaults, "discount_id", "") or "").strip()
    mabang_discount_id = str(_value_or_default(payload, profile_defaults, "mabang_discount_id", discount_id) or "").strip()
    if not discount_id:
        discount_id = mabang_discount_id
    requested_version_id = str(payload.get("category_profile_version_id") or "").strip()
    published_version_id = str(profile_defaults.get("category_profile_version_id") or "").strip()
    if requested_version_id and requested_version_id != published_version_id:
        raise ValueError("Store templates can only reference the published category profile version")
    attribute_values = payload.get("attribute_values")
    if not isinstance(attribute_values, dict):
        attribute_values = payload.get("category_attributes")
    if not isinstance(attribute_values, dict):
        attribute_values = (
            profile_defaults.get("attribute_values")
            or profile_defaults.get("category_attributes")
            or DEFAULT_CATEGORY_ATTRIBUTES
        )
    attribute_values = deepcopy(attribute_values)
    stock = _int_or_empty(payload.get("stock"))
    weight_kg = _float_or_empty(payload.get("weight_kg"))
    package_length_cm = _float_or_empty(payload.get("package_length_cm"))
    package_width_cm = _float_or_empty(payload.get("package_width_cm"))
    package_height_cm = _float_or_empty(payload.get("package_height_cm"))
    return {
        "id": payload.get("id") or str(uuid.uuid4()),
        "store_name": str(payload.get("store_name") or "").strip(),
        "platform": str(payload.get("platform") or "shopee").strip().lower(),
        "country": str(_value_or_default(payload, profile_defaults, "country", "MY") or "MY").strip().upper(),
        "currency": str(_value_or_default(payload, profile_defaults, "currency", "MYR") or "MYR").strip(),
        "template_name": str(payload.get("template_name") or "").strip(),
        "category_profile_id": str(_value_or_default(payload, profile_defaults, "category_profile_id", "") or "").strip(),
        "category_profile_version_id": requested_version_id or published_version_id,
        "product_line": str(_value_or_default(payload, profile_defaults, "product_line", payload.get("category") or "") or "").strip(),
        "marketplace_category_path": str(_value_or_default(payload, profile_defaults, "marketplace_category_path", "") or "").strip(),
        "mabang_store_id": str(payload.get("mabang_store_id") or payload.get("store_id") or "").strip(),
        # 模板按马帮账号隔离：账号由所选店铺推导（店铺只属于一个账号），前端选店时一并带上。
        "account_id": str(payload.get("account_id") or "").strip(),
        "mabang_category_id": str(_value_or_default(payload, profile_defaults, "mabang_category_id", payload.get("category_id") or "") or "").strip(),
        "mabang_discount_id": mabang_discount_id,
        "normal_price": _float_or_empty(_value_or_default(payload, profile_defaults, "normal_price")),
        "sale_price": _float_or_empty(_value_or_default(payload, profile_defaults, "sale_price")),
        "size_price_overrides": _normalize_size_price_overrides(payload.get("size_price_overrides")),
        "discount_label": str(_value_or_default(payload, profile_defaults, "discount_label", "") or "").strip(),
        "discount_id": discount_id,
        "sizes": sizes,
        "variation_rule": str(_value_or_default(payload, profile_defaults, "variation_rule", "color_size") or "color_size").strip(),
        "sku_template": str(_value_or_default(payload, profile_defaults, "sku_template", "{main_sku}_{color}||{size}") or "{main_sku}_{color}||{size}").strip(),
        "title_suffix": str(_value_or_default(payload, profile_defaults, "title_suffix", "") or "").strip(),
        "description": str(_value_or_default(payload, profile_defaults, "description", "") or "").strip(),
        "attribute_values": attribute_values,
        "category_attributes": deepcopy(attribute_values),
        "variation": _normalize_variation(_value_or_default(payload, profile_defaults, "variation", DEFAULT_VARIATION)),
        # 本土专属：默认 SKU(白T恤) 与「SKU 去括号」开关；跨境模板保存时保留但不生效。
        "default_sku": _normalize_default_sku(payload.get("default_sku")),
        "sku_strip_parentheses": payload.get("sku_strip_parentheses") not in (False, "false", 0, "0"),
        # 本土专属：产品图最后一张放尺码表（满 9 张时第 9 张被替换，不足 9 张追加到末尾；
        # 描述末尾那批图不重复插尺码表）。跨境没有这条规则，保存时强制关闭。
        "size_chart_as_last_product_image": (
            _size_chart_last_product_image_enabled(payload)
            if str(payload.get("listing_mode") or "site").strip().lower() != "cnsc_global"
            else False
        ),
        "default_size_chart_path": str(_value_or_default(payload, profile_defaults, "default_size_chart_path", "") or "").strip(),
        # 本土专属：默认视频（马帮视频库 videoId）。可直接手填，或留空由首次自动化
        # 上传成功后回填（见 finish_automation_job → _record_default_video_id_from_result）。
        # 跨境模板不设该字段，自动化照旧每次上传（视频标题统一用主 SKU）。
        "default_video_id": str(payload.get("default_video_id") or "").strip(),
        "size_chart_asset_id": str(payload.get("size_chart_asset_id") or "").strip(),
        "size_chart_preview_url": str(payload.get("size_chart_preview_url") or "").strip(),
        "size_chart_source": _normalize_size_chart_source(payload.get("size_chart_source")),
        "mabang_size_chart_template_id": str(payload.get("mabang_size_chart_template_id") or "").strip(),
        "mabang_size_chart_template_name": str(payload.get("mabang_size_chart_template_name") or "").strip(),
        "description_profile_version_id": str(payload.get("description_profile_version_id") or "").strip(),
        "promotion_activity_id": str(payload.get("promotion_activity_id") or mabang_discount_id).strip(),
        "listing_mode": str(payload.get("listing_mode") or "site").strip().lower() or "site",
        "cnsc_store": deepcopy(payload.get("cnsc_store") or {}),
        "target_site_stores": _normalize_cnsc_target_sites(payload.get("target_site_stores")),
        "site_price_order": [
            site for site in (
                str(item or "").strip().upper() for item in (payload.get("site_price_order") or [])
            )
            if site in _BASE_PRICE_SITES
        ],
        "default_site_discount_percent": _float_or_empty(payload.get("default_site_discount_percent") if "default_site_discount_percent" in payload else 40),
        "price_tiers": _normalize_cnsc_price_tiers(payload.get("price_tiers")),
        "warehouse_stock_defaults": _normalize_cnsc_warehouse_stocks(payload.get("warehouse_stock_defaults")),
        "cnsc_import_sku_rows": deepcopy(payload.get("cnsc_import_sku_rows") or []),
        "missing_configuration": list(payload.get("missing_configuration") or []),
        "main_image_watermark": _normalize_watermark_reference(payload.get("main_image_watermark")),
        "product_image_watermark": _normalize_watermark_reference(payload.get("product_image_watermark")),
        "stock": stock,
        "weight_kg": weight_kg,
        "package_length_cm": package_length_cm,
        "package_width_cm": package_width_cm,
        "package_height_cm": package_height_cm,
        "transaction_info": {
            "stock": stock,
            "weight_kg": weight_kg,
            "package_length_cm": package_length_cm,
            "package_width_cm": package_width_cm,
            "package_height_cm": package_height_cm,
        },
        "template_product_id": str(_value_or_default(payload, profile_defaults, "template_product_id", "") or "").strip(),
        "status": str(payload.get("status") or "active").strip(),
        "created_at": payload.get("created_at") or now,
        "updated_at": payload.get("updated_at") or now,
    }


def _canonical_category_path(value: Any) -> str:
    """Canonical form of a Mabang category path: "A > B > C" (single spaces).

    存量模板/旧采集存的是紧凑形态 "A>B>C"；真正的身份校验是 category_id，
    路径比较只该拦「层级真的不同」，分隔符写法差异不算不匹配。"""
    parts = [part.strip() for part in re.split(r"\s*>\s*", str(value or "").strip())]
    return " > ".join(part for part in parts if part)


def _validate_store_template_category_binding(data: dict[str, Any], template: dict[str, Any]) -> None:
    platform = str(template.get("platform") or "").strip().lower()
    country = str(template.get("country") or "").strip().upper()
    store_id = str(template.get("mabang_store_id") or "").strip()
    category_id = str(template.get("mabang_category_id") or "").strip()
    if not platform or not country or not store_id or not category_id:
        return

    active_store_options = [
        item
        for item in data.get("mabang_store_options", [])
        if str(item.get("status") or "active").strip().lower() == "active"
    ]
    all_categories = list(data.get("marketplace_categories", []))
    if not active_store_options and not all_categories:
        return

    store_options = [
        item
        for item in active_store_options
        if str(item.get("mabang_store_id") or "").strip() == store_id
    ]
    if active_store_options and not store_options:
        raise CategoryProfileConflict("Store template category does not match the selected store scope")
    if store_options:
        canonical_store = next(
            (
                item
                for item in store_options
                if str(item.get("platform") or "").strip().lower() == platform
            ),
            None,
        )
        canonical_country = str((canonical_store or {}).get("country") or "").strip().upper()
        if not canonical_store or not canonical_country or canonical_country != country:
            raise CategoryProfileConflict("Store template category does not match the selected store scope")

    store_categories = [
        item
        for item in all_categories
        if str(item.get("store_id") or "").strip() == store_id
    ]
    # 该店存在于已选店铺（store_options 已验证 platform/country），但从未采集过
    # 类目树（store_categories 为空）：允许保存模板，等采集后再做类目内容强校验——
    # 否则已存在的模板连其它字段都保存不了（采集入口独立于模板保存）。
    # 下方 profile 一致性校验仍会执行。
    catalog_missing_for_store = bool(all_categories) and not store_categories
    if catalog_missing_for_store and not store_options:
        raise CategoryProfileConflict("Store template category does not match the selected store scope")
    category = None
    if not catalog_missing_for_store:
        catalog_scopes = {
            (
                str(item.get("platform") or "").strip().lower(),
                str(item.get("country") or "").strip().upper(),
            )
            for item in store_categories
        }
        if catalog_scopes and (platform, country) not in catalog_scopes:
            raise CategoryProfileConflict("Store template category does not match the selected store scope")

        scope_categories = [
            item
            for item in store_categories
            if str(item.get("platform") or "").strip().lower() == platform
            and str(item.get("country") or "").strip().upper() == country
        ]
        if scope_categories:
            category = next(
                (
                    item
                    for item in scope_categories
                    if str(item.get("category_id") or "").strip() == category_id
                    and item.get("active", True)
                    and item.get("is_leaf")
                ),
                None,
            )
            if not category:
                raise CategoryProfileConflict("Store template category must be an active leaf in the selected store catalog")

    profile_id = str(template.get("category_profile_id") or "").strip()
    version_id = str(template.get("category_profile_version_id") or "").strip()
    if not profile_id or not version_id:
        raise CategoryProfileConflict("Store template category requires a published category profile")
    profile = next((item for item in data.get("category_profiles", []) if item.get("id") == profile_id), None)
    version = next((item for item in data.get("category_profile_versions", []) if item.get("id") == version_id), None)
    if not profile or not version or version.get("status") not in {"active", "published"}:
        raise CategoryProfileConflict("Store template category requires an active category profile")
    current_version_id = profile.get("active_version_id") or profile.get("published_version_id")
    if version.get("profile_id") != profile_id or current_version_id != version_id:
        raise CategoryProfileConflict("Store template category profile version does not match its published profile")

    profile_category_id = str(profile.get("marketplace_category_id") or profile.get("mabang_category_id") or "").strip()
    profile_platform = str(profile.get("platform") or "").strip().lower()
    profile_country = str(profile.get("country") or "").strip().upper()
    if profile_category_id != category_id or profile_platform != platform or profile_country != country:
        raise CategoryProfileConflict("Store template category does not match the published category profile")

    if not category:
        return
    selected_path = str(category.get("path") or category.get("path_en") or "").strip()
    template_path = str(template.get("marketplace_category_path") or "").strip()
    if not selected_path or not template_path:
        return
    if _canonical_category_path(template_path) != _canonical_category_path(selected_path):
        raise CategoryProfileConflict("Store template category path does not match the selected catalog category")
    canonical_path = _canonical_category_path(selected_path)
    if template_path != canonical_path:
        # 仅分隔符写法不同（存量模板 "A>B>C" vs 目录 "A > B > C"）：就地采纳
        # 目录 canonical 形态，让这次保存自愈存量数据而不是失败。
        template["marketplace_category_path"] = canonical_path


def _normalize_mabang_store_option(payload: dict[str, Any], *, now: str) -> dict[str, Any]:
    platform = str(payload.get("platform") or "shopee").strip().lower()
    country = str(payload.get("country") or payload.get("site") or "").strip().upper()
    store_name = str(payload.get("store_name") or payload.get("name") or payload.get("label") or "").strip()
    mabang_store_id = str(payload.get("mabang_store_id") or payload.get("store_id") or payload.get("value") or "").strip()
    return {
        "id": payload.get("id") or str(uuid.uuid4()),
        "platform": platform,
        "country": country,
        "store_name": store_name,
        "mabang_store_id": mabang_store_id,
        "account_id": str(payload.get("account_id") or "").strip(),
        "source": str(payload.get("source") or "browser_use").strip(),
        "status": str(payload.get("status") or "active").strip(),
        "created_at": payload.get("created_at") or now,
        "updated_at": now,
    }


def _normalize_watermark_reference(value: Any) -> dict[str, Any]:
    # Reference stored on store_templates.main_image_watermark / product_image_watermark.
    # Structure: {template_id, name, mode, positions}
    #   mode="auto" (default): executor uses hardcoded slots — main=[1], product=[2..N]
    #   mode="custom": executor uses positions (1-based image indexes) verbatim
    # Empty template_id/name reduces to {} (means "no watermark").
    if not isinstance(value, dict):
        return {}
    template_id = str(value.get("template_id") or value.get("id") or "").strip()
    name = str(value.get("name") or "").strip()
    if not template_id or not name:
        return {}
    mode = str(value.get("mode") or "auto").strip().lower()
    if mode not in {"auto", "custom"}:
        mode = "auto"
    raw_positions = value.get("positions")
    positions: list[int] = []
    if isinstance(raw_positions, list):
        seen: set[int] = set()
        for entry in raw_positions:
            try:
                slot = int(entry)
            except (TypeError, ValueError):
                continue
            if slot < 1 or slot > 9 or slot in seen:
                continue
            seen.add(slot)
            positions.append(slot)
        positions.sort()
    return {"template_id": template_id, "name": name, "mode": mode, "positions": positions}


def _normalize_size_chart_source(value: Any) -> str:
    source = str(value or "").strip().lower()
    return source if source in {"upload", "template", "mabang_capture"} else "upload"


def _size_chart_last_product_image_enabled(source: dict[str, Any]) -> bool:
    """「产品图最后一张放尺码表」开关（本土专属，默认开）。

    字段缺失按「开」处理：存量模板/副本没有这个字段时按新的默认行为走，
    只有显式 false（或 "false"/0/"0"）才算操作员手动关掉。
    """
    if not isinstance(source, dict):
        return True
    return source.get("size_chart_as_last_product_image") not in (False, "false", 0, "0")


def _size_chart_fields_from_template(template: dict[str, Any]) -> dict[str, str]:
    size_chart_path = str(template.get("default_size_chart_path") or "").strip()
    if not size_chart_path:
        return {"size_chart_image": "", "size_chart_image_path": ""}
    return {
        "size_chart_image": Path(size_chart_path).name,
        "size_chart_image_path": size_chart_path,
    }


def _build_store_listing_copy(
    base: dict[str, Any],
    template: dict[str, Any],
    now: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cnsc_binding_mode, copy_price_tiers = _cnsc_copy_price_tiers(base, template)
    base_name = base.get("product_name_cn") or base.get("base_name") or base.get("group_id") or ""
    title = str(base.get("listing_title") or "").strip()
    if not title:
        title = " ".join(part for part in [base_name, template.get("title_suffix") or "", base.get("group_id") or ""] if str(part or "").strip()).strip()
    size_chart_source = _normalize_size_chart_source(template.get("size_chart_source"))
    # 尺码图表选「选择模板」时表单页上没有可引用的尺码图地址（表单项里只有
    # 「上传图片 / 选择模板 / 同步」，没有预览图），描述末尾要靠店铺模板里一并上传的
    # 这张本地图，所以来源是模板时本地上传的图照样带给副本（2026-09-23 起）。
    # 没上传时这里是空路径，行为与改动前一致（描述那张尺码表图降级为警告）。
    size_chart = _size_chart_fields_from_template(template)
    snapshot = _profile_snapshot_for_template(template, data or {})
    attribute_values = deepcopy(template.get("attribute_values") or template.get("category_attributes") or {})
    copy_category_id, copy_category_path, category_from_base = _resolve_copy_category(base, template, data)
    effective_profile_id = str(template.get("category_profile_id") or "").strip()
    effective_version_id = str(template.get("category_profile_version_id") or "").strip()
    # 属性规则快照必须跟「生效类目」一致：基础商品选了类目（category_source=
    # base_product）时上架类目是基础商品的类目，模板方案的字段（如 Hoodies 的
    # 「套筒长度」）在那个类目的马帮表单上根本不存在，执行器会整单 blocked
    # （select_field_not_found，现场 2026-09-26 FL0067 在 Costumes 页面找不到「材料」）。
    # 生效类目有自己的已发布方案就换绑过去（属性值重置为该方案默认值）；
    # 没有就清空快照与绑定，执行器跳过属性填写——宁可少填，也不填错类目的属性。
    if category_from_base and copy_category_id and copy_category_id != str(template.get("mabang_category_id") or "").strip():
        effective_profile_id, effective_version_id, snapshot = _profile_binding_for_category(
            copy_category_id,
            data or {},
            platform=template.get("platform") or "shopee",
            country=template.get("country") or "MY",
        )
        attribute_values = {
            str(field.get("field_id")): deepcopy(field.get("default_value"))
            for field in (snapshot.get("fields") or [])
            if field.get("field_id") and field.get("default_value") is not None
        }
        # 操作员在商品维护里定的属性值（AI 推荐/手动）优先于方案默认值
        base_values = base.get("attribute_values") if isinstance(base.get("attribute_values"), dict) else {}
        snapshot_field_ids = {str(field.get("field_id")) for field in (snapshot.get("fields") or [])}
        if snapshot_field_ids and base_values:
            attribute_values = {
                field_id: deepcopy(base_values[field_id])
                for field_id in snapshot_field_ids
                if field_id in base_values and base_values[field_id] not in ("", None)
            }
    item = {
        "id": str(uuid.uuid4()),
        "listing_kind": "store_listing_copy",
        "base_product_id": base.get("id") or "",
        "template_id": template.get("id") or "",
        "store_name": template.get("store_name") or "",
        "platform": template.get("platform") or "shopee",
        "listing_mode": template.get("listing_mode") or "site",
        "country": template.get("country") or "MY",
        "currency": template.get("currency") or "MYR",
        "category": template.get("product_line") or base.get("category") or "",
        # Category rule: base product filled a product_category → use it and
        # ignore the store template's category; base has none → template default.
        "expected_category_path": "",
        "category_source": "base_product" if category_from_base else "store_template",
        "category_profile_id": effective_profile_id,
        "category_profile_version_id": effective_version_id,
        "marketplace_category_path": copy_category_path or template.get("marketplace_category_path") or "",
        "mabang_store_id": template.get("mabang_store_id") or "",
        "mabang_category_id": copy_category_id or template.get("mabang_category_id") or "",
        "mabang_discount_id": template.get("mabang_discount_id") or template.get("discount_id") or "",
        "cnsc_store": deepcopy(template.get("cnsc_store") or {}),
        "target_site_stores": deepcopy(template.get("target_site_stores") or []),
        "site_price_order": [str(site or "").strip().upper() for site in (template.get("site_price_order") or [])],
        "default_site_discount_percent": template.get("default_site_discount_percent") or 40,
        "price_tiers": deepcopy(copy_price_tiers),
        # 默认视频（马帮 videoId，本土模板）：副本带上它，执行器按 ID 选视频而不再上传。
        "default_video_id": str(template.get("default_video_id") or "").strip(),
        # 模板的尺码 chip 顺序：本土副本尺码轴按它排（模板已删时执行器退回这份）。
        "sizes": deepcopy(template.get("sizes") or []),
        "cnsc_binding_mode": cnsc_binding_mode if template.get("listing_mode") == "cnsc_global" else "",
        "warehouse_stock_defaults": deepcopy(template.get("warehouse_stock_defaults") or {"TH": 0, "CN_GZ": 0, "PH": 0}),
        "group_id": base.get("group_id") or "",
        "main_sku": base.get("main_sku") or base.get("group_id") or "",
        "product_name_cn": base_name,
        "listing_title": title,
        # 模板固定描述优先：店铺模板「模板基础 → 描述模板」填写的固定描述对
        # 所有复制到该模板的详情生效；留空才回退到基础商品自己的详情。
        "description": str(template.get("description") or "").strip() or str(base.get("description") or "").strip(),
        "discount_label": template.get("discount_label") or "",
        "discount_id": template.get("discount_id") or "",
        "asset_root": base.get("asset_root") or "",
        "main_image": base.get("main_image") or "",
        # 产品图顺序 = 商品维护「产品图顺序」里操作员排好的顺序，原样带到副本、自动化
        # 参数和马帮草稿。曾按 template_id 重排第 2..N 张（规避 Shopee 重复刊登检测），
        # 但那会让操作员排好的顺序在副本里就变掉，2026-09-20 移除。
        "product_images": list(base.get("product_images") or []),
        "size_chart_image": size_chart["size_chart_image"],
        "size_chart_image_path": size_chart["size_chart_image_path"],
        "recognized_colors": list(base.get("recognized_colors") or []),
        "recognized_attributes": dict(base.get("recognized_attributes") or {}),
        "variant_image_map": dict(base.get("variant_image_map") or {}),
        "attribute_values": attribute_values,
        "category_attributes": deepcopy(attribute_values),
        "variation": _normalize_variation(template.get("variation") or {}),
        "variation_rule": template.get("variation_rule") or "color_size",
        "sku_template": template.get("sku_template") or "{main_sku}_{color}||{size}",
        # 默认 SKU(白T恤) 随模板落到副本：执行器链路只读副本，跨境为禁用空配置。
        "default_sku": deepcopy(template.get("default_sku") or _normalize_default_sku(None)),
        "sku_strip_parentheses": template.get("sku_strip_parentheses") is not False,
        "default_size_chart_path": template.get("default_size_chart_path") or "",
        "size_chart_asset_id": template.get("size_chart_asset_id") or "",
        "size_chart_preview_url": template.get("size_chart_preview_url") or "",
        "mabang_size_chart_template_id": template.get("mabang_size_chart_template_id") or "",
        "mabang_size_chart_template_name": template.get("mabang_size_chart_template_name") or "",
        # 本土模板开关快照：自动化据此决定「产品图」区最后一张是不是尺码表。
        "size_chart_as_last_product_image": (
            False
            if str(template.get("listing_mode") or "site") == "cnsc_global"
            else _size_chart_last_product_image_enabled(template)
        ),
        "size_chart_snapshot": {
            "source": size_chart_source,
            "asset_id": str(template.get("size_chart_asset_id") or ""),
            "path": size_chart["size_chart_image_path"],
            "template_id": str(template.get("mabang_size_chart_template_id") or ""),
            "template_name": str(template.get("mabang_size_chart_template_name") or ""),
        },
        "main_image_watermark": deepcopy(template.get("main_image_watermark") or {}),
        "product_image_watermark": deepcopy(template.get("product_image_watermark") or {}),
        "video": deepcopy(base.get("video") or {"validation_status": "missing", "validation_errors": ["video_missing"]}),
        "description_profile_version_id": template.get("description_profile_version_id") or "",
        "promotion_activity_id": template.get("promotion_activity_id") or template.get("mabang_discount_id") or "",
        "stock": template.get("stock"),
        "weight_kg": template.get("weight_kg"),
        "package_length_cm": template.get("package_length_cm"),
        "package_width_cm": template.get("package_width_cm"),
        "package_height_cm": template.get("package_height_cm"),
        "transaction_info": deepcopy(template.get("transaction_info") or {}),
        "status": "copy_ready",
        "created_at": now,
        "updated_at": now,
    }
    if snapshot:
        item["category_profile_snapshot"] = snapshot
        item["profile_snapshot_hash"] = hash_profile_snapshot(snapshot)
    return item


def _profile_snapshot_for_template(template: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    version_id = str(template.get("category_profile_version_id") or "").strip()
    profile_id = str(template.get("category_profile_id") or "").strip()
    if not version_id or not profile_id:
        return {}
    version = next((item for item in data.get("category_profile_versions", []) if item.get("id") == version_id), None)
    profile = next((item for item in data.get("category_profiles", []) if item.get("id") == profile_id), None)
    if not version or version.get("status") not in {"active", "published", "superseded"} or not profile:
        return {}
    return build_profile_snapshot(profile, version)


def _profile_binding_for_category(
    category_id: str,
    data: dict[str, Any],
    *,
    platform: str,
    country: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """已发布方案里找某个类目的绑定，返回 ``(profile_id, version_id, snapshot)``。

    ``country`` 给定时时严格匹配（副本创建路径）；传 None 表示不限国家（商品维护
    的类目预览——商品本身不属于任何店铺）。找不到（快速类目模板只存类目、还没
    采集必填项）时返回 ``("", "", {})``，调用方据此清空副本的属性快照——宁可跳过
    属性填写，也不能拿别的类目的属性字段去填（字段在马帮表单上根本不存在）。"""
    wanted = str(category_id or "").strip()
    if not wanted:
        return "", "", {}
    wanted_platform = str(platform or "").strip().lower()
    wanted_country = str(country or "").strip().upper() or None
    for profile in data.get("category_profiles", []):
        if str(profile.get("marketplace_category_id") or profile.get("mabang_category_id") or "").strip() != wanted:
            continue
        if str(profile.get("platform") or "").strip().lower() != wanted_platform:
            continue
        if wanted_country and str(profile.get("country") or "").strip().upper() != wanted_country:
            continue
        version_id = str(profile.get("active_version_id") or profile.get("published_version_id") or "").strip()
        version = next((item for item in data.get("category_profile_versions", []) if item.get("id") == version_id), None)
        if not version or version.get("status") not in {"active", "published"}:
            continue
        return str(profile.get("id") or ""), version_id, build_profile_snapshot(profile, version)
    return "", "", {}


def _cnsc_sale_price(original: Any, sale: Any, discount_percent: float | None = None, default_percent: float = 40) -> float | str:
    """跨境站点售价：手填 sale 非空时尊重手填；为空按 original × (1 - %OFF) 推导。"""
    original_num = _float_or_empty(original)
    if original_num == "" or float(original_num) <= 0:
        return ""
    sale_num = _float_or_empty(sale)
    if sale_num != "":
        return float(sale_num)
    off = float(discount_percent) if discount_percent is not None else float(default_percent)
    return round(float(original_num) * (1 - float(off) / 100), 2)


def _generate_listing_skus(base: dict[str, Any], template: dict[str, Any], listing_copy: dict[str, Any]) -> list[dict[str, Any]]:
    colors = [str(item or "").strip() for item in base.get("recognized_colors") or [] if str(item or "").strip()] or ["default"]
    cnsc = listing_copy.get("listing_mode") == "cnsc_global"
    # SKU 尺码源：本土按模板尺码（模板 chip 顺序即上架顺序），模板没配尺码才回退
    # 商品勾选/识别尺码；跨境仍以商品勾选/识别尺码优先。见 _listing_sizes。
    # 跨境与本土一致：SKU 均为 颜色×尺码；价格仍按颜色档（同色不同码同价）。
    sizes = _listing_sizes(base, template)
    rows = []
    now = utc_now()
    base_color_tiers = {
        str((tier.get("colors") or [""])[0] or "").strip().casefold(): tier
        for tier in (_normalize_base_price_tiers(base.get("price_tiers") or []) or [])
        if (tier.get("colors") or [])
    }
    for color in colors:
        for size in sizes:
            sku_code = _format_listing_sku(template, base, color, size)
            tier_source = listing_copy.get("price_tiers") if cnsc else template.get("price_tiers")
            tier = next((item for item in (tier_source or []) if _tier_matches(item, color, size)), None)
            warehouse_stocks = _normalize_cnsc_warehouse_stocks(template.get("warehouse_stock_defaults"))
            site_price_matrix = deepcopy(tier.get("site_prices") or {}) if tier else {}
            # 全球商品价格：CNSC 的 SKU 表 *全球商品价格 列必填。颜色档在复制时已
            # 用成本价填充（见 _cnsc_copy_price_tiers）；此处再兜底一次——旧 copy
            # 数据颜色档可能仍缺 global，用 base 同颜色档成本价补齐，绝不写空串。
            global_price_cny = tier.get("global_price_cny") if tier else ""
            if cnsc and global_price_cny in ("", None):
                base_tier = base_color_tiers.get(str(color or "").strip().casefold())
                if base_tier:
                    cost = base_tier.get("cost_price")
                    if cost not in ("", None) and float(cost) > 0:
                        global_price_cny = float(cost)
                if global_price_cny in ("", None):
                    global_price_cny = _default_template_global_price(template)
            if cnsc:
                # 跨境售价：来源档（base 颜色档 / 模板尺码档）手填了 sale 则尊重手填值；
                # sale 留空的站点按目标店铺活动折扣（%OFF）推导 sale = original × (1 - off/100)。
                default_off = float(listing_copy.get("default_site_discount_percent") or 40)
                site_discounts = {
                    str(s.get("site_code") or "").strip().upper(): float(s.get("discount_percent") or default_off)
                    for s in (listing_copy.get("target_site_stores") or [])
                    if str(s.get("site_code") or "").strip()
                }
                derived = {}
                for site_code, entry in site_price_matrix.items():
                    site_code = str(site_code or "").strip().upper()
                    if not isinstance(entry, dict):
                        continue
                    original = _float_or_empty(entry.get("original_price"))
                    if original == "" or float(original) <= 0:
                        continue
                    off = site_discounts.get(site_code, default_off)
                    sale = _cnsc_sale_price(original, entry.get("sale_price"), off)
                    derived[site_code] = {"original_price": float(original), "sale_price": sale}
                site_price_matrix = derived
            rows.append({
                "id": str(uuid.uuid4()),
                "listing_copy_id": listing_copy.get("id") or "",
                "base_product_id": base.get("id") or "",
                "template_id": template.get("id") or "",
                "sku_code": sku_code,
                "group_id": listing_copy.get("group_id") or "",
                "color_code": color,
                "color_cn": color,
                "size": size,
                "variant_name": color if size == "default" else f"{color}/{size}",
                "variant_image": _variant_image_for_color(base, color),
                "stock": _int_or_empty(template.get("stock")),
                "price_tier_id": tier.get("id") if tier else "",
                "global_price_cny": global_price_cny,
                "site_price_matrix": site_price_matrix,
                "warehouse_stocks": warehouse_stocks,
                "status": "active",
                "updated_at": now,
            })
    # 默认 SKU(白T恤) 组固定追加在最后：与浏览器输入的颜色轴顺序、
    # 以及马帮 SKU 表按颜色分组生成的行顺序保持一致。
    rows.extend(_default_sku_rows(base, template, listing_copy, sizes, now=now))
    return rows


def _order_sizes_by_template(sizes: list[str], template_sizes: Any) -> list[str]:
    """本土：尺码轴按模板的尺码顺序排（模板 chip 拖动顺序即上架顺序）。

    先按原值精确匹配，再按去括号后的尺码匹配（模板尺码普遍带括号补充值
    ``90cm(10-12kg)``，基础商品勾选/识别的可能是简写或不同写法）；模板里没有的
    尺码按原相对顺序排在后面。跨境不调用——跨境网格按行号填写，换序属于跨境
    行为变更，需单独验证。"""
    ordered_template = [str(item or "").strip() for item in (template_sizes or []) if str(item or "").strip()]
    if not ordered_template:
        return list(sizes)
    template_index = {value: index for index, value in enumerate(ordered_template)}
    template_stripped = {value: _strip_parenthetical(value) for value in ordered_template}

    def sort_key(pair: tuple[int, str]) -> tuple[int, int, int]:
        original_index, value = pair
        value = str(value or "").strip()
        if value in template_index:
            return (0, template_index[value], original_index)
        stripped = _strip_parenthetical(value)
        if stripped:
            for candidate, candidate_stripped in template_stripped.items():
                if candidate_stripped and candidate_stripped == stripped:
                    return (0, template_index[candidate], original_index)
        return (1, 0, original_index)

    return [value for _, value in sorted(enumerate(sizes), key=sort_key)]


def _template_sizes_for_copy(listing_copy: dict[str, Any], data: dict[str, Any] | None = None) -> Any:
    """本土副本的尺码顺序来源：**模板当前**的尺码 chip 顺序（操作员重排后
    「再次尝试」即生效），模板已删时退回副本创建时携带的 sizes。"""
    template_id = str(listing_copy.get("template_id") or "").strip()
    if template_id:
        store = data if data is not None else load_store()
        template = next((item for item in store.get("store_templates", []) if item.get("id") == template_id), None)
        sizes = (template or {}).get("sizes")
        if sizes:
            return sizes
    return listing_copy.get("sizes")


def _listing_size_order_rank(
    listing_copy: dict[str, Any],
    sizes: list[Any],
    data: dict[str, Any] | None = None,
) -> dict[str, int] | None:
    """本土副本的尺码顺序秩：按模板 chip 顺序（复用 _order_sizes_by_template），
    模板里没有的尺码按原相对顺序排在后面。跨境返回 None，调用方沿用
    _size_sort_value 旧排序——跨境网格按行号填写，换序属于跨境行为变更。"""
    if str(listing_copy.get("listing_mode") or "site") == "cnsc_global":
        return None
    unique_sizes: list[str] = []
    seen: set[str] = set()
    for value in sizes:
        size = str(value or "").strip()
        if size and size not in seen:
            seen.add(size)
            unique_sizes.append(size)
    ordered = _order_sizes_by_template(unique_sizes, _template_sizes_for_copy(listing_copy, data))
    return {size: index for index, size in enumerate(ordered)}


def _ordered_size_sort_key(size: Any, rank: dict[str, int] | None) -> tuple[int, int]:
    value = str(size or "").strip()
    if rank is None:
        return (0, _size_sort_value(value))
    if value in rank:
        return (0, rank[value])
    return (1, _size_sort_value(value))


def _listing_sizes(base: dict[str, Any], template: dict[str, Any]) -> list[str]:
    """SKU 尺码源。

    本土(site)：**模板尺码优先**。模板 chip 就是店铺刊登的尺码轴（拖动顺序即上架
    顺序），复制到模板后 SKU 一律按模板的尺码生成；模板没配尺码才回退到商品勾选/
    识别尺码(selected_sizes → recognized_sizes)，最后 ``["default"]``。
    2026-10-26 起：商品 AI 识别的字母码（XXS–XXXL）不再覆盖童装模板的 cm 码
    （现场 HN0083/HN0084 复制到 Oct.3H 模板生成的是 XXS–XXXL 而不是 90cm…）。

    跨境(cnsc_global)：保持旧优先级 商品勾选 → 识别 → 模板尺码。跨境网格按行号
    填写，改优先级属于跨境行为变更，需单独验证。"""
    template_sizes = [
        str(item or "").strip()
        for item in (template or {}).get("sizes") or []
        if str(item or "").strip()
    ]
    base_sizes = [
        str(item or "").strip()
        for item in (base.get("selected_sizes") or base.get("recognized_sizes") or [])
        if str(item or "").strip()
    ]
    if str((template or {}).get("listing_mode") or "site") == "cnsc_global":
        return base_sizes or template_sizes or ["default"]
    return template_sizes or base_sizes or ["default"]


def _default_sku_rows(
    base: dict[str, Any],
    template: dict[str, Any],
    listing_copy: dict[str, Any],
    sizes: list[str],
    *,
    now: str,
) -> list[dict[str, Any]]:
    """默认 SKU(白T恤) 的 SKU 行：一个额外颜色 × 现有尺码，SKU 为固定编码。"""
    default_sku = _active_default_sku(template, listing_copy)
    if not default_sku:
        return []
    color = str(default_sku.get("color_name") or "").strip()
    if not color:
        return []
    image_name = _default_sku_image_name(default_sku, _default_sku_image_path(default_sku))
    rows: list[dict[str, Any]] = []
    for size in sizes:
        rows.append({
            "id": str(uuid.uuid4()),
            "listing_copy_id": listing_copy.get("id") or "",
            "base_product_id": base.get("id") or "",
            "template_id": template.get("id") or "",
            "sku_code": _format_default_sku_code(default_sku, size),
            "group_id": listing_copy.get("group_id") or "",
            "color_code": color,
            "color_cn": color,
            "size": size,
            "variant_name": color if size == "default" else f"{color}/{size}",
            "variant_image": image_name,
            "stock": _int_or_empty(template.get("stock")),
            "price_tier_id": "",
            "global_price_cny": "",
            "site_price_matrix": {},
            "warehouse_stocks": {},
            "sku_source": "default_sku",
            "status": "active",
            "updated_at": now,
        })
    return rows


def _format_listing_sku(template: dict[str, Any], base: dict[str, Any], color: str, size: str) -> str:
    size = str(size or "").strip()
    # 本土模板默认把尺码的括号补充值从 SKU 编码里去掉（130cm(21-26kg) → 130cm）；
    # 展示用尺码、变种轴与类目属性仍是原值。跨境(CNSC)不处理。
    if size and _sku_parentheses_stripped(template):
        size = _strip_parenthetical(size)
    sku_template = str(template.get("sku_template") or "{main_sku}_{color}||{size}").strip()
    # size 为空（历史/异常数据）时去掉模板里的尺寸占位段，避免悬空 ||。
    if not size and ("{size}" in sku_template or "||" in sku_template):
        sku_template = "{main_sku}_{color}"
    values = _SkuTemplateValues({
        "main_sku": base.get("main_sku") or base.get("group_id") or "",
        "color": color,
        "size": size,
    })
    try:
        return sku_template.format_map(values)
    except (KeyError, ValueError):
        return "-".join(part for part in [values["main_sku"], color, size] if str(part or "").strip())


class _SkuTemplateValues(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _sync_listing_sku_codes(
    skus: list[dict[str, Any]],
    prices: list[dict[str, Any]],
    base: dict[str, Any],
    template: dict[str, Any],
    *,
    now: str,
) -> None:
    prices_by_sku_id = {price.get("sku_id"): price for price in prices if price.get("sku_id")}
    for sku in skus:
        if sku.get("sku_source") == "default_sku":
            # 默认 SKU 是固定编码（不跟随 main_sku / sku_template），重算会把它改写坏。
            continue
        sku_code = _format_listing_sku(template, base, sku.get("color_code") or sku.get("color_cn") or "", sku.get("size") or "")
        sku["sku_code"] = sku_code
        sku["updated_at"] = now
        price = prices_by_sku_id.get(sku.get("id"))
        if price is not None:
            price["sku_code"] = sku_code
            price["updated_at"] = now


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return None


def _site_sale_price_at_template_ratio(template: dict[str, Any], normal_price: Any) -> float | str:
    """按模板「交易信息」的折扣比例把原价换算成售价。

    比例 = 模板六折售价 / 模板原价（操作员自己填的两价）；模板没填售价时是固定的
    六折（0.6）——老模板与页面导入的默认行为不变。只有按尺码单独设了原价的行需要
    换算：模板自己那一行直接用填的售价，不再换算。
    """
    normal = _decimal_or_none(normal_price)
    if normal is None or normal <= 0:
        return ""
    template_normal = _decimal_or_none(template.get("normal_price"))
    template_sale = _decimal_or_none(template.get("sale_price"))
    if template_normal is not None and template_normal > 0 and template_sale is not None and template_sale > 0:
        sale = normal * template_sale / template_normal
    else:
        sale = normal * Decimal("0.6")
    return float(sale.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _site_row_prices(template: dict[str, Any], size: Any) -> tuple[float | str, float | str]:
    """一行本土(SITE)价格行的 (原价, 售价)。

    模板原价/六折售价照搬操作员填的值（留空才按六折兜底）。按尺码覆盖时：填了原价
    就按同一折扣比例换算（保持各行折扣一致），填了售价就直接用那个价；只填了售价的
    行原价继续继承模板。老数据是「尺码 → 原价」标量，等价于只覆盖原价。
    """
    override = (template.get("size_price_overrides") or {}).get(str(size or "")) or {}
    if isinstance(override, dict):
        override_normal = _float_or_empty(override.get("normal_price"))
        override_sale = _float_or_empty(override.get("sale_price"))
    else:
        override_normal, override_sale = _float_or_empty(override), ""
    normal_price = override_normal if override_normal != "" else _float_or_empty(template.get("normal_price"))
    if normal_price == "":
        return "", ""
    if override_sale != "":
        return normal_price, override_sale
    if override_normal == "":
        sale_price = _float_or_empty(template.get("sale_price"))
        if sale_price != "":
            return normal_price, sale_price
    return normal_price, _site_sale_price_at_template_ratio(template, normal_price)


def _generate_listing_prices(
    template: dict[str, Any],
    listing_copy: dict[str, Any],
    skus: list[dict[str, Any]],
    *,
    base_price_tiers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Generate listing price rows for a copy.

    CNSC copies pull site prices from the base product's price tiers (matched by
    ``_tier_matches`` color × size, trimmed to the template's target sites — base
    tier site_prices already use the copy-consumed original/sale keys) and carry
    ``cost_price`` from the base tier with ``price_source="base_product"``.
    Non-CNSC templates keep their template-driven normal/sale prices (see
    ``_site_row_prices``: the template's 六折售价 wins, blank means 原价 × 0.6);
    only the cost price is inherited.
    """
    now = utc_now()
    base_tiers = [tier for tier in (base_price_tiers or []) if isinstance(tier, dict)]
    # CNSC: only copy site prices for the template's target sites, in template order.
    cnsc_mode = listing_copy.get("listing_mode") == "cnsc_global"
    target_sites: list[str] = []
    if cnsc_mode:
        target_sites = [
            str(entry.get("site_code") or "").strip().upper()
            for entry in listing_copy.get("target_site_stores") or []
            if str(entry.get("site_code") or "").strip()
        ]
        if not target_sites:
            target_sites = [str(site or "").strip().upper() for site in (listing_copy.get("site_price_order") or [])]
    default_sku = _active_default_sku(template, listing_copy)
    rows = []
    for sku in skus:
        global_mode = listing_copy.get("listing_mode") == "cnsc_global"
        is_default_sku = default_sku is not None and sku.get("sku_source") == "default_sku"
        if is_default_sku:
            # 默认 SKU(白T恤) 的售价/折扣价独立设置：不取模板原价、不套六折规则。
            # 填了分尺码覆盖（如童码 90cm）时只有那个尺码用覆盖价，其余尺码继续
            # 用默认 SKU 自己的售价/折扣价——不影响其他颜色，也不影响同色其他码。
            override = _default_sku_size_price(default_sku, sku.get("size"))
            if override:
                normal_price = override["normal_price"]
                sale_price = override["sale_price"]
            else:
                normal_price = _float_or_empty(default_sku.get("normal_price"))
                sale_price = _float_or_empty(default_sku.get("sale_price"))
        else:
            normal_price, sale_price = ("", "") if global_mode else _site_row_prices(template, sku.get("size"))
        color = str(sku.get("color_code") or sku.get("color_cn") or "").strip()
        size = str(sku.get("size") or "").strip()
        base_tier = next((tier for tier in base_tiers if _tier_matches(tier, color, size)), None)
        site_price_matrix: dict[str, Any] = {}
        if global_mode:
            # SKU 行已按目标店铺 %OFF 算好售价（sale = original × (1-off)）；
            # 价格行直接复用 SKU 矩阵保持一致。
            site_price_matrix = deepcopy(sku.get("site_price_matrix") or {})
            if target_sites:
                site_price_matrix = {site: site_price_matrix[site] for site in target_sites if site in site_price_matrix}
        cost_price = (base_tier or {}).get("cost_price")
        rows.append({
            "id": str(uuid.uuid4()),
            "listing_copy_id": listing_copy.get("id") or "",
            "base_product_id": listing_copy.get("base_product_id") or "",
            "template_id": template.get("id") or "",
            "sku_id": sku.get("id") or "",
            "sku_code": sku.get("sku_code") or "",
            "platform": template.get("platform") or "shopee",
            "store": template.get("store_name") or "",
            "country": template.get("country") or "MY",
            "currency": template.get("currency") or "MYR",
            "normal_price": normal_price,
            "sale_price": sale_price,
            "cost_price": "" if cost_price in ("", None) else float(cost_price),
            "price_source": "base_product" if base_tier is not None else "",
            "discount_label": "" if global_mode else (template.get("discount_label") or ""),
            "discount_id": "" if global_mode else (template.get("discount_id") or ""),
            "global_price_cny": sku.get("global_price_cny") if listing_copy.get("listing_mode") == "cnsc_global" else "",
            "site_price_matrix": site_price_matrix if global_mode else {},
            "status": "active",
            "updated_at": now,
        })
    return rows


def _positive_price_or_empty(value: Any) -> float | str:
    price = _float_or_empty(value)
    if price == "" or float(price) <= 0:
        return ""
    return price


def _normalize_size_price_overrides(value: Any) -> dict[str, dict[str, float | str]]:
    """按尺码的价格覆盖：尺码 → {"normal_price", "sale_price"}。

    形态兼容：存量数据是「尺码 → 原价」标量（售价按模板折扣比自动换算），照旧读得懂；
    新数据一律存成一对价格，留空的项表示继续自动换算。两项都空的尺码直接丢掉。
    """
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, float | str]] = {}
    for raw_size, raw_price in value.items():
        size = str(raw_size or "").strip()
        if not size:
            continue
        if isinstance(raw_price, dict):
            normal = _positive_price_or_empty(raw_price.get("normal_price"))
            sale = _positive_price_or_empty(raw_price.get("sale_price"))
        else:
            normal, sale = _positive_price_or_empty(raw_price), ""
        if normal == "" and sale == "":
            continue
        normalized[size] = {"normal_price": normal, "sale_price": sale}
    return normalized


def _sync_listing_prices_from_template(prices: list[dict[str, Any]], template: dict[str, Any], *, now: str) -> None:
    discount_id = template.get("discount_id") or template.get("mabang_discount_id") or ""
    global_mode = template.get("listing_mode") == "cnsc_global"
    for price in prices:
        price.update({
            "platform": template.get("platform") or "shopee",
            "store": template.get("store_name") or "",
            "country": template.get("country") or "MY",
            "currency": template.get("currency") or "MYR",
            "normal_price": "" if global_mode else _float_or_empty(template.get("normal_price")),
            "sale_price": "" if global_mode else _float_or_empty(template.get("sale_price")),
            "discount_label": "" if global_mode else (template.get("discount_label") or ""),
            "discount_id": "" if global_mode else discount_id,
            "updated_at": now,
        })


def _check_automation_readiness(
    listing_copy: dict[str, Any],
    *,
    skus: list[dict[str, Any]] | None = None,
    prices: list[dict[str, Any]] | None = None,
    expected_snapshot_hash: str = "",
) -> dict[str, Any]:
    skus = skus if skus is not None else list_listing_skus(listing_copy.get("id") or "")
    prices = prices if prices is not None else list_listing_prices(listing_copy.get("id") or "")
    is_cnsc = str(listing_copy.get("listing_mode") or "").strip().lower() == "cnsc_global"
    required = {
        "listing_title": listing_copy.get("listing_title"),
        "description": listing_copy.get("description"),
        "store_name": listing_copy.get("store_name"),
        "mabang_store_id": listing_copy.get("mabang_store_id"),
        "marketplace_category_path": listing_copy.get("marketplace_category_path"),
        "mabang_category_id": listing_copy.get("mabang_category_id"),
        "main_image": listing_copy.get("main_image"),
        "asset_root": listing_copy.get("asset_root"),
        "stock": listing_copy.get("stock"),
        "weight_kg": listing_copy.get("weight_kg"),
        "package_length_cm": listing_copy.get("package_length_cm"),
        "package_width_cm": listing_copy.get("package_width_cm"),
        "package_height_cm": listing_copy.get("package_height_cm"),
    }
    if not is_cnsc:
        required["size_chart_image"] = (
            listing_copy.get("size_chart_image")
            or listing_copy.get("size_chart_image_path")
            # 上传的尺码图 asset 与马帮尺码图模板同样是有效来源：执行器
            # (_build_browser_inputs) 会把 asset id / 模板 resolve 成实际文件，
            # readiness 只认 image/path 两字段会误报「缺少尺码图」。
            or listing_copy.get("size_chart_asset_id")
            or listing_copy.get("mabang_size_chart_template_id")
        )
        required["mabang_discount_id"] = listing_copy.get("mabang_discount_id")
    missing = [
        key
        for key, value in required.items()
        if _is_missing_required_value(key, value)
    ]
    if not is_cnsc:
        # 默认 SKU(白T恤)：启用后图片/SKU/售价/折扣价必填，且颜色名不能与商品颜色
        # 重名（重复的颜色轴值会让马帮少生成一组行）。跨境不检查。
        default_sku_config = _normalize_default_sku(listing_copy.get("default_sku"))
        if default_sku_config.get("enabled"):
            other_colors = [
                str(sku.get("color_code") or sku.get("color_cn") or "").strip()
                for sku in skus
                if str(sku.get("sku_source") or "") != "default_sku"
            ] or [str(item or "").strip() for item in listing_copy.get("recognized_colors") or []]
            missing.extend(
                key
                for key in _default_sku_configuration_problems(default_sku_config, colors=other_colors)
                if key not in missing
            )
    # 描述生成方案只在描述尚未就绪时才要求：自动化只消费描述文案本身，
    # 已经有描述（且不在生成中）的副本不应再提醒"请选择描述生成方案"。
    description_text = str(listing_copy.get("description") or "").strip()
    if not description_text or listing_copy.get("description_status") == "pending":
        if _is_missing_required_value(
            "description_profile_version_id", listing_copy.get("description_profile_version_id")
        ):
            missing.append("description_profile_version_id")
    if listing_copy.get("description_status") == "pending":
        missing.append("description_pending")
    if not skus:
        missing.append("listing_skus")
    if not prices:
        missing.append("listing_prices")
    if is_cnsc:
        # 跨境(CNSC)的成本价以基础商品价格表为唯一来源：每个价格行必须带成本价
        # （缺失阻止自动化但仍允许保存副本）。本土(site)店铺的成本价走模板/导出
        # 自己的兜底，不要求基础商品维护里补——故此处只在 CNSC 分支检查。
        if any(price.get("cost_price") in ("", None) for price in prices):
            missing.append("base_product_cost_price")
        # CNSC 没有总库存字段：只要任一 SKU 的泰国/广州/菲律宾仓有库存，
        # 库存即视为就绪，不再提醒"缺少库存"。2026-09-27 起三仓全 0 也是合法状态
        # （马帮把三仓列当必填，0 也要照填，留空才报错）：每个仓都能解析出非负整数
        # （SKU 自带值 → 副本级默认）即放行，执行器会把 0 逐仓写上页面。
        if "stock" in missing and (
            _cnsc_warehouse_stock_present(skus)
            or _cnsc_warehouse_stocks_resolvable(skus, listing_copy.get("warehouse_stock_defaults"))
        ):
            missing.remove("stock")
        # Real (color, size) combos so color-bound tiers (combo products) and
        # unconstrained uniform tiers validate against the actual SKUs.
        sku_variants = [
            (str(sku.get("color_code") or sku.get("color_cn") or "").strip(), str(sku.get("size") or "").strip())
            for sku in skus
        ]
        # Full CNSC gate: same source of truth as the strict template validator, so
        # a copy missing a discount activity / price tier / %OFF consistency is
        # blocked here (fail-closed) rather than only at the Playwright executor.
        for key in _cnsc_configuration_problems(listing_copy, selected_variants=sku_variants):
            if key not in missing:
                missing.append(key)
    current_snapshot_hash = str(listing_copy.get("profile_snapshot_hash") or "").strip()
    if expected_snapshot_hash and current_snapshot_hash != expected_snapshot_hash:
        missing.append("profile_snapshot_changed")
    snapshot = listing_copy.get("category_profile_snapshot")
    category_warnings: list[str] = []
    if isinstance(snapshot, dict) and snapshot.get("fields"):
        values = listing_copy.get("attribute_values") or listing_copy.get("category_attributes") or {}
        category_validation = validate_attribute_values(snapshot, values)
        missing.extend(
            f"category_attributes.{error.field_id}"
            for error in category_validation.errors
            if error.field_id
        )
        # 枚举外的自选值（原产地=Philippines）不阻塞：采集端会把页面上的自填值并入枚举、
        # 执行器走马帮「自填选项」写入。只提醒，让操作员知道该值不在类目枚举里。
        category_warnings.extend(
            f"category_custom_option.{warning.field_id}"
            for warning in category_validation.warnings
            if warning.field_id
        )
    else:
        # 类目方案缺失：副本有要刊登的类目、却**连档案绑定都没有**（category_profile_id /
        # category_profile_version_id 都为空、快照也是空的）时，属性阶段没有任何规则可依
        # （就绪检查本身也失去了校验对象）。
        # 现场 2026-09-28：两个跨境副本（Men/Women Clothes > Costumes，100059/100115）就是
        # 这种状态——这两个类目从未采集；就绪页却显示"就绪"，执行器只能靠现场读必填 + AI
        # 现猜，跨境这条兜底最弱 → 保存被 pre_save_validation_failed 拦。
        # 只提醒、不阻塞：类目可以确实没有属性（采过但快照字段为空 = 有绑定），而且历史上
        # 无档案副本一直可以生成任务（AI 现场补齐），所以这里不能把它变成硬缺项。
        has_binding = bool(
            str(listing_copy.get("category_profile_id") or "").strip()
            or str(listing_copy.get("category_profile_version_id") or "").strip()
        )
        if (
            not has_binding
            and (
                str(listing_copy.get("marketplace_category_path") or "").strip()
                or str(listing_copy.get("mabang_category_id") or "").strip()
            )
        ):
            category_warnings.append("category_profile_missing")
    return {
        "ready": not missing,
        "status": "ready" if not missing else "needs_manual",
        "missing_fields": missing,
        "warnings": category_warnings,
    }


def _cnsc_warehouse_stock_present(skus: list[dict[str, Any]]) -> bool:
    """Whether any CNSC SKU carries stock in any warehouse (泰国/广州/菲律宾仓)."""

    for sku in skus:
        for value in (sku.get("warehouse_stocks") or {}).values():
            try:
                if int(str(value).strip() or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _cnsc_stock_value_ok(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric >= 0 and numeric.is_integer()


def _cnsc_warehouse_stocks_resolvable(skus: list[dict[str, Any]], defaults: Any) -> bool:
    """Whether every CNSC SKU can resolve TH/CN_GZ/PH stocks — 0 included.

    马帮把三仓库存列当必填，留空才报错，0 是合法值。解析顺序与 Playwright
    执行器一致：SKU 自带值 → 副本级 warehouse_stock_defaults；每仓都能落到
    非负整数即视为就绪（全 0 也放行，交给执行器照填），任一仓无法解析仍拦。
    """

    if not skus:
        return False
    default_stocks = defaults if isinstance(defaults, dict) else {}
    for sku in skus:
        stocks = sku.get("warehouse_stocks") if isinstance(sku.get("warehouse_stocks"), dict) else {}
        for code in ("TH", "CN_GZ", "PH"):
            if _cnsc_stock_value_ok(stocks.get(code)) or _cnsc_stock_value_ok(default_stocks.get(code)):
                continue
            return False
    return True


def _runtime_configuration_missing(data: dict[str, Any], listing_copy: dict[str, Any], *, now: str) -> list[str]:
    missing: list[str] = []
    is_cnsc = str(listing_copy.get("listing_mode") or "").strip().lower() == "cnsc_global"
    profile_id = str(listing_copy.get("category_profile_id") or "").strip()
    version_id = str(listing_copy.get("category_profile_version_id") or "").strip()
    profiles = data.get("category_profiles") or []
    if profile_id and profiles:
        profile = next((item for item in profiles if item.get("id") == profile_id), None)
        if not profile or str(profile.get("active_version_id") or profile.get("published_version_id") or "") != version_id:
            missing.append("category_profile_version_expired")

    # CNSC 全球草稿没有站点折扣活动步骤，不能因为旧站点的促销配置检查而阻塞。
    if is_cnsc:
        return missing

    activity_id = str(listing_copy.get("promotion_activity_id") or listing_copy.get("mabang_discount_id") or "").strip()
    activities = data.get("store_promotion_activities") or []
    activity = next(
        (
            item for item in activities
            if str(item.get("activity_id") or item.get("id") or "") == activity_id
            and str(item.get("store_id") or "") == str(listing_copy.get("mabang_store_id") or "")
        ),
        None,
    )
    state = promotion_state(activity_id, activity, now=now)
    if state == "expired":
        missing.append("promotion_expired")
    elif state == "unresolved":
        missing.append("promotion_unresolved")
    elif state == "unconfigured":
        missing.append("promotion_unconfigured")
    return missing


def _is_missing_required_value(key: str, value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if key.startswith("mabang_") and key.endswith("_id"):
        normalized = re.sub(r"[\s_-]+", "", text).lower()
        placeholders = {
            "id",
            "storeid",
            "categoryid",
            "discountid",
            "mabangstoreid",
            "mabangcategoryid",
            "mabangdiscountid",
        }
        return normalized in placeholders
    return False


def _build_browser_inputs(
    listing_copy: dict[str, Any],
    skus: list[dict[str, Any]],
    prices: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    main_image = listing_copy.get("main_image") or ""
    main_asset = next((asset for asset in assets if asset.get("file_name") == main_image), None)
    product_image_names = _normalize_product_images(listing_copy.get("product_images") or [])
    if not product_image_names:
        product_image_names = _default_product_images(assets, main_asset)
    product_image_paths = _asset_paths_for_names(assets, product_image_names)
    size_chart_image_path = str(listing_copy.get("size_chart_image_path") or "").strip()
    size_chart_image = str(listing_copy.get("size_chart_image") or "").strip()
    size_chart_asset_id = str(listing_copy.get("size_chart_asset_id") or "").strip()
    if not size_chart_image_path and size_chart_asset_id:
        uploaded_size_chart = resolve_size_chart(size_chart_asset_id, storage_dir=settings.STORAGE_DIR)
        if uploaded_size_chart:
            size_chart_image_path = str(uploaded_size_chart[0])
    if not size_chart_image and size_chart_image_path:
        size_chart_image = Path(str(size_chart_image_path)).name
    if size_chart_image and not size_chart_image_path:
        size_chart_asset = _asset_by_name(assets, size_chart_image)
        if size_chart_asset and size_chart_asset.get("file_path"):
            size_chart_image_path = str(size_chart_asset.get("file_path") or "")
    # 「产品图最后一张放尺码表」（本土开关，默认开）：
    #   产品图满 9 张 → 第 9 张被尺码表替换；不足 9 张 → 尺码表追加到末尾（不替换）。
    # 产品图列表本身不动：描述末尾那批图按真实产品图插（尺码表由描述开头那一张负责），
    # 尺码表只加在真正传给马帮「产品图」区的这一份里。
    product_image_form_paths = list(product_image_paths)
    size_chart_last_product_image = False
    size_chart_slot_skipped = ""
    if (
        str(listing_copy.get("listing_mode") or "site") != "cnsc_global"
        and _size_chart_last_product_image_enabled(listing_copy)
    ):
        size_chart_file = str(size_chart_image_path or "").strip()
        if not product_image_form_paths:
            size_chart_slot_skipped = "product_images_missing"
        elif not size_chart_file:
            size_chart_slot_skipped = "size_chart_missing"
        elif not Path(size_chart_file).is_file():
            # 尺码图路径可能是已经掉线的 NAS 路径。坏路径绝不能塞进「产品图」区：
            # 上传阶段会 upload_file_missing，整单被 blocked，不如退回原样并在结果里说明。
            size_chart_slot_skipped = "size_chart_file_missing"
        else:
            # 操作员自己已经把同一张尺码表放进产品图列表时先摘掉，避免出现两张尺码表。
            stripped_paths = [path for path in product_image_form_paths if str(path) != size_chart_file]
            if not stripped_paths:
                size_chart_slot_skipped = "product_images_missing"
            else:
                if len(stripped_paths) >= PRODUCT_IMAGE_LIMIT:
                    stripped_paths = stripped_paths[: PRODUCT_IMAGE_LIMIT - 1]
                product_image_form_paths = [*stripped_paths, size_chart_file]
                size_chart_last_product_image = True
    sizes = _unique_sorted_sizes(skus)
    # 颜色轴顺序：本土按操作员在商品维护里排的顺序（recognized_colors）生成；跨境
    # 沿用字母序——跨境网格按行号填写，换序属于跨境行为变更，要单独验证。
    if str(listing_copy.get("listing_mode") or "site") == "cnsc_global":
        colors = _unique_sorted_colors(skus, listing_copy)
    else:
        # 尺码轴顺序：本土按模板的尺码 chip 顺序（操作员在模板里拖动的顺序即
        # 上架顺序，重排后「再次尝试」即生效）；跨境保持 _size_sort_value 行序，
        # 换序属于跨境行为变更，需单独验证。
        # data 传进来就别再 load_store()：调用方（建任务/重排队）手里已经有一份完整的
        # 库，这里回退读盘会让整库白白多解析一遍（实测每次多 511ms）。
        sizes = _order_sizes_by_template(sizes, _template_sizes_for_copy(listing_copy, data))
        colors = _ordered_variation_colors(skus, listing_copy)
        skus, prices = _order_sku_rows_by_colors(skus, prices, colors)
    variant_image_map = _normalize_variant_image_map(listing_copy.get("variant_image_map") or {})
    variant_image_paths = _asset_path_map_for_names(assets, variant_image_map)
    # 默认 SKU(白T恤) 必须是颜色轴的最后一个、且其 SKU 行排在末尾：马帮按颜色轴
    # 生成网格、执行器按行号逐行填写，两处顺序必须一致（SKU 行读取时按 color_code
    # 排序，所以这里显式重排）。跨境恒不参与，输出与改动前完全一致。
    default_sku = _active_default_sku({}, listing_copy)
    default_sku_color = str((default_sku or {}).get("color_name") or "").strip()
    if default_sku and default_sku_color:
        def _is_default_sku_row(sku: dict[str, Any]) -> bool:
            if str(sku.get("sku_source") or "") == "default_sku":
                return True
            return str(sku.get("color_code") or sku.get("color_cn") or "").strip() == default_sku_color

        skus = [
            *[sku for sku in skus if not _is_default_sku_row(sku)],
            *[sku for sku in skus if _is_default_sku_row(sku)],
        ]
        # prices 必须跟着 skus 一起走：两个列表各自来自 list_listing_skus（按 color_code
        # 排序）与 list_listing_prices（生成时的顺序），下标本来就对不上。只重排 skus 会
        # 让「网格行 ↔ 价格行」整体错位一个颜色块，默认 SKU 的 90cm 覆盖价会落到别的行上。
        # 这里按 sku_code 把价格排成同一顺序；对不齐就保持原样，让执行器按 sku_code 查找。
        price_by_sku = {str(price.get("sku_code") or ""): price for price in prices}
        aligned_prices = [price_by_sku[str(sku.get("sku_code") or "")] for sku in skus if str(sku.get("sku_code") or "") in price_by_sku]
        if len(aligned_prices) == len(skus):
            prices = aligned_prices
        colors = [color for color in colors if color != default_sku_color]
        colors.append(default_sku_color)
        default_image_path = _default_sku_image_path(default_sku)
        default_image_name = _default_sku_image_name(default_sku, default_image_path)
        if default_image_name:
            variant_image_map[default_sku_color] = default_image_name
        if default_image_path:
            variant_image_paths[default_sku_color] = default_image_path
    # 马帮「变种信息 → 自定义缩略图」按颜色逐个必填：颜色轴上任何一个颜色没有图，
    # 页面右上角就报「1 必填项未填」，保存被 pre_save_validation_failed 拦下
    # （实测单色商品：颜色轴是占位色 default + 默认 SKU 的 PureWhite，只有后者有图）。
    # 没有专用颜色图的颜色沿用主图，绝不空着。
    fallback_image_path = str((main_asset or {}).get("file_path") or "")
    if fallback_image_path:
        for color in colors:
            key = str(color or "").strip()
            if key and not str(variant_image_paths.get(key) or "").strip():
                variant_image_paths[key] = fallback_image_path
                if main_image:
                    variant_image_map.setdefault(key, str(main_image))
    category_attributes = deepcopy(listing_copy.get("attribute_values") or listing_copy.get("category_attributes") or {})
    variation_defaults = _normalize_variation(listing_copy.get("variation") or {})
    return {
        "platform": listing_copy.get("platform") or "",
        "listing_mode": listing_copy.get("listing_mode") or "site",
        "store_name": listing_copy.get("store_name") or "",
        "mabang_store_id": listing_copy.get("mabang_store_id") or "",
        "country": listing_copy.get("country") or "",
        "currency": listing_copy.get("currency") or "",
        "group_id": listing_copy.get("group_id") or "",
        # Parent SKU identity: the operator-edited 主 SKU (base product) is what
        # should reach Mabang's Parent SKU field and draft-inbox lookups. When a
        # base was auto-assigned a main_sku (auto-increment prefix) it differs from
        # the folder-derived group_id; group_id stays only as the asset key.
        "main_sku": listing_copy.get("main_sku") or listing_copy.get("group_id") or "",
        "title": listing_copy.get("listing_title") or "",
        "description": listing_copy.get("description") or "",
        "marketplace_category_path": listing_copy.get("marketplace_category_path") or "",
        "mabang_category_id": listing_copy.get("mabang_category_id") or "",
        "discount_label": listing_copy.get("discount_label") or "",
        "mabang_discount_id": listing_copy.get("mabang_discount_id") or "",
        "cnsc_store": deepcopy(listing_copy.get("cnsc_store") or {}),
        "target_site_stores": deepcopy(listing_copy.get("target_site_stores") or []),
        "default_site_discount_percent": listing_copy.get("default_site_discount_percent") or 40,
        "price_tiers": deepcopy(listing_copy.get("price_tiers") or []),
        "warehouse_stock_defaults": deepcopy(listing_copy.get("warehouse_stock_defaults") or {}),
        "asset_root": listing_copy.get("asset_root") or "",
        "main_image": main_image,
        "main_image_path": main_asset.get("file_path") if main_asset else "",
        "product_images": product_image_names,
        "product_image_paths": product_image_paths,
        # 实际按顺序传给马帮「产品图」区的列表：末位可能是被替换/追加进去的尺码表。
        "product_image_form_paths": product_image_form_paths,
        "size_chart_last_product_image": size_chart_last_product_image,
        # 开关开着但这一单没能放进去时的原因（"" = 正常）。
        "size_chart_slot_skipped": size_chart_slot_skipped,
        "size_chart_image": size_chart_image,
        "size_chart_image_path": size_chart_image_path,
        "size_chart": {
            "source": _normalize_size_chart_source((listing_copy.get("size_chart_snapshot") or {}).get("source")),
            "template_id": str(listing_copy.get("mabang_size_chart_template_id") or ""),
            "template_name": str(listing_copy.get("mabang_size_chart_template_name") or ""),
            "image_path": size_chart_image_path,
        },
        "video": {
            **deepcopy(listing_copy.get("video") or {"validation_status": "missing", "validation_errors": ["video_missing"]}),
            # 模板默认视频 ID（本土）：有值时执行器直接按 ID 选视频，不再上传文件。
            "default_video_id": str(listing_copy.get("default_video_id") or "").strip(),
        },
        "main_image_watermark": deepcopy(listing_copy.get("main_image_watermark") or {}),
        "product_image_watermark": deepcopy(listing_copy.get("product_image_watermark") or {}),
        "variant_image_map": variant_image_map,
        "variant_image_paths": variant_image_paths,
        "default_sku": deepcopy(default_sku) if default_sku else _normalize_default_sku(None),
        "category_attributes": category_attributes,
        "category_profile_snapshot": deepcopy(listing_copy.get("category_profile_snapshot") or {}),
        "profile_snapshot_hash": listing_copy.get("profile_snapshot_hash") or "",
        "transaction_info": {
            "stock": listing_copy.get("stock"),
            "weight_kg": listing_copy.get("weight_kg"),
            "package_length_cm": listing_copy.get("package_length_cm"),
            "package_width_cm": listing_copy.get("package_width_cm"),
            "package_height_cm": listing_copy.get("package_height_cm"),
        },
        "variation": {
            "type": variation_defaults.get("type") or "variant",
            "axes": variation_defaults.get("axes") or ["color", "size"],
            "colors": colors,
            "sizes": sizes,
        },
        "sku_count": len(skus),
        "price_count": len(prices),
        "skus": skus,
        "prices": prices,
    }


def _common_asset_root(assets: list[dict[str, Any]]) -> str:
    roots = [asset.get("group_root") or asset.get("asset_root") for asset in assets if asset.get("group_root") or asset.get("asset_root")]
    if roots:
        return str(roots[0])
    parents = []
    for asset in assets:
        raw = asset.get("file_path") or ""
        if raw:
            parents.append(str(Path(raw).parent))
    return parents[0] if parents else ""


def _derive_group_from_path(value: str) -> str:
    parts = [part for part in re.split(r"[\\/]+", str(value or "")) if part]
    for part in reversed(parts):
        matched = _derive_main_sku_from_folder_name(part)
        if matched:
            return matched
    return ""


def _derive_main_sku_from_folder_name(value: str) -> str:
    """Extract the main SKU from a NAS folder name: keep the leading run of
    ASCII letters+digits and cut at the first Chinese/space/symbol character.

    Product folders are commonly named like ``AL0301男语言月`` (SKU glued to a
    Chinese product name) or ``AL0002 我的世界`` — the old word-boundary regex
    missed the glued form because CJK counts as a word char, so no ``\\b``
    exists between the digits and the Chinese. Returns "" when the leading run
    is not SKU-like (must start with a letter, be ≥4 chars and contain a
    digit), mirroring the group-token rule the asset scanner uses
    (workers/asset_scanner/scanner.py). Year-first names (``2024夏季新款``) do
    not match and fall back to auto-assign."""
    name = Path(str(value or "")).name.strip().upper()
    token: list[str] = []
    for ch in name:
        if token:
            if ch.isascii() and (ch.isalpha() or ch.isdigit()):
                token.append(ch)
                continue
            break  # Chinese / space / symbols (#, -, &, …) end the run
        if ch.isascii() and ch.isalpha():
            token.append(ch)  # token must start with a letter
            continue
        break  # digit-/symbol-/Chinese-led names never start a SKU token
    raw = "".join(token)
    if len(raw) >= 4 and any(ch.isdigit() for ch in raw):
        return raw
    return ""


def _auto_assign_sku_enabled(payload: dict[str, Any]) -> bool:
    """Auto-assign is on when the runtime setting is enabled; the caller may pass
    ``main_sku`` explicitly to override the generated one."""
    if str(payload.get("main_sku") or "").strip():
        return False
    with _LOCK:
        config = merge_configuration(load_store().get("runtime_configuration"))
    return bool((config.get("product_sku") or {}).get("auto_assign_on_import"))


def _allocate_auto_main_sku() -> str:
    """Atomically take the next SKU (``prefix + zero-padded number``) from the
    runtime configuration, skipping numbers whose SKU already exists, and persist
    the bumped counter. Returns "" when the prefix is unset."""
    sku = ""
    with locked_store() as (data, save):
        config = merge_configuration(data.get("runtime_configuration"))
        sku_cfg = config.setdefault("product_sku", {})
        prefix = str(sku_cfg.get("prefix") or "").strip().upper()
        if not prefix:
            return ""
        width = int(sku_cfg.get("number_width") or 4)
        number = int(sku_cfg.get("next_number") or 1)
        existing = {
            str(item.get("main_sku") or "").strip().upper()
            for item in data.get("base_products", [])
        }
        existing |= {
            str(item.get("main_sku") or "").strip().upper()
            for item in data.get("store_templates", [])
        }
        existing.discard("")
        while True:
            candidate = f"{prefix}{str(number).zfill(width)}"
            number += 1
            if candidate not in existing:
                break
        sku_cfg["next_number"] = number
        data["runtime_configuration"] = config
        save(data)
        sku = candidate
    return sku


def _derive_product_name_from_folder_name(value: str, main_sku: str) -> str:
    name = Path(str(value or "")).name.strip()
    # Strip a leading main-SKU token regardless of what follows it: a space
    # ("AL0002 我的世界") or Chinese glued straight on ("AL0301男语言月").
    # The old `^\S+\s*` regex only handled the space form.
    if main_sku and name.upper().startswith(main_sku.upper()):
        rest = name[len(main_sku):].lstrip(" \t-_#")
        return rest.strip() or main_sku
    return name


def _derive_product_name(group_id: str, assets: list[dict[str, Any]]) -> str:
    names = [asset.get("product_name") or asset.get("base_name") for asset in assets if asset.get("product_name") or asset.get("base_name")]
    if names:
        return str(names[0])
    roots = [asset.get("group_root") or asset.get("file_path") for asset in assets if asset.get("group_root") or asset.get("file_path")]
    if roots:
        parts = [part for part in re.split(r"[\\/]+", str(roots[0])) if part]
        if len(parts) >= 2:
            return parts[-2] if parts[-1].upper() == group_id.upper() else parts[-1]
    return group_id


def _sanitize_product_name_candidate(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if _looks_like_test_text(text):
        return ""
    return text


def _sanitize_listing_title_candidate(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if _looks_like_test_text(text):
        return ""
    return text


def _looks_like_test_text(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return any(token in normalized for token in ["test product", "sample product", "placeholder", "todo"])


def _build_product_name_cleanup_preview(product: dict[str, Any]) -> dict[str, Any] | None:
    suggested = _suggest_clean_product_fields(product)
    current = {key: product.get(key) or "" for key in ["base_name", "product_name_cn", "listing_title"]}
    changed = any((suggested.get(key) or "") != (current.get(key) or "") for key in current)
    if not changed:
        return None
    return {
        "product_id": product.get("id") or "",
        "group_id": product.get("group_id") or "",
        "current": current,
        "suggested": suggested,
        "dirty_types": _detect_product_dirty_types(product),
    }


def _suggest_clean_product_fields(product: dict[str, Any]) -> dict[str, Any]:
    group_id = product.get("group_id") or ""
    template = get_product_template(product.get("category") or "")
    base = _sanitize_product_name_candidate(product.get("product_name_cn") or product.get("base_name") or "") or group_id
    title = _sanitize_listing_title_candidate(product.get("listing_title") or product.get("generated_title") or "")
    if not title:
        title = _build_default_listing_title(template, base, group_id)
    return {"base_name": base, "product_name_cn": base, "listing_title": title}


def _detect_product_dirty_types(product: dict[str, Any]) -> list[str]:
    dirty = []
    if not _sanitize_product_name_candidate(product.get("product_name_cn") or product.get("base_name") or ""):
        dirty.append("name_missing")
    if not _sanitize_listing_title_candidate(product.get("listing_title") or product.get("generated_title") or ""):
        dirty.append("title_missing")
    return dirty


def _apply_template_defaults(product: dict[str, Any], template: dict[str, Any]) -> None:
    if not (product.get("target_audience") or "").strip():
        product["target_audience"] = template.get("target_audience") or ""
    if not (product.get("marketplace_category_path") or "").strip():
        product["marketplace_category_path"] = template.get("marketplace_category_path") or ""
        product["marketplace_category_source"] = "template_default" if product["marketplace_category_path"] else "missing"
    for field in ["attribute_brand", "attribute_gender", "attribute_material", "attribute_sleeve_length", "attribute_collar"]:
        if not (product.get(field) or "").strip():
            product[field] = template.get(field) or ""
    if not (product.get("template_id") or "").strip():
        product["template_id"] = template.get("template_id") or ""


def _generate_skus_from_assets(product: dict[str, Any], assets: list[dict[str, Any]], sizes: list[str]) -> list[dict[str, Any]]:
    variant_assets = [asset for asset in assets if (asset.get("asset_type") or "").lower() == "variant"] or assets[:1]
    rows = []
    for asset in variant_assets:
        color_code = _color_code_from_asset(asset)
        color_cn = asset.get("sku_option") or asset.get("color_cn") or asset.get("file_stem") or color_code
        for size in sizes:
            rows.append({
                "sku_code": "-".join(part for part in [product.get("group_id"), color_code.upper(), str(size).upper()] if part),
                "group_id": product.get("group_id") or "",
                "color_code": color_code,
                "color_cn": color_cn,
                "size": size,
                "variant_name": f"{color_cn}/{size}",
                "stock": 999,
                "weight_kg": product.get("weight_kg"),
                "length_cm": product.get("length_cm"),
                "width_cm": product.get("width_cm"),
                "height_cm": product.get("height_cm"),
                "variant_image": asset.get("file_name") or Path(asset.get("file_path") or "").name,
                "status": "active",
            })
    return rows


def _generate_prices_from_skus(product: dict[str, Any], skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    template = get_product_template(product.get("category") or "")
    price = template.get("price") or {}
    rows = []
    for sku in skus:
        is_large = _is_large_size(str(sku.get("size") or ""))
        rows.append({
            "sku_code": sku.get("sku_code") or "",
            "platform": product.get("platform") or "shopee",
            "store": product.get("store") or "",
            "country": "MY",
            "currency": "MYR",
            "cost_price": price.get("large_cost") if is_large else price.get("cost"),
            "shipping_cost": price.get("shipping_cost") or 0,
            "fee_rate": 0.08,
            "margin_rate": 0.35,
            "normal_price": price.get("large_normal") if is_large else price.get("normal"),
            "sale_price": price.get("large_sale") if is_large else price.get("sale"),
            "status": "active",
        })
    return rows


def _export_product_template(category: str, template: dict[str, Any]) -> dict[str, Any]:
    item = dict(template)
    price = dict(item.get("price") or {})
    item["category"] = category
    item["normal_price"] = price.get("normal", "")
    item["sale_price"] = price.get("sale", "")
    item["large_delta"] = (price.get("large_normal") or 0) - (price.get("normal") or 0)
    item["cost_price"] = price.get("cost", "")
    item["large_cost_price"] = price.get("large_cost", "")
    item["shipping_cost"] = price.get("shipping_cost", "")
    return item


def _export_category_profile(profile: dict[str, Any]) -> dict[str, Any]:
    item = dict(profile)
    item["required_attributes"] = _normalize_category_attributes(profile.get("required_attributes") or {})
    item["category_attributes"] = dict(item["required_attributes"])
    item["variation"] = _normalize_variation(profile.get("variation") or {})
    item["sizes"] = [str(size or "").strip() for size in profile.get("sizes") or [] if str(size or "").strip()]
    item["mabang_ready"] = bool(str(item.get("mabang_category_id") or "").strip())
    return item


def _resolve_marketplace_category_path(payload: dict[str, Any], template: dict[str, Any]) -> str:
    if "marketplace_category_path" in payload:
        return str(payload.get("marketplace_category_path") or "").strip()
    return str(template.get("marketplace_category_path") or "").strip()


def _resolve_marketplace_category_source(payload: dict[str, Any], template: dict[str, Any], category_path: str) -> str:
    source = str(payload.get("marketplace_category_source") or "").strip()
    if source in {"template_default", "manual_override", "missing"}:
        return source
    if "marketplace_category_path" in payload:
        return "manual_override" if category_path else "missing"
    return "template_default" if category_path and template.get("marketplace_category_path") else "missing"


def _build_default_listing_title(template: dict[str, Any], product_name: str, group_id: str) -> str:
    return " ".join(part for part in [product_name, template.get("title_suffix") or "", group_id] if str(part or "").strip()).strip()


def _color_code_from_asset(asset: dict[str, Any]) -> str:
    candidate = asset.get("sku_option") or asset.get("color_code") or asset.get("file_stem") or Path(asset.get("file_path") or "").stem or "default"
    code = re.sub(r"[^A-Za-z0-9]+", "-", str(candidate).strip()).strip("-").lower()
    return code or "default"


def _is_large_size(size: str) -> bool:
    return str(size or "").upper() in {"2XL", "3XL", "4XL", "5XL"}


def _size_sort_value(size: str) -> int:
    order = {"XXS": 1, "XS": 2, "S": 3, "M": 4, "L": 5, "XL": 6, "2XL": 7, "3XL": 8, "4XL": 9, "5XL": 10}
    return order.get(str(size or "").upper(), 999)


def _value_present(value: Any) -> bool:
    return str(value or "").strip() not in {"", "0", "0.0", "none", "None"}


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _float_or_empty(value: Any) -> float | str:
    if value is None or value == "":
        return ""
    try:
        return float(value)
    except (TypeError, ValueError):
        return ""


def _int_or_empty(value: Any) -> int | str:
    if value is None or value == "":
        return ""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    return number if number > 0 else ""
