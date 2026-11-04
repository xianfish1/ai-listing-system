from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

import pytest
from cryptography.fernet import Fernet

from services.api import settings
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


def test_title_skills_crud_over_http() -> None:
    server, base = _start_api()
    try:
        status, data = _call("GET", base + "/api/title-skills")
        assert status == 200 and data == {"items": []}

        status, first = _call("POST", base + "/api/title-skills", {
            "name": "主推款标题", "title_min_chars": 40, "title_max_chars": 100,
            "hot_word_policy": "reference", "instructions": "开头放品类词",
        })
        assert status == 201
        assert first["is_default"] is True  # 第一个 Skill 自动成为默认

        status, second = _call("POST", base + "/api/title-skills", {
            "name": "清仓款标题", "is_default": True,
        })
        assert status == 201 and second["is_default"] is True

        status, items = _call("GET", base + "/api/title-skills")
        assert status == 200
        assert [row["is_default"] for row in items["items"]] == [False, True]

        status, updated = _call("PUT", base + f"/api/title-skills/{first['id']}", {"title_min_chars": 50})
        assert status == 200 and updated["title_min_chars"] == 50

        status, rejected = _call("PUT", base + f"/api/title-skills/{first['id']}", {"title_min_chars": 300})
        assert status == 422 and "最少字数" in rejected["error"]

        status, missing = _call("PUT", base + "/api/title-skills/unknown-id", {"name": "x"})
        assert status == 404

        status, selected = _call("POST", base + f"/api/title-skills/{first['id']}/set-default", {})
        assert status == 200 and selected["is_default"] is True

        status, deleted = _call("DELETE", base + f"/api/title-skills/{second['id']}")
        assert status == 200
        status, items = _call("GET", base + "/api/title-skills")
        assert len(items["items"]) == 1
        assert items["items"][0]["is_default"] is True

        status, _ = _call("DELETE", base + f"/api/title-skills/{first['id']}")
        assert status == 200
        status, items = _call("GET", base + "/api/title-skills")
        assert items["items"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_hot_words_endpoints_over_http() -> None:
    server, base = _start_api()
    try:
        status, created = _call("POST", base + "/api/hot-words", {"word": "oversized tee", "tag": "品类"})
        assert status == 201 and created["word"] == "oversized tee"

        status, duplicate = _call("POST", base + "/api/hot-words", {"word": "Oversized Tee"})
        assert status == 422 and "已存在" in duplicate["error"]

        # 批量：大小写去重 + 空行跳过。
        status, batch = _call("POST", base + "/api/hot-words", {
            "words": ["Gift For Mom", "gift for mom", "  ", "new wave"], "tag": "节日",
        })
        assert status == 201 and batch == {"created": 2, "skipped": 2}

        status, items = _call("GET", base + "/api/hot-words")
        assert status == 200
        words = {row["word"] for row in items["items"]}
        assert words == {"oversized tee", "Gift For Mom", "new wave"}

        first_id = items["items"][0]["id"]
        status, toggled = _call("PUT", base + f"/api/hot-words/{first_id}", {"enabled": False})
        assert status == 200 and toggled["enabled"] is False

        other_id = next(row["id"] for row in items["items"] if row["word"] == "new wave")
        status, clash = _call("PUT", base + f"/api/hot-words/{other_id}", {"word": "oversized tee"})
        assert status == 422 and "已存在" in clash["error"]

        status, missing = _call("PUT", base + "/api/hot-words/unknown-id", {"word": "x"})
        assert status == 404

        status, deleted = _call("DELETE", base + f"/api/hot-words/{first_id}")
        assert status == 200

        status, cleared = _call("DELETE", base + "/api/hot-words")
        assert status == 200 and cleared == {"deleted": 2}

        status, items = _call("GET", base + "/api/hot-words")
        assert items == {"items": []}
    finally:
        server.shutdown()
        server.server_close()
