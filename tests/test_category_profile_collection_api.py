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
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _capture():
    return {
        "platform": "shopee",
        "country": "PH",
        "marketplace_category_id": "900001",
        "observed_category_id": "900001",
        "store_id": "store-1",
        "marketplace_category_path": "Baby & Kids Fashion > Tops",
        "display_name": "Kids Tops",
        "page_url": "https://www.mabangerp.com/category/900001",
        "fields": [
            {
                "field_id": "age_group",
                "label": "Age Group",
                "type": "single_select",
                "required": True,
                "options": [{"value": "kids", "label": "Kids"}],
                "automation": {"page_field_id": "age_group"},
            }
        ],
        "variation": {"required": True, "axes": ["color", "size"]},
    }


def test_category_collection_activates_version_api_flow():
    server, base_url = _start_api()
    try:
        status, run = _request_json(
            "POST",
            f"{base_url}/api/category-profiles/collection-runs",
            {"country": "PH", "category_id": "900001", "store_id": "store-1"},
        )
        assert status == 201

        status, completed = _request_json(
            "POST",
            f"{base_url}/api/category-profile-collection-runs/{run['id']}/complete",
            {"raw_capture": _capture()},
        )
        assert status == 201
        version_id = completed["version"]["id"]
        assert completed["version"]["status"] == "active"
        assert completed["run"]["status"] == "completed"

        status, versions = _request_json(
            "GET",
            f"{base_url}/api/category-profiles/{completed['profile']['id']}/versions",
        )
        assert status == 200
        assert versions["items"][0]["id"] == version_id

        status, validation = _request_json(
            "POST",
            f"{base_url}/api/category-profile-versions/{version_id}/validate",
            {"values": {}},
        )
        assert status == 200
        assert validation["valid"] is False
        assert validation["errors"][0]["field_id"] == "age_group"

    finally:
        server.shutdown()
        server.server_close()


def test_collection_api_rejects_incomplete_run():
    server, base_url = _start_api()
    try:
        status, payload = _request_json(
            "POST",
            f"{base_url}/api/category-profiles/collection-runs",
            {"country": "PH"},
        )
        assert status == 422
        assert "category_id" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
