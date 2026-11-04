from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib import error, request

import pytest
from cryptography.fernet import Fernet

from services.api import main, settings, storage, vision_listing
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


def _seed_base_product(product_id: str = "bp-1", **extra: object) -> dict:
    product = {
        "id": product_id,
        "group_id": "G1",
        "main_sku": "AL0001",
        "base_name": "男童卡通 T 恤",
        "category": "POD Mens T-Shirt",
        "listing_title": "",
        "recognized_attributes": {"Item Type": "T-Shirts", "Gender": "Boys"},
        "updated_at": "2026-10-01T00:00:00+00:00",
    }
    product.update(extra)
    with storage.locked_store() as (data, save):
        data.setdefault("base_products", []).append(product)
        save(data)
    return product


def test_keyword_library_batch_rows_flow_over_http() -> None:
    server, base = _start_api()
    try:
        status, library = _call("POST", base + "/api/keyword-libraries", {"name": "测试词库"})
        assert status == 201 and library["id"]

        status, rejected = _call("POST", base + "/api/keyword-libraries", {"name": "测试词库"})
        assert status == 422 and "已存在" in rejected["error"]

        paste = {
            "library_id": library["id"],
            "source": "测试表格",
            "source_type": "measured",
            "data_date": "2026-10-25",
            "text": "关键词\t搜索量\nboys tee\t200\ngift for mom\t150",
        }
        status, preview = _call("POST", base + "/api/keyword-import/preview", paste)
        assert status == 200 and preview["total"] == 2 and preview["errors"] == []
        assert preview["mapping"]["word"] == "关键词"

        # 重复预览同一内容 → 指向已有批次提示由 confirm 幂等保证。
        status, confirmed = _call("POST", base + "/api/keyword-import/confirm", {"preview_id": preview["preview_id"]})
        assert status == 201 and confirmed["duplicate"] is False
        status, again = _call("POST", base + "/api/keyword-import/confirm", {"preview_id": preview["preview_id"]})
        assert status == 201 and again["duplicate"] is True

        status, batches = _call("GET", base + f"/api/keyword-batches?library_id={library['id']}")
        assert status == 200 and len(batches["items"]) == 1

        status, rows = _call("GET", base + f"/api/keyword-rows?library_id={library['id']}&page_size=50")
        assert status == 200 and rows["total"] == 2
        assert rows["items"][0]["data_date"] == "2026-10-25"

        row_id = rows["items"][0]["id"]
        status, updated = _call("POST", base + "/api/keyword-rows", {"ids": [row_id], "changes": {"category": "tshirt"}})
        assert status == 200 and updated["updated"] == 1

        status, sorted_rows = _call("GET", base + f"/api/keyword-rows?library_id={library['id']}&sort=search_volume&direction=desc")
        assert status == 200
        assert sorted_rows["items"][0]["word"] == "boys tee"

        batch_id = confirmed["batch"]["id"]
        status, toggled = _call("PUT", base + f"/api/keyword-batches/{batch_id}", {"enabled": False})
        assert status == 200 and toggled["enabled"] is False
        status, missing = _call("PUT", base + "/api/keyword-batches/unknown", {"enabled": True})
        assert status == 404

        req = request.Request(base + f"/api/keyword-workbench/export?library_id={library['id']}&status=enabled")
        with request.urlopen(req, timeout=10) as response:
            payload = response.read()
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("application/vnd.openxmlformats")
        assert payload[:2] == b"PK"  # xlsx zip magic
    finally:
        server.shutdown()
        server.server_close()


def test_import_confirm_rejects_preview_with_errors() -> None:
    server, base = _start_api()
    try:
        status, library = _call("POST", base + "/api/keyword-libraries", {"name": "词库"})
        # 缺数据来源 → 预览带错误返回；confirm 必须拒绝。
        status, preview = _call("POST", base + "/api/keyword-import/preview", {
            "library_id": library["id"], "data_date": "2026-10-25",
            "text": "关键词\nplain word",
        })
        assert status == 200 and preview["errors"]
        status, rejected = _call("POST", base + "/api/keyword-import/confirm", {"preview_id": preview["preview_id"]})
        assert status == 422 and "修正" in rejected["error"]

        status, expired = _call("POST", base + "/api/keyword-import/confirm", {"preview_id": "missing"})
        assert status == 422
    finally:
        server.shutdown()
        server.server_close()


def test_title_workbench_products_history_and_initialize() -> None:
    server, base = _start_api()
    try:
        status, initialized = _call("POST", base + "/api/keyword-workbench/initialize", {})
        assert status == 200 and initialized["initialized"] is True
        status, repeated = _call("POST", base + "/api/keyword-workbench/initialize", {})
        assert status == 200 and repeated["initialized"] is False  # 幂等

        status, libraries = _call("GET", base + "/api/keyword-libraries")
        assert status == 200 and {row["id"] for row in libraries["items"]} >= {"ph-keywords"}
        status, batches = _call("GET", base + "/api/keyword-batches?library_id=ph-keywords")
        assert status == 200
        seed_batch = next(row for row in batches["items"] if row["id"] == "ph-keywords-20260920")
        assert seed_batch["data_date"] == "2026-09-20" and seed_batch["row_count"] == 1161

        status, rows = _call("GET", base + "/api/keyword-rows?batch_id=ph-keywords-20260920&page_size=5")
        assert status == 200 and rows["total"] == 1161 and len(rows["items"]) == 5

        _seed_base_product()
        with storage.locked_store() as (data, save):
            data.setdefault("assets", []).append({
                "id": "asset-1", "group_id": "G1", "asset_type": "main", "file_name": "front.png",
            })
            save(data)
        status, products = _call("GET", base + "/api/title-workbench/products")
        assert status == 200
        product = next(row for row in products["items"] if row["id"] == "bp-1")
        assert product["main_sku"] == "AL0001"
        assert product["image_url"].endswith("/api/assets/asset-1/thumb")

        status, history = _call("GET", base + "/api/title-workbench/history?product_id=bp-1")
        assert status == 200 and history["items"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_preview_uses_explicitly_selected_rule_even_when_scope_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预览面板手动选规则 = 「试这条规则」。

    导入瞬间商品范围常常还是 other/unknown（没有 Gender/Item Type 属性），
    这时按严格范围校验会把操作员的选择顶回去，报「不存在、已停用或不匹配」。
    手动选规则后应改用规则自身范围选词，并给出提示。
    """
    server, base = _start_api()
    try:
        _seed_base_product(
            "bp-unconfirmed",
            category="",
            recognized_attributes={"Material": "Cotton Blend"},
        )
        men = storage.create_title_skill({
            "name": "男装 T 恤", "category": "tshirt", "audience": "men", "is_default": True,
            "library_id": "", "fixed_terms": [], "banned_terms": [],
        })
        monkeypatch.setattr(
            storage, "get_default_ai_model_connection_private",
            lambda: {"id": "conn", "protocol": "anthropic_messages", "model_id": "test"},
        )
        monkeypatch.setattr(
            vision_listing, "generate_listing_from_images",
            lambda **kwargs: SimpleNamespace(title_candidates=[{
                "title": "Cartoon Print T Shirt for Men", "chars": 32, "status": "REVIEW",
                "warnings": [], "errors": [], "accepted_terms": [],
            }], error="", ok=True),
        )

        status, preview = _call("POST", base + "/api/title-workbench/preview", {
            "product_id": "bp-unconfirmed", "skill_id": men["id"],
        })
        assert status == 200
        assert preview["skill"]["id"] == men["id"]
        # 规则的范围接管本次预览的选词范围
        assert preview["context"]["scope"] == {"market": "PH", "category": "tshirt", "audience": "men"}
        assert any("指定规则" in warning for warning in preview["context"]["warnings"])

        # 操作员手动确认人群后，这份确认是权威的：与规则冲突仍然报错。
        status, rejected = _call("POST", base + "/api/title-workbench/preview", {
            "product_id": "bp-unconfirmed", "skill_id": men["id"], "audience": "kids",
        })
        assert status == 422
        assert "不匹配商品范围" in rejected["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_title_preview_and_apply_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    server, base = _start_api()
    try:
        _seed_base_product()
        storage.create_title_skill({
            "name": "童装 T 恤", "category": "tshirt", "audience": "kids", "is_default": True,
            "library_id": "", "fixed_terms": [], "banned_terms": [],
        })
        monkeypatch.setattr(
            storage, "get_default_ai_model_connection_private",
            lambda: {"id": "conn", "protocol": "anthropic_messages", "model_id": "test"},
        )
        stub_titles = [
            {"title": "Cartoon Print T Shirt for Kids Boy", "chars": 35, "status": "PASS", "warnings": [], "errors": [], "accepted_terms": [{"word": "boys tee"}]},
            {"title": "Kids Boy Cartoon Graphic Tee Daily Wear", "chars": 40, "status": "REVIEW", "warnings": ["低于目标长度"], "errors": [], "accepted_terms": []},
        ]
        captured: dict = {}

        def fake_generate(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(title_candidates=[dict(t) for t in stub_titles], error="", ok=True)

        monkeypatch.setattr(vision_listing, "generate_listing_from_images", fake_generate)

        status, preview = _call("POST", base + "/api/title-workbench/preview", {"product_id": "bp-1"})
        assert status == 200
        assert preview["titles"][0]["status"] == "PASS"
        assert preview["recommended_index"] == 0
        assert preview["skill"]["name"] == "童装 T 恤"
        # 商品属性 Gender=Boy 按新口径落到 boys；旧「童装」规则仍可匹配男童商品。
        assert preview["context"]["scope"]["audience"] == "boys"
        # 统一链路：预览同样带上 title_context 与候选数 3。
        assert captured["candidate_count"] == 3
        assert captured["title_context"]["scope"]["audience"] == "boys"

        status, history = _call("GET", base + "/api/title-workbench/history?product_id=bp-1")
        assert status == 200 and len(history["items"]) == 1
        assert history["items"][0]["source"] == "preview"

        status, applied = _call("POST", base + "/api/title-workbench/apply", {
            "product_id": "bp-1", "title": "Cartoon Print T Shirt for Kids Boy",
        })
        assert status == 200 and applied["listing_title"] == "Cartoon Print T Shirt for Kids Boy"
        stored = storage.get_item("base_products", "bp-1")
        assert stored["listing_title"] == "Cartoon Print T Shirt for Kids Boy"
        assert stored.get("last_edited_at")  # 采用标题算操作员编辑

        status, missing = _call("POST", base + "/api/title-workbench/preview", {"product_id": "nope"})
        assert status == 404
        status, blank = _call("POST", base + "/api/title-workbench/apply", {"product_id": "bp-1", "title": " "})
        assert status == 422
    finally:
        server.shutdown()
        server.server_close()
