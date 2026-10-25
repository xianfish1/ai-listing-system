from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest

from services.api import settings
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


def test_category_catalog_collection_and_search_api_flow():
    server, base_url = _start_api()
    try:
        status, run = _request_json(
            "POST",
            f"{base_url}/api/marketplace-categories/collection-runs",
            {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        )
        assert status == 201

        status, completed = _request_json(
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
                ]
            },
        )
        assert status == 201
        assert completed["saved_count"] == 2

        status, catalog = _request_json(
            "GET",
            f"{base_url}/api/marketplace-categories?platform=shopee&country=PH&store_id=store-1&q=shirt&leaf_only=true",
        )
        assert status == 200
        assert catalog["count"] == 1
        assert catalog["items"][0]["category_id"] == "111"
        assert catalog["items"][0]["path"] == "Men Clothes > T-Shirts"

        status, roots = _request_json(
            "GET",
            f"{base_url}/api/marketplace-categories?platform=shopee&country=PH&store_id=store-1&parent_id=0&leaf_only=false",
        )
        assert status == 200
        assert [item["category_id"] for item in roots["items"]] == ["100"]
        assert roots["items"][0]["has_children"] is True

        status, children = _request_json(
            "GET",
            f"{base_url}/api/marketplace-categories?platform=shopee&country=PH&store_id=store-1&parent_id=100&leaf_only=false",
        )
        assert status == 200
        assert [item["category_id"] for item in children["items"]] == ["111"]
        assert children["items"][0]["has_children"] is False
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "query",
    [
        "country=PH&store_id=store-1",
        "platform=shopee&store_id=store-1",
        "platform=shopee&country=PH",
    ],
)
def test_category_catalog_search_api_requires_complete_store_scope(query: str):
    server, base_url = _start_api()
    try:
        with pytest.raises(error.HTTPError) as exc_info:
            _request_json("GET", f"{base_url}/api/marketplace-categories?{query}")
        assert exc_info.value.code == 422
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload["error"] == "platform, country and store_id are required"
    finally:
        server.shutdown()
        server.server_close()
