from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "AI_CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode())


def create(**changes):
    return storage.create_title_skill({"name": "标题规则", **changes})


def test_defaults_are_unique_within_scope_not_globally():
    kids = create(category="tshirt", audience="kids")
    men = create(category="tshirt", audience="men")
    replacement = create(category="tshirt", audience="kids", is_default=True)
    assert storage.get_title_skill(kids["id"])["is_default"] is False
    assert storage.get_title_skill(men["id"])["is_default"] is True
    assert replacement["is_default"] is True
    assert storage.get_title_skill(kids["id"])["version"] == 2


def test_scope_move_repairs_old_default_and_respects_new_default():
    kids = create(category="tshirt", audience="kids")
    other = create(category="tshirt", audience="kids")
    men = create(category="tshirt", audience="men")
    changed = storage.update_title_skill(kids["id"], {"audience": "men", "is_default": False})
    assert changed["is_default"] is False
    assert storage.get_title_skill(other["id"])["is_default"] is True
    assert storage.get_title_skill(men["id"])["is_default"] is True


def test_disabled_only_scope_has_no_default_and_reenable_repairs():
    rule = create(enabled=False)
    assert rule["is_default"] is False
    rule = storage.update_title_skill(rule["id"], {"enabled": True})
    assert rule["is_default"] is True
    storage.update_title_skill(rule["id"], {"enabled": False})
    assert storage.get_title_skill(rule["id"])["is_default"] is False


def test_extended_fields_roundtrip_and_term_normalization():
    rule = create(category="tshirt", audience="men", language="taglish", ranking="growth",
                  library_id="library-1", max_age_days=14,
                  structure=["core", "theme", "attributes", "audience"],
                  fixed_terms=["  Graphic  Print ", "graphic print", ""],
                  banned_terms=["COD"], operator_allowances=["100% Cotton"], examples="sample")
    assert rule["fixed_terms"] == ["Graphic Print"]
    assert rule["operator_allowances"] == ["100% Cotton"]
    assert storage.get_title_skill(rule["id"])["max_age_days"] == 14


@pytest.mark.parametrize("changes", [
    {"market": "SG"}, {"category": "shoe"}, {"audience": "baby"},
    {"language": "zh"}, {"ranking": "random"}, {"max_age_days": 0},
    {"max_age_days": 2.5}, {"max_age_days": True}, {"fixed_terms": "word"},
    {"banned_terms": [12]}, {"structure": ["core", "core"]},
    {"title_max_chars": 101}, {"enabled": "false"},
])
def test_invalid_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        create(**changes)


def test_deletion_transfers_default_only_in_deleted_scope():
    kids = create(audience="kids")
    other = create(audience="kids")
    men = create(audience="men")
    storage.delete_title_skill(kids["id"])
    assert storage.get_title_skill(other["id"])["is_default"] is True
    assert storage.get_title_skill(men["id"])["is_default"] is True


def test_product_rule_binding_validation_and_roundtrip():
    rule = create(audience="men")
    with storage.locked_store() as (data, save):
        data["base_products"] = [{"id": "product-1"}]
        save(data)
    product = storage.update_base_product("product-1", {
        "title_skill_id": rule["id"], "title_category": "tshirt", "title_audience": "men"})
    assert product["title_skill_id"] == rule["id"]
    assert product["title_category"] == "tshirt"
    with pytest.raises(ValueError):
        storage.update_base_product("product-1", {"title_skill_id": "missing"})
    with pytest.raises(ValueError):
        storage.update_base_product("product-1", {"title_audience": "invalid"})
