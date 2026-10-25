from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib import error, request

import pytest
from cryptography.fernet import Fernet

from services.api import main, settings
from services.api import storage
from services.api.model_gateway import GenerationResult
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "AI_CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode("ascii"))


def _call(method: str, url: str, payload: dict | None = None):
    raw = None if payload is None else json.dumps(payload).encode()
    req = request.Request(url, data=raw, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _source_store_template() -> dict:
    return {
        "id": "template-source",
        "store_name": "PH A",
        "platform": "shopee",
        "country": "PH",
        "currency": "PHP",
        "template_name": "Source",
        "mabang_store_id": "store-a",
        "mabang_category_id": "category-1",
        "marketplace_category_path": "Men > Tops",
        "category_profile_id": "profile-1",
        "category_profile_version_id": "version-1",
        "attribute_values": {"brand": "NoBrand"},
        "category_attributes": {"brand": "NoBrand"},
        "normal_price": 100,
        "sale_price": 60,
        "size_price_overrides": {"S": 90},
        "sizes": ["S", "M"],
        "sku_template": "{main_sku}_{color}||{size}",
        "stock": 20,
        "weight_kg": 0.35,
        "package_length_cm": 25,
        "package_width_cm": 20,
        "package_height_cm": 2,
        "description_profile_version_id": "description-v1",
        "size_chart_asset_id": "chart-1",
        "promotion_activity_id": "promotion-a",
        "mabang_discount_id": "promotion-a",
        "main_image_watermark": {"template_id": "wm-main", "name": "主图"},
        "product_image_watermark": {"template_id": "wm-product", "name": "产品图"},
        "version": 1,
        "current_version_id": "template-version-1",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _save_copy_targets() -> None:
    data = storage.load_store()
    data["store_templates"] = [_source_store_template()]
    data["mabang_store_options"] = [
        {"platform": "shopee", "country": "PH", "currency": "PHP", "store_name": "PH B", "mabang_store_id": "store-b"},
        {"platform": "shopee", "country": "SG", "currency": "SGD", "store_name": "SG", "mabang_store_id": "store-sg"},
    ]
    storage.save_store(data)


def test_completing_an_imported_cnsc_template_activates_it_and_keeps_capture_rows() -> None:
    data = storage.load_store()
    data["store_templates"] = [{
        "id": "cnsc-imported",
        "store_name": "广州小苗服饰有限公司",
        "platform": "shopee",
        "country": "GLOBAL",
        "currency": "CNY",
        "template_name": "CNSC 导入模板",
        "mabang_store_id": "cnsc-store",
        "mabang_category_id": "100244",
        "marketplace_category_path": "男装>上衣>T恤",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "target_site_stores": [],
        "sizes": ["S", "XL"],
        "price_tiers": [
            {"id": "standard", "sizes": ["S"], "global_price_cny": 19.2, "site_prices": {}},
            {"id": "large", "sizes": ["XL"], "global_price_cny": 22.2, "site_prices": {}},
        ],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        "cnsc_import_sku_rows": [{"seller_sku": "11991_A||S", "size": "S"}],
        "weight_kg": 0.3,
        "package_length_cm": 20,
        "package_width_cm": 15,
        "package_height_cm": 2,
        "status": "needs_configuration",
        "missing_configuration": ["target_site_stores", "site_price_matrix"],
        "version": 1,
        "created_at": "2026-08-20T00:00:00Z",
    }]
    storage.save_store(data)

    updated = storage.update_store_template("cnsc-imported", {
        "default_site_discount_percent": 40,
        "target_site_stores": [{
            "site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH",
            "mabang_discount_id": "discount-ph", "discount_percent": 40,
        }],
        "price_tiers": [
            {"id": "standard", "sizes": ["S"], "global_price_cny": 19.2, "site_prices": {"PH": {"sale_price": 746, "original_price": 1243}}},
            {"id": "large", "sizes": ["XL"], "global_price_cny": 22.2, "site_prices": {"PH": {"sale_price": 811, "original_price": 1352}}},
        ],
    })

    assert updated is not None
    assert updated["status"] == "active"
    assert updated["missing_configuration"] == []
    assert updated["cnsc_import_sku_rows"] == [{"seller_sku": "11991_A||S", "size": "S"}]


def test_cnsc_template_cannot_be_activated_without_explicit_warehouse_defaults() -> None:
    with pytest.raises(ValueError, match="warehouse_stock_defaults"):
        storage.create_store_template({
            "store_name": "广州小苗服饰有限公司",
            "template_name": "CNSC 缺少仓库配置",
            "listing_mode": "cnsc_global",
            "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
            "default_site_discount_percent": 40,
            "target_site_stores": [
                {
                    "site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH",
                    "mabang_discount_id": "discount-ph", "discount_percent": 40,
                },
            ],
            "sizes": ["S", "XL"],
            "price_tiers": [
                {
                    "id": "standard",
                    "sizes": ["S"],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 746, "original_price": 1243}},
                },
                {
                    "id": "large",
                    "sizes": ["XL"],
                    "global_price_cny": 22.2,
                    "site_prices": {"PH": {"sale_price": 811, "original_price": 1352}},
                },
            ],
        })


def test_cnsc_template_allows_missing_target_activity_as_needs_configuration() -> None:
    base = {
        "store_name": "广州小苗服饰有限公司",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "sizes": ["S"],
        "price_tiers": [{
            "id": "standard",
            "sizes": ["S"],
            "global_price_cny": 19.2,
            "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
        }],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
    }
    saved = storage.create_store_template({
        **base,
        "template_name": "CNSC 缺少站点活动",
        "default_site_discount_percent": 40,
        "target_site_stores": [{"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}],
    })
    assert saved["status"] == "needs_configuration"
    assert saved["missing_configuration"] == ["target_site_stores.PH.discount_activity"]

    with pytest.raises(ValueError, match="discount_percent"):
        storage.create_store_template({
            **base,
            "template_name": "CNSC 无效折扣",
            "default_site_discount_percent": 100,
            "target_site_stores": [{
                "site_code": "PH",
                "store_id": "ph-store",
                "store_name": "海宁PH",
                "mabang_discount_id": "discount-1",
            }],
        })


def test_cnsc_template_accepts_any_sale_price_off_percent_no_longer_checked() -> None:
    # 售价由 %OFF 自动算 → 模板保存不再校验 sale 与原价的折扣差。
    created = storage.create_store_template({
        "store_name": "广州小苗服饰有限公司",
        "template_name": "CNSC 任意售价",
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "default_site_discount_percent": 40,
        "target_site_stores": [{
            "site_code": "TW", "store_id": "tw-store", "store_name": "海宁TW",
            "mabang_discount_id": "discount-40",
        }],
        "sizes": ["S"],
        "price_tiers": [{
            "id": "standard", "sizes": ["S"], "global_price_cny": 19.2,
            "site_prices": {"TW": {"original_price": 372, "sale_price": 300}},
        }],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
    })
    assert created["template_name"] == "CNSC 任意售价"
def test_runtime_settings_api_is_persistent_and_publish_safe() -> None:
    server, base = _start_api()
    try:
        status, initial = _call("GET", base + "/api/settings")
        assert status == 200 and initial["safety"]["publish_allowed"] is False

        status, worker = _call("GET", base + "/api/worker/status")
        assert status == 200 and worker["status"] in {"stopped", "paused", "failed", "running"}

        status, updated = _call("PUT", base + "/api/settings", {
            "nas": {"asset_root": "X:/products"},
            "worker": {"cdp_url": "http://127.0.0.1:9223"},
        })
        assert status == 200
        assert updated["version"] == 1
        assert updated["nas"]["asset_root"] == "X:/products"
        assert updated["worker"]["restart_required"] is True

        status, audit = _call("GET", base + "/api/settings/audit")
        assert status == 200
        assert audit["items"][0]["version"] == 1
        assert audit["items"][0]["groups"] == ["nas", "worker"]

        status, rejected = _call("PUT", base + "/api/settings", {"safety": {"publish_allowed": True}})
        assert status == 422
        assert "禁止修改发布策略" in rejected["error"]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _reset_worker_wake_state():
    """唤醒状态是模块级全局，用例之间必须清干净，否则会读到上一条的旧结果。"""

    main._WORKER_WAKE_STATE.update({"ok": None, "action": "", "at": "", "error": ""})
    yield
    main._WORKER_WAKE_STATE.update({"ok": None, "action": "", "at": "", "error": ""})


def _wait_for_worker_wake(timeout: float = 5.0) -> dict:
    """唤醒 Worker 现在是后台线程（接口不再等它）：测试要等它跑完再看结果。"""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = dict(main._WORKER_WAKE_STATE)
        if state.get("at"):
            return state
        time.sleep(0.02)
    return dict(main._WORKER_WAKE_STATE)


def test_created_ready_jobs_start_worker_through_login_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "created": [{"id": "job-1", "status": "ready", "attempt_count": 0}],
        "created_count": 1,
    }
    monkeypatch.setattr(
        main,
        "_run_worker_action",
        lambda action: {
            "status": "running",
            "browser_mode": "headless",
            "auto_login": {"status": "logged_in", "error": ""},
        },
    )

    prepared = main._wake_worker_for_created_jobs(result)

    # 接口只触发唤醒（不再同步等 worker 起来），因此这里立刻就能返回
    assert prepared["worker_start"]["status"] == "starting"
    assert _wait_for_worker_wake()["ok"] is True
    assert prepared["created"][0]["status"] == "ready"
    assert prepared["created"][0]["attempt_count"] == 0


def test_created_ready_jobs_stay_unclaimed_when_login_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "created": [{"id": "job-1", "status": "ready", "attempt_count": 0}],
        "created_count": 1,
    }

    def fail(_action: str) -> dict:
        raise RuntimeError("马帮登录未确认：failed")

    monkeypatch.setattr(main, "_run_worker_action", fail)

    prepared = main._wake_worker_for_created_jobs(result)

    assert prepared["worker_start"]["status"] == "starting"
    wake = _wait_for_worker_wake()
    assert wake["ok"] is False and "马帮登录未确认" in wake["error"]
    assert prepared["created"][0]["status"] == "ready"
    assert prepared["created"][0]["attempt_count"] == 0


def test_created_ready_jobs_restart_a_live_internally_paused_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "created": [{"id": "job-1", "status": "ready", "attempt_count": 0}],
        "created_count": 1,
    }
    actions: list[str] = []
    supervisor = object()
    monkeypatch.setattr(main, "_worker_supervisor", lambda: supervisor)
    monkeypatch.setattr(main, "_latest_worker_activity", lambda current: {"activity": "paused"})
    monkeypatch.setattr(
        main,
        "_run_worker_action",
        lambda action: actions.append(action) or {"status": "running"},
    )

    prepared = main._wake_worker_for_created_jobs(result)

    wake = _wait_for_worker_wake()
    assert wake["action"] == "restart"
    assert actions == ["restart"]
    assert wake["ok"] is True


def test_created_ready_jobs_do_not_interrupt_an_active_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "created": [{"id": "job-1", "status": "ready", "attempt_count": 0}],
        "created_count": 1,
    }
    actions: list[str] = []
    monkeypatch.setattr(
        main,
        "_run_worker_action",
        lambda action: actions.append(action) or {
            "status": "running",
            "activity": "running",
            "already_running": True,
        },
    )

    prepared = main._wake_worker_for_created_jobs(result)

    wake = _wait_for_worker_wake()
    assert wake["action"] == "start"
    assert actions == ["start"]
    assert wake["outcome"]["already_running"] is True


def test_created_ready_jobs_ignore_paused_activity_from_previous_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "created": [{"id": "job-1", "status": "ready", "attempt_count": 0}],
        "created_count": 1,
    }
    actions: list[str] = []

    class Supervisor:
        def status(self):
            return {"status": "running", "updated_at": "2026-09-17T04:22:15Z"}

    supervisor = Supervisor()
    monkeypatch.setattr(main, "_worker_supervisor", lambda: supervisor)
    monkeypatch.setattr(
        main,
        "_latest_worker_activity",
        lambda current: {"activity": "paused", "activity_at": "2026-09-17T04:21:05Z"},
    )
    monkeypatch.setattr(
        main,
        "_run_worker_action",
        lambda action: actions.append(action) or {"status": "running", "already_running": True},
    )

    prepared = main._wake_worker_for_created_jobs(result)

    wake = _wait_for_worker_wake()
    assert wake["action"] == "start"
    assert actions == ["start"]
    assert wake["outcome"]["already_running"] is True


def test_save_as_template_clears_store_bound_values_same_site() -> None:
    _save_copy_targets()

    copied = storage.copy_store_template(
        "template-source",
        {"mabang_store_id": "store-b", "template_name": "PH B template"},
    )

    source = _source_store_template()
    assert copied["id"] != source["id"]
    assert copied["mabang_category_id"] == source["mabang_category_id"]
    assert copied["attribute_values"] == source["attribute_values"]
    assert copied["promotion_activity_id"] == ""
    assert copied["mabang_discount_id"] == ""
    assert copied["main_image_watermark"] == {}
    assert copied["product_image_watermark"] == {}
    assert storage.get_item("store_templates", "template-source") == source


def test_save_as_template_clears_site_bound_values_cross_site() -> None:
    _save_copy_targets()

    copied = storage.copy_store_template(
        "template-source",
        {"mabang_store_id": "store-sg", "template_name": "SG template"},
    )

    source = _source_store_template()
    assert copied["country"] == "SG"
    assert copied["currency"] == "SGD"
    assert copied["mabang_category_id"] == ""
    assert copied["marketplace_category_path"] == ""
    assert copied["category_profile_id"] == ""
    assert copied["category_profile_version_id"] == ""
    assert copied["attribute_values"] == {}
    assert copied["normal_price"] == ""
    assert copied["sale_price"] == ""
    assert copied["size_price_overrides"] == {}
    assert copied["sizes"] == source["sizes"]
    assert copied["weight_kg"] == source["weight_kg"]
    assert copied["size_chart_asset_id"] == source["size_chart_asset_id"]


def test_save_as_template_api_creates_target_copy_and_rejects_unknown_store() -> None:
    _save_copy_targets()
    server, base = _start_api()
    try:
        status, copied = _call(
            "POST",
            base + "/api/store-templates/template-source/copy",
            {"mabang_store_id": "store-b", "template_name": "PH B API template"},
        )
        assert status == 201
        assert copied["mabang_store_id"] == "store-b"
        assert copied["template_name"] == "PH B API template"

        status, error_body = _call(
            "POST",
            base + "/api/store-templates/template-source/copy",
            {"mabang_store_id": "missing-store", "template_name": "Invalid"},
        )
        assert status == 422
        assert error_body["error"] == "目标店铺尚未从马帮采集"
    finally:
        server.shutdown()
        server.server_close()


def test_duplicate_store_template_keeps_store_bound_values() -> None:
    _save_copy_targets()

    copied = storage.duplicate_store_template("template-source")

    source = _source_store_template()
    assert copied["id"] != source["id"]
    assert copied["current_version_id"] != source["current_version_id"]
    assert copied["template_name"] == "Source - 副本"
    assert copied["mabang_store_id"] == source["mabang_store_id"]
    # 同店复制与「另存为模板」相反：活动、水印、类目、价格全部照抄。
    assert copied["promotion_activity_id"] == source["promotion_activity_id"]
    assert copied["mabang_discount_id"] == source["mabang_discount_id"]
    assert copied["main_image_watermark"] == source["main_image_watermark"]
    assert copied["product_image_watermark"] == source["product_image_watermark"]
    assert copied["normal_price"] == source["normal_price"]
    assert copied["attribute_values"] == source["attribute_values"]
    assert storage.get_item("store_templates", "template-source") == source
    versions = [
        item
        for item in storage.load_store()["store_template_versions"]
        if item.get("template_id") == copied["id"]
    ]
    assert len(versions) == 1


def test_duplicate_store_template_numbers_extra_copies() -> None:
    _save_copy_targets()

    first = storage.duplicate_store_template("template-source")
    second = storage.duplicate_store_template("template-source")

    assert first["template_name"] == "Source - 副本"
    assert second["template_name"] == "Source - 副本 (2)"


def test_duplicate_store_template_api_creates_same_store_copy() -> None:
    _save_copy_targets()
    server, base = _start_api()
    try:
        status, copied = _call(
            "POST",
            base + "/api/store-templates/template-source/duplicate",
            {},
        )
        assert status == 201
        assert copied["mabang_store_id"] == "store-a"
        assert copied["template_name"] == "Source - 副本"

        status, error_body = _call(
            "POST",
            base + "/api/store-templates/missing-template/duplicate",
            {},
        )
        assert status == 404
        assert error_body["error"] == "Store template not found"
    finally:
        server.shutdown()
        server.server_close()


def test_store_template_list_filters_by_account() -> None:
    _save_copy_targets()
    data = storage.load_store()
    data["store_templates"] = [
        {**_source_store_template(), "id": "t-a", "account_id": "account-a"},
        {**_source_store_template(), "id": "t-b", "account_id": "account-b"},
    ]
    storage.save_store(data)
    server, base = _start_api()
    try:
        status, scoped = _call("GET", base + "/api/store-templates?account_id=account-a")
        assert status == 200
        assert [item["id"] for item in scoped["items"]] == ["t-a"]

        status, all_items = _call("GET", base + "/api/store-templates")
        assert status == 200
        assert {item["id"] for item in all_items["items"]} == {"t-a", "t-b"}
    finally:
        server.shutdown()
        server.server_close()


def test_delete_store_template_removes_unreferenced_template() -> None:
    _save_copy_targets()
    server, base = _start_api()
    try:
        status, payload = _call("DELETE", base + "/api/store-templates/template-source")

        assert status == 200
        assert payload == {"deleted": True, "archived": False, "id": "template-source"}
        assert storage.get_item("store_templates", "template-source") is None
    finally:
        server.shutdown()
        server.server_close()


def test_delete_store_template_archives_referenced_template_and_hides_it_from_listing() -> None:
    _save_copy_targets()
    data = storage.load_store()
    data["store_listing_copies"] = [{"id": "copy-1", "template_id": "template-source"}]
    storage.save_store(data)
    server, base = _start_api()
    try:
        status, payload = _call("DELETE", base + "/api/store-templates/template-source")

        assert status == 200
        assert payload == {"deleted": False, "archived": True, "id": "template-source"}
        assert storage.get_item("store_templates", "template-source")["status"] == "archived"
        assert storage.list_store_templates() == []
        assert storage.get_store_listing_copy_detail("copy-1") is not None
    finally:
        server.shutdown()
        server.server_close()


def test_update_store_template_preserves_imported_size_chart_media_when_payload_omits_it() -> None:
    imported = storage.create_store_template({
        "store_name": "PH A",
        "template_name": "已导入模板",
        "mabang_store_id": "store-a",
        "mabang_category_id": "category-1",
        "marketplace_category_path": "Men > Tops",
        "size_chart_source": "upload",
        "size_chart_asset_id": "chart-1",
        "size_chart_preview_url": "/api/uploads/size-chart/chart-1",
        "default_size_chart_path": "C:/charts/chart-1.png",
    })

    updated = storage.update_store_template(imported["id"], {"template_name": "已编辑"})

    assert updated is not None
    assert updated["size_chart_source"] == "upload"
    assert updated["size_chart_asset_id"] == "chart-1"
    assert updated["size_chart_preview_url"] == "/api/uploads/size-chart/chart-1"
    assert updated["default_size_chart_path"] == "C:/charts/chart-1.png"


def test_store_watermark_catalog_api_is_scoped_to_the_requested_store() -> None:
    data = storage.load_store()
    data["store_watermark_templates"] = [
        {"id": "local-a", "store_id": "store-a", "template_id": "wm-1", "name": "A", "status": "available"},
        {"id": "local-b", "store_id": "store-b", "template_id": "wm-1", "name": "B", "status": "available"},
    ]
    storage.save_store(data)
    server, base = _start_api()
    try:
        status, payload = _call("GET", base + "/api/store-watermarks?store_id=store-a")

        assert status == 200
        assert [(item["template_id"], item["name"]) for item in payload["items"]] == [("wm-1", "A")]
    finally:
        server.shutdown()
        server.server_close()


def test_listing_copy_video_can_be_replaced_and_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    data = storage.load_store()
    data["store_listing_copies"] = [{"id": "copy-video", "revision": 1, "created_at": "2026-01-01T00:00:00Z"}]
    storage.save_store(data)
    monkeypatch.setattr(
        "services.api.storage.validate_video",
        lambda path: {
            "mime_type": "video/mp4", "size_bytes": path.stat().st_size, "duration_seconds": 30,
            "width": 720, "height": 720, "validation_status": "valid", "validation_errors": [],
        },
    )
    content = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20
    server, base = _start_api()
    try:
        status, updated = _call("POST", base + "/api/store-listing-copies/copy-video/video", {
            "file_name": "商品视频.mp4", "content_type": "video/mp4",
            "content_base64": base64.b64encode(content).decode("ascii"),
        })
        assert status == 200
        assert updated["video"]["file_name"] == "商品视频.mp4"
        assert updated["video"]["validation_status"] == "valid"
        assert Path(updated["video"]["path"]).is_file()

        status, removed = _call("POST", base + "/api/store-listing-copies/copy-video/video/remove", {})
        assert status == 200
        assert removed["video"]["validation_status"] == "missing"
    finally:
        server.shutdown()
        server.server_close()


def test_model_connections_and_description_profiles_are_versioned_and_secret_safe() -> None:
    server, base = _start_api()
    try:
        status, connection = _call("POST", base + "/api/ai/model-connections", {
            "name": "Anthropic 兼容主模型", "protocol": "anthropic_messages",
            "base_url": "https://ai.example/v1", "api_key": "secret-key",
            "model_id": "model-a", "api_version": "2023-06-01",
        })
        assert status == 201
        assert connection["api_key_configured"] is True
        assert "api_key" not in connection and "encrypted_api_key" not in connection

        status, connections = _call("GET", base + "/api/ai/model-connections")
        assert status == 200 and connections["items"][0]["id"] == connection["id"]
        assert "secret-key" not in json.dumps(connections)

        status, profile = _call("POST", base + "/api/description-profiles", {
            "name": "Fly_Show 组合描述", "mode": "combined", "opening": "Welcome to Fly_Show",
            "after_sales": "Contact us first.", "protected_facts": {"shipping_time": "7-12 days"},
        })
        assert status == 201 and profile["version"] == 1
        status, updated = _call("PUT", base + f"/api/description-profiles/{profile['id']}", {"name": "组合描述 v2"})
        assert status == 200 and updated["version"] == 2
    finally:
        server.shutdown(); server.server_close()


def test_description_profile_default_must_be_enabled() -> None:
    server, base = _start_api()
    try:
        _, active = _call("POST", base + "/api/description-profiles", {
            "name": "Active", "mode": "combined", "enabled": True,
        })
        _, disabled = _call("POST", base + "/api/description-profiles", {
            "name": "Disabled", "mode": "full", "enabled": False,
        })

        status, selected = _call("POST", base + f"/api/description-profiles/{active['id']}/set-default", {})
        assert status == 200 and selected["is_default"] is True

        status, rejected = _call("POST", base + f"/api/description-profiles/{disabled['id']}/set-default", {})
        assert status == 422
        assert "停用" in rejected["error"]
    finally:
        server.shutdown(); server.server_close()


def test_size_chart_upload_checks_content_signature() -> None:
    server, base = _start_api()
    try:
        png = b"\x89PNG\r\n\x1a\n" + b"content"
        status, uploaded = _call("POST", base + "/api/uploads/size-chart", {
            "file_name": "chart.png", "content_type": "image/png",
            "content_base64": base64.b64encode(png).decode(),
        })
        assert status == 201
        assert uploaded["preview_url"].startswith("/api/uploads/size-chart/")
        assert "path" not in uploaded

        status, invalid = _call("POST", base + "/api/uploads/size-chart", {
            "file_name": "fake.png", "content_type": "image/png",
            "content_base64": base64.b64encode(b"not an image").decode(),
        })
        assert status == 422 and invalid["code"] == "invalid_image_signature"
    finally:
        server.shutdown(); server.server_close()


def test_model_default_rotation_listing_copy_edit_and_job_cancel() -> None:
    server, base = _start_api()
    try:
        status, connection = _call("POST", base + "/api/ai/model-connections", {
            "name": "模型 A", "protocol": "anthropic_messages", "base_url": "https://ai.example/v1",
            "api_key": "first-key", "model_id": "model-a",
        })
        assert status == 201
        status, defaulted = _call("POST", base + f"/api/ai/model-connections/{connection['id']}/set-default", {})
        assert status == 200 and defaulted["is_default"] is True
        status, rotated = _call("POST", base + f"/api/ai/model-connections/{connection['id']}/rotate-key", {"api_key": "second-key"})
        assert status == 200 and rotated["api_key_configured"] is True
        assert "second-key" not in json.dumps(rotated)

        data = storage.load_store()
        data["store_listing_copies"] = [{
            "id": "copy-1", "revision": 1, "description": "Old", "description_status": "ready",
            "profile_snapshot_hash": "hash", "created_at": "2026-01-01T00:00:00Z",
        }]
        data["automation_jobs"] = [{
            "id": "job-1", "listing_copy_id": "copy-1", "status": "ready", "run_log": [],
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }]
        storage.save_store(data)
        status, copy = _call("PUT", base + "/api/store-listing-copies/copy-1", {"title": "Reviewed title", "description": "Reviewed"})
        assert status == 200 and copy["revision"] == 2 and copy["description"] == "Reviewed"
        assert copy["listing_title"] == "Reviewed title"
        status, canceled = _call("POST", base + "/api/automation/jobs/job-1/cancel", {"reason": "重新检查"})
        assert status == 200 and canceled["status"] == "canceled"
    finally:
        server.shutdown(); server.server_close()


def test_openai_compatible_model_connection_can_be_edited_and_deleted() -> None:
    server, base = _start_api()
    try:
        status, _ = _call("POST", base + "/api/ai/model-connections", {
            "name": "Default", "protocol": "anthropic_messages",
            "base_url": "https://api.anthropic.example/v1", "api_key": "anthropic-key",
            "model_id": "claude-test",
        })
        assert status == 201
        status, connection = _call("POST", base + "/api/ai/model-connections", {
            "name": "DeepSeek", "protocol": "openai_chat_completions",
            "base_url": "https://api.deepseek.example/v1", "api_key": "deepseek-key",
            "model_id": "deepseek-chat",
        })
        assert status == 201
        assert connection["protocol"] == "openai_chat_completions"
        assert connection["api_version"] == ""

        status, updated = _call("PUT", base + f"/api/ai/model-connections/{connection['id']}", {
            "name": "DeepSeek V3", "model_id": "deepseek-reasoner",
        })
        assert status == 200
        assert updated["name"] == "DeepSeek V3"
        assert updated["model_id"] == "deepseek-reasoner"
        assert "deepseek-key" not in json.dumps(updated)

        status, deleted = _call("DELETE", base + f"/api/ai/model-connections/{connection['id']}")
        assert status == 200
        assert deleted["deleted"] is True
    finally:
        server.shutdown(); server.server_close()


def test_chat_completions_endpoint_protocol_connection_round_trips() -> None:
    server, base = _start_api()
    try:
        # First connection becomes the default and cannot be deleted; the
        # chat_completions one is created second so it is deletable.
        status, _ = _call("POST", base + "/api/ai/model-connections", {
            "name": "Default", "protocol": "anthropic_messages",
            "base_url": "https://api.anthropic.example/v1", "api_key": "anthropic-key",
            "model_id": "claude-test",
        })
        assert status == 201
        status, connection = _call("POST", base + "/api/ai/model-connections", {
            "name": "CommandCode", "protocol": "chat_completions",
            "base_url": "https://api.commandcode.ai/provider/v1/chat/completions",
            "api_key": "cc-key", "model_id": "z-ai/glm-5.3-flash",
        })
        assert status == 201
        assert connection["protocol"] == "chat_completions"
        assert connection["api_version"] == ""
        assert connection["base_url"] == "https://api.commandcode.ai/provider/v1/chat/completions"
        assert "cc-key" not in json.dumps(connection)

        status, updated = _call("PUT", base + f"/api/ai/model-connections/{connection['id']}", {
            "name": "CommandCode renamed",
        })
        assert status == 200 and updated["name"] == "CommandCode renamed"
        assert updated["protocol"] == "chat_completions"

        status, deleted = _call("DELETE", base + f"/api/ai/model-connections/{connection['id']}")
        assert status == 200 and deleted["deleted"] is True
    finally:
        server.shutdown(); server.server_close()


def test_listing_copy_detail_api_updates_sku_price_and_transaction_fields() -> None:
    data = storage.load_store()
    data["store_listing_copies"] = [{
        "id": "copy-1",
        "revision": 1,
        "listing_title": "Old title",
        "description": "Ready description",
        "description_status": "ready",
        "store_name": "POD PH",
        "mabang_store_id": "store-1",
        "marketplace_category_path": "Men > Tops",
        "mabang_category_id": "category-1",
        "mabang_discount_id": "promotion-1",
        "promotion_activity_id": "promotion-1",
        "main_image": "main.jpg",
        "product_images": ["main.jpg", "model-1.jpg"],
        "asset_root": "G:/products/DL0001",
        "size_chart_image": "size-chart.png",
        "description_profile_version_id": "description-v1",
        "stock": 20,
        "weight_kg": 0.35,
        "package_length_cm": 25,
        "package_width_cm": 20,
        "package_height_cm": 2,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }]
    data["listing_skus"] = [{
        "id": "sku-1", "listing_copy_id": "copy-1", "sku_code": "DL0001-WHITE-S",
        "color_code": "White", "size": "S", "stock": 20,
    }]
    data["listing_prices"] = [{
        "id": "price-1", "listing_copy_id": "copy-1", "sku_id": "sku-1",
        "sku_code": "DL0001-WHITE-S", "normal_price": 180, "sale_price": 108,
        "cost_price": 12.5, "price_source": "base_product",
    }]
    storage.save_store(data)

    server, base = _start_api()
    try:
        status, detail = _call("GET", base + "/api/store-listing-copies/copy-1")
        assert status == 200
        assert detail["skus"][0]["price"]["normal_price"] == 180
        assert detail["readiness"]["ready"] is True

        status, updated = _call(
            "PUT",
            base + "/api/store-listing-copies/copy-1",
            {
                "title": "Reviewed title",
                "weight_kg": 0.42,
                "package_length_cm": 30,
                "skus": [{"id": "sku-1", "stock": 8}],
                "prices": [{"id": "price-1", "sku_id": "sku-1", "normal_price": 90, "sale_price": 54}],
            },
        )
        assert status == 200
        assert updated["listing_title"] == "Reviewed title"
        assert updated["weight_kg"] == 0.42
        assert updated["package_length_cm"] == 30
        assert updated["skus"][0]["stock"] == 8
        assert updated["skus"][0]["price"]["sale_price"] == 54

        status, invalid = _call(
            "PUT",
            base + "/api/store-listing-copies/copy-1",
            {"skus": [{"id": "foreign-sku", "stock": 1}]},
        )
        assert status == 422
        assert invalid["error"] == "SKU 不属于当前店铺刊登副本"
    finally:
        server.shutdown(); server.server_close()


def test_description_generation_api_updates_copy_and_appends_history(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_generate(self, *, connection, api_key, request):
        assert api_key == "secret-key"
        return GenerationResult(
            text="Soft hoodie for daily wear.", model_id=connection.model_id,
            stop_reason="end_turn", usage={"input_tokens": 4, "output_tokens": 6},
        )

    monkeypatch.setattr("services.api.anthropic_messages_adapter.AnthropicMessagesAdapter.generate", fake_generate)
    server, base = _start_api()
    try:
        _, connection = _call("POST", base + "/api/ai/model-connections", {
            "name": "模型 A", "protocol": "anthropic_messages", "base_url": "https://ai.example/v1",
            "api_key": "secret-key", "model_id": "model-a", "is_default": True,
        })
        _, profile = _call("POST", base + "/api/description-profiles", {
            "name": "组合描述", "mode": "combined", "opening": "Welcome",
            "after_sales": "Contact us first.",
        })
        data = storage.load_store()
        data["store_listing_copies"] = [{
            "id": "copy-1", "revision": 1, "product_name": "Hoodie", "package_item": "Hoodie",
            "description": "", "description_status": "pending", "created_at": "2026-01-01T00:00:00Z",
        }]
        storage.save_store(data)

        status, generated = _call(
            "POST", base + "/api/store-listing-copies/copy-1/description/generate",
            {},
        )
        assert status == 200 and generated["status"] == "ready"
        assert generated["description"] == "Welcome\n\nSoft hoodie for daily wear.\n\nContact us first."
        saved = storage.get_item("store_listing_copies", "copy-1")
        assert saved["description_status"] == "ready" and saved["revision"] == 2
        assert saved["description_profile_version_id"] == profile["current_version_id"]
        assert len(storage.list_ai_outputs_for_product("copy-1", "listing_description")) == 1
    finally:
        server.shutdown(); server.server_close()


def test_model_connection_check_reserves_text_tokens_for_reasoning_models(monkeypatch: pytest.MonkeyPatch) -> None:
    observed = {}

    def fake_generate(self, *, connection, api_key, request):
        observed["max_tokens"] = request.max_tokens
        return GenerationResult(
            text="OK", model_id=connection.model_id,
            stop_reason="end_turn", usage={"input_tokens": 4, "output_tokens": 2},
        )

    monkeypatch.setattr("services.api.anthropic_messages_adapter.AnthropicMessagesAdapter.generate", fake_generate)
    server, base = _start_api()
    try:
        status, connection = _call("POST", base + "/api/ai/model-connections", {
            "name": "Reasoning model", "protocol": "anthropic_messages", "base_url": "https://ai.example/v1",
            "api_key": "secret-key", "model_id": "model-a",
        })
        assert status == 201

        status, result = _call("POST", base + f"/api/ai/model-connections/{connection['id']}/test", {})

        assert status == 200
        assert result["ok"] is True
        assert observed["max_tokens"] == 64
    finally:
        server.shutdown(); server.server_close()
