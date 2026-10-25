from __future__ import annotations

import json

import pytest

from services.api import settings, storage
from services.api.credential_crypto import CredentialCrypto


def _configure_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _two_accounts() -> tuple[dict, dict]:
    a = storage.create_mabang_account(
        {"name": "账号A", "login_account": "acct-a", "login_password": "pw-a", "cdp_port": 9223}
    )
    b = storage.create_mabang_account(
        {"name": "账号B", "login_account": "acct-b", "login_password": "pw-b", "cdp_port": 9224}
    )
    return a, b


def test_legacy_store_seeds_one_account_and_claims_its_store_options(monkeypatch, tmp_path) -> None:
    """单账号时代：旧配置被迁移成一个账号，存量店铺回填归属，且可重复加载不重复建号。"""

    _configure_store(monkeypatch, tmp_path)
    token = CredentialCrypto(settings.get_ai_credentials_master_key()).encrypt("pw-a")
    raw = {
        "runtime_configuration": {"mabang": {"login_account": "acct-a", "login_password": token}},
        "mabang_store_options": [
            {
                "id": "o1",
                "platform": "shopee",
                "country": "PH",
                "store_name": "HN_POD",
                "mabang_store_id": "2022625115",
                "status": "active",
            }
        ],
    }
    settings.STORE_FILE.write_text(json.dumps(raw), encoding="utf-8")

    data = storage.load_store()
    accounts = data["mabang_accounts"]
    assert len(accounts) == 1
    assert accounts[0]["login_account"] == "acct-a"
    assert data["mabang_store_options"][0]["account_id"] == accounts[0]["id"]
    # 端口由系统分配，且只有一个账号时仍是 9223（与旧的全局 cdp_url 一致）
    assert accounts[0]["cdp_port"] == 9223
    # 迁出来的账号继续用现网的浏览器目录，避免把已登录的马帮会话丢掉
    assert storage.resolve_mabang_account_runtime(accounts[0]["id"])["profile_dir"].endswith(
        "browser-use-profile"
    )

    # 迁移落盘后再次加载不再新增账号。
    persisted = json.loads(settings.STORE_FILE.read_text(encoding="utf-8"))
    assert len(persisted["mabang_accounts"]) == 1
    assert storage.load_store()["mabang_accounts"][0]["id"] == accounts[0]["id"]


def test_same_mabang_store_id_coexists_under_two_accounts(monkeypatch, tmp_path) -> None:
    """复合键 (account_id, platform, store_id)：第二个账号不再覆盖第一个账号的店铺。"""

    _configure_store(monkeypatch, tmp_path)
    a, b = _two_accounts()

    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "PH", "store_name": "A店", "mabang_store_id": "shared-1"},
        account_id=a["id"],
    )
    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "TW", "store_name": "B店", "mabang_store_id": "shared-1"},
        account_id=b["id"],
    )

    items = storage.list_mabang_store_options("shopee")
    assert len(items) == 2
    assert {item["account_id"] for item in items} == {a["id"], b["id"]}
    assert {item["country"] for item in items} == {"PH", "TW"}
    assert [item["store_name"] for item in storage.list_mabang_store_options("shopee", a["id"])] == ["A店"]


def test_store_option_upsert_defaults_to_the_default_account(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    a, _b = _two_accounts()

    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "PH", "store_name": "A店", "mabang_store_id": "s-1"}
    )

    assert storage.list_mabang_store_options("shopee")[0]["account_id"] == a["id"]


def test_credentials_resolve_per_account_and_never_echo_the_password(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    a, b = _two_accounts()

    assert storage.get_mabang_login_credentials(a["id"]) == ("acct-a", "pw-a")
    assert storage.get_mabang_login_credentials(b["id"]) == ("acct-b", "pw-b")
    # 没有旧配置时，不指定账号 = 第一个启用的账号。
    assert storage.get_mabang_login_credentials() == ("acct-a", "pw-a")

    public = storage.list_mabang_accounts()
    assert [item["has_password"] for item in public] == [True, True]
    assert "encrypted_password" not in json.dumps(public)
    assert "pw-a" not in json.dumps(public)
    assert "encrypted_password" not in json.dumps(storage.get_mabang_account(a["id"]))


def test_account_rejects_duplicate_login_and_cdp_port(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    _two_accounts()

    with pytest.raises(ValueError):
        storage.create_mabang_account({"name": "A2", "login_account": "acct-a", "cdp_port": 9225})
    with pytest.raises(ValueError):
        storage.create_mabang_account({"name": "A3", "login_account": "acct-c", "cdp_port": 9223})


def test_account_with_owned_stores_cannot_be_deleted(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    a, b = _two_accounts()
    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "PH", "store_name": "A店", "mabang_store_id": "s-1"},
        account_id=a["id"],
    )

    with pytest.raises(ValueError):
        storage.delete_mabang_account(a["id"])

    assert storage.delete_mabang_account(b["id"])["login_account"] == "acct-b"
    assert [item["id"] for item in storage.list_mabang_accounts()] == [a["id"]]


def test_legacy_jobs_get_their_shop_account_backfilled(monkeypatch, tmp_path) -> None:
    """历史任务没有 account_id：按店铺归属补齐，否则多账号下没人能领走它。"""

    _configure_store(monkeypatch, tmp_path)
    a = storage.create_mabang_account({"name": "A", "login_account": "acct-a", "cdp_port": 9223})
    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "PH", "store_name": "A店", "mabang_store_id": "s-1"},
        account_id=a["id"],
    )
    with storage.locked_store() as (data, save):
        data["store_listing_copies"] = [
            {"id": "copy-1", "platform": "shopee", "mabang_store_id": "s-1"},
            {"id": "copy-2", "platform": "shopee", "mabang_store_id": "未采集的店"},
        ]
        data["automation_jobs"] = [
            {"id": "j1", "status": "ready", "listing_copy_id": "copy-1", "attempt_count": 0},
            {"id": "j2", "status": "ready", "listing_copy_id": "copy-2", "attempt_count": 0},
        ]
        save(data)

    jobs = {item["id"]: item for item in storage.load_store()["automation_jobs"]}

    # 能判定的补上；判不定的保持空（多账号下谁都不会领走，而不是填错账号）
    assert jobs["j1"]["account_id"] == a["id"]
    assert str(jobs["j2"].get("account_id") or "") == ""


def test_page_import_run_records_and_validates_the_account(monkeypatch, tmp_path) -> None:
    """页面导入的 run 必须记住账号，采集才知道该读哪个浏览器。"""

    _configure_store(monkeypatch, tmp_path)
    a = storage.create_mabang_account({"name": "A", "login_account": "acct-a"})

    run = storage.create_mabang_page_import(
        {"platform": "shopee", "country": "PH", "account_id": a["id"]}
    )

    assert run["account_id"] == a["id"]
    assert storage.get_mabang_page_import(run["id"])["account_id"] == a["id"]

    with pytest.raises(storage.MabangPageImportError):
        storage.create_mabang_page_import(
            {"platform": "shopee", "country": "PH", "account_id": "不存在的账号"}
        )


def test_shared_profile_directory_is_refused(monkeypatch, tmp_path) -> None:
    """两个账号共用一个 Chrome Profile 会互相顶掉 cookie，必须拒绝。"""

    _configure_store(monkeypatch, tmp_path)
    shared = str(tmp_path / "one-profile")
    storage.create_mabang_account(
        {"name": "A", "login_account": "acct-a", "cdp_port": 9223, "profile_dir": shared}
    )

    with pytest.raises(ValueError):
        storage.create_mabang_account(
            {"name": "B", "login_account": "acct-b", "cdp_port": 9224, "profile_dir": shared}
        )


def test_runtime_resolves_each_accounts_own_port_and_profile(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    a, b = _two_accounts()

    runtime_a = storage.resolve_mabang_account_runtime(a["id"])
    runtime_b = storage.resolve_mabang_account_runtime(b["id"])

    assert runtime_a["cdp_url"] == "http://127.0.0.1:9223"
    assert runtime_b["cdp_url"] == "http://127.0.0.1:9224"
    # 未显式配置时按账号 id 派生，保证 Profile（cookie）互相隔离。
    assert runtime_a["profile_dir"] != runtime_b["profile_dir"]
    assert str(a["id"]) in runtime_a["profile_dir"]
    assert runtime_a["login_account"] == "acct-a"


def test_runtime_falls_back_to_the_legacy_single_account(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)

    runtime = storage.resolve_mabang_account_runtime()

    assert runtime["account_id"] == ""
    assert runtime["profile_dir"].endswith("browser-use-profile")
    assert runtime["cdp_url"].startswith("http://127.0.0.1:")


def _seed_jobs(jobs: list[dict]) -> None:
    with storage.locked_store() as (data, save):
        data["automation_jobs"] = jobs
        save(data)


def _ready_job(job_id: str, account_id: str, created_at: str) -> dict:
    return {
        "id": job_id,
        "status": "ready",
        "account_id": account_id,
        "created_at": created_at,
        "attempt_count": 0,
        "worker_id": "",
        "lease_token": "",
        "lease_expires_at": "",
    }


def test_listing_copy_account_comes_from_the_shop_owner(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    a, b = _two_accounts()
    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "PH", "store_name": "A店", "mabang_store_id": "s-1"},
        account_id=a["id"],
    )
    storage.upsert_mabang_store_options(
        {"platform": "shopee", "country": "TW", "store_name": "B店", "mabang_store_id": "s-2"},
        account_id=b["id"],
    )
    data = storage.load_store()

    assert storage._account_id_for_listing_copy_in_store(
        data, {"platform": "shopee", "mabang_store_id": "s-2"}
    ) == b["id"]
    # 店铺没采过 → 不猜账号
    assert storage._account_id_for_listing_copy_in_store(
        data, {"platform": "shopee", "mabang_store_id": "missing"}
    ) == ""


def test_next_ready_automation_account_follows_the_earliest_job(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    a, b = _two_accounts()
    _seed_jobs([
        _ready_job("j2", b["id"], "2026-01-02T00:00:00+00:00"),
        _ready_job("j1", a["id"], "2026-01-01T00:00:00+00:00"),
    ])

    assert storage.next_ready_automation_account() == a["id"]


def test_claim_next_automation_job_only_hands_out_the_workers_own_account(monkeypatch, tmp_path) -> None:
    _configure_store(monkeypatch, tmp_path)
    a, b = _two_accounts()
    _seed_jobs([
        _ready_job("j1", a["id"], "2026-01-01T00:00:00+00:00"),
        _ready_job("j2", b["id"], "2026-01-02T00:00:00+00:00"),
    ])

    claimed = storage.claim_next_automation_job("worker-b", account_id=b["id"])

    assert claimed["job"]["id"] == "j2"
    assert claimed["job"]["account_id"] == b["id"]
    # A 账号的任务原封不动，工作量和重试次数都没被消耗
    jobs = {item["id"]: item for item in storage.load_store()["automation_jobs"]}
    assert jobs["j1"]["status"] == "ready"
    assert jobs["j1"]["attempt_count"] == 0
    assert jobs["j2"]["status"] == "claimed"
