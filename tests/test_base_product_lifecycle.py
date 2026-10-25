"""基础商品四段分类（商品维护「分类」行）：首次导入 / 未复制 / 待执行 / 已完成。

覆盖 ``storage._derive_lifecycle_stage`` 经列表接口暴露出来的结果，以及
``update_base_product`` 的 ``mark_edited`` 契约：

- 刚导入 → 首次导入；导入时的 AI 识别不算编辑
- 操作员保存维护内容且内容真的变了 → 未复制
- 原样保存（没改动）不算编辑
- 编辑后复制 → 待执行；复制后又编辑 → 退回未复制（副本不含新改动）
- 所有副本保存完成 → 已完成
- ``PUT /api/base-products/{id}`` 走操作员路径，会打上编辑标记
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib import request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _transaction_defaults() -> dict:
    return {
        "stock": 100,
        "weight_kg": 0.3,
        "package_length_cm": 30,
        "package_width_cm": 20,
        "package_height_cm": 5,
    }


def _make_base(tmp_path: Path, folder_name: str = "AL0601 Lifecycle Tee") -> dict:
    folder = tmp_path / folder_name
    folder.mkdir()
    for name in ["main.jpg", "White.jpg", "Black.jpg", "size-chart.png"]:
        (folder / name).write_bytes(b"image")
    return storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})


def _make_template(suffix: str) -> dict:
    return storage.create_store_template(
        {
            "store_name": f"POD Store {suffix}",
            "platform": "shopee",
            "country": "PH",
            "currency": "PHP",
            "template_name": f"Mens Tee {suffix}",
            "product_line": "POD Mens T-Shirt",
            "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
            "mabang_store_id": f"store-{suffix}",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["S"],
            "sku_template": "{main_sku}_{color}||{size}",
            "description": "Template default description.",
            **_transaction_defaults(),
        }
    )


def _stage(base_product_id: str) -> str:
    products = {item["id"]: item for item in storage.list_base_products_with_summary()}
    return products[base_product_id]["lifecycle_stage"]


def _prepare_copy_for_execution(copy_id: str, base: dict) -> None:
    """补齐自动化前置字段（描述 + 尺码图），否则任务建出来是 needs_manual，领不到。"""

    folder = Path(base["asset_root"])
    storage.update_store_listing_copy(
        copy_id,
        {
            "description": "Lifecycle test description",
            "description_status": "ready",
            "size_chart_image": "size-chart.png",
            "size_chart_image_path": str(folder / "size-chart.png"),
        },
    )


def _drive_job_to_saved(job_id: str) -> dict:
    claim = storage.claim_next_automation_job("worker-test", lease_seconds=120, max_attempts=3)
    assert claim["job"] and claim["job"]["id"] == job_id, "expected claim to return the target job"
    return storage.finish_automation_job(
        job_id,
        worker_id="worker-test",
        lease_token=claim["lease_token"],
        status="saved",
        result={
            "ok": True,
            "status": "saved",
            "exit_code": 0,
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
        },
    )


def test_fresh_import_lands_in_first_import_bucket(tmp_path: Path) -> None:
    base = _make_base(tmp_path)

    assert _stage(base["id"]) == "imported"


def test_import_time_ai_enrichment_is_not_an_edit(tmp_path: Path) -> None:
    """导入时 AI 写入标题/详情走的是同一个 update_base_product，但不该算人工编辑。"""

    base = _make_base(tmp_path)

    storage.update_base_product(base["id"], {"listing_title": "AI title", "ai_generation_error": ""})

    refreshed = storage.get_item("base_products", base["id"])
    assert not refreshed.get("last_edited_at")
    assert _stage(base["id"]) == "imported"


def test_operator_save_marks_edited_and_moves_to_uncopied(tmp_path: Path) -> None:
    base = _make_base(tmp_path)

    updated = storage.update_base_product(base["id"], {"listing_title": "人工改过的标题"}, mark_edited=True)

    assert updated["last_edited_at"]
    assert _stage(base["id"]) == "uncopied"


def test_noop_operator_save_keeps_first_import(tmp_path: Path) -> None:
    """点「保存维护内容」但什么都没改：不该把商品挪进未复制。"""

    base = _make_base(tmp_path)

    updated = storage.update_base_product(
        base["id"],
        {"product_name_cn": base["product_name_cn"], "status": base["status"]},
        mark_edited=True,
    )

    assert not updated.get("last_edited_at")
    assert _stage(base["id"]) == "imported"


def test_copy_before_edit_is_pending_then_edit_falls_back_to_uncopied(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _make_template("A")

    storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert _stage(base["id"]) == "pending"

    # 复制之后再改：现有副本不含这次改动，退回未复制（副本 key 已存在，重新复制不会再建）。
    storage.update_base_product(base["id"], {"listing_title": "复制后又改的标题"}, mark_edited=True)

    assert _stage(base["id"]) == "uncopied"


def test_edit_before_copy_stays_pending_until_executed(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    storage.update_base_product(base["id"], {"listing_title": "复制前先改好"}, mark_edited=True)
    assert _stage(base["id"]) == "uncopied"

    template = _make_template("B")
    storage.copy_base_product_to_store_templates(base["id"], [template["id"]])

    assert _stage(base["id"]) == "pending"


def test_all_copies_saved_moves_to_completed(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    copies = []
    for suffix in ("A", "B"):
        template = _make_template(suffix)
        result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
        copies.extend(result["created"])
    for copy in copies:
        _prepare_copy_for_execution(copy["id"], base)
    assert _stage(base["id"]) == "pending"

    first_job = storage.create_automation_job_from_listing_copy(copies[0]["id"])
    _drive_job_to_saved(first_job["id"])
    assert _stage(base["id"]) == "pending"

    second_job = storage.create_automation_job_from_listing_copy(copies[1]["id"])
    _drive_job_to_saved(second_job["id"])

    assert _stage(base["id"]) == "completed"


def test_edit_after_completion_returns_to_uncopied(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    template = _make_template("A")
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["created"][0]
    _prepare_copy_for_execution(copy["id"], base)
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    _drive_job_to_saved(job["id"])
    assert _stage(base["id"]) == "completed"

    storage.update_base_product(base["id"], {"description": "完成之后又改详情"}, mark_edited=True)

    assert _stage(base["id"]) == "uncopied"


def test_put_endpoint_marks_the_product_edited(tmp_path: Path) -> None:
    """维护弹窗保存走 PUT /api/base-products/{id}，必须打编辑标记。"""

    base = _make_base(tmp_path)
    server, base_url = _start_api()
    try:
        payload = json.dumps({"listing_title": "从维护弹窗保存的标题"}).encode("utf-8")
        req = request.Request(
            f"{base_url}/api/base-products/{base['id']}",
            data=payload,
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=10) as response:
            assert response.status == 200
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()

    assert body["last_edited_at"]
    assert _stage(base["id"]) == "uncopied"
