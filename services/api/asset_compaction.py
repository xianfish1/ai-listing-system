"""商品素材生命周期：上架完成后压缩颜色图、超期后清理本地素材。

设计：``docs/superpowers/specs/2026-10-19-listing-asset-lifecycle-design.md``。

现场口径（2026-10-19）：本地素材 5.82GB / 2474 文件，其中 1769 个 PNG 占了 5.54GB，
平均 3.28MB/张；转成长边 1000 / 质量 88 的 JPEG 后平均 0.18MB（省 94.6%）。

本模块只负责「判定 + 转码 + 生成改名映射」，**不直接改 store.json**：写完文件后交给
``storage.apply_asset_name_map`` 在同一个事务里改写引用（assets / base_products /
store_listing_copies / listing_skus）。删除类逻辑在 ``disk_cleanup`` 里，本模块只提供
候选目录判定，删除必须走那边的白名单与逐文件删除。
"""

from __future__ import annotations

import datetime
import hashlib
import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import storage as storage_module

try:  # Pillow 是既有依赖（asset_thumbnails / vision_listing 也用它）
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - 依赖缺失时整块能力优雅退化
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

CATEGORY_ID = "product_assets"
LEGACY_CATEGORY_ID = "product_assets_legacy"

# 与 disk_cleanup.ACTIVE_JOB_STATUSES 保持一致：这里复制一份是为了避免
# disk_cleanup → asset_compaction → disk_cleanup 的循环导入。
ACTIVE_JOB_STATUSES = frozenset(
    {"ready", "claimed", "running", "needs_confirmation", "needs_manual", "needs_regeneration"}
)

# 会被转成 JPEG 的格式（GIF 可能是动图，跳过）。
CONVERT_SUFFIXES = frozenset({".png", ".webp", ".bmp", ".tif", ".tiff"})
# 已经是 JPEG 的：只在「超边长」或「省得够多」时替换，避免无谓的有损重编码。
JPEG_SUFFIXES = frozenset({".jpg", ".jpeg"})
# 明确不碰的文件（视频、源文件）。
SKIP_SUFFIXES = frozenset({".mp4", ".mov", ".avi", ".mkv", ".psd", ".ai", ".zip", ".rar"})

# 估算口径：PNG 系列按 90% 可省（实测 94.6%，取保守值）；超边长的 JPEG 按 50% 可省。
_ESTIMATE_CONVERT_RATIO = 0.90
_ESTIMATE_JPEG_DOWNSCALE_RATIO = 0.50

_DEFAULT_MAX_EDGE = 1000
_DEFAULT_JPEG_QUALITY = 88
_DEFAULT_DELAY_DAYS = 3
_DEFAULT_MIN_GAIN = 0.85
_MIN_EDGE = 200
_MAX_EDGE = 4000


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


@dataclass(frozen=True)
class CompactionSettings:
    enabled: bool = True
    delay_days: int = _DEFAULT_DELAY_DAYS
    max_edge: int = _DEFAULT_MAX_EDGE
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY
    min_gain: float = _DEFAULT_MIN_GAIN

    @classmethod
    def from_config(cls, config: Any) -> "CompactionSettings":
        value = config if isinstance(config, dict) else {}
        return cls(
            enabled=value.get("compact_enabled", True) is not False,
            delay_days=_clamp_int(value.get("compact_delay_days"), default=_DEFAULT_DELAY_DAYS, minimum=0, maximum=365),
            max_edge=_clamp_int(value.get("compact_max_edge"), default=_DEFAULT_MAX_EDGE, minimum=_MIN_EDGE, maximum=_MAX_EDGE),
            jpeg_quality=_clamp_int(value.get("compact_jpeg_quality"), default=_DEFAULT_JPEG_QUALITY, minimum=50, maximum=95),
            min_gain=_DEFAULT_MIN_GAIN,
        )


@dataclass(frozen=True)
class Eligibility:
    ok: bool
    reason: str


@dataclass
class ConvertResult:
    path: Path
    new_path: Path
    old_name: str
    new_name: str
    old_size: int
    new_size: int
    width: int
    height: int
    file_hash: str
    status: str  # converted / unchanged / failed
    error: str = ""

    @property
    def saved_bytes(self) -> int:
        if self.status != "converted":
            return 0
        return max(0, self.old_size - self.new_size)


@dataclass
class CompactionReport:
    product_id: str = ""
    group_id: str = ""
    root: str = ""
    results: list[ConvertResult] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    # 不符合压缩条件时的原因（not_eligible:*）。这不是错误：未完成、有在途任务、NAS 素材的
    # 商品本来就不该被压，混进 errors 会让报告看起来像失败（现场 143 个商品全被记成错误）。
    skipped_reason: str = ""

    @property
    def converted(self) -> list[ConvertResult]:
        return [item for item in self.results if item.status == "converted"]

    @property
    def saved_bytes(self) -> int:
        return sum(item.saved_bytes for item in self.results)

    @property
    def file_updates(self) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        for item in self.converted:
            updates.append(
                {
                    "old_name": item.old_name,
                    "new_name": item.new_name,
                    "old_path": str(item.path),
                    "new_path": str(item.new_path),
                    "size_bytes": item.new_size,
                    "width": item.width,
                    "height": item.height,
                    "file_hash": item.file_hash,
                }
            )
        return updates


def _normcase(path: Any) -> str:
    return os.path.normcase(os.path.normpath(str(path)))

def local_asset_root(base_product: dict[str, Any], storage_dir: Path) -> Path | None:
    """``asset_root`` 落在本地 ``storage/uploads/base-products/`` 之下时返回该目录。

    NAS 路径（``\\\\Juns\\…``）与任何其它位置一律返回 None —— 这些素材不属于本设计范围。
    """

    raw = str((base_product or {}).get("asset_root") or "").strip()
    if not raw:
        return None
    root = Path(storage_dir) / "uploads" / "base-products"
    candidate = _normcase(raw)
    prefix = _normcase(root)
    if candidate != prefix and not candidate.startswith(prefix + os.sep):
        return None
    return Path(raw)


def parse_timestamp(value: Any) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _now(value: datetime.datetime | None) -> datetime.datetime:
    if value is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value


def copies_for_base(copies: Iterable[dict[str, Any]], base_product_id: str) -> list[dict[str, Any]]:
    return [item for item in copies if str(item.get("base_product_id") or "") == base_product_id]


def active_listing_copy_ids(
    store: dict[str, Any], active_job_ids: set[str] | None = None
) -> set[str]:
    """在途任务对应的**刊登副本** id。

    判定「这个副本正在跑，别动它的素材」比的是副本 id，而不是任务 id：
    ``active_job_ids`` 是任务 id，必须经由任务的 ``listing_copy_id`` 换算。
    """

    active = set(active_job_ids) if active_job_ids is not None else _active_job_ids(store)
    result: set[str] = set()
    for job in store.get("automation_jobs") or []:
        if str(job.get("id") or "") not in active:
            continue
        copy_id = str(job.get("listing_copy_id") or "").strip()
        if copy_id:
            result.add(copy_id)
    return result


def _shared_guards(
    base_product: dict[str, Any],
    copies: list[dict[str, Any]],
    active_copy_ids: set[str],
    *,
    days: int,
    field_name: str,
    storage_dir: Path,
    now: datetime.datetime,
) -> Eligibility:
    """压缩与清理共用的前四条判定。"""

    status = str(base_product.get("status") or "").strip().lower()
    if status != "completed":
        return Eligibility(False, "not_completed")
    completed_at = parse_timestamp(base_product.get(field_name) or base_product.get("completed_at"))
    if completed_at is None:
        return Eligibility(False, "missing_completed_at")
    if (now - completed_at).total_seconds() < days * 86400:
        return Eligibility(False, "too_recent")
    if not copies:
        return Eligibility(False, "no_copies")
    if any(str(item.get("status") or "").strip().lower() != "completed" for item in copies):
        return Eligibility(False, "copy_open")
    copy_ids = {str(item.get("id") or "") for item in copies}
    if copy_ids & active_copy_ids:
        return Eligibility(False, "job_active")
    root = local_asset_root(base_product, storage_dir)
    if root is None:
        return Eligibility(False, "not_local")
    if not root.is_dir():
        return Eligibility(False, "missing_dir")
    return Eligibility(True, "ok")


def compaction_eligibility(
    base_product: dict[str, Any],
    copies: list[dict[str, Any]],
    active_copy_ids: set[str],
    *,
    settings: CompactionSettings,
    storage_dir: Path,
    now: datetime.datetime | None = None,
) -> Eligibility:
    """``active_copy_ids`` 是**副本** id 集合（用 ``active_listing_copy_ids`` 换算）。"""

    return _shared_guards(
        base_product,
        copies,
        active_copy_ids,
        days=settings.delay_days,
        field_name="completed_at",
        storage_dir=storage_dir,
        now=_now(now),
    )


def cleanup_eligibility(
    base_product: dict[str, Any],
    copies: list[dict[str, Any]],
    active_copy_ids: set[str],
    *,
    retention_days: int,
    storage_dir: Path,
    now: datetime.datetime | None = None,
) -> Eligibility:
    """``active_copy_ids`` 是**副本** id 集合（用 ``active_listing_copy_ids`` 换算）。"""

    if str(base_product.get("assets_cleaned_at") or "").strip():
        return Eligibility(False, "already_cleaned")
    return _shared_guards(
        base_product,
        copies,
        active_copy_ids,
        days=retention_days,
        field_name="completed_at",
        storage_dir=storage_dir,
        now=_now(now),
    )


def _iter_image_files(root: Path) -> Iterator[Path]:
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
        for name in files:
            path = current_path / name
            if path.is_symlink():
                continue
            suffix = path.suffix.lower()
            if suffix in SKIP_SUFFIXES:
                continue
            if suffix in CONVERT_SUFFIXES or suffix in JPEG_SUFFIXES:
                yield path


def _target_name(name: str) -> str:
    return f"{Path(name).stem}.jpg"


def convert_image(path: Path, settings: CompactionSettings) -> ConvertResult:
    """把一张图压成长边 ``max_edge`` 的 JPEG（同名换扩展名）。

    - PNG/WebP/BMP/TIFF：一律转 JPEG（转完更大就保持原样）。
    - 已是 JPEG：只有「长边超限」或「至少省 15%」才替换。
    - 任何异常都返回 ``failed`` 而不抛出：单张坏图不能中断整批。
    """

    old_size = 0
    try:
        old_size = path.stat().st_size
    except OSError as exc:
        return ConvertResult(path, path, path.name, path.name, 0, 0, 0, 0, "", "failed", str(exc))
    suffix = path.suffix.lower()
    new_path = path.with_name(_target_name(path.name))
    failed = ConvertResult(path, new_path, path.name, _target_name(path.name), old_size, 0, 0, 0, "", "failed")
    if Image is None or ImageOps is None:
        failed.error = "pillow_unavailable"
        return failed
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened)
            width, height = image.size
            edge_exceeded = max(width, height) > settings.max_edge
            if image.mode in {"RGBA", "LA", "P"}:
                converted = image.convert("RGBA")
                canvas = Image.new("RGB", converted.size, (255, 255, 255))
                canvas.paste(converted, mask=converted.split()[-1])
                image = canvas
            elif image.mode != "RGB":
                image = image.convert("RGB")
            if edge_exceeded:
                image.thumbnail((settings.max_edge, settings.max_edge))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=settings.jpeg_quality, optimize=True, progressive=True)
            payload = buffer.getvalue()
            width, height = image.size
    except Exception as exc:  # noqa: BLE001 - 坏图/解码失败都不能中断批量
        failed.error = f"{type(exc).__name__}: {exc}"
        return failed
    new_size = len(payload)
    if suffix in JPEG_SUFFIXES:
        worth_it = edge_exceeded or new_size <= int(old_size * settings.min_gain)
        if not worth_it or new_size >= old_size:
            return ConvertResult(
                path, path, path.name, path.name, old_size, old_size, width, height, "", "unchanged"
            )
        target = path
    else:
        if new_size >= old_size:
            return ConvertResult(
                path, path, path.name, path.name, old_size, old_size, width, height, "", "unchanged"
            )
        target = new_path
    try:
        temporary = target.with_name(f"{target.name}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        if target != path:
            try:
                path.unlink()
            except OSError:
                pass
    except OSError as exc:
        failed.error = str(exc)
        return failed
    return ConvertResult(
        path,
        target,
        path.name,
        target.name,
        old_size,
        new_size,
        width,
        height,
        hashlib.sha256(payload).hexdigest(),
        "converted",
    )


def plan_directory(root: Path, settings: CompactionSettings) -> list[Path]:
    return list(_iter_image_files(Path(root)))


def compact_directory(root: Path, settings: CompactionSettings) -> CompactionReport:
    report = CompactionReport(root=str(root))
    for path in plan_directory(root, settings):
        result = convert_image(path, settings)
        report.results.append(result)
        if result.status == "unchanged":
            report.skipped += 1
        elif result.status == "failed":
            report.errors.append(f"{path.name}: {result.error}")
    return report


def estimate_reclaimable_bytes(root: Path, settings: CompactionSettings) -> int:
    """粗略估算可释放字节（``storage/usage`` 用，不求精确）。"""

    total = 0
    for path in plan_directory(root, settings):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        suffix = path.suffix.lower()
        if suffix in CONVERT_SUFFIXES:
            total += int(size * _ESTIMATE_CONVERT_RATIO)
            continue
        try:
            with Image.open(path) as image:  # type: ignore[union-attr]
                width, height = image.size
        except Exception:  # noqa: BLE001 - 读不出尺寸就不估算
            continue
        if max(width, height) > settings.max_edge:
            total += int(size * _ESTIMATE_JPEG_DOWNSCALE_RATIO)
    return total


def unowned_asset_dirs(
    store: dict[str, Any],
    *,
    storage_dir: Path,
    older_than_days: int,
    now: datetime.datetime | None = None,
) -> list[Path]:
    """没有任何基础商品归属的上传目录 = 商品已被删除后遗留的素材。

    现场实测：这类目录共 80 个 / 2.24GB，其中 74 个仍被 ``assets`` 记录（或历史执行
    快照、base_product_id 已悬空的副本）引用——正因如此它们**只能手动清理**，
    不进自动集合；真正「任何地方都没引用」的只有 6 个 / 0.02GB。

    带 ``session.json`` 的目录是在途上传会话（还没生成基础商品），永远跳过——
    它们归 ``temporary_files`` 类目按 7 天口径处理。
    """

    root = Path(storage_dir) / "uploads" / "base-products"
    if not root.is_dir():
        return []
    referenced = {
        _normcase(item)
        for item in (str(row.get("asset_root") or "") for row in store.get("base_products") or [])
        if item
    }
    cutoff = _now(now).timestamp() - older_than_days * 86400
    protected = _only_copy_dirs(store, root)
    result: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.is_symlink():
            continue
        if (child / "session.json").is_file():
            continue
        child_key = _normcase(child)
        covered = any(
            ref == child_key or ref.startswith(child_key + os.sep) for ref in referenced
        )
        if covered:
            continue
        if child_key in protected:
            # 这个目录是某个「未清理商品」某张图的唯一现存副本：删了它，商品就真的没图了。
            continue
        try:
            newest = max(
                (path.stat().st_mtime for path in child.rglob("*") if not path.is_dir()),
                default=child.stat().st_mtime,
            )
        except OSError:
            continue
        if newest < cutoff:
            result.append(child)
    return result


def _referenced_names(base_product: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("main_image", "size_chart_image"):
        value = str(base_product.get(key) or "").strip()
        if value:
            names.append(value)
    names.extend(str(item) for item in (base_product.get("product_images") or []) if item)
    names.extend(str(item) for item in (base_product.get("variant_image_map") or {}).values() if item)
    return list(dict.fromkeys(names))


def _only_copy_dirs(store: dict[str, Any], root: Path) -> set[str]:
    """找出「是某个未清理商品某张图的唯一现存副本」的上传目录。

    现场踩到的坑：同一个 group 多次导入时，``assets`` 里会堆积多个会话的同名记录，
    解析器按名字取第一条。若那条指向旧会话，旧会话又恰好是某张图的唯一现存副本，
    删掉它就会让在售商品解析到不存在的文件。这类目录一律不删。
    """

    assets_by_group: dict[str, list[dict[str, Any]]] = {}
    for asset in store.get("assets") or []:
        path = str(asset.get("file_path") or "")
        if not path:
            continue
        try:
            Path(path).stat()
        except OSError:
            continue
        group = str(asset.get("group_id") or "").upper()
        assets_by_group.setdefault(group, []).append(asset)
    if not assets_by_group:
        return set()
    protected: set[str] = set()
    for base in store.get("base_products") or []:
        if str(base.get("assets_cleaned_at") or "").strip():
            continue
        group = str(base.get("group_id") or "").upper()
        pool = assets_by_group.get(group) or []
        if not pool:
            continue
        names = _referenced_names(base)
        for name in names:
            target = name.lower()
            matches = [a for a in pool if str(a.get("file_name") or "").lower() == target]
            if len(matches) != 1:
                continue
            only = matches[0]
            path = Path(str(only.get("file_path") or ""))
            try:
                path.relative_to(root)
            except ValueError:
                continue
            key = _normcase(path)
            session = Path(_normcase(root))
            try:
                relative = Path(key).relative_to(session)
            except ValueError:
                continue
            if not relative.parts:
                continue
            protected.add(_normcase(session / relative.parts[0]))
    return protected


def plan_asset_pointer_repair(
    store: dict[str, Any], *, storage_dir: Path
) -> list[dict[str, Any]]:
    """把「指向已不存在文件」的本地资产记录，改成同组同名、真实存在的那条。

    背景：同一个 group 多次导入会堆出多份同名 ``assets`` 记录，解析器取第一条。
    清理掉旧会话后，第一条可能就成死链，而商品自己的目录里明明还有同名文件。
    这里做一次原地改指（保留 asset id，前端按 id 取的图不会断）。

    只处理**同组同名**的本地记录，绝不跨 group 借图；修不了的（素材确实被清理掉的）
    保持原样，商品会在就绪检查里被判为缺素材。
    """

    root = Path(storage_dir) / "uploads" / "base-products"
    root_key = _normcase(root)

    def under_root(path: str) -> bool:
        key = _normcase(path)
        return key.startswith(root_key + os.sep)

    by_group: dict[str, list[dict[str, Any]]] = {}
    for asset in store.get("assets") or []:
        path = str(asset.get("file_path") or "")
        if not path or not under_root(path):
            continue
        by_group.setdefault(str(asset.get("group_id") or "").upper(), []).append(asset)
    updates: list[dict[str, Any]] = []
    for assets in by_group.values():
        live_by_name: dict[str, dict[str, Any]] = {}
        for asset in assets:
            if Path(str(asset.get("file_path") or "")).is_file():
                live_by_name.setdefault(str(asset.get("file_name") or "").lower(), asset)
        if not live_by_name:
            continue
        for asset in assets:
            path = str(asset.get("file_path") or "")
            if Path(path).is_file():
                continue
            live = live_by_name.get(str(asset.get("file_name") or "").lower())
            if not live or live.get("id") == asset.get("id"):
                continue
            updates.append(
                {
                    "asset_id": asset.get("id"),
                    "source_id": live.get("id"),
                    "file_name": live.get("file_name"),
                    "file_path": live.get("file_path"),
                    "size_bytes": live.get("size_bytes"),
                    "width": live.get("width"),
                    "height": live.get("height"),
                    "file_hash": live.get("file_hash"),
                }
            )
    return updates


def cleanup_candidate_dirs(
    store: dict[str, Any],
    *,
    storage_dir: Path,
    retention_days: int,
    active_job_ids: set[str],
    now: datetime.datetime | None = None,
) -> tuple[list[Path], list[Path]]:
    """返回 ``(已完成商品素材目录, 无归属遗留目录)``，两类都只包含可安全删除的。

    第一类进 ``product_assets``（自动+手动），第二类进 ``product_assets_legacy``（仅手动）。
    """

    base_products = store.get("base_products") or []
    copies = store.get("store_listing_copies") or []
    active_copy_ids = active_listing_copy_ids(store, active_job_ids)
    moment = _now(now)
    product_dirs: list[Path] = []
    for base in base_products:
        eligibility = cleanup_eligibility(
            base,
            copies_for_base(copies, str(base.get("id") or "")),
            active_copy_ids,
            retention_days=retention_days,
            storage_dir=storage_dir,
            now=moment,
        )
        if not eligibility.ok:
            continue
        root = local_asset_root(base, storage_dir)
        if root is not None and root.is_dir():
            product_dirs.append(root)
    return product_dirs, unowned_asset_dirs(
        store, storage_dir=storage_dir, older_than_days=retention_days, now=moment
    )


def scan_compaction(
    store: dict[str, Any],
    *,
    settings: CompactionSettings,
    storage_dir: Path,
    active_job_ids: set[str],
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """只读扫描：多少商品可压缩、多少文件、预计省多少。"""

    base_products = store.get("base_products") or []
    copies = store.get("store_listing_copies") or []
    active_copy_ids = active_listing_copy_ids(store, active_job_ids)
    moment = _now(now)
    products = 0
    files = 0
    estimated = 0
    for base in base_products:
        eligibility = compaction_eligibility(
            base,
            copies_for_base(copies, str(base.get("id") or "")),
            active_copy_ids,
            settings=settings,
            storage_dir=storage_dir,
            now=moment,
        )
        if not eligibility.ok:
            continue
        root = local_asset_root(base, storage_dir)
        if root is None or not root.is_dir():
            continue
        candidates = plan_directory(root, settings)
        if not candidates:
            continue
        products += 1
        files += len(candidates)
        estimated += estimate_reclaimable_bytes(root, settings)
    return {"products": products, "files": files, "estimated_bytes": estimated}


def compact_base_product(
    store: dict[str, Any],
    base_product_id: str,
    *,
    settings: CompactionSettings,
    storage_dir: Path,
    active_job_ids: set[str],
    now: datetime.datetime | None = None,
    apply_updates: bool = True,
) -> CompactionReport:
    """压缩一个基础商品的本地素材并改写引用（``apply_updates=False`` 只转文件）。"""

    base = next(
        (item for item in store.get("base_products") or [] if str(item.get("id") or "") == base_product_id),
        None,
    )
    if not base:
        report = CompactionReport(product_id=base_product_id, errors=["base_product_not_found"])
        return report
    report = CompactionReport(product_id=base_product_id, group_id=str(base.get("group_id") or ""))
    eligibility = compaction_eligibility(
        base,
        copies_for_base(store.get("store_listing_copies") or [], base_product_id),
        active_listing_copy_ids(store, active_job_ids),
        settings=settings,
        storage_dir=storage_dir,
        now=_now(now),
    )
    if not eligibility.ok:
        report.skipped_reason = f"not_eligible:{eligibility.reason}"
        return report
    root = local_asset_root(base, storage_dir)
    if root is None or not root.is_dir():
        report.skipped_reason = "not_eligible:not_local"
        return report
    report.root = str(root)
    result = compact_directory(root, settings)
    report.results = result.results
    report.skipped = result.skipped
    report.errors.extend(result.errors)
    if apply_updates and report.file_updates:
        storage_module.apply_asset_name_map(
            base_product_id,
            report.file_updates,
            saved_bytes=report.saved_bytes,
            now=_iso(_now(now)),
        )
    return report


def run_compaction(
    *,
    settings: CompactionSettings,
    storage_dir: Path,
    now: datetime.datetime | None = None,
    store: dict[str, Any] | None = None,
    active_job_ids: set[str] | None = None,
) -> dict[str, Any]:
    """批量压缩所有符合条件的基础商品；逐个 best-effort。"""

    if not settings.enabled:
        return {"ok": True, "skipped_reason": "disabled", "products": 0, "files": 0, "saved_bytes": 0, "errors": []}
    data = store if store is not None else storage_module.load_store()
    jobs = active_job_ids if active_job_ids is not None else _active_job_ids(data)
    moment = _now(now)
    products = 0
    files = 0
    saved = 0
    errors: list[str] = []
    skipped: dict[str, int] = {}
    for base in list(data.get("base_products") or []):
        try:
            report = compact_base_product(
                data,
                str(base.get("id") or ""),
                settings=settings,
                storage_dir=storage_dir,
                active_job_ids=jobs,
                now=moment,
            )
        except Exception as exc:  # noqa: BLE001 - 单个商品失败不能中断整批
            errors.append(f"{base.get('group_id') or base.get('id')}: {type(exc).__name__}: {exc}")
            continue
        if report.converted:
            products += 1
            files += len(report.converted)
            saved += report.saved_bytes
        if report.skipped_reason:
            skipped[report.skipped_reason] = skipped.get(report.skipped_reason, 0) + 1
        errors.extend(report.errors)
    return {
        "ok": not errors,
        "products": products,
        "files": files,
        "saved_bytes": saved,
        "errors": errors,
        "skipped": skipped,
    }


def _active_job_ids(store: dict[str, Any]) -> set[str]:
    return {
        str(job.get("id") or "")
        for job in store.get("automation_jobs") or []
        if str(job.get("status") or "") in ACTIVE_JOB_STATUSES and str(job.get("id") or "")
    }


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).isoformat()
