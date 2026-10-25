"""店铺模板按马帮账号隔离：归属推导、列表过滤、跨账号防护与历史回填。

现场口径（2026-10-14）：模板卡在某个马帮账号下；两个账号可能有同名店铺，
所以模板不能只认 mabang_store_id，必须记住 account_id，列表也按账号过滤。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from services.api import settings
from services.api import storage


ACCOUNT_A = "account-a"
ACCOUNT_B = "account-b"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "AI_CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode("ascii"))


def _seed(*, store_accounts: dict[str, list[str]], templates: list[dict] | None = None) -> dict:
    data = storage.load_store()
    data["mabang_accounts"] = [
        {"id": ACCOUNT_A, "name": "账号A", "login_account": "13714205569", "enabled": True, "cdp_port": 9223},
        {"id": ACCOUNT_B, "name": "账号B", "login_account": "15915789598", "enabled": True, "cdp_port": 9224},
    ]
    options = []
    for store_id, accounts in store_accounts.items():
        for index, account_id in enumerate(accounts):
            options.append(
                {
                    "id": f"{store_id}-{account_id}",
                    "account_id": account_id,
                    "platform": "shopee",
                    "country": "PH",
                    "store_name": f"HN_POD{'' if index == 0 else f'-{index + 1}'}",
                    "mabang_store_id": store_id,
                    "status": "active",
                }
            )
    data["mabang_store_options"] = options
    data["store_templates"] = templates or []
    storage.save_store(data)
    return data


def _template_payload(**overrides) -> dict:
    payload = {
        "template_name": "Mens Tee",
        "store_name": "HN_POD",
        "platform": "shopee",
        "country": "PH",
        "currency": "PHP",
        "mabang_store_id": "store-1",
        "sizes": ["S", "M"],
        "stock": 20,
        "weight_kg": 0.3,
        "package_length_cm": 25,
        "package_width_cm": 20,
        "package_height_cm": 2,
    }
    payload.update(overrides)
    return payload


def test_create_store_template_records_the_selected_account() -> None:
    _seed(store_accounts={"store-1": [ACCOUNT_A]})

    template = storage.create_store_template(_template_payload(account_id=ACCOUNT_A))

    assert template["account_id"] == ACCOUNT_A


def test_create_store_template_derives_the_account_from_the_store() -> None:
    # 前端没带账号时按店铺归属推导：店铺只属于一个账号，不需要人工选。
    _seed(store_accounts={"store-1": [ACCOUNT_B]})

    template = storage.create_store_template(_template_payload())

    assert template["account_id"] == ACCOUNT_B


def test_create_store_template_rejects_a_store_owned_by_another_account() -> None:
    _seed(store_accounts={"store-1": [ACCOUNT_A]})

    with pytest.raises(ValueError, match="属于其它马帮账号"):
        storage.create_store_template(_template_payload(account_id=ACCOUNT_B))


def test_create_store_template_refuses_to_guess_when_two_accounts_share_a_store() -> None:
    _seed(store_accounts={"store-1": [ACCOUNT_A, ACCOUNT_B]})

    with pytest.raises(ValueError, match="多个马帮账号"):
        storage.create_store_template(_template_payload())


def test_list_store_templates_filters_by_account() -> None:
    _seed(store_accounts={"store-1": [ACCOUNT_A], "store-2": [ACCOUNT_B]})
    storage.create_store_template(_template_payload(template_name="A 模板", mabang_store_id="store-1", account_id=ACCOUNT_A))
    storage.create_store_template(_template_payload(template_name="B 模板", mabang_store_id="store-2", account_id=ACCOUNT_B))

    assert [item["template_name"] for item in storage.list_store_templates(account_id=ACCOUNT_A)] == ["A 模板"]
    assert [item["template_name"] for item in storage.list_store_templates(account_id=ACCOUNT_B)] == ["B 模板"]
    assert len(storage.list_store_templates()) == 2


def test_update_store_template_follows_the_new_store_account() -> None:
    _seed(store_accounts={"store-1": [ACCOUNT_A], "store-2": [ACCOUNT_B]})
    template = storage.create_store_template(_template_payload(account_id=ACCOUNT_A))

    updated = storage.update_store_template(template["id"], {"mabang_store_id": "store-2", "store_name": "HN_POD-2"})

    assert updated is not None
    assert updated["account_id"] == ACCOUNT_B


def test_copy_store_template_uses_the_target_store_account() -> None:
    _seed(store_accounts={"store-1": [ACCOUNT_A], "store-2": [ACCOUNT_B]})
    source = storage.create_store_template(_template_payload(account_id=ACCOUNT_A))

    copied = storage.copy_store_template(source["id"], {"mabang_store_id": "store-2", "template_name": "搬到 B"})

    assert copied["account_id"] == ACCOUNT_B
    assert copied["mabang_store_id"] == "store-2"


def test_duplicate_store_template_keeps_the_source_account() -> None:
    _seed(store_accounts={"store-1": [ACCOUNT_B]})
    source = storage.create_store_template(_template_payload(account_id=ACCOUNT_B))

    copied = storage.duplicate_store_template(source["id"])

    assert copied["account_id"] == ACCOUNT_B
    assert copied["template_name"] == "Mens Tee - 副本"


def test_backfill_writes_the_store_account_and_leaves_archived_alone() -> None:
    data = _seed(
        store_accounts={"store-1": [ACCOUNT_B]},
        templates=[
            {"id": "t-active", "template_name": "已用", "mabang_store_id": "store-1", "platform": "shopee", "status": "active"},
            {"id": "t-archived", "template_name": "归档", "mabang_store_id": "store-1", "platform": "shopee", "status": "archived"},
            {"id": "t-unknown-store", "template_name": "没采过店铺", "mabang_store_id": "missing-store", "platform": "shopee", "status": "active"},
        ],
    )

    changed = storage._migrate_store_template_accounts(data)

    assert changed is True
    templates = {item["id"]: item for item in data["store_templates"]}
    assert templates["t-active"]["account_id"] == ACCOUNT_B
    # 命中不了店铺的落默认账号（旧配置没有 login_account，取第一个启用账号）。
    assert templates["t-unknown-store"]["account_id"] == ACCOUNT_A
    assert "account_id" not in templates["t-archived"]


def test_backfill_is_idempotent_and_skips_libraries_without_accounts() -> None:
    data = _seed(
        store_accounts={"store-1": [ACCOUNT_B]},
        templates=[{"id": "t-1", "template_name": "已用", "mabang_store_id": "store-1", "platform": "shopee", "status": "active"}],
    )
    assert storage._migrate_store_template_accounts(data) is True
    assert storage._migrate_store_template_accounts(data) is False

    legacy = {"store_templates": [{"id": "t-1", "mabang_store_id": "store-1", "status": "active"}]}
    assert storage._migrate_store_template_accounts(legacy) is False
    assert "account_id" not in legacy["store_templates"][0]
