from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mabang_page_capture_pod_ph.json"
BUSINESS_COLLECTIONS = (
    "mabang_store_options",
    "category_profiles",
    "category_profile_versions",
    "store_promotion_activities",
    "store_templates",
    "store_template_versions",
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


@pytest.fixture(autouse=True)
def _no_real_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    """页面导入采集结束后会把窗口切回设置里的模式；测试里绝不真的去起 Chrome。"""

    monkeypatch.setattr(
        "services.api.main.launch_mabang_browser",
        lambda cdp_url, *, profile_dir, headless=False, **kwargs: {
            "ok": True,
            "status": "started",
            "login_required": False,
            "browser_mode": "headless" if headless else "headed",
        },
    )


def _request_json(method: str, url: str, payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _capture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _business_state() -> dict:
    data = storage.load_store()
    return {key: data[key] for key in BUSINESS_COLLECTIONS}


def _create_run(base_url: str, *, expected_store_id: str = "") -> dict:
    status, run = _request_json(
        "POST",
        f"{base_url}/api/mabang-page-imports",
        {"platform": "shopee", "country": "PH", "conflict_action": "update", "expected_store_id": expected_store_id},
    )
    assert status == 201
    return run


def test_start_mabang_browser_uses_the_configured_local_cdp(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_launch(cdp_url: str, *, profile_dir: Path, headless: bool = False, **kwargs) -> dict:
        calls["cdp_url"] = cdp_url
        calls["profile_dir"] = profile_dir
        calls["headless"] = headless
        return {"ok": True, "status": "started", "login_required": True}

    monkeypatch.setattr("services.api.main.launch_mabang_browser", fake_launch)
    storage.save_runtime_configuration({"mabang": {"headless": True}})
    server, base_url = _start_api()
    try:
        status, payload = _request_json("POST", f"{base_url}/api/mabang-browser/start", {})

        assert status == 200
        assert payload["ok"] is True
        assert payload["status"] == "started"
        assert payload["login_required"] is True
        # 未绑定自动登录账号时只报告 no_credentials，不执行登录。
        assert payload["auto_login"] == {"status": "no_credentials", "error": ""}
        assert calls == {
            "cdp_url": "http://127.0.0.1:9223",
            "profile_dir": settings.STORAGE_DIR / "browser-use-profile",
            # 设置里的无头开关必须到达启动器，否则开关形同虚设。
            "headless": True,
        }
    finally:
        server.shutdown()
        server.server_close()


def test_active_page_import_session_tracks_status_age_and_account() -> None:
    """Worker 让路的判据：这个账号有没有「还没走完」的导入任务（且不能是陈年旧任务）。"""

    other = storage.create_mabang_account({"name": "账号B", "login_account": "acct-b"})
    run = storage.create_mabang_page_import({"platform": "shopee", "country": "PH"})
    assert storage.active_mabang_page_import()["id"] == run["id"]
    # 空 account_id（单账号时代）对任何账号都算「正在导入」
    assert storage.active_mabang_page_import(other["id"])["id"] == run["id"]

    # 采集完但还没确认导入：窗口还在被用，仍算进行中
    _set_run(run["id"], status="collected_with_warnings")
    assert storage.active_mabang_page_import()["id"] == run["id"]

    # 已确认导入 → 操作员已经离开弹窗，Worker 立刻恢复
    _set_run(run["id"], status="completed")
    assert storage.active_mabang_page_import() is None

    # 别的账号的导入不该挡住这个账号
    _set_run(run["id"], status="pending", account_id=other["id"])
    assert storage.active_mabang_page_import("acct-other") is None
    assert storage.active_mabang_page_import(other["id"])["id"] == run["id"]

    # 没人管的陈旧弹窗（超过 TTL）不再一直挡着 Worker
    stale = "2026-10-05T04:00:00+00:00"
    _set_run(run["id"], updated_at=stale, created_at=stale)
    assert storage.active_mabang_page_import(now="2026-10-05T04:10:00+00:00")["id"] == run["id"]
    assert storage.active_mabang_page_import(now="2026-10-05T05:00:00+00:00") is None


def _set_run(run_id: str, **changes) -> None:
    with storage.locked_store() as (data, save):
        target = next(item for item in data["mabang_page_import_runs"] if item["id"] == run_id)
        target.update(changes)
        save(data)


def test_active_page_import_endpoint_reports_the_running_session() -> None:
    server, base_url = _start_api()
    try:
        status, payload = _request_json("GET", f"{base_url}/api/mabang-page-imports/active")
        assert status == 200
        assert payload == {"active": False, "run": None}

        run = _create_run(base_url)
        status, payload = _request_json("GET", f"{base_url}/api/mabang-page-imports/active?account_id=")

        assert status == 200
        assert payload["active"] is True
        assert payload["run"]["id"] == run["id"]
    finally:
        server.shutdown()
        server.server_close()


def test_auto_login_during_a_page_import_reuses_the_window_instead_of_switching_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """导入期间 Worker/自动登录只能复用那只浏览器，不能按设置把它切成无头。

    现场问题（2026-10-05）：弹窗刚开出来的可见窗口，被 Worker 的自动登录按设置里的
    「无头」杀掉重起 → 操作员看到窗口自己关了。
    """

    storage.save_runtime_configuration({"mabang": {"headless": True}})
    calls: list[dict] = []

    def fake_launch(cdp_url, *, profile_dir, headless=False, **kwargs):
        calls.append({"headless": headless, **kwargs})
        return {"ok": True, "status": "running", "login_required": False, "browser_mode": "headed"}

    monkeypatch.setattr("services.api.main.launch_mabang_browser", fake_launch)

    server, base_url = _start_api()
    try:
        run = _create_run(base_url)

        status, _payload = _request_json("POST", f"{base_url}/api/mabang-browser/auto-login", {})
        assert status == 200
        assert calls[-1]["headless"] is True  # 设置里仍是无头……
        assert calls[-1]["reuse_running_only"] is True  # ……但只复用，不切模式

        # 弹窗自己「服务器 Chrome 打开」是显式要窗口：不受复用保护影响
        status, _payload = _request_json(
            "POST", f"{base_url}/api/mabang-browser/start", {"headless": False}
        )
        assert status == 200
        assert calls[-1]["headless"] is False
        assert calls[-1]["reuse_running_only"] is False

        # 导入确认后恢复原样：自动登录又按设置切模式
        _request_json("POST", f"{base_url}/api/mabang-browser/start", {"headless": False})
        _set_run(run["id"], status="completed")
        status, _payload = _request_json("POST", f"{base_url}/api/mabang-browser/auto-login", {})
        assert status == 200
        assert calls[-1]["headless"] is True
        assert calls[-1]["reuse_running_only"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_target_store_import_rejects_a_different_mabang_page_without_writing_business_data() -> None:
    server, base_url = _start_api()
    try:
        created = _create_run(base_url, expected_store_id="different-store")
        before = _business_state()

        status, payload = _request_json(
            "POST",
            f"{base_url}/api/mabang-page-imports/{created['id']}/complete",
            {"raw_capture": _capture()},
        )

        assert status == 422
        assert payload["error"] == "当前马帮页面店铺与目标店铺不一致"
        assert _business_state() == before
        _, run = _request_json("GET", f"{base_url}/api/mabang-page-imports/{created['id']}")
        assert run["status"] == "pending"
        assert "snapshot" not in run
    finally:
        server.shutdown()
        server.server_close()


def test_complete_stages_snapshot_without_writing_template() -> None:
    server, base_url = _start_api()
    try:
        created = _create_run(base_url)
        before = _business_state()

        status, staged = _request_json(
            "POST",
            f"{base_url}/api/mabang-page-imports/{created['id']}/complete",
            {"raw_capture": _capture()},
        )

        assert status == 201
        assert staged["status"] == "collected_with_warnings"
        assert staged["snapshot"]["store"]["id"] == "2022625115"
        assert staged["snapshot"]["page"]["url"] == "https://www.mabangerp.com/index.php"
        assert _business_state() == before
    finally:
        server.shutdown()
        server.server_close()


def test_collect_stages_snapshot_without_writing_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.api.main.run_mabang_page_import_collect",
        lambda plan, **kwargs: {"ok": True, "status": "completed", "capture": _capture()},
    )
    # 采集之后会自动补一次水印目录（best-effort）：测试里换掉，别真去连浏览器。
    _stub_collect(monkeypatch, {"ok": False, "status": "not_on_product_edit_page", "error": "测试里不采集"})
    server, base_url = _start_api()
    try:
        created = _create_run(base_url)
        before = _business_state()

        status, staged = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/collect", {}
        )

        assert status == 201
        assert staged["status"] == "collected_with_warnings"
        assert staged["snapshot"]["variation"]["sizes"] == ["S", "M", "L", "XL", "2XL"]
        assert _business_state() == before
    finally:
        server.shutdown()
        server.server_close()


def test_page_import_collect_refreshes_the_watermark_catalog_from_the_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """采集结果自带水印目录时直接写库，不再额外开浏览器。"""

    capture = _capture()
    capture["watermark_catalog_loaded"] = True
    capture["watermark_templates"] = [
        {"template_id": "wm-main", "name": "主图水印", "preview_url": "https://cdn.example/main.png"},
    ]
    monkeypatch.setattr(
        "services.api.main.run_mabang_page_import_collect",
        lambda plan, **kwargs: {"ok": True, "status": "completed", "capture": capture},
    )

    def fail_collect(**kwargs):  # pragma: no cover - 调用了就说明走了多余的浏览器采集
        raise AssertionError("采集结果已经带水印目录，不该再开一次浏览器")

    monkeypatch.setattr("services.api.main.run_store_watermark_collect", fail_collect)
    server, base_url = _start_api()
    try:
        created = _create_run(base_url)
        status, _staged = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/collect", {}
        )

        assert status == 201
        _, listed = _request_json("GET", f"{base_url}/api/store-watermarks?store_id=2022625115")
        assert [item["template_id"] for item in listed["items"]] == ["wm-main"]
        assert listed["status"]["status"] == "ok"
    finally:
        server.shutdown()
        server.server_close()


def test_page_import_collect_falls_back_to_the_playwright_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """采集结果里没有水印目录时，用 Playwright 采集器补一次。"""

    monkeypatch.setattr(
        "services.api.main.run_mabang_page_import_collect",
        lambda plan, **kwargs: {"ok": True, "status": "completed", "capture": _capture()},
    )
    calls: list[dict] = []

    def fake_collect(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "status": "ok",
            "catalog_loaded": True,
            "store": {"id": "2022625115", "name": "POD菲律宾"},
            "templates": [{"template_id": "wm-live", "name": "现场水印"}],
        }

    monkeypatch.setattr("services.api.main.run_store_watermark_collect", fake_collect)
    server, base_url = _start_api()
    try:
        created = _create_run(base_url)
        status, _staged = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/collect", {}
        )

        assert status == 201
        assert calls, "应该调用一次 Playwright 水印采集"
        _, listed = _request_json("GET", f"{base_url}/api/store-watermarks?store_id=2022625115")
        assert [item["template_id"] for item in listed["items"]] == ["wm-live"]
    finally:
        server.shutdown()
        server.server_close()


def test_complete_allows_recollecting_an_unconfirmed_run() -> None:
    server, base_url = _start_api()
    try:
        created = _create_run(base_url)
        status, first = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/complete", {"raw_capture": _capture()}
        )
        assert status == 201
        capture = _capture()
        capture["sku_rows"][0]["size"] = "XS"

        status, second = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/complete", {"raw_capture": capture}
        )

        assert status == 201
        assert first["snapshot"]["variation"]["sizes"][0] == "S"
        assert second["snapshot"]["variation"]["sizes"][0] == "XS"
        assert _business_state() == {key: [] for key in BUSINESS_COLLECTIONS}
    finally:
        server.shutdown()
        server.server_close()


def test_failed_collect_records_error_without_clearing_a_pending_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.api.main.run_mabang_page_import_collect",
        lambda plan, **kwargs: {"ok": False, "status": "blocked", "error": "采集环境未连接"},
    )
    server, base_url = _start_api()
    try:
        created = _create_run(base_url)

        status, payload = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/collect", {}
        )
        get_status, run = _request_json("GET", f"{base_url}/api/mabang-page-imports/{created['id']}")

        assert status == 422
        assert payload["error"] == "采集环境未连接"
        assert get_status == 200
        assert run["status"] == "pending"
        assert run["last_error"] == "采集环境未连接"
        assert "snapshot" not in run
    finally:
        server.shutdown()
        server.server_close()


def test_confirm_writes_business_data_once_after_a_staged_snapshot() -> None:
    server, base_url = _start_api()
    try:
        created = _create_run(base_url)
        status, staged = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/complete", {"raw_capture": _capture()}
        )
        assert status == 201
        assert staged["status"] == "collected_with_warnings"

        status, confirmed = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/confirm", {}
        )
        assert status == 201
        assert confirmed["template"]["sizes"] == ["S", "M", "L", "XL", "2XL"]
        assert confirmed["template"]["promotion_activity_id"] == ""

        status, repeated = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{created['id']}/confirm", {}
        )
        assert status == 200
        assert repeated["template"]["id"] == confirmed["template"]["id"]
        assert len(storage.load_store()["store_template_versions"]) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_mabang_page_import_rejects_incomplete_plan_and_deprecates_review() -> None:
    server, base_url = _start_api()
    try:
        status, payload = _request_json("POST", f"{base_url}/api/mabang-page-imports", {"country": "PH"})
        assert status == 422
        assert "platform" in payload["error"]

        status, payload = _request_json(
            "POST", f"{base_url}/api/category-profile-versions/old-version/publish", {}
        )
        assert status == 410
        assert "自动激活" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# 采集水印目录（POST /api/store-watermarks/collect）
#
# 2026-10-01 之前这条路由有两层问题：
#   1) 读的是 capture["watermark_catalog"]，而页面采集器输出 watermark_templates
#      → raw_items 恒为 []，"采集成功 0 条"把整店目录清空；
#   2) 采集本身走 browser-harness，而它的 daemon 在本机 Python 3.14 下起不来，
#      于是这条路由 100% 失败。
# 现在采集改走 Playwright/CDP（scripts/mabang_watermark_collect.cjs），下面几条把
# "写库 / 失败不覆盖 / 店铺不一致 / 结果缺字段" 四个分支钉死。
# ---------------------------------------------------------------------------

WATERMARK_STORE_ID = "2022625115"


def _seed_watermark_catalog(store_id: str = WATERMARK_STORE_ID) -> None:
    storage.replace_store_watermark_catalog(
        store_id,
        [{"template_id": "wm-old", "name": "旧水印", "preview_url": "https://cdn.example/old.png"}],
    )


def _collect(server, base_url: str, store_id: str = WATERMARK_STORE_ID):
    return _request_json("POST", f"{base_url}/api/store-watermarks/collect", {"store_id": store_id})


def _stub_collect(monkeypatch: pytest.MonkeyPatch, result: dict) -> None:
    """把 Playwright 水印采集器换掉（路由真正的接缝）。"""
    monkeypatch.setattr("services.api.main.run_store_watermark_collect", lambda **kwargs: result)


def test_collect_watermarks_writes_the_catalog_from_the_playwright_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_collect(monkeypatch, {
        "ok": True,
        "status": "ok",
        "catalog_loaded": True,
        "catalog_source": "vue+dom",
        "page_url": "https://publish.mabangerp.com/publish-ui/#/productDetailShopee?id=1",
        "store": {"id": WATERMARK_STORE_ID, "name": "PH 测试店铺"},
        "templates": [
            {"template_id": "wm-main", "name": "主图水印", "preview_url": "https://cdn.example/main.png"},
            {"template_id": "wm-product", "name": "产品图水印", "preview_url": "https://cdn.example/product.png"},
        ],
        "range_probe": {"candidates": [{"text": "第1张", "tag": "label"}]},
    })
    server, base_url = _start_api()
    try:
        status, payload = _collect(server, base_url)

        assert status == 200
        assert payload["count"] == 2
        assert sorted(item["template_id"] for item in payload["items"]) == ["wm-main", "wm-product"]
        assert payload["status"]["status"] == "ok"
        assert payload["status"]["count"] == 2
        assert payload["catalog_source"] == "vue+dom"
        # 采集时顺手读回来的「图片范围」结构要回给界面/排查用。
        assert payload["range_probe"]["candidates"][0]["text"] == "第1张"

        get_status, listed = _request_json("GET", f"{base_url}/api/store-watermarks?store_id={WATERMARK_STORE_ID}")
        assert get_status == 200
        assert sorted(item["template_id"] for item in listed["items"]) == ["wm-main", "wm-product"]
        assert listed["status"]["status"] == "ok"
        assert listed["status"]["collected_at"]
    finally:
        server.shutdown()
        server.server_close()


def test_collect_watermarks_failure_keeps_the_previous_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_watermark_catalog()
    _stub_collect(monkeypatch, {
        "ok": False,
        "status": "not_on_product_edit_page",
        "error": "当前浏览器没有打开 Shopee 商品编辑页。请先在马帮里打开一个商品（编辑），再点采集水印。",
    })
    server, base_url = _start_api()
    try:
        _, before = _request_json("GET", f"{base_url}/api/store-watermarks?store_id={WATERMARK_STORE_ID}")
        status, payload = _collect(server, base_url)

        assert status == 422
        assert "商品编辑页" in payload["error"]
        _, listed = _request_json("GET", f"{base_url}/api/store-watermarks?store_id={WATERMARK_STORE_ID}")
        assert [item["template_id"] for item in listed["items"]] == ["wm-old"], "失败不得覆盖旧目录"
        assert listed["status"]["status"] == "failed"
        assert "商品编辑页" in listed["status"]["error"]
        assert listed["status"]["count"] == 1
        # 失败不算一次「采集」：collected_at 仍是上次成功那次的时间。
        assert listed["status"]["collected_at"] == before["status"]["collected_at"]
    finally:
        server.shutdown()
        server.server_close()


def test_collect_watermarks_rejects_a_page_from_another_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_watermark_catalog()
    _stub_collect(monkeypatch, {
        "ok": True,
        "status": "ok",
        "catalog_loaded": True,
        "store": {"id": "some-other-store", "name": "别的店"},
        "templates": [{"template_id": "wm-x", "name": "别的店水印"}],
    })
    server, base_url = _start_api()
    try:
        status, payload = _collect(server, base_url)

        assert status == 422
        assert "不是要采集的店铺" in payload["error"]
        assert "别的店" in payload["error"]
        _, listed = _request_json("GET", f"{base_url}/api/store-watermarks?store_id={WATERMARK_STORE_ID}")
        assert [item["template_id"] for item in listed["items"]] == ["wm-old"]
    finally:
        server.shutdown()
        server.server_close()


def test_collect_watermarks_rejects_a_result_without_the_template_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """采集结果里没有模板列表时绝不写库：这正是旧代码把目录清空的路径。"""

    _seed_watermark_catalog()
    _stub_collect(monkeypatch, {
        "ok": True,
        "status": "ok",
        "catalog_loaded": True,
        "store": {"id": WATERMARK_STORE_ID, "name": "PH 测试店铺"},
        "templates": None,
    })
    server, base_url = _start_api()
    try:
        status, payload = _collect(server, base_url)

        assert status == 422
        assert "没有模板列表" in payload["error"]
        _, listed = _request_json("GET", f"{base_url}/api/store-watermarks?store_id={WATERMARK_STORE_ID}")
        assert [item["template_id"] for item in listed["items"]] == ["wm-old"]
        assert listed["status"]["status"] == "failed"
    finally:
        server.shutdown()
        server.server_close()


def test_page_import_collects_from_the_run_account_and_restores_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """采集要读 run 所属账号的 CDP 端口；采集完把窗口切回无头。"""

    # 设置里是「无头」，采集结束后必须恢复成无头。
    storage.save_runtime_configuration({"mabang": {"headless": True}})
    calls: dict[str, list] = {"cdp": [], "launches": []}

    def fake_collect(plan, *, cdp_url="", **kwargs):
        calls["cdp"].append(cdp_url)
        return {"ok": True, "capture": _capture()}

    def fake_launch(cdp_url, *, profile_dir, headless=False, **kwargs):
        calls["launches"].append({"cdp_url": cdp_url, "headless": headless})
        return {
            "ok": True,
            "status": "started",
            "login_required": False,
            "browser_mode": "headless" if headless else "headed",
        }

    monkeypatch.setattr("services.api.main.run_mabang_page_import_collect", fake_collect)
    monkeypatch.setattr("services.api.main.launch_mabang_browser", fake_launch)
    # 水印补采会另开浏览器，这里不需要，直接跳过。
    monkeypatch.setattr(
        "services.api.main.run_store_watermark_collect",
        lambda **kwargs: {"ok": False, "error": "skipped in test"},
    )

    server, base_url = _start_api()
    try:
        _status, account = _request_json(
            "POST",
            f"{base_url}/api/mabang/accounts",
            {"name": "账号A", "login_account": "acct-a"},
        )
        assert account["cdp_port"] == 9223

        _status, run = _request_json(
            "POST",
            f"{base_url}/api/mabang-page-imports",
            {
                "platform": "shopee",
                "country": "PH",
                "conflict_action": "update",
                "account_id": account["id"],
            },
        )
        assert run["account_id"] == account["id"]

        status, _collected = _request_json(
            "POST", f"{base_url}/api/mabang-page-imports/{run['id']}/collect", {}
        )
        assert status == 201

        # 采集读的是这个账号自动分配的端口（不是写死的 9223 默认值）
        assert calls["cdp"] == ["http://127.0.0.1:9223"]
        # 采集结束后把窗口恢复成设置里的模式（默认无头）
        assert calls["launches"][-1] == {"cdp_url": "http://127.0.0.1:9223", "headless": True}
    finally:
        server.shutdown()
        server.server_close()

