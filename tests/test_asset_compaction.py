"""商品素材压缩与超期清理（2026-10-19 设计规格）。

钉四件事：

1. **判定**：只有「上架完成满延后期 + 所有副本完成 + 无在途任务 + 本地素材目录」的商品
   才进压缩/清理范围；NAS 素材、未完成、回滚过的一律不动。
2. **改名一致性**：PNG → 同名 JPEG 后，`assets` / `base_products` /
   `store_listing_copies` / `listing_skus.variant_image` 四处引用必须一致
   （解析器按「整名全等」找图，漏一处就是静默丢图）。
3. **幂等与失败隔离**：连跑两次第二次为 0；单张坏图不影响同目录其它文件。
4. **清理边界**：只删符合规则的目录；在途会话、符号链接、白名单外路径不动；
   遗留素材类目不进每日自动集合。
"""
from __future__ import annotations

import datetime
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from services.api import asset_compaction, disk_cleanup, settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "storage").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "SCREENSHOT_DIR", tmp_path / "storage" / "screenshots")
    monkeypatch.setattr(settings, "LOG_DIR", tmp_path / "storage" / "logs")


def _photo(path: Path, size: tuple[int, int] = (1100, 1100), *, sigma: float = 60.0) -> Path:
    """造一张「像照片」的图。

    纯色或渐变会被 PNG 压到十几 KB，转 JPEG 反而更大，测不出收益；
    真实商品图带纹理/噪点（现场平均 3.28MB/张，转 JPEG 后 0.18MB），所以这里用噪声。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.effect_noise(size, sigma).convert("RGB").save(path)
    return path


def _jpeg(path: Path, size: tuple[int, int] = (900, 900), quality: int = 88) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.effect_noise(size, 60.0).convert("RGB")
    image.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    return path


def _touch(path: Path, *, age_days: int = 0) -> Path:
    """只需要「有个目录 + 有个文件、mtime 可控」时用，不必造大图。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _build_store(
    tmp_path: Path,
    *,
    days_ago: int = 5,
    status: str = "completed",
    copy_status: str = "completed",
    job_status: str = "saved_with_warnings",
    asset_root: Path | None = None,
    folder: str = "session-1",
) -> dict:
    root = asset_root or (tmp_path / "storage" / "uploads" / "base-products" / folder / "HN0001")
    white = _photo(root / "A白色.png")
    black = _photo(root / "A黑色.png")
    completed_at = (_now() - datetime.timedelta(days=days_ago)).isoformat()
    return {
        "base_products": [
            {
                "id": "base-1",
                "group_id": "HN0001",
                "status": status,
                "completed_at": completed_at if status == "completed" else "",
                "asset_root": str(root),
                "main_image": "A白色.png",
                "product_images": ["A白色.png", "A黑色.png"],
                "variant_image_map": {"White": "A白色.png", "Black": "A黑色.png"},
                "size_chart_image": "",
            }
        ],
        "store_listing_copies": [
            {
                "id": "copy-1",
                "base_product_id": "base-1",
                "status": copy_status,
                "completed_at": completed_at if copy_status == "completed" else "",
                "main_image": "A白色.png",
                "product_images": ["A白色.png", "A黑色.png"],
                "variant_image_map": {"White": "A白色.png"},
            }
        ],
        "listing_skus": [
            {"id": "sku-1", "base_product_id": "base-1", "listing_copy_id": "copy-1", "variant_image": "A白色.png"},
            {"id": "sku-2", "base_product_id": "base-1", "listing_copy_id": "copy-1", "variant_image": "A黑色.png"},
        ],
        "assets": [
            {
                "id": "asset-white",
                "group_id": "HN0001",
                "file_name": "A白色.png",
                "file_path": str(white),
                "size_bytes": white.stat().st_size,
                "width": 1200,
                "height": 1200,
                "file_hash": "old-hash",
                "status": "ready",
            },
            {
                "id": "asset-black",
                "group_id": "HN0001",
                "file_name": "A黑色.png",
                "file_path": str(black),
                "size_bytes": black.stat().st_size,
                "width": 1200,
                "height": 1200,
                "file_hash": "old-hash-2",
                "status": "ready",
            },
        ],
        "automation_jobs": [
            {"id": "job-1", "listing_copy_id": "copy-1", "status": job_status},
        ],
    }


def _settings(delay_days: int = 3) -> asset_compaction.CompactionSettings:
    return asset_compaction.CompactionSettings(delay_days=delay_days)


def _run(tmp_path: Path, store: dict, *, delay_days: int = 3) -> dict:
    storage.save_store(store)
    return asset_compaction.run_compaction(
        settings=_settings(delay_days),
        storage_dir=tmp_path / "storage",
        store=store,
    )


def test_compaction_converts_png_and_rewrites_every_reference(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    root = Path(store["base_products"][0]["asset_root"])

    report = _run(tmp_path, store)

    assert report["ok"] is True and report["files"] == 2 and report["products"] == 1
    assert report["saved_bytes"] > 0
    assert (root / "A白色.jpg").is_file() and (root / "A黑色.jpg").is_file()
    assert not (root / "A白色.png").exists() and not (root / "A黑色.png").exists()

    saved = storage.load_store()
    base = saved["base_products"][0]
    assert base["main_image"] == "A白色.jpg"
    assert base["product_images"] == ["A白色.jpg", "A黑色.jpg"]
    # 颜色键不能变，只改文件名的扩展名。
    assert base["variant_image_map"] == {"White": "A白色.jpg", "Black": "A黑色.jpg"}
    assert base["assets_compacted_at"]
    assert base["assets_compacted_bytes"] == report["saved_bytes"]

    copy = saved["store_listing_copies"][0]
    assert copy["main_image"] == "A白色.jpg"
    assert copy["product_images"] == ["A白色.jpg", "A黑色.jpg"]
    assert copy["variant_image_map"] == {"White": "A白色.jpg"}

    skus = {item["id"]: item["variant_image"] for item in saved["listing_skus"]}
    assert skus == {"sku-1": "A白色.jpg", "sku-2": "A黑色.jpg"}

    assets = {item["id"]: item for item in saved["assets"]}
    assert assets["asset-white"]["file_name"] == "A白色.jpg"
    assert assets["asset-white"]["file_path"] == str(root / "A白色.jpg")
    assert assets["asset-white"]["file_hash"] != "old-hash"
    assert assets["asset-white"]["size_bytes"] == (root / "A白色.jpg").stat().st_size
    # id 必须原地保留：前端与副本都按 asset id 取图。
    assert {item["id"] for item in saved["assets"]} == {"asset-white", "asset-black"}


def test_compaction_is_idempotent_and_updates_asset_metadata_once(tmp_path: Path) -> None:
    store = _build_store(tmp_path)

    first = _run(tmp_path, store)
    assert first["files"] == 2
    saved_bytes = first["saved_bytes"]

    reloaded = storage.load_store()
    second = asset_compaction.run_compaction(
        settings=_settings(), storage_dir=tmp_path / "storage", store=reloaded
    )
    assert second["files"] == 0 and second["saved_bytes"] == 0
    assert storage.load_store()["base_products"][0]["assets_compacted_bytes"] == saved_bytes


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"status": "base_ready"}, "not_completed"),
        ({"days_ago": 1}, "too_recent"),
        ({"copy_status": "copy_ready"}, "copy_open"),
        ({"job_status": "running"}, "job_active"),
    ],
)
def test_compaction_eligibility_blocks_unfinished_products(
    tmp_path: Path, kwargs: dict, reason: str
) -> None:
    store = _build_store(tmp_path, **kwargs)
    base = store["base_products"][0]
    active = {"job-1"} if kwargs.get("job_status") == "running" else set()

    eligibility = asset_compaction.compaction_eligibility(
        base,
        store["store_listing_copies"],
        # 在途判定比的是「副本 id」：任务 id 要经任务的 listing_copy_id 换算。
        asset_compaction.active_listing_copy_ids(store, active),
        settings=_settings(),
        storage_dir=tmp_path / "storage",
    )

    assert eligibility.ok is False and eligibility.reason == reason
    report = asset_compaction.compact_base_product(
        store,
        "base-1",
        settings=_settings(),
        storage_dir=tmp_path / "storage",
        active_job_ids=active,
        apply_updates=False,
    )
    assert report.converted == []
    # 不符合条件不是错误：原因记在 skipped_reason，不污染 errors（否则报告看着像失败）。
    assert report.skipped_reason == f"not_eligible:{reason}"
    assert report.errors == []
    assert Path(base["asset_root"], "A白色.png").is_file()


def test_compaction_skips_nas_asset_root(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    store["base_products"][0]["asset_root"] = r"\\Juns\产品编辑区\HN0001"

    eligibility = asset_compaction.compaction_eligibility(
        store["base_products"][0],
        store["store_listing_copies"],
        set(),
        settings=_settings(),
        storage_dir=tmp_path / "storage",
    )

    assert eligibility.ok is False and eligibility.reason == "not_local"


def test_small_jpeg_is_left_untouched(tmp_path: Path) -> None:
    # 已经是 900px 的 JPEG，按同一套编码参数重压省不到 15%，不该做无谓的有损重编码。
    path = _jpeg(tmp_path / "storage" / "uploads" / "base-products" / "s" / "HN0002" / "small.jpg")
    before = path.read_bytes()

    result = asset_compaction.convert_image(path, _settings())

    assert result.status == "unchanged"
    assert path.read_bytes() == before


def test_oversized_jpeg_is_downscaled(tmp_path: Path) -> None:
    path = _jpeg(
        tmp_path / "storage" / "uploads" / "base-products" / "s" / "HN0006" / "big.jpg",
        (1600, 1600),
    )
    before = path.stat().st_size

    result = asset_compaction.convert_image(path, _settings())

    assert result.status == "converted"
    assert result.path == result.new_path  # 同名就地重压，扩展名不变
    with Image.open(path) as image:
        assert max(image.size) == 1000
    assert path.stat().st_size < before


def test_broken_image_does_not_stop_the_batch(tmp_path: Path) -> None:
    root = tmp_path / "storage" / "uploads" / "base-products" / "s" / "HN0003"
    root.mkdir(parents=True, exist_ok=True)
    (root / "broken.png").write_bytes(b"not an image")
    _photo(root / "good.png", (1100, 1100))

    report = asset_compaction.compact_directory(root, _settings())

    assert [item.status for item in report.results].count("converted") == 1
    assert report.errors and "broken.png" in report.errors[0]
    assert (root / "good.jpg").is_file() and not (root / "good.png").exists()


def test_cleanup_candidates_exclude_protected_products(tmp_path: Path) -> None:
    # 这个商品完成已满 40 天 → 是唯一该删的。
    storage.save_store(_build_store(tmp_path, folder="safe", days_ago=40))
    data = storage.load_store()
    # 同一份库里再加三个不该删的：未完成、回滚过（completed_at 空）、有在途任务。
    data["base_products"].append(
        {
            "id": "base-open",
            "group_id": "HN0002",
            "status": "base_ready",
            "completed_at": "",
            "asset_root": str(_touch(tmp_path / "storage" / "uploads" / "base-products" / "open" / "HN0002" / "A白色.png").parent),
        }
    )
    data["base_products"].append(
        {
            "id": "base-rolled",
            "group_id": "HN0003",
            "status": "base_ready",
            "completed_at": "",
            "rolled_back_at": _now().isoformat(),
            "asset_root": str(_touch(tmp_path / "storage" / "uploads" / "base-products" / "rolled" / "HN0003" / "A白色.png").parent),
        }
    )
    legacy = _touch(
        tmp_path / "storage" / "uploads" / "base-products" / "legacy" / "HN0004" / "A白色.png",
        age_days=40,
    ).parent
    session = _touch(tmp_path / "storage" / "uploads" / "base-products" / "in-flight" / "session.json").parent
    storage.save_store(data)

    product_dirs, legacy_dirs = asset_compaction.cleanup_candidate_dirs(
        data,
        storage_dir=tmp_path / "storage",
        retention_days=30,
        active_job_ids=set(),
    )

    # 只有「完成满 40 天」的那个商品在候选里；未完成 / 回滚过的一律排除。
    assert product_dirs == [Path(data["base_products"][0]["asset_root"])]
    assert all("open" not in str(path) and "rolled" not in str(path) for path in product_dirs)
    # 遗留类目抓到的是「上传会话目录」；带 session.json 的在途会话不算。
    assert legacy.parent in legacy_dirs
    assert session.parent not in legacy_dirs
    assert session.is_dir()
    assert legacy.is_dir()


def test_legacy_category_is_manual_only(tmp_path: Path) -> None:
    assert asset_compaction.CATEGORY_ID in disk_cleanup.AUTOMATIC_CATEGORIES
    assert asset_compaction.LEGACY_CATEGORY_ID not in disk_cleanup.AUTOMATIC_CATEGORIES
    assert asset_compaction.LEGACY_CATEGORY_ID in disk_cleanup.CATEGORY_IDS


def test_disk_cleanup_deletes_expired_product_assets_and_marks_the_base(tmp_path: Path) -> None:
    store = _build_store(tmp_path, days_ago=40)
    root = Path(store["base_products"][0]["asset_root"])
    storage.save_store(store)

    result = disk_cleanup.cleanup_categories(
        [asset_compaction.CATEGORY_ID],
        {
            "enabled": True,
            "temp_retention_days": 7,
            "screenshot_retention_days": 30,
            "export_retention_days": 90,
            "log_retention_days": 30,
            "assets_retention_days": 30,
            "compact_enabled": False,
        },
        active_job_ids=set(),
        cdp_available=lambda: False,
        store=store,
    )

    assert result["deleted_files"] == 2 and result["freed_bytes"] > 0
    assert not root.exists()
    assert result["categories"][asset_compaction.CATEGORY_ID]["cleaned_products"] == ["base-1"]
    saved = storage.load_store()["base_products"][0]
    assert saved["assets_cleaned_at"] and saved["assets_cleaned_bytes"] > 0


def test_disk_cleanup_keeps_recent_product_assets(tmp_path: Path) -> None:
    store = _build_store(tmp_path, days_ago=10)
    root = Path(store["base_products"][0]["asset_root"])
    storage.save_store(store)

    result = disk_cleanup.cleanup_categories(
        [asset_compaction.CATEGORY_ID],
        {
            "enabled": True,
            "assets_retention_days": 30,
            "compact_enabled": False,
        },
        active_job_ids=set(),
        cdp_available=lambda: False,
        store=store,
    )

    assert result["deleted_files"] == 0
    assert root.is_dir()
    assert not storage.load_store()["base_products"][0].get("assets_cleaned_at")


def test_legacy_cleanup_skips_dirs_that_are_the_only_copy(tmp_path: Path) -> None:
    """无归属目录若是某个未清理商品某张图的唯一现存副本，就不能删。"""

    root = tmp_path / "storage" / "uploads" / "base-products"
    only = _photo(root / "legacy" / "HN0009" / "A白色.png", (600, 600))
    stamp = time.time() - 40 * 86400
    os.utime(only, (stamp, stamp))
    store = {
        "base_products": [
            {
                "id": "base-1",
                "group_id": "HN0009",
                "status": "needs_check",
                "completed_at": "",
                "asset_root": str(root / "gone" / "HN0009"),
                "main_image": "A白色.png",
                "product_images": ["A白色.png"],
            }
        ],
        "assets": [
            {
                "id": "asset-only",
                "group_id": "HN0009",
                "file_name": "A白色.png",
                "file_path": str(only),
                "size_bytes": only.stat().st_size,
                "width": 600,
                "height": 600,
            }
        ],
        "store_listing_copies": [],
        "automation_jobs": [],
    }

    _product_dirs, legacy_dirs = asset_compaction.cleanup_candidate_dirs(
        store, storage_dir=tmp_path / "storage", retention_days=30, active_job_ids=set()
    )

    assert legacy_dirs == []
    assert only.is_file()


def test_asset_pointer_repair_points_dead_records_at_live_copies(tmp_path: Path) -> None:
    """同组同名：死链记录改指到现存那条（保留 asset id，不跨组借图）。"""

    root = tmp_path / "storage" / "uploads" / "base-products"
    live = _photo(root / "new" / "HN0010" / "A白色.png", (500, 500))
    dead = root / "old" / "HN0010" / "A白色.png"
    store = {
        "base_products": [
            {
                "id": "base-1",
                "group_id": "HN0010",
                "status": "needs_check",
                "completed_at": "",
                "asset_root": str(root / "new" / "HN0010"),
                "main_image": "A白色.png",
                "product_images": ["A白色.png"],
            }
        ],
        "assets": [
            {"id": "asset-dead", "group_id": "HN0010", "file_name": "A白色.png", "file_path": str(dead)},
            {
                "id": "asset-live",
                "group_id": "HN0010",
                "file_name": "A白色.png",
                "file_path": str(live),
                "size_bytes": live.stat().st_size,
                "width": 500,
                "height": 500,
                "file_hash": "live-hash",
            },
        ],
        "store_listing_copies": [],
        "automation_jobs": [],
    }
    storage.save_store(store)

    updates = asset_compaction.plan_asset_pointer_repair(store, storage_dir=tmp_path / "storage")
    assert [item["asset_id"] for item in updates] == ["asset-dead"]
    assert storage.apply_asset_pointer_repair(updates) == 1

    saved = {item["id"]: item for item in storage.load_store()["assets"]}
    assert saved["asset-dead"]["file_path"] == str(live)
    assert saved["asset-dead"]["file_hash"] == "live-hash"


def test_cleanup_reports_repaired_pointers(tmp_path: Path) -> None:
    root = tmp_path / "storage" / "uploads" / "base-products"
    live = _photo(root / "new" / "HN0011" / "A白色.png", (500, 500))
    dead = root / "old" / "HN0011" / "A白色.png"
    store = {
        "base_products": [
            {
                "id": "base-1",
                "group_id": "HN0011",
                "status": "needs_check",
                "completed_at": "",
                "asset_root": str(root / "new" / "HN0011"),
                "main_image": "A白色.png",
            }
        ],
        "assets": [
            {"id": "asset-dead", "group_id": "HN0011", "file_name": "A白色.png", "file_path": str(dead)},
            {"id": "asset-live", "group_id": "HN0011", "file_name": "A白色.png", "file_path": str(live)},
        ],
        "store_listing_copies": [],
        "automation_jobs": [],
    }
    storage.save_store(store)

    result = disk_cleanup.cleanup_categories(
        [asset_compaction.CATEGORY_ID],
        {"enabled": True, "assets_retention_days": 30, "compact_enabled": False},
        active_job_ids=set(),
        cdp_available=lambda: False,
        store=store,
    )

    assert result["deleted_files"] == 0
    assert result["repaired_asset_pointers"] == 1
    saved = {item["id"]: item for item in storage.load_store()["assets"]}
    assert saved["asset-dead"]["file_path"] == str(live)


def test_compaction_is_single_flight(tmp_path: Path) -> None:
    """手动按钮与每日自动任务不能同时跑：锁被占时返回 busy，不重复改写。"""

    store = _build_store(tmp_path, days_ago=40)
    storage.save_store(store)
    claimed = disk_cleanup._COMPACTION_LOCK.acquire(blocking=False)
    assert claimed is True
    try:
        result = disk_cleanup.run_asset_compaction(
            {"enabled": True, "compact_enabled": True, "assets_retention_days": 30},
            store=store,
            active_job_ids=set(),
            now=time.time(),
        )
    finally:
        disk_cleanup._COMPACTION_LOCK.release()
    assert result is not None and result["busy"] is True and result["files"] == 0
    assert Path(store["base_products"][0]["asset_root"], "A白色.png").is_file()


def test_settings_page_wires_asset_lifecycle_controls() -> None:
    """设置页三件套：输入框、保存字段、手动压缩按钮都必须接上。"""

    root = Path(__file__).resolve().parents[1]
    index = (root / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (root / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    for element_id in (
        "settings-storage-assets-retention",
        "settings-storage-compact-enabled",
        "settings-storage-compact-delay",
        "settings-storage-compact-edge",
        "settings-storage-compact-quality",
        "settings-storage-compact-btn",
        "settings-storage-compact-result",
    ):
        assert f'id="{element_id}"' in index, element_id
    for snippet in (
        "assets_retention_days:",
        "compact_enabled:",
        "compact_delay_days:",
        "compact_max_edge:",
        "compact_jpeg_quality:",
        "/api/settings/storage/compact",
        "compactStorageAssets()",
        "product_assets_legacy",
    ):
        assert snippet in script, snippet


def test_scan_usage_reports_asset_categories(tmp_path: Path) -> None:
    store = _build_store(tmp_path, days_ago=40)
    _touch(
        tmp_path / "storage" / "uploads" / "base-products" / "legacy" / "HN0005" / "A白色.png",
        age_days=40,
    )
    storage.save_store(store)

    usage = disk_cleanup.scan_usage(
        {"enabled": True, "assets_retention_days": 30, "compact_enabled": True},
        active_job_ids=set(),
        store=store,
    )

    assets = usage["categories"][asset_compaction.CATEGORY_ID]
    legacy = usage["categories"][asset_compaction.LEGACY_CATEGORY_ID]
    assert assets["label"] == "商品素材" and assets["automatic"] is True
    assert assets["reclaimable_file_count"] == 2
    assert legacy["label"] == "已删商品遗留素材" and legacy["automatic"] is False
    assert legacy["reclaimable_bytes"] > 0
