from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _incomplete_cnsc_copy(tmp_path: Path) -> dict:
    """Create a CNSC listing copy that is complete except for the per-site
    discount activity (mabang_discount_id). Returns the created copy row."""
    folder = tmp_path / "CNSC2001 全球商品"
    folder.mkdir()
    (folder / "主图.jpg").write_bytes(b"image")
    (folder / "白色.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD 男装T恤"}
    )
    template = storage.create_store_template(
        {
            "store_name": "广州小苗服饰有限公司",
            "template_name": "CNSC 缺站点活动",
            "listing_mode": "cnsc_global",
            "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
            "sizes": ["S"],
            "default_site_discount_percent": 40,
            "target_site_stores": [
                {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}
            ],
            "price_tiers": [
                {
                    "id": "uniform",
                    "sizes": [],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
                }
            ],
            "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    return result["created"][0]


def _combo_cnsc_copy(tmp_path: Path) -> dict:
    """A CNSC copy whose base product carries two variant colors (衣服 / 假发)."""
    folder = tmp_path / "CNSC3001 组合商品"
    folder.mkdir()
    (folder / "主图.jpg").write_bytes(b"image")
    (folder / "白色.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD 组合商品"}
    )
    base = storage.update_base_product(base["id"], {"recognized_colors": ["衣服", "假发"]})
    template = storage.create_store_template(
        {
            "store_name": "广州小苗服饰有限公司",
            "template_name": "CNSC 组合品",
            "listing_mode": "cnsc_global",
            "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
            "sizes": ["S"],
            "default_site_discount_percent": 40,
            "target_site_stores": [
                {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}
            ],
            "price_tiers": [
                {
                    "id": "uniform",
                    "sizes": [],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
                }
            ],
            "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    return result["created"][0]


def _complete_ph_target() -> list[dict]:
    return [
        {
            "site_code": "PH",
            "store_id": "ph-store",
            "store_name": "海宁PH",
            "mabang_discount_id": "discount-ph",
            "discount_percent": 40,
        }
    ]


def _cnsc_readiness_fields(detail: dict) -> list[str]:
    return [
        field
        for field in detail["readiness"]["missing_fields"]
        if field.startswith("target_site_stores")
        or field.startswith("site_price_matrix")
        or field.startswith("price_tiers")
        or field == "cnsc_price_tier_size_coverage"
    ]


def test_cnsc_copy_readiness_flags_missing_discount_activity(tmp_path: Path) -> None:
    copy = _incomplete_cnsc_copy(tmp_path)
    detail = storage.get_store_listing_copy_detail(copy["id"])

    assert detail["readiness"]["ready"] is False
    assert "target_site_stores.PH.discount_activity" in detail["readiness"]["missing_fields"]


def test_cnsc_copy_readiness_flags_percent_off_and_size_coverage(tmp_path: Path) -> None:
    copy = _incomplete_cnsc_copy(tmp_path)
    # Supply the discount activity so that check clears, then inject two new
    # problems: an inconsistent %OFF (sale 900 vs 999*0.6=599.4) and a price
    # tier that no longer covers the SKU size "S" (covers "M" instead).
    storage.update_store_listing_copy(
        copy["id"],
        {
            "target_site_stores": [
                {
                    "site_code": "PH",
                    "store_id": "ph-store",
                    "store_name": "海宁PH",
                    "mabang_discount_id": "discount-ph",
                    "discount_percent": 40,
                }
            ],
            "price_tiers": [
                {
                    "id": "standard",
                    "sizes": ["M"],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 900, "original_price": 999}},
                }
            ],
        },
    )
    detail = storage.get_store_listing_copy_detail(copy["id"])
    fields = detail["readiness"]["missing_fields"]

    assert "target_site_stores.PH.discount_activity" not in fields
    # 售价由 %OFF 自动算：手工填的 sale 不再做一致性校验，percent_off 不再报。
    assert not any(f.endswith(".percent_off") for f in fields)
    # 档绑尺码 M 不覆盖颜色 SKU → 仍报 coverage。
    assert "cnsc_price_tier_size_coverage" in fields


def test_cnsc_incomplete_copy_creates_needs_manual_job(tmp_path: Path) -> None:
    copy = _incomplete_cnsc_copy(tmp_path)
    job = storage.create_automation_job_from_listing_copy(copy["id"])

    assert job["status"] == "needs_manual"
    assert "target_site_stores.PH.discount_activity" in job["missing_fields"]


def test_cnsc_editor_save_completes_config_and_bumps_revision(tmp_path: Path) -> None:
    copy = _incomplete_cnsc_copy(tmp_path)
    before = storage.get_item("store_listing_copies", copy["id"])
    revision_before = int(before.get("revision") or 1)

    detail = storage.update_store_listing_copy(
        copy["id"],
        {
            "target_site_stores": [
                {
                    "site_code": "PH",
                    "store_id": "ph-store",
                    "store_name": "海宁PH",
                    "mabang_discount_id": "discount-ph",
                    "discount_percent": 40,
                }
            ],
            "price_tiers": [
                {
                    "id": "uniform",
                    "sizes": [],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
                }
            ],
        },
    )

    cnsc_keys = [
        field
        for field in detail["readiness"]["missing_fields"]
        if field.startswith("target_site_stores")
        or field.startswith("site_price_matrix")
        or field.startswith("price_tiers")
        or field == "cnsc_price_tier_size_coverage"
    ]
    assert cnsc_keys == []
    assert detail["revision"] == revision_before + 1


def _valid_cnsc_config() -> dict:
    return {
        "listing_mode": "cnsc_global",
        "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
        "sizes": ["S"],
        "default_site_discount_percent": 40,
        "target_site_stores": [
            {
                "site_code": "PH",
                "store_id": "ph-store",
                "store_name": "海宁PH",
                "mabang_discount_id": "discount-ph",
                "discount_percent": 40,
            }
        ],
        "price_tiers": [
            {
                "id": "standard",
                "sizes": ["S"],
                "global_price_cny": 19.2,
                "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
            }
        ],
        "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
    }


def test_cnsc_problem_detection_agrees_with_validator(tmp_path: Path) -> None:
    valid = _valid_cnsc_config()
    # Empty problem set <=> validator does not raise.
    assert storage._cnsc_configuration_problems(valid) == []
    storage._validate_cnsc_store_template(valid)  # must not raise

    def mutate(**overrides: object) -> dict:
        config = _valid_cnsc_config()
        config.update(overrides)
        return config

    bad_cases = [
        mutate(target_site_stores=[]),
        mutate(price_tiers=[]),
        mutate(warehouse_stock_defaults={}),
        mutate(
            target_site_stores=[
                {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}
            ]
        ),
        # Price tier no longer covers the template's size set ("M" instead of "S").
        mutate(
            price_tiers=[
                {
                    "id": "standard",
                    "sizes": ["M"],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
                }
            ]
        ),
    ]
    for config in bad_cases:
        assert storage._cnsc_configuration_problems(config), config
        with pytest.raises(ValueError):
            storage._validate_cnsc_store_template(config)


def test_tier_matches_supports_size_color_and_uniform_binding() -> None:
    size_only = {"id": "standard", "sizes": ["S", "M"], "colors": []}
    color_only = {"id": "dress", "sizes": [], "colors": ["衣服"]}
    both = {"id": "dress_s", "sizes": ["S"], "colors": ["衣服"]}
    uniform = {"id": "uniform", "sizes": [], "colors": []}

    assert storage._tier_matches(size_only, "白色", "S")
    assert not storage._tier_matches(size_only, "白色", "L")
    assert storage._tier_matches(size_only, "假发", "M")

    assert storage._tier_matches(color_only, "衣服", "L")
    assert not storage._tier_matches(color_only, "假发", "L")

    assert storage._tier_matches(both, "衣服", "S")
    assert not storage._tier_matches(both, "衣服", "M")
    assert not storage._tier_matches(both, "假发", "S")

    assert storage._tier_matches(uniform, "衣服", "L")
    assert storage._tier_matches(uniform, "", "")
    # Legacy tiers without a colors key keep size-only semantics.
    assert storage._tier_matches({"id": "legacy", "sizes": ["S"]}, "任意", "S")
    assert not storage._tier_matches({"id": "legacy", "sizes": ["S"]}, "任意", "M")


def test_normalize_cnsc_price_tiers_parses_colors() -> None:
    tiers = storage._normalize_cnsc_price_tiers([
        {"id": "dress", "colors": "衣服, 假发", "sizes": "S, M", "global_price_cny": 19.2, "site_prices": {}},
        {"id": "legacy", "sizes": ["S"], "global_price_cny": 22.2, "site_prices": {}},
    ])

    assert tiers[0]["colors"] == ["衣服", "假发"]
    assert tiers[1]["colors"] == []


def test_cnsc_uniform_price_tier_covers_every_sku(tmp_path: Path) -> None:
    copy = _incomplete_cnsc_copy(tmp_path)
    detail = storage.update_store_listing_copy(
        copy["id"],
        {
            "target_site_stores": _complete_ph_target(),
            "price_tiers": [
                {
                    "id": "uniform",
                    "sizes": [],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
                }
            ],
        },
    )

    assert _cnsc_readiness_fields(detail) == []
    assert detail["skus"]
    assert all(sku["price_tier_id"] == "uniform" for sku in detail["skus"])
    assert all(sku["global_price_cny"] == 19.2 for sku in detail["skus"])
    assert all(
        sku["site_price_matrix"]["PH"] == {"sale_price": 599.4, "original_price": 999.0}
        for sku in detail["skus"]
    )


def test_cnsc_combo_product_price_tiers_bind_by_color(tmp_path: Path) -> None:
    copy = _combo_cnsc_copy(tmp_path)
    detail = storage.update_store_listing_copy(
        copy["id"],
        {
            "target_site_stores": _complete_ph_target(),
            "price_tiers": [
                {
                    "id": "dress",
                    "colors": ["衣服"],
                    "sizes": [],
                    "global_price_cny": 39.2,
                    "site_prices": {"PH": {"sale_price": 1198.8, "original_price": 1998}},
                },
                {
                    "id": "wig",
                    "colors": ["假发"],
                    "sizes": [],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
                },
            ],
        },
    )

    assert _cnsc_readiness_fields(detail) == []
    by_color = {sku["color_code"]: sku for sku in detail["skus"]}
    assert set(by_color) == {"衣服", "假发"}
    assert by_color["衣服"]["price_tier_id"] == "dress"
    assert by_color["衣服"]["global_price_cny"] == 39.2
    assert by_color["衣服"]["site_price_matrix"]["PH"]["sale_price"] == 1198.8
    assert by_color["假发"]["price_tier_id"] == "wig"
    assert by_color["假发"]["global_price_cny"] == 19.2
    assert by_color["假发"]["site_price_matrix"]["PH"]["sale_price"] == 599.4


def test_cnsc_combo_price_tier_overlap_and_gap_report_coverage(tmp_path: Path) -> None:
    copy = _combo_cnsc_copy(tmp_path)
    # Both tiers claim 衣服 → (衣服,S) overlaps two tiers while (假发,S) is uncovered.
    overlapping = storage.update_store_listing_copy(
        copy["id"],
        {
            "target_site_stores": _complete_ph_target(),
            "price_tiers": [
                {
                    "id": "dress",
                    "colors": ["衣服"],
                    "sizes": [],
                    "global_price_cny": 39.2,
                    "site_prices": {"PH": {"sale_price": 1198.8, "original_price": 1998}},
                },
                {
                    "id": "wig",
                    "colors": ["衣服"],
                    "sizes": [],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
                },
            ],
        },
    )
    assert "cnsc_price_tier_size_coverage" in overlapping["readiness"]["missing_fields"]

    # A single tier bound to 衣服 only leaves (假发,S) uncovered.
    uncovered = storage.update_store_listing_copy(
        copy["id"],
        {
            "price_tiers": [
                {
                    "id": "dress",
                    "colors": ["衣服"],
                    "sizes": [],
                    "global_price_cny": 39.2,
                    "site_prices": {"PH": {"sale_price": 1198.8, "original_price": 1998}},
                }
            ],
        },
    )
    assert "cnsc_price_tier_size_coverage" in uncovered["readiness"]["missing_fields"]


def test_copy_persists_binding_mode_and_keeps_two_size_tiers(tmp_path: Path) -> None:
    """Bug2 regression: a base product without color tiers copied to a two-size-tier
    template must NOT collapse into a single uniform tier — the copy keeps both
    standard/large tiers and persists cnsc_binding_mode="sizes"."""
    folder = tmp_path / "SMOKE2001 双档卫衣"
    folder.mkdir()
    for name in ["主图.jpg", "黑色.jpg", "白色.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD 男装T恤"}
    )
    # base 无颜色档（不填 price_tiers）
    assert set(base.get("recognized_colors") or []) == {"Black", "White"}
    template = storage.create_store_template(
        {
            "store_name": "广州小苗服饰有限公司",
            "template_name": "CNSC 双档",
            "listing_mode": "cnsc_global",
            "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
            "sizes": ["S", "M", "L"],
            "default_site_discount_percent": 40,
            "target_site_stores": [
                {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}
            ],
            "price_tiers": [
                {
                    "id": "standard",
                    "sizes": ["S", "M"],
                    "global_price_cny": 30,
                    "site_prices": {"PH": {"sale_price": 60, "original_price": 100}},
                },
                {
                    "id": "large",
                    "sizes": ["L"],
                    "global_price_cny": 40,
                    "site_prices": {"PH": {"sale_price": 84, "original_price": 140}},
                },
            ],
            "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    assert result["created_count"] == 1
    copy = result["created"][0]
    assert copy.get("cnsc_binding_mode") == "sizes"
    tier_ids = [tier.get("id") for tier in copy.get("price_tiers") or []]
    assert tier_ids == ["standard", "large"]
    # SKU 各尺码命中各自档位，价格保留
    detail = storage.get_store_listing_copy_detail(copy["id"])
    assert detail is not None
    by_size = {sku["size"]: sku for sku in detail["skus"] if sku["color_code"] == "Black"}
    assert by_size["S"]["price_tier_id"] == "standard"
    assert by_size["L"]["price_tier_id"] == "large"
    assert by_size["S"]["site_price_matrix"]["PH"] == {
        "original_price": 100.0,
        "sale_price": 60.0,
    }
    assert by_size["L"]["site_price_matrix"]["PH"] == {
        "original_price": 140.0,
        "sale_price": 84.0,
    }


def test_base_color_price_original_only_survives_copy_and_sale_is_derived(tmp_path: Path) -> None:
    """Bug1 regression: the base page only fills original_price (sale stays empty);
    saving must not drop the site entry, and copying must derive
    sale = original × (1 - %OFF)."""
    folder = tmp_path / "SMOKE2002 原价卫衣"
    folder.mkdir()
    for name in ["主图.jpg", "黑色.jpg", "白色.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD 男装T恤"}
    )
    storage.update_base_product(base["id"], {"recognized_colors": ["black", "white"]})
    # 前端基础商品页只填 original_price（无 sale_price 键）
    updated = storage.update_base_product(
        base["id"],
        {
            "price_tiers": [
                {
                    "id": "Black",
                    "colors": ["Black"],
                    "sizes": [],
                    "cost_price": 12,
                    "site_prices": {"PH": {"original_price": 200}, "TW": {"original_price": 400}},
                }
            ],
        },
    )
    tiers = updated.get("price_tiers") or []
    # 保存后 original-only 站点条目不被丢弃
    assert tiers and tiers[0]["site_prices"]["PH"]["original_price"] == 200.0
    assert tiers[0]["site_prices"]["TW"]["original_price"] == 400.0
    assert tiers[0]["site_prices"]["PH"]["sale_price"] == ""

    template = storage.create_store_template(
        {
            "store_name": "广州小苗服饰有限公司",
            "template_name": "CNSC 单档",
            "listing_mode": "cnsc_global",
            "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
            "sizes": ["S"],
            "default_site_discount_percent": 40,
            "target_site_stores": [
                {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}
            ],
            "price_tiers": [
                {
                    "id": "uniform",
                    "sizes": [],
                    "global_price_cny": 30,
                    "site_prices": {"PH": {"sale_price": 60, "original_price": 100}},
                }
            ],
            "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    copy = result["created"][0]
    assert copy.get("cnsc_binding_mode") == "colors"
    sku = storage.get_store_listing_copy_detail(copy["id"])["skus"][0]
    # 复制时售价按 40%OFF 推导，PH original 200 → sale 120
    assert sku["site_price_matrix"]["PH"] == {
        "original_price": 200.0,
        "sale_price": 120.0,
    }
    assert sku["price_tier_id"] == "Black"


def test_update_rejects_invalid_binding_mode_and_readiness_accepts_empty_sale(tmp_path: Path) -> None:
    copy = _incomplete_cnsc_copy(tmp_path)
    # fixture 副本 SKU 颜色为 White（文件夹 白色.jpg）
    # 非法 cnsc_binding_mode 被拒
    with pytest.raises(ValueError, match="cnsc_binding_mode must be colors or sizes"):
        storage.update_store_listing_copy(copy["id"], {"cnsc_binding_mode": "uniform"})
    # 合法 colors 持久化
    detail = storage.update_store_listing_copy(
        copy["id"],
        {
            "cnsc_binding_mode": "colors",
            "target_site_stores": _complete_ph_target(),
            "price_tiers": [
                {
                    "id": "White",
                    "colors": ["White"],
                    "sizes": [],
                    "global_price_cny": "",
                    "site_prices": {"PH": {"original_price": 999}},
                }
            ],
        },
    )
    assert detail.get("cnsc_binding_mode") == "colors"
    assert any(sku["price_tier_id"] == "White" for sku in detail["skus"])
    # original-only 站点不再报 site_price_matrix 缺失（sale 可空由折扣推导）
    assert "site_price_matrix" not in " ".join(
        field for field in detail["readiness"]["missing_fields"] if field.startswith("site_price_matrix")
    )
    # sale > original 仍报错
    bad = storage.update_store_listing_copy(
        copy["id"],
        {
            "cnsc_binding_mode": "colors",
            "price_tiers": [
                {
                    "id": "White",
                    "colors": ["White"],
                    "sizes": [],
                    "global_price_cny": "",
                    "site_prices": {"PH": {"sale_price": 1200, "original_price": 999}},
                }
            ],
        },
    )
    assert any(
        field.startswith("site_price_matrix.") for field in bad["readiness"]["missing_fields"]
    )


def test_legacy_copy_without_binding_mode_infers_colors_for_color_tiers(tmp_path: Path) -> None:
    """旧副本没有 cnsc_binding_mode 字段时，按档位形状回填：带 colors 的档 → colors，
    单 uniform 档（无 sizes/colors）→ colors（一价覆盖全部 SKU 的旧全覆盖语义）。"""
    # 单 uniform 档（_incomplete_cnsc_copy 的模板即 uniform 单档）→ 推断 colors
    copy = _incomplete_cnsc_copy(tmp_path)
    assert copy.get("cnsc_binding_mode") == "colors" or "cnsc_binding_mode" not in copy
    # 直接手工删掉该字段模拟旧数据
    import json  # noqa: PLC0415
    data = json.loads(open(settings.STORE_FILE, encoding="utf-8").read())
    for row in data.get("store_listing_copies", []):
        if row.get("id") == copy["id"]:
            row.pop("cnsc_binding_mode", None)
    open(settings.STORE_FILE, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2))
    detail = storage.get_store_listing_copy_detail(copy["id"])
    assert detail is not None
    # readiness 对推断后的形状仍通过（单 uniform colors 行 = 合法）
    assert "price_tiers" not in " ".join(detail["readiness"]["missing_fields"])


def test_copy_respects_filled_sale_and_derives_empty_sale_per_site(tmp_path: Path) -> None:
    """基础商品双价行复制语义：PH 手填售价 150 被尊重；TW 售价留空按 40%OFF
    推导（400 → 240）。"""
    folder = tmp_path / "SMOKE2003 双价卫衣"
    folder.mkdir()
    for name in ["主图.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD 男装T恤"}
    )
    storage.update_base_product(base["id"], {"recognized_colors": ["black"]})
    storage.update_base_product(
        base["id"],
        {
            "price_tiers": [
                {
                    "id": "Black",
                    "colors": ["Black"],
                    "sizes": [],
                    "cost_price": 12,
                    "site_prices": {
                        "PH": {"sale_price": 150, "original_price": 200},
                        "TW": {"original_price": 400},  # 无 sale_price 键（留空）
                    },
                }
            ],
        },
    )
    template = storage.create_store_template(
        {
            "store_name": "广州小苗服饰有限公司",
            "template_name": "CNSC 双站",
            "listing_mode": "cnsc_global",
            "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
            "sizes": ["S"],
            "default_site_discount_percent": 40,
            "target_site_stores": [
                {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH", "mabang_discount_id": "disc-ph", "discount_percent": 40},
                {"site_code": "TW", "store_id": "tw-store", "store_name": "台湾TW", "mabang_discount_id": "disc-tw", "discount_percent": 40},
            ],
            "price_tiers": [
                {
                    "id": "uniform",
                    "sizes": [],
                    "global_price_cny": 30,
                    "site_prices": {
                        "PH": {"sale_price": 1, "original_price": 2},
                        "TW": {"sale_price": 1, "original_price": 2},
                    },
                }
            ],
            "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    sku = storage.get_store_listing_copy_detail(result["created"][0]["id"])["skus"][0]
    matrix = sku["site_price_matrix"]
    # PH 手填售价 150 被尊重；TW 留空按 40%OFF 推导 400×0.6=240
    assert matrix["PH"] == {"original_price": 200.0, "sale_price": 150.0}
    assert matrix["TW"] == {"original_price": 400.0, "sale_price": 240.0}


def test_copy_carries_recognized_attributes_from_base(tmp_path: Path) -> None:
    """基础商品 AI 识别属性随复制带到店铺副本，字段行按属性名取值才能生效。"""
    folder = tmp_path / "ATTR1001 发饰商品"
    folder.mkdir()
    (folder / "主图.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "发饰"}
    )
    base = storage.update_base_product(
        base["id"],
        {
            "recognized_attributes": {
                "Item Type": "Hair Ornament",
                "Material": "Zinc Alloy",
                "Style": "Anime",
                "Theme": "Anime Character",
                "Pattern Type": "Flower",
                "Applicable Scene": "Cosplay",
                "Applicable Season": "Four Seasons",
                "Gender": "Women",
            }
        },
    )
    template = storage.create_store_template(
        {
            "store_name": "广州小苗服饰有限公司",
            "template_name": "发饰属性模板",
            "sizes": ["S"],
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    copy = result["created"][0]
    assert copy.get("recognized_attributes") == {
        "Item Type": "Hair Ornament",
        "Material": "Zinc Alloy",
        "Style": "Anime",
        "Theme": "Anime Character",
        "Pattern Type": "Flower",
        "Applicable Scene": "Cosplay",
        "Applicable Season": "Four Seasons",
        "Gender": "Women",
    }


def _cnsc_copy_with_case_drifted_color_tiers(tmp_path: Path) -> dict:
    """Base product whose saved price tiers keep pre-rename casing ("Wig and ear")
    while recognized_colors are title-cased ("Wig And Ear") — the drift the
    2026-09-21 color title-case rollout left behind on 09-21 data."""
    folder = tmp_path / "CNSC4001 假发组合"
    folder.mkdir()
    (folder / "主图.jpg").write_bytes(b"image")
    (folder / "白色.jpg").write_bytes(b"image")
    base = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD 组合商品"}
    )
    base = storage.update_base_product(
        base["id"],
        {
            "recognized_colors": ["Wig And Ear", "Wig"],
            "price_tiers": [
                {"id": "Wig and ear", "colors": ["Wig and ear"], "cost_price": 58},
                {"id": "Wig", "colors": ["Wig"], "cost_price": 35},
            ],
        },
    )
    template = storage.create_store_template(
        {
            "store_name": "广州小苗服饰有限公司",
            "template_name": "CNSC 大小写漂移",
            "listing_mode": "cnsc_global",
            "cnsc_store": {"id": "cnsc-store", "name": "广州小苗服饰有限公司"},
            "sizes": ["S"],
            "default_site_discount_percent": 40,
            "target_site_stores": [
                {"site_code": "PH", "store_id": "ph-store", "store_name": "海宁PH"}
            ],
            "price_tiers": [
                {
                    "id": "uniform",
                    "sizes": [],
                    "global_price_cny": 19.2,
                    "site_prices": {"PH": {"sale_price": 599.4, "original_price": 999}},
                }
            ],
            "warehouse_stock_defaults": {"TH": 300, "CN_GZ": 0, "PH": 0},
        }
    )
    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    return result["created"][0]


def test_cnsc_copy_carries_cost_price_across_color_case_drift(tmp_path: Path) -> None:
    copy = _cnsc_copy_with_case_drifted_color_tiers(tmp_path)
    detail = storage.get_store_listing_copy_detail(copy["id"])

    missing = detail["readiness"]["missing_fields"]
    assert "base_product_cost_price" not in missing
    # 每个颜色档的全球价 = 该档成本价（_cnsc_copy_price_tiers），大小写漂移不影响。
    tiers = {tier["id"]: tier for tier in detail["price_tiers"]}
    assert tiers["Wig and ear"]["global_price_cny"] == 58.0
    assert tiers["Wig"]["global_price_cny"] == 35.0
    # 价格行成本价按 (color,size) 匹配基础商品档位取到，颜色大小写不同也能对上。
    costs = {sku["color_code"]: sku["price"]["cost_price"] for sku in detail["skus"]}
    assert costs == {"Wig And Ear": 58.0, "Wig": 35.0}
    globals_by_color = {sku["color_code"]: sku["global_price_cny"] for sku in detail["skus"]}
    assert globals_by_color == {"Wig And Ear": 58.0, "Wig": 35.0}


def test_cnsc_copy_editor_save_refills_color_tier_global_and_cost(tmp_path: Path) -> None:
    copy = _cnsc_copy_with_case_drifted_color_tiers(tmp_path)
    # 模拟副本编辑器 colors 模式保存：档位按副本颜色重建，global 留空
    # （界面不展示该列）、cost_price 不随行携带。
    updated = storage.update_store_listing_copy(
        copy["id"],
        {
            "price_tiers": [
                {
                    "id": "Wig And Ear",
                    "colors": ["Wig And Ear"],
                    "global_price_cny": "",
                    "site_prices": {"PH": {"original_price": 999}},
                },
                {
                    "id": "Wig",
                    "colors": ["Wig"],
                    "global_price_cny": "",
                    "site_prices": {"PH": {"original_price": 888}},
                },
            ]
        },
    )

    tiers = {tier["id"]: tier for tier in updated["price_tiers"]}
    # 空全球价从基础商品同颜色档成本价补齐（而非模板 uniform 档的 19.2）。
    assert tiers["Wig And Ear"]["global_price_cny"] == 58.0
    assert tiers["Wig"]["global_price_cny"] == 35.0
    detail = storage.get_store_listing_copy_detail(copy["id"])
    missing = detail["readiness"]["missing_fields"]
    assert "base_product_cost_price" not in missing
    assert not [field for field in missing if field.startswith("price_tiers.")]
    costs = {sku["color_code"]: sku["price"]["cost_price"] for sku in detail["skus"]}
    assert costs == {"Wig And Ear": 58.0, "Wig": 35.0}
    globals_by_color = {sku["color_code"]: sku["global_price_cny"] for sku in detail["skus"]}
    assert globals_by_color == {"Wig And Ear": 58.0, "Wig": 35.0}
