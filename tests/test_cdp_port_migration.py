"""老库 CDP 端口自动迁移：9223 段 → 18223（2026-11-17 生产基址迁移的兜底）。"""

from services.api import storage


def test_legacy_cdp_ports_are_migrated_on_load(monkeypatch) -> None:
    monkeypatch.delenv("AI_LISTING_CDP_PORT_BASE", raising=False)
    data = {
        "mabang_accounts": [
            {"id": "a", "login_account": "a", "cdp_port": 9223},
            {"id": "b", "login_account": "b", "cdp_port": 9322},
            {"id": "c", "login_account": "c", "cdp_port": 18223},
            {"id": "d", "login_account": "d", "cdp_port": 19223},
        ],
        "runtime_configuration": {
            "worker": {"cdp_url": "http://127.0.0.1:9223"},
            "mabang": {"browser_cdp_url": "http://localhost:9224"},
        },
    }

    changed = storage._normalize_legacy_store_shape(data)

    assert changed is True
    assert [item["cdp_port"] for item in data["mabang_accounts"]] == [18223, 18322, 18223, 19223]
    assert data["runtime_configuration"]["worker"]["cdp_url"] == "http://127.0.0.1:18223"
    assert data["runtime_configuration"]["mabang"]["browser_cdp_url"] == "http://localhost:18224"


def test_migration_is_idempotent(monkeypatch) -> None:
    monkeypatch.delenv("AI_LISTING_CDP_PORT_BASE", raising=False)
    data = {
        "mabang_accounts": [{"id": "a", "login_account": "a", "cdp_port": 9223}],
        "runtime_configuration": {"worker": {"cdp_url": "http://127.0.0.1:9223"}},
    }

    storage._normalize_legacy_store_shape(data)
    assert storage._migrate_legacy_cdp_ports(data) is False


def test_migration_skipped_when_a_custom_base_is_configured(monkeypatch) -> None:
    # 测试/自定义基址（19223 段）不能把库里的端口迁到 18223，否则会和测试端口打架。
    monkeypatch.setenv("AI_LISTING_CDP_PORT_BASE", "19223")
    data = {"mabang_accounts": [{"id": "a", "login_account": "a", "cdp_port": 9223}]}

    assert storage._migrate_legacy_cdp_ports(data) is False
    assert data["mabang_accounts"][0]["cdp_port"] == 9223
