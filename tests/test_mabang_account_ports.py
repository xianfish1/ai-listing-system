from __future__ import annotations

import pytest


def _configure_store(monkeypatch, tmp_path) -> None:
    from services.api import settings

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def test_cdp_port_is_assigned_by_the_system(monkeypatch, tmp_path) -> None:
    """操作员不配端口：建账号时不传，系统按 9223、9224、9225… 依次分配。"""

    from services.api import storage

    _configure_store(monkeypatch, tmp_path)
    a = storage.create_mabang_account({"name": "A", "login_account": "acct-a"})
    b = storage.create_mabang_account({"name": "B", "login_account": "acct-b"})
    c = storage.create_mabang_account({"name": "C", "login_account": "acct-c"})

    assert [item["cdp_port"] for item in (a, b, c)] == [9223, 9224, 9225]
    assert len({item["cdp_port"] for item in (a, b, c)}) == 3
    # 数据目录也按账号派生，天然互相隔离
    runtime_dirs = {
        storage.resolve_mabang_account_runtime(item["id"])["profile_dir"] for item in (a, b, c)
    }
    assert len(runtime_dirs) == 3


def test_bad_port_value_never_blocks_saving(monkeypatch, tmp_path) -> None:
    """端口字段已经不在界面上；万一旧客户端传了离谱的值，也不能因此保存失败。"""

    from services.api import storage

    _configure_store(monkeypatch, tmp_path)
    a = storage.create_mabang_account(
        {"name": "A", "login_account": "acct-a", "cdp_port": 999999}
    )
    b = storage.create_mabang_account(
        {"name": "B", "login_account": "acct-b", "cdp_port": "not-a-port"}
    )

    assert a["cdp_port"] == 9223
    assert b["cdp_port"] == 9224


def test_clearing_a_port_reassigns_one(monkeypatch, tmp_path) -> None:
    from services.api import storage

    _configure_store(monkeypatch, tmp_path)
    a = storage.create_mabang_account({"name": "A", "login_account": "acct-a"})
    storage.create_mabang_account({"name": "B", "login_account": "acct-b"})

    updated = storage.update_mabang_account(a["id"], {"cdp_port": 0})

    # 清空端口不会让账号失去端口：重新分配当前最小的空闲端口（别的账号占着 9224）。
    assert updated["cdp_port"] == 9223
    assert updated["cdp_port"] not in {9224}
