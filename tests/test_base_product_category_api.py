"""HTTP-level tests for the base-product expected category endpoints.

Covers:
- GET /api/marketplace-categories/merged (cross-store, no scope required)
- POST /api/category-quick-templates + GET + DELETE
- PUT /api/base-products/{id} persisting product_category
- POST /api/store-listing-copies/from-base surfacing category_mismatches
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _request_json(method: str, url: str, payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _seed_catalog_via_api(base_url: str) -> None:
    status, run = _request_json(
        "POST",
        f"{base_url}/api/marketplace-categories/collection-runs",
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
    )
    assert status == 201
    _request_json(
        "POST",
        f"{base_url}/api/marketplace-category-catalog-runs/{run['id']}/complete",
        {
            "discovery_source": "mabang_shopee_getCategory_api",
            "category_count": 2,
            "categories": [
                {
                    "id": "100",
                    "name": "Men Clothes",
                    "children": [{"id": "111", "name": "T-Shirts"}],
                }
            ],
        },
    )
    # Second store with the same leaf path to prove merged de-dup.
    status, run2 = _request_json(
        "POST",
        f"{base_url}/api/marketplace-categories/collection-runs",
        {"platform": "shopee", "country": "MY", "store_id": "store-2"},
    )
    assert status == 201
    _request_json(
        "POST",
        f"{base_url}/api/marketplace-category-catalog-runs/{run2['id']}/complete",
        {
            "discovery_source": "mabang_shopee_getCategory_api",
            "category_count": 2,
            "categories": [
                {
                    "id": "100",
                    "name": "Men Clothes",
                    "children": [{"id": "111", "name": "T-Shirts"}],
                }
            ],
        },
    )


def test_merged_categories_api_returns_deduped_leaf_catalog() -> None:
    server, base_url = _start_api()
    try:
        _seed_catalog_via_api(base_url)

        status, data = _request_json("GET", f"{base_url}/api/marketplace-categories/merged")
        assert status == 200
        # Same leaf path across both stores collapses to a single option.
        assert data["count"] == 1
        assert data["items"][0]["category_id"] == "111"
        assert "T-Shirts" in data["items"][0]["path"]

        status, filtered = _request_json("GET", f"{base_url}/api/marketplace-categories/merged?q=shirt")
        assert status == 200
        assert filtered["count"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_quick_templates_api_roundtrip() -> None:
    server, base_url = _start_api()
    try:
        status, created = _request_json(
            "POST",
            f"{base_url}/api/category-quick-templates",
            {
                "name": "男装T恤",
                "category": {"category_id": "111", "path": "Men Clothes > T-Shirts"},
                "attribute_values": {"100037": "35", "100134": ["1149"]},
            },
        )
        assert status == 201
        assert created["attribute_values"] == {"100037": "35", "100134": ["1149"]}
        template_id = created["id"]

        status, listed = _request_json("GET", f"{base_url}/api/category-quick-templates")
        assert status == 200
        assert len(listed["items"]) == 1
        assert listed["items"][0]["category"]["path"] == "Men Clothes > T-Shirts"
        assert listed["items"][0]["attribute_values"] == {"100037": "35", "100134": ["1149"]}

        status, removed = _request_json("DELETE", f"{base_url}/api/category-quick-templates/{template_id}")
        assert status == 200
        assert removed["id"] == template_id
    finally:
        server.shutdown()
        server.server_close()


def test_quick_templates_api_rejects_missing_category() -> None:
    server, base_url = _start_api()
    try:
        with pytest.raises(error.HTTPError) as exc_info:
            _request_json("POST", f"{base_url}/api/category-quick-templates", {"name": "缺类目"})
        assert exc_info.value.code == 422
    finally:
        server.shutdown()
        server.server_close()


def test_base_product_put_persists_product_category() -> None:
    folder = Path(settings.DATA_DIR) / "AL0501 接口类目"
    folder.mkdir(parents=True)
    (folder / "主图.jpg").write_bytes(b"img")
    (folder / "白色.jpg").write_bytes(b"img")
    base = storage.create_base_product_from_folder({"root_path": str(folder)})
    assert base is not None

    server, base_url = _start_api()
    try:
        status, updated = _request_json(
            "PUT",
            f"{base_url}/api/base-products/{base['id']}",
            {"product_category": {"category_id": "111", "path": "Men Clothes > T-Shirts", "store_id": "store-1"}},
        )
        assert status == 200
        assert updated["product_category"]["category_id"] == "111"
    finally:
        server.shutdown()
        server.server_close()


def test_from_base_surfaces_category_mismatch() -> None:
    folder = Path(settings.DATA_DIR) / "AL0502 接口复制"
    folder.mkdir(parents=True)
    (folder / "主图.jpg").write_bytes(b"img")
    (folder / "白色.jpg").write_bytes(b"img")
    base = storage.create_base_product_from_folder({"root_path": str(folder)})
    assert base is not None
    storage.update_base_product(
        base["id"],
        {"product_category": {"category_id": "111", "path": "Men Clothes > T-Shirts"}},
    )
    template = storage.create_store_template(
        {
            "store_name": "POD菲律宾",
            "template_name": "PH 模板",
            "mabang_store_id": "2022625115",
            "platform": "shopee",
            "country": "PH",
            "marketplace_category_path": "Women > Dresses",
        }
    )

    server, base_url = _start_api()
    try:
        status, result = _request_json(
            "POST",
            f"{base_url}/api/store-listing-copies/from-base",
            {"base_product_id": base["id"], "template_ids": [template["id"]]},
        )
        assert status == 201
        assert result["created_count"] == 1
        copy = result["created"][0]
        # Product filled a category → the copy uses it, template category ignored.
        assert copy["category_source"] == "base_product"
        assert copy["mabang_category_id"] == "111"
        assert copy["marketplace_category_path"] == "Men Clothes > T-Shirts"
        assert result["category_mismatch_count"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_merged_children_api_returns_cross_store_tree() -> None:
    server, base_url = _start_api()
    try:
        _seed_catalog_via_api(base_url)
        # Roots across both stores collapse to one (same category id 100).
        status, roots = _request_json("GET", f"{base_url}/api/marketplace-categories/children")
        assert status == 200
        root_ids = [item["category_id"] for item in roots["items"]]
        assert "100" in root_ids
        # Children of the Men Clothes root include the shared T-Shirts leaf.
        status, kids = _request_json("GET", f"{base_url}/api/marketplace-categories/children?parent_id=100")
        assert status == 200
        kid_ids = [item["category_id"] for item in kids["items"]]
        assert "111" in kid_ids
        # Chinese-name query filters children.
        status, filtered = _request_json("GET", f"{base_url}/api/marketplace-categories/children?parent_id=100&q=T-Shirts")
        assert status == 200
        assert [item["category_id"] for item in filtered["items"]] == ["111"]
    finally:
        server.shutdown()
        server.server_close()
