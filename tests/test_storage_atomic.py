from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import services.api.storage as storage_module
from services.api import settings, storage
from services.api.storage import _atomic_replace


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_atomic_replace_succeeds_on_first_try(tmp_path: Path) -> None:
    target = tmp_path / "store.json"
    _write_json(target, {"version": 0})
    tmp = tmp_path / "store.tmp"
    _write_json(tmp, {"version": 1})

    _atomic_replace(tmp, target)

    assert not tmp.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}


def test_atomic_replace_retries_on_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "store.json"
    _write_json(target, {"version": 0})
    tmp = tmp_path / "store.tmp"
    _write_json(tmp, {"version": 1})

    real_replace = os.replace
    calls = {"count": 0}

    def flaky_replace(src: str, dst: str) -> None:
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError("simulated antivirus lock")
        return real_replace(src, dst)

    monkeypatch.setattr(storage_module.os, "replace", flaky_replace)
    _atomic_replace(tmp, target, attempts=5, backoff_seconds=0.001)

    assert calls["count"] == 3
    assert not tmp.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}


def test_atomic_replace_raises_after_exhausting_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "store.json"
    _write_json(target, {"version": 0})
    tmp = tmp_path / "store.tmp"
    _write_json(tmp, {"version": 1})

    def always_fail(src: str, dst: str) -> None:
        raise PermissionError("permanent lock")

    monkeypatch.setattr(storage_module.os, "replace", always_fail)

    with pytest.raises(OSError, match="atomic replace"):
        _atomic_replace(tmp, target, attempts=3, backoff_seconds=0.001)


def test_save_store_round_trip_via_isolated_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = tmp_path / "store.json"
    monkeypatch.setattr(settings, "STORE_FILE", sandbox)

    storage.save_store({"foo": [{"id": "a", "value": 1}]})

    loaded = storage.load_store()
    assert loaded["foo"] == [{"id": "a", "value": 1}]


def test_save_store_persists_ai_outputs_in_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = tmp_path / "store.json"
    # DATA_DIR 也要一并隔离：legacy 兜底会顺着旧布局读 DATA_DIR 下的集合，
    # 只隔离 STORE_FILE 的话真实 data/ai_outputs.json 会混进沙盒（读侧泄漏）。
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORE_FILE", sandbox)

    storage.create_ai_output(
        {
            "product_id": "prod-1",
            "output_type": "mabang_export_audit",
            "prompt": "",
            "response": "audit",
            "model": "local_rules",
            "status": "suggested",
            "payload_json": {"group_id": "DL0001"},
        }
    )

    outputs = storage.list_ai_outputs_for_product("prod-1", "mabang_export_audit")
    assert len(outputs) == 1
    assert outputs[0]["payload_json"]["group_id"] == "DL0001"


def test_split_collections_follow_the_isolated_store_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离 STORE_FILE 的测试/临时库绝不能写到真实 data/ 下的 split 集合。

    2026-10-05 现场：跑一次全量 pytest 就把真实的 `data/mabang_page_import_runs.json`、
    `data/ai_outputs.json` 覆盖成了 `[]`（那时 split 路径跟 DATA_DIR 走，而用例只隔离了
    STORE_FILE）。这份用例拿真实目录的字节数当哨兵。
    """

    real_paths = [settings.DATA_DIR / f"{key}.json" for key in storage_module._SPLIT_STORE_KEYS]
    before = {path: (path.read_bytes() if path.exists() else None) for path in real_paths}

    sandbox = tmp_path / "store.json"
    monkeypatch.setattr(settings, "STORE_FILE", sandbox)
    monkeypatch.setattr(storage_module, "_SPLIT_LAST_BYTES", {})

    storage.create_ai_output(
        {
            "product_id": "prod-1",
            "output_type": "mabang_export_audit",
            "prompt": "",
            "response": "audit",
            "model": "local_rules",
            "status": "suggested",
            "payload_json": {},
        }
    )
    storage.create_mabang_page_import({"platform": "shopee", "country": "PH"})

    assert (tmp_path / "ai_outputs.json").is_file()
    assert (tmp_path / "mabang_page_import_runs.json").is_file()
    for path in real_paths:
        assert (path.read_bytes() if path.exists() else None) == before[path], path


def test_writing_an_empty_split_collection_keeps_a_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = tmp_path / "store.json"
    monkeypatch.setattr(settings, "STORE_FILE", sandbox)
    monkeypatch.setattr(storage_module, "_SPLIT_LAST_BYTES", {})

    storage.create_mabang_page_import({"platform": "shopee", "country": "PH"})
    runs_path = tmp_path / "mabang_page_import_runs.json"
    assert json.loads(runs_path.read_text(encoding="utf-8"))

    # 空集合覆盖之前必须留备份（读失败/写错库/误清空都还有救）
    storage.save_store({**storage.load_store(), "mabang_page_import_runs": []})

    assert json.loads(runs_path.read_text(encoding="utf-8")) == []
    backups = list(tmp_path.glob("mabang_page_import_runs.json.emptied-*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")), "备份里必须是清空前的数据"


def test_split_collections_are_still_read_from_the_legacy_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧布局（DATA_DIR 下的 split 文件）还要读得到，不能因为换路径把数据判成丢了。"""

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    legacy = data_dir / "mabang_page_import_runs.json"
    legacy.write_text(json.dumps([{"id": "legacy-run", "status": "pending"}]), encoding="utf-8")

    store_file = tmp_path / "elsewhere" / "store.json"
    monkeypatch.setattr(settings, "DATA_DIR", data_dir)
    monkeypatch.setattr(settings, "STORE_FILE", store_file)
    monkeypatch.setattr(storage_module, "_SPLIT_LAST_BYTES", {})
    monkeypatch.setattr(storage_module, "_STORE_CACHE", {"key": None, "data": None})

    runs = storage.load_store()["mabang_page_import_runs"]

    assert [run["id"] for run in runs] == ["legacy-run"]
    # 下一次保存写到新位置，旧文件留着当证据
    storage.save_store({**storage.load_store(), "mabang_page_import_runs": runs + [{"id": "new"}]})
    assert json.loads((store_file.parent / "mabang_page_import_runs.json").read_text(encoding="utf-8"))[-1]["id"] == "new"


def test_list_prices_for_product_does_not_borrow_other_product_sku_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "store.json"
    monkeypatch.setattr(settings, "STORE_FILE", sandbox)
    storage.save_store(
        {
            "assets": [],
            "products": [
                {"id": "product-a", "group_id": "A"},
                {"id": "product-b", "group_id": "B"},
            ],
            "skus": [
                {
                    "id": "sku-a-1",
                    "product_id": "product-a",
                    "sku_code": "DUPLICATE-SKU",
                    "color_code": "white",
                    "size": "S",
                },
                {
                    "id": "sku-b-1",
                    "product_id": "product-b",
                    "sku_code": "DUPLICATE-SKU",
                    "color_code": "white",
                    "size": "S",
                },
            ],
            "prices": [
                {
                    "id": "price-b-1",
                    "product_id": "product-b",
                    "sku_id": "sku-b-1",
                    "sku_code": "DUPLICATE-SKU",
                    "cost_price": 99,
                }
            ],
            "ai_outputs": [],
        }
    )

    prices = storage.list_prices_for_product("product-a")

    assert prices == []
