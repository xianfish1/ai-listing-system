from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "AI_CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode("ascii"))


def _skill_payload(**overrides: object) -> dict:
    payload = {
        "name": "Shopee 标题 · 主推款",
        "instructions": "标题开头放品类词",
        "title_min_chars": 40,
        "title_max_chars": 100,
        "hot_word_policy": "reference",
        "enabled": True,
        "is_default": False,
    }
    payload.update(overrides)
    return payload


def test_first_title_skill_becomes_default_automatically() -> None:
    first = storage.create_title_skill(_skill_payload())
    assert first["is_default"] is True
    second = storage.create_title_skill(_skill_payload(name="备选", is_default=True))
    assert second["is_default"] is True
    assert [row["is_default"] for row in storage.list_title_skills()] == [False, True]


def test_create_title_skill_rejects_bad_payloads() -> None:
    with pytest.raises(ValueError, match="名称"):
        storage.create_title_skill(_skill_payload(name="  "))
    with pytest.raises(ValueError, match="最少字数"):
        storage.create_title_skill(_skill_payload(title_min_chars=200))
    with pytest.raises(ValueError, match="最多字数"):
        storage.create_title_skill(_skill_payload(title_max_chars=5))
    with pytest.raises(ValueError, match="不能大于"):
        storage.create_title_skill(_skill_payload(title_min_chars=90, title_max_chars=80))
    with pytest.raises(ValueError, match="热词策略"):
        storage.create_title_skill(_skill_payload(hot_word_policy="always"))


def test_update_title_skill_validates_merged_result() -> None:
    skill = storage.create_title_skill(_skill_payload())
    with pytest.raises(ValueError, match="不能大于"):
        storage.update_title_skill(skill["id"], {"title_min_chars": 120})
    updated = storage.update_title_skill(skill["id"], {"title_min_chars": 50, "instructions": "新规则"})
    assert updated["title_min_chars"] == 50
    assert updated["instructions"] == "新规则"
    assert updated["version"] == skill["version"] + 1
    assert storage.update_title_skill("missing", {"name": "x"}) is None


def test_set_default_title_skill_rejects_disabled_and_keeps_unique() -> None:
    default = storage.create_title_skill(_skill_payload())
    other = storage.create_title_skill(_skill_payload(name="备选"))
    storage.update_title_skill(default["id"], {"enabled": False})
    with pytest.raises(ValueError, match="停用"):
        storage.set_default_title_skill(default["id"])
    selected = storage.set_default_title_skill(other["id"])
    assert selected["is_default"] is True
    assert [row["is_default"] for row in storage.list_title_skills()] == [False, True]


def test_delete_default_title_skill_transfers_to_first_enabled() -> None:
    first = storage.create_title_skill(_skill_payload())
    second = storage.create_title_skill(_skill_payload(name="备选"))
    disabled = storage.create_title_skill(_skill_payload(name="停用项"))
    storage.update_title_skill(disabled["id"], {"enabled": False})
    storage.set_default_title_skill(first["id"])
    storage.delete_title_skill(first["id"])
    rows = storage.list_title_skills()
    assert [row["is_default"] for row in rows] == [True, False]
    assert rows[0]["id"] == second["id"]


def test_get_active_title_skill_prefers_enabled_default() -> None:
    skill_a = storage.create_title_skill(_skill_payload())
    skill_b = storage.create_title_skill(_skill_payload(name="备选"))
    assert storage.get_active_title_skill()["id"] == skill_a["id"]
    storage.update_title_skill(skill_a["id"], {"enabled": False})
    active = storage.get_active_title_skill()
    assert active["id"] == skill_b["id"]
    storage.update_title_skill(skill_b["id"], {"enabled": False})
    assert storage.get_active_title_skill() is None
    assert storage.get_title_skill(skill_a["id"])["enabled"] is False


def test_hot_word_batch_add_dedupes_case_insensitively() -> None:
    result = storage.create_hot_words_batch(["Gift For Mom", "gift for mom", "  ", "new wave"])
    assert result == {"created": 2, "skipped": 2}
    result = storage.create_hot_words_batch(["GIFT FOR MOM", "beach cover up"], tag="夏季")
    assert result == {"created": 1, "skipped": 1}
    words = storage.list_hot_words()
    assert [row["word"] for row in words] == ["Gift For Mom", "new wave", "beach cover up"]
    assert words[-1]["tag"] == "夏季"


def test_hot_word_single_create_and_update_dedupe() -> None:
    created = storage.create_hot_word({"word": "oversized tee", "tag": "品类"})
    assert created["enabled"] is True
    with pytest.raises(ValueError, match="已存在"):
        storage.create_hot_word({"word": "Oversized Tee"})
    other = storage.create_hot_word({"word": "party dress"})
    with pytest.raises(ValueError, match="已存在"):
        storage.update_hot_word(other["id"], {"word": "oversized tee"})
    updated = storage.update_hot_word(other["id"], {"enabled": False, "word": "Party Dress"})
    assert updated["enabled"] is False
    assert updated["word"] == "Party Dress"
    with pytest.raises(ValueError, match="不能为空"):
        storage.update_hot_word(other["id"], {"word": "  "})
    assert storage.update_hot_word("missing", {"word": "x"}) is None


def test_list_enabled_hot_words_and_delete_all() -> None:
    storage.create_hot_words_batch(["alpha", "beta", "gamma"])
    disabled = storage.list_hot_words()[1]
    storage.update_hot_word(disabled["id"], {"enabled": False})
    assert storage.list_enabled_hot_words() == ["alpha", "gamma"]
    assert storage.delete_hot_word(disabled["id"]) == {"id": disabled["id"], "deleted": True}
    assert storage.delete_hot_word("missing") is None
    result = storage.delete_all_hot_words()
    assert result == {"deleted": 2}
    assert storage.list_hot_words() == []
    assert storage.list_enabled_hot_words() == []


@pytest.mark.parametrize("audience", ["boys", "girls", "men", "kids"])
def test_title_skill_accepts_new_and_legacy_audiences(audience: str) -> None:
    skill = storage.create_title_skill(_skill_payload(audience=audience))
    assert skill["audience"] == audience
