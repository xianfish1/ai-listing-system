"""就绪状态批量计算：与逐个商品检查同口径，但整库只扫一遍。

现场问题：/api/deployment/readiness 首屏每次 618ms —— 它给 91 个商品每个都调一次
check_product_readiness，而每次都把 2549 个素材、5118 个 SKU、5118 条价格整表扫一遍。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "data" / "store.json")
    storage._SPLIT_LAST_BYTES.clear()
    storage._MAIN_LAST_BYTES.clear()
    storage._FILE_PARSE_CACHE.clear()
    storage._STORE_CACHE["key"] = None
    storage._STORE_CACHE["data"] = None
    yield


def _seed() -> list[dict]:
    products = [
        {
            "id": "p-complete",
            "group_id": "GN001",
            "product_name_cn": "完整商品",
            "listing_title": "Complete Tee",
            "category": next(iter(storage.PRODUCT_TEMPLATES)),
            "marketplace_category_path": "Clothes > Tops",
            "main_image": "main.jpg",
            "weight_kg": 0.3,
            "length_cm": 30,
            "width_cm": 20,
            "height_cm": 5,
        },
        {
            "id": "p-scoped",
            "group_id": "GN002",
            "product_name_cn": "指定素材目录",
            "listing_title": "Scoped Tee",
            "category": next(iter(storage.PRODUCT_TEMPLATES)),
            "marketplace_category_path": "Clothes > Tops",
            "asset_root": "C:/assets/gn002/new",
            "weight_kg": 0.2,
        },
        {
            "id": "p-bare",
            "group_id": "GN003",
            "product_name_cn": "缺资料",
        },
    ]
    assets = [
        {"id": "a1", "group_id": "GN001", "asset_type": "main", "file_name": "main.jpg", "file_path": "C:/assets/gn001/main.jpg"},
        {"id": "a2", "group_id": "GN002", "asset_type": "main", "file_name": "old.jpg", "file_path": "C:/assets/gn002/old/old.jpg"},
        {"id": "a3", "group_id": "GN002", "asset_type": "variant", "file_name": "new.jpg", "file_path": "C:/assets/gn002/new/new.jpg"},
        {"id": "a4", "group_id": "GN002", "asset_type": "main", "file_name": "upload.jpg", "file_path": "C:/elsewhere/upload.jpg", "origin": "manual_upload"},
    ]
    skus = [
        {"id": "s1", "product_id": "p-complete", "sku_code": "C-1", "size": "S", "color_code": "black", "weight_kg": 0.3, "length_cm": 30, "width_cm": 20, "height_cm": 5},
        {"id": "s2", "product_id": "p-scoped", "sku_code": "S-1", "size": "M", "color_code": "red"},
    ]
    prices = [
        {"id": "pr1", "product_id": "p-complete", "sku_id": "s1", "sku_code": "C-1", "cost_price": 12.5},
        # 挂在这个商品的 SKU 上、product_id 却写着别的（历史合并后的形态）：
        # list_prices_for_product 认 sku_id，批量版也必须认。
        {"id": "pr2", "product_id": "p-other", "sku_id": "s2", "sku_code": "S-1", "cost_price": 8},
    ]
    storage.save_store({"products": products, "assets": assets, "skus": skus, "prices": prices})
    return products


def test_bulk_readiness_matches_the_per_product_check() -> None:
    products = _seed()
    data = storage.load_store()

    bulk = storage._readiness_by_product(data, products)

    for product in products:
        product_id = product["id"]
        assert bulk[product_id] == storage.check_product_readiness(product_id), product_id


def test_bulk_readiness_handles_asset_root_scoping_and_sku_linked_prices() -> None:
    _seed()

    scoped = storage.check_product_readiness("p-scoped")
    # 指定目录里没有 main 素材，但有 manual_upload 的主图 → 有主图
    assert scoped["checks"][4]["code"] == "main_image" and scoped["checks"][4]["ok"] is True
    # 价格挂在 SKU 上（product_id 不是本商品）也要算到成本价
    assert scoped["checks"][6]["code"] == "cost_price" and scoped["checks"][6]["ok"] is True

    bare = storage.check_product_readiness("p-bare")
    assert bare["ready"] is False
    assert "title" in bare["blockers"] and "main_image" in bare["blockers"]
    data = storage.load_store()
    bulk = storage._readiness_by_product(data, data["products"])
    assert bare["blockers"] == bulk["p-bare"]["blockers"]


def test_deployment_readiness_does_not_call_per_product_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed()
    calls = {"count": 0}

    def forbidden(product_id: str):
        calls["count"] += 1
        raise AssertionError("deployment_readiness 不该再逐个商品检查")

    monkeypatch.setattr(storage, "check_product_readiness", forbidden)
    readiness = storage.deployment_readiness()

    assert calls["count"] == 0
    assert readiness["counts"]["blocked_products"] == 2
    assert readiness["counts"]["exportable_products"] == 1
    assert readiness["counts"]["products"] == 3


def test_list_products_with_summary_uses_the_bulk_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed()

    def forbidden(product_id: str):
        raise AssertionError("商品列表摘要不该再逐个商品检查")

    monkeypatch.setattr(storage, "check_product_readiness", forbidden)
    rows = storage.list_products_with_summary()

    assert len(rows) == 3
    assert {row["id"] for row in rows} == {"p-complete", "p-scoped", "p-bare"}
    assert all("readiness" in row for row in rows)
