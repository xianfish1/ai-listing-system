"""部分加载的写路径：只动主库 / 只动任务时，不要再解析和重写其它拆分集合。

现场三个抱怨的操作（复制店铺模板 / 生成副本 / 生成自动化任务）原来每次都整库解析 +
对每个拆分集合重新序列化：实测 0.9~1.3s。改成 locked_store(split_keys=...) 之后：
复制模板 272ms、生成副本 258ms、生成任务 664ms（同一份生产库副本实测）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")
    storage._SPLIT_LAST_BYTES.clear()
    storage._MAIN_LAST_BYTES.clear()
    storage._FILE_PARSE_CACHE.clear()
    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    storage._LAST_GOOD_WRITTEN_AT = 0.0
    yield


def _job(job_id: str = "job-1", *, status: str = "running", lease_token: str = "tok") -> dict:
    return {
        "id": job_id,
        "listing_copy_id": "copy-1",
        "status": status,
        "worker_id": "worker-1",
        "lease_token": lease_token,
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
        "attempt_count": 1,
        "run_log": [],
        "created_at": "2026-10-08T00:00:00+00:00",
        "updated_at": "2026-10-08T00:00:00+00:00",
    }


def _seed() -> None:
    storage.save_store(
        {
            "store_templates": [{"id": "t1", "template_name": "T1"}],
            "automation_jobs": [_job()],
            "marketplace_categories": [{"id": "cat-1"}],
            "ai_outputs": [{"id": "out-1"}],
        }
    )
    storage.load_store()


def _split_path(name: str) -> Path:
    return settings.STORE_FILE.parent / f"{name}.json"


def test_main_only_save_leaves_skipped_split_files_untouched() -> None:
    _seed()
    jobs_path = _split_path("automation_jobs")
    categories_path = _split_path("marketplace_categories")
    jobs_before = jobs_path.read_bytes()
    categories_before = categories_path.read_bytes()

    with storage.locked_store(split_keys=()) as (data, save):
        data["store_templates"].append({"id": "t2", "template_name": "T2"})
        save(data)

    assert jobs_path.read_bytes() == jobs_before, "跳过的任务集合不该被重写"
    assert categories_path.read_bytes() == categories_before
    loaded = storage.load_store()
    assert [t["id"] for t in loaded["store_templates"]] == ["t1", "t2"]
    assert [j["id"] for j in loaded["automation_jobs"]] == ["job-1"], "跳过不等于删掉"
    assert [c["id"] for c in loaded["marketplace_categories"]] == ["cat-1"]


def test_partial_save_does_not_clobber_the_last_good_snapshot() -> None:
    _seed()
    storage.save_store(storage.load_store())  # 整库保存 → 刷新 last-good
    last_good = settings.STORE_FILE.with_name(f"{settings.STORE_FILE.name}.last-good")
    assert last_good.is_file()
    before = last_good.read_bytes()
    assert b"automation_jobs" in before or b"job-1" in before

    with storage.locked_store(split_keys=()) as (data, save):
        data["store_templates"][0]["template_name"] = "renamed"
        save(data)

    assert last_good.read_bytes() == before, "部分保存没有完整快照，不能拿它覆盖 last-good"


def test_partial_save_does_not_treat_skipped_collections_as_empty_store() -> None:
    """只有任务有业务数据时，主库部分保存不能被「空库覆盖」护栏误伤。"""

    storage.save_store({"automation_jobs": [_job()]})
    storage._LAST_GOOD_WRITTEN_AT = 0.0
    storage.save_store(storage.load_store())  # 写一份 last-good

    with storage.locked_store(split_keys=()) as (data, save):
        data.setdefault("store_templates", []).append({"id": "t1"})
        save(data)

    assert _split_path("automation_jobs").read_bytes()
    assert [j["id"] for j in storage.load_store()["automation_jobs"]] == ["job-1"]


def test_copy_store_template_reads_no_split_collections(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed()
    reads: list[str] = []
    real_read = storage._read_split_key

    def recording(key, **kwargs):
        reads.append(key)
        return real_read(key, **kwargs)

    monkeypatch.setattr(storage, "_read_split_key", recording)
    storage.save_store(
        {
            "store_templates": [
                {"id": "t1", "template_name": "T1", "mabang_store_id": "s1", "status": "active"},
            ],
            "mabang_store_options": [
                {"mabang_store_id": "s2", "platform": "shopee", "country": "PH", "account_id": "a1"},
            ],
        }
    )
    reads.clear()
    storage.copy_store_template("t1", {"mabang_store_id": "s2", "template_name": "T2", "account_id": "a1"})

    assert reads == [], f"复制模板不该解析任何拆分集合，实际读了 {reads}"


def test_automation_job_creation_reads_only_the_jobs_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed()
    reads: list[str] = []
    real_read = storage._read_split_key

    def recording(key, **kwargs):
        reads.append(key)
        return real_read(key, **kwargs)

    monkeypatch.setattr(storage, "_read_split_key", recording)
    storage.create_automation_jobs_from_listing_copies([])

    # 2026-11-03 副本/SKU/价格/素材拆出主库后，建任务的就绪度与快照要从这几个
    # 拆分文件读；13MB 类目和整份主库仍然一个字节都不碰。
    assert set(reads) == {
        "automation_jobs",
        "automation_job_snapshots",
        "store_listing_copies",
        "listing_skus",
        "listing_prices",
        "assets",
    }, f"建任务只该读任务/快照/副本/SKU/价格/素材集合，实际读了 {reads}"

def test_job_creation_keeps_the_main_store_byte_identical(tmp_path: Path) -> None:
    """main_readonly 的契约：建任务一个字节都不改主库（否则那次改动会被吞掉）。"""

    from tests.test_base_product_lifecycle import _make_base, _make_template, _prepare_copy_for_execution

    template = _make_template("Readonly")
    base = _make_base(tmp_path, folder_name="AL0911 Readonly")
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    _prepare_copy_for_execution(copy["id"], base)

    main_path = settings.STORE_FILE
    before_bytes = main_path.read_bytes()
    before_mtime = main_path.stat().st_mtime_ns

    job = storage.create_automation_job_from_listing_copy(copy["id"])

    assert job["id"]
    assert main_path.read_bytes() == before_bytes, "建任务不该重写主库"
    assert main_path.stat().st_mtime_ns == before_mtime


def test_main_readonly_falls_back_when_the_caller_replaces_main() -> None:
    """身份护栏：调用方真的替换了主库集合时，必须退回完整保存而不是吞掉改动。"""

    storage.save_store({"store_templates": [{"id": "t1"}], "automation_jobs": [{"id": "j1", "run_log": []}]})
    storage.load_store()

    with storage.locked_store(split_keys=("automation_jobs",), main_readonly=True) as (data, save):
        data["store_templates"] = [{"id": "t2"}]
        save(data)

    templates = [item["id"] for item in storage.load_store()["store_templates"]]
    assert templates == ["t2"], f"替换主库集合时要退回完整保存，实际 {templates}"


def test_remaining_write_paths_load_only_what_they_need(monkeypatch: pytest.MonkeyPatch) -> None:
    """AI 结果落库 / 补全属性 / 取消 / 人工确认：都不该整库加载。"""

    storage.save_store(
        {
            "store_templates": [{"id": "t1", "template_name": "T1", "category_attributes": {"101639": "M"}}],
            "store_listing_copies": [
                {
                    "id": "c1",
                    "template_id": "t1",
                    "group_id": "GN1",
                    "attribute_values": {},
                    "category_profile_snapshot": {"fields": [{"field_id": "101639"}]},
                }
            ],
            "listing_skus": [{"id": "s1", "listing_copy_id": "c1", "sku_code": "X"}],
            "listing_prices": [{"id": "p1", "listing_copy_id": "c1", "sku_id": "s1", "sku_code": "X"}],
            "automation_jobs": [{"id": "j1", "listing_copy_id": "c1", "status": "needs_manual", "run_log": []}],
            "automation_job_snapshots": {"j1": {"listing_copy": {"id": "c1"}, "skus": [], "prices": []}},
            "ai_outputs": [],
            "marketplace_categories": [{"id": "cat-1"}],
            "listing_copy_revisions": [],
        }
    )
    storage.load_store()

    def record_reads() -> list[str]:
        reads: list[str] = []
        real = storage._read_split_key

        def recording(key, **kwargs):
            reads.append(key)
            return real(key, **kwargs)

        monkeypatch.setattr(storage, "_read_split_key", recording)
        return reads

    reads = record_reads()
    storage.record_listing_description_generation("c1", {"description": "x", "status": "ready"})
    assert set(reads) == {"ai_outputs"}, f"AI 结果落库读了 {reads}"

    reads = record_reads()
    storage.create_ai_output({"product_id": "prod-1", "output_type": "suggestion", "response": "y"})
    assert set(reads) == {"ai_outputs"}, f"AI 结果落库读了 {reads}"

    reads = record_reads()
    filled = storage.fill_listing_copy_missing_attributes(["c1"])
    # 副本集合拆出主库后，这条路径显式点名加载它（模板仍在主库）。
    assert set(reads) == {"store_listing_copies"}, f"补全属性读了 {reads}"
    assert filled["filled_count"] == 1, filled
    assert storage.get_item("store_listing_copies", "c1")["attribute_values"]["101639"] == "M"

    reads = record_reads()
    storage.cancel_automation_job("j1", reason="bench")
    assert set(reads) == {"automation_jobs"}, f"取消任务读了 {reads}"

    reads = record_reads()
    storage.manual_finish_automation_job("j1", status="failed", note="bench")
    assert set(reads) == {"automation_jobs"}, f"人工确认读了 {reads}"


def test_settings_and_media_writes_stay_on_the_main_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """设置保存 / 上传商品图 / 回滚：一个拆分集合都不该读。"""

    import base64
    import io

    from PIL import Image

    storage.save_store(
        {
            "base_products": [{"id": "bp1", "group_id": "GN1", "status": "completed", "product_images": []}],
            "store_listing_copies": [
                {"id": "c1", "base_product_id": "bp1", "group_id": "GN1", "status": "completed"}
            ],
            "runtime_configuration": storage.merge_configuration({}),
            "runtime_configuration_version": 0,
            "runtime_configuration_audit": [],
            "automation_jobs": [{"id": "j1", "listing_copy_id": "c1", "status": "blocked", "run_log": []}],
            "automation_job_snapshots": {"j1": {"listing_copy": {"id": "c1"}}},
            "marketplace_categories": [{"id": "cat-1"}],
        }
    )
    storage.load_store()

    def record_reads() -> list[str]:
        reads: list[str] = []
        real = storage._read_split_key

        def recording(key, **kwargs):
            reads.append(key)
            return real(key, **kwargs)

        monkeypatch.setattr(storage, "_read_split_key", recording)
        return reads

    reads = record_reads()
    storage.save_runtime_configuration({"worker": {"poll_seconds": 3}})
    assert reads == [], f"设置保存读了 {reads}"

    buffer = io.BytesIO()
    Image.new("RGB", (12, 12), (10, 20, 30)).save(buffer, "PNG")
    payload = {
        "file_name": "upload.png",
        "content_type": "image/png",
        "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }
    reads = record_reads()
    saved = storage.add_base_product_product_image("bp1", payload)
    assert saved and saved["asset"]["file_name"], saved
    # base_products / assets 都是拆分集合：上传只点这两个，不碰主库。
    assert set(reads) == {"base_products", "assets"}, f"上传商品图读了 {reads}"

    reads = record_reads()
    rolled = storage.rollback_base_product("bp1")
    assert rolled and rolled["id"] == "bp1"
    assert set(reads) == {"base_products", "store_listing_copies"}, f"回滚读了 {reads}"


def test_save_paths_load_only_the_collections_they_touch(monkeypatch: pytest.MonkeyPatch) -> None:
    """保存路径不整库加载：只解析自己会动的那几个拆分集合。

    实测（生产库临时副本）：保存基础商品/模板/副本原来 742~1280ms，现在 268~295ms。
    """

    storage.save_store(
        {
            "base_products": [{"id": "bp1", "group_id": "GN1", "product_name_cn": "旧名"}],
            "store_templates": [
                {"id": "t1", "template_name": "T1", "mabang_store_id": "s1", "normal_price": 10, "sale_price": 9}
            ],
            "store_listing_copies": [
                {
                    "id": "c1",
                    "base_product_id": "bp1",
                    "template_id": "t1",
                    "group_id": "GN1",
                    "store_name": "S",
                    "normal_price": 10,
                    "sale_price": 9,
                }
            ],
            "listing_skus": [{"id": "s1", "listing_copy_id": "c1", "sku_code": "X", "size": "M", "color_code": "black"}],
            "listing_prices": [{"id": "pr1", "listing_copy_id": "c1", "sku_id": "s1", "sku_code": "X", "normal_price": 10}],
            "automation_jobs": [{"id": "j1", "listing_copy_id": "c1", "status": "blocked", "missing_fields": [], "run_log": []}],
            "automation_job_snapshots": {"j1": {"listing_copy": {"id": "c1"}}},
            "marketplace_categories": [{"id": "cat-1"}],
            "listing_copy_revisions": [],
            # 拆分集合的空文件也要在 seed 时落出来，否则第一次保存会先触发整库迁移。
            "assets": [],
            "title_skills": [],
            "title_generation_history": [],
        }
    )
    storage.load_store()

    def record_reads() -> list[str]:
        reads: list[str] = []
        real = storage._read_split_key

        def recording(key, **kwargs):
            reads.append(key)
            return real(key, **kwargs)

        monkeypatch.setattr(storage, "_read_split_key", recording)
        return reads

    reads = record_reads()
    storage.update_base_product("bp1", {"product_name_cn": "新名"}, mark_edited=True)
    # 2026-11-03 快路径：base_products/title_skills 都是拆分集合，主库 store.json
    # 一个字节都不读、不写（实测生产库副本 1.3~2.2s → ~40ms）。
    assert set(reads) == {"base_products", "title_skills"}, f"保存基础商品读了 {reads}"

    reads = record_reads()
    storage.update_store_template("t1", {"template_name": "T1", "normal_price": 11})
    assert set(reads) == {
        "automation_jobs",
        "store_listing_copies",
        "listing_skus",
        "listing_prices",
        "base_products",
    }, f"保存模板读了 {reads}"

    reads = record_reads()
    storage.update_store_listing_copy("c1", {"title": "新标题", "normal_price": 11})
    assert set(reads) == {
        "listing_copy_revisions",
        "store_listing_copies",
        "listing_skus",
        "listing_prices",
        "base_products",
    }, f"保存副本读了 {reads}"

    reads = record_reads()
    storage.delete_store_listing_copy("c1")
    assert set(reads) <= {
        "automation_jobs",
        "automation_job_snapshots",
        "store_listing_copies",
        "listing_skus",
        "listing_prices",
    }, f"删副本读了 {reads}"



def test_finish_job_writes_only_the_jobs_file() -> None:
    _seed()
    categories_path = _split_path("marketplace_categories")
    categories_before = categories_path.read_bytes()

    storage.finish_automation_job("job-1", "worker-1", "tok", status="blocked", error="needs operator")

    assert categories_path.read_bytes() == categories_before
    stored = storage.get_item("automation_jobs", "job-1")
    assert stored and stored["status"] == "blocked"
    assert stored["last_error"] == "needs operator"


def test_partial_save_still_round_trips_through_the_read_cache() -> None:
    _seed()
    storage.load_store()

    with storage.locked_store(split_keys=()) as (data, save):
        data["store_templates"].append({"id": "t2", "template_name": "T2"})
        save(data)

    fresh = storage.load_store()
    assert [t["id"] for t in fresh["store_templates"]] == ["t1", "t2"]
    assert json.loads(_split_path("automation_jobs").read_text(encoding="utf-8"))[0]["id"] == "job-1"
