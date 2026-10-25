from __future__ import annotations

from services.api.marketplace_category_catalog import (
    MarketplaceCategoryCatalogConflict,
    complete_catalog_run,
    create_catalog_run,
    sanitize_source_url,
    search_catalog,
)

import pytest


def _store() -> dict:
    return {
        "marketplace_categories": [],
        "marketplace_category_catalog_runs": [],
    }


def _capture(categories: list[dict], *, source_url: str = "https://publish.mabangerp.com/publish-ui/?secret=value") -> dict:
    def count(items: list[dict]) -> int:
        return sum(1 + count(item.get("children") or []) for item in items)

    return {
        "discovery_source": "mabang_shopee_getCategory_api",
        "category_count": count(categories),
        "source_url": source_url,
        "categories": categories,
    }


def test_complete_catalog_run_flattens_nested_categories_with_stable_paths():
    data = _store()
    run = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T10:00:00+00:00",
    )

    result = complete_catalog_run(
        data,
        run["id"],
        _capture(
            [
                {
                    "category_id": "100",
                    "name": "Men Clothes",
                    "children": [
                        {
                            "category_id": "110",
                            "name": "Tops",
                            "children": [
                                {"category_id": "111", "name": "T-Shirts"},
                            ],
                        }
                    ],
                }
            ]
        ),
        now="2026-07-12T10:01:00+00:00",
    )

    assert result["saved_count"] == 3
    by_id = {item["category_id"]: item for item in data["marketplace_categories"]}
    assert by_id["100"]["path"] == "Men Clothes"
    assert by_id["100"]["is_leaf"] is False
    assert by_id["111"]["path"] == "Men Clothes > Tops > T-Shirts"
    assert by_id["111"]["parent_id"] == "110"
    assert by_id["111"]["level"] == 3
    assert by_id["111"]["is_leaf"] is True
    assert by_id["111"]["active"] is True


def test_catalog_upsert_marks_missing_categories_inactive_without_deleting_them():
    data = _store()
    first = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T10:00:00+00:00",
    )
    complete_catalog_run(
        data,
        first["id"],
        _capture(
            [
                {"id": "100", "name": "Men Clothes", "parent_id": ""},
                {"id": "111", "name": "T-Shirts", "parent_id": "100"},
            ]
        ),
        now="2026-07-12T10:01:00+00:00",
    )

    second = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T11:00:00+00:00",
    )
    second_result = complete_catalog_run(
        data,
        second["id"],
        _capture(
            [
                {"id": "100", "name": "Men Fashion", "parent_id": ""},
                {"id": "112", "name": "Polo Shirts", "parent_id": "100"},
            ]
        ),
        now="2026-07-12T11:01:00+00:00",
    )

    by_id = {item["category_id"]: item for item in data["marketplace_categories"]}
    assert set(by_id) == {"100", "111", "112"}
    assert by_id["100"]["name"] == "Men Fashion"
    assert by_id["111"]["active"] is False
    assert by_id["112"]["active"] is True
    assert second_result["deactivated_count"] == 1

def test_search_catalog_scopes_results_and_returns_selectable_leaf_categories():
    data = _store()
    for store_id, country, leaf_id in (
        ("store-1", "PH", "111"),
        ("store-2", "PH", "211"),
        ("store-1", "MY", "311"),
    ):
        run = create_catalog_run(
            data,
            {"platform": "shopee", "country": country, "store_id": store_id},
            now="2026-07-12T10:00:00+00:00",
        )
        complete_catalog_run(
            data,
            run["id"],
            _capture(
                [
                    {"id": f"{leaf_id}0", "name": "Men Clothes"},
                    {"id": leaf_id, "name": "T-Shirts", "parent_id": f"{leaf_id}0"},
                ]
            ),
            now="2026-07-12T10:01:00+00:00",
        )

    results = search_catalog(
        data["marketplace_categories"],
        platform="shopee",
        country="PH",
        store_id="store-1",
        query="shirt",
        leaf_only=True,
    )

    assert [item["category_id"] for item in results] == ["111"]
    assert results[0]["path"].endswith("T-Shirts")


def test_search_catalog_browses_direct_children_and_reports_child_state():
    data = _store()
    run = create_catalog_run(
        data,
        {"platform": "shopee", "country": "TH", "store_id": "store-th"},
        now="2026-07-12T10:00:00+00:00",
    )
    complete_catalog_run(
        data,
        run["id"],
        _capture(
            [
                {
                    "id": "100642",
                    "name": "Tickets, Vouchers & Services",
                    "children": [
                        {
                            "id": "100765",
                            "name": "Beauty & Wellness",
                            "children": [{"id": "101516", "name": "Dental"}],
                        }
                    ],
                },
                {"id": "100001", "name": "Women's Apparel"},
            ]
        ),
        now="2026-07-12T10:01:00+00:00",
    )

    roots = search_catalog(
        data["marketplace_categories"],
        platform="shopee",
        country="TH",
        store_id="store-th",
        parent_id="0",
        leaf_only=False,
    )
    children = search_catalog(
        data["marketplace_categories"],
        platform="shopee",
        country="TH",
        store_id="store-th",
        parent_id="100642",
        leaf_only=False,
    )
    leaves = search_catalog(
        data["marketplace_categories"],
        platform="shopee",
        country="TH",
        store_id="store-th",
        parent_id="100765",
        leaf_only=False,
    )

    assert {item["category_id"] for item in roots} == {"100642", "100001"}
    assert next(item for item in roots if item["category_id"] == "100642")["has_children"] is True
    assert next(item for item in roots if item["category_id"] == "100001")["has_children"] is False
    assert [item["category_id"] for item in children] == ["100765"]
    assert children[0]["has_children"] is True
    assert [item["category_id"] for item in leaves] == ["101516"]
    assert leaves[0]["has_children"] is False
    assert leaves[0]["is_leaf"] is True


def test_catalog_preserves_localized_names_and_searches_chinese_paths():
    data = _store()
    run = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T10:00:00+00:00",
    )
    complete_catalog_run(
        data,
        run["id"],
        _capture(
            [
                {
                    "category_id": "100011",
                    "name": "Men Clothes",
                    "name_zh": "男装",
                    "name_en": "Men Clothes",
                    "children": [
                        {
                            "category_id": "100054",
                            "name": "Shirts, Blouses",
                            "name_zh": "上衣",
                            "name_en": "Shirts, Blouses",
                            "name_en_path": "Men Clothes,Shirts, Blouses",
                        }
                    ],
                }
            ]
        ),
        now="2026-07-12T10:01:00+00:00",
    )

    results = search_catalog(
        data["marketplace_categories"],
        platform="shopee",
        country="PH",
        store_id="store-1",
        query="男装",
    )

    assert len(results) == 1
    assert results[0]["name_zh"] == "上衣"
    assert results[0]["path_zh"] == "男装 > 上衣"
    assert results[0]["path_en"] == "Men Clothes > Shirts, Blouses"


def test_catalog_rejects_non_authoritative_or_partial_capture_without_mutating_existing_data():
    data = _store()
    first = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T10:00:00+00:00",
    )
    complete_catalog_run(
        data,
        first["id"],
        _capture(
            [
                {"id": "100", "name": "Root", "children": [
                    {"id": str(100 + index), "name": f"Leaf {index}"} for index in range(1, 11)
                ]}
            ]
        ),
        now="2026-07-12T10:01:00+00:00",
    )
    before = [dict(item) for item in data["marketplace_categories"]]

    dom_run = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T11:00:00+00:00",
    )
    with pytest.raises(MarketplaceCategoryCatalogConflict, match="authoritative"):
        complete_catalog_run(
            data,
            dom_run["id"],
            {"discovery_source": "rendered_dom", "category_count": 1, "categories": [{"id": "dom-0", "name": "Visible"}]},
            now="2026-07-12T11:01:00+00:00",
        )
    assert data["marketplace_categories"] == before

    dom_run["status"] = "failed"
    partial_run = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T12:00:00+00:00",
    )
    with pytest.raises(MarketplaceCategoryCatalogConflict, match="shrank"):
        complete_catalog_run(
            data,
            partial_run["id"],
            _capture([{"id": "999", "name": "Only one"}]),
            now="2026-07-12T12:01:00+00:00",
        )
    assert data["marketplace_categories"] == before


def test_catalog_rejects_stale_run_completion_and_sanitizes_source_url():
    data = _store()
    older = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T10:00:00+00:00",
    )
    older["status"] = "failed"
    newer = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T11:00:00+00:00",
    )
    complete_catalog_run(
        data,
        newer["id"],
        _capture([{"id": "100", "name": "Root"}]),
        now="2026-07-12T11:01:00+00:00",
    )
    older["status"] = "pending"

    with pytest.raises(MarketplaceCategoryCatalogConflict, match="stale"):
        complete_catalog_run(
            data,
            older["id"],
            _capture([{"id": "101", "name": "Old Root"}]),
            now="2026-07-12T12:00:00+00:00",
        )

    assert sanitize_source_url("https://publish.mabangerp.com/publish-ui/?token=secret#hash") == "https://publish.mabangerp.com/publish-ui/"
    assert data["marketplace_category_catalog_runs"][-1]["source_url"] == "https://publish.mabangerp.com/publish-ui/"


def test_explicit_path_does_not_split_commas_inside_category_name():
    data = _store()
    run = create_catalog_run(
        data,
        {"platform": "shopee", "country": "PH", "store_id": "store-1"},
        now="2026-07-12T10:00:00+00:00",
    )
    complete_catalog_run(
        data,
        run["id"],
        _capture([{"id": "200", "name": "Shirts, Blouses", "path": "Accessories > Shirts, Blouses"}]),
        now="2026-07-12T10:01:00+00:00",
    )

    assert data["marketplace_categories"][0]["path"] == "Accessories > Shirts, Blouses"
