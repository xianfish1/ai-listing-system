from __future__ import annotations

import json
from urllib import request as urlrequest

from services.api.cnsc_price_matrix import parse_price_matrix
from tests.test_api_boundaries import _start_api


def test_parse_price_matrix_uses_site_codes_in_any_column_order():
    text = (
        "商品\tPH 菲律宾\tPH 上新价\tTW 台湾\tTW 上新价\tBR 巴西\tBR 上新价\n"
        "standard\t746\t1243\t342\t570\t97\t162\n"
        "large\t811\t1352\t487\t811\t145\t242"
    )

    result = parse_price_matrix(
        text,
        tiers=[
            {"id": "standard", "sizes": ["XS", "S", "M", "L"]},
            {"id": "large", "sizes": ["XL", "XXL", "XXXL"]},
        ],
        target_sites=["TW", "PH", "BR"],
    )

    assert result["errors"] == []
    assert result["site_order"] == ["PH", "TW", "BR"]
    assert result["tiers"][0]["site_prices"]["TW"] == {"sale_price": 342.0, "original_price": 570.0}
    assert result["tiers"][1]["site_prices"]["BR"] == {"sale_price": 145.0, "original_price": 242.0}


def test_parse_price_matrix_rejects_missing_duplicate_and_invalid_prices():
    text = (
        "TW 台湾\tTW 上新价\tTW 台湾重复\tPH 菲律宾\n"
        "standard\t0\t570\t746\n"
        "large\t500\t400\tbad"
    )

    result = parse_price_matrix(
        text,
        tiers=[{"id": "standard", "sizes": ["S"]}, {"id": "large", "sizes": ["XL"]}],
        target_sites=["TW", "PH", "SG"],
    )

    assert "missing_site:SG" in result["errors"]
    assert any(item.startswith("duplicate_site:TW") for item in result["errors"])
    assert "invalid_price:TW:standard:sale_price" in result["errors"]
    assert "sale_price_greater_than_original:TW:large" in result["errors"]
    assert "invalid_price:PH:large:original_price" in result["errors"]


def test_parse_price_matrix_warns_for_unselected_sites_and_maps_rows_to_tiers():
    text = "MY 马来\tMY 上新价\tMY 原价\tVN 越南\tVN 上新价\tVN 原价\n342\t570\t800\t900\t1000"
    result = parse_price_matrix(
        text,
        tiers=[{"id": "standard", "sizes": ["S"]}],
        target_sites=["MY"],
    )

    assert result["errors"] == []
    assert "unselected_site:VN" in result["warnings"]
    assert result["tiers"][0]["site_prices"]["MY"] == {"sale_price": 342.0, "original_price": 570.0}


def test_parse_price_matrix_accepts_site_label_plus_sale_and_original_headers():
    text = (
        "商品\tPH 菲律宾\tPH 售价\tPH 原价\tTW 台湾\tTW 售价\tTW 原价\n"
        "standard\t746\t746\t1243\t342\t342\t570\n"
        "large\t811\t811\t1352\t487\t487\t811"
    )
    result = parse_price_matrix(
        text,
        tiers=[{"id": "standard"}, {"id": "large"}],
        target_sites=["PH", "TW"],
    )
    assert result["errors"] == []
    assert result["tiers"][1]["site_prices"]["TW"] == {"sale_price": 487.0, "original_price": 811.0}


def test_parse_price_matrix_uses_price_and_discount_price_semantics_in_any_order():
    text = (
        "档位\tTW 价格\tTW 折扣价\tPH 折扣价\tPH 价格\n"
        "standard\t372\t223.2\t599.4\t999\n"
        "large\t405\t243\t659.4\t1099"
    )

    result = parse_price_matrix(
        text,
        tiers=[{"id": "standard"}, {"id": "large"}],
        target_sites=["TW", "PH"],
    )

    assert result["errors"] == []
    assert result["tiers"][0]["site_prices"] == {
        "TW": {"original_price": 372.0, "sale_price": 223.2},
        "PH": {"original_price": 999.0, "sale_price": 599.4},
    }


def test_parse_price_matrix_echoes_tier_colors_for_combo_products():
    text = "商品\tPH 售价\tPH 原价\ndress\t746\t1243"
    result = parse_price_matrix(
        text,
        tiers=[{"id": "dress", "sizes": [], "colors": ["衣服"]}],
        target_sites=["PH"],
    )

    assert result["errors"] == []
    assert result["tiers"][0]["colors"] == ["衣服"]
    assert result["tiers"][0]["site_prices"]["PH"] == {"sale_price": 746.0, "original_price": 1243.0}


def test_parse_price_matrix_accepts_headerless_numeric_rows():
    # Pasted straight from CNSC without the site header: two numeric columns
    # per site (sale, original) in the UI target-site order.
    text = (
        "315\t525\t696\t1160\t19\t32\t348\t580\t48\t81\t331\t551\t290595\t363000\t92\t153\n"
        "325\t545\t706\t1180\t21\t34\t358\t590\t50\t83\t341\t561\t291595\t364000\t94\t155"
    )
    targets = ["TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR"]

    result = parse_price_matrix(
        text,
        tiers=[{"id": "tier_1"}, {"id": "tier_2"}],
        target_sites=targets,
    )

    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["site_order"] == targets
    assert result["tiers"][0]["site_prices"]["TW"] == {"sale_price": 315.0, "original_price": 525.0}
    assert result["tiers"][0]["site_prices"]["VN"] == {"sale_price": 290595.0, "original_price": 363000.0}
    assert result["tiers"][1]["site_prices"]["BR"] == {"sale_price": 94.0, "original_price": 155.0}


def test_parse_price_matrix_headerless_tolerates_leading_tier_labels():
    text = (
        "标准\t315\t525\t696\t1160\n"
        "加大\t325\t545\t706\t1180"
    )

    result = parse_price_matrix(
        text,
        tiers=[{"id": "tier_1"}, {"id": "tier_2"}],
        target_sites=["TW", "PH"],
    )

    assert result["errors"] == []
    assert result["tiers"][1]["site_prices"]["PH"] == {"sale_price": 706.0, "original_price": 1180.0}


def test_parse_price_matrix_short_paste_fills_remaining_tiers_from_last_row():
    text = "315\t525\t696\t1160\t19\t32\t348\t580\t48\t81\t331\t551\t290595\t363000\t92\t153"
    targets = ["TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR"]

    result = parse_price_matrix(
        text,
        tiers=[{"id": "tier_1"}, {"id": "tier_2"}],
        target_sites=targets,
    )

    assert result["errors"] == []
    assert result["warnings"] == ["tier_row_count:1:2"]
    expected = {"sale_price": 315.0, "original_price": 525.0}
    assert result["tiers"][0]["site_prices"]["TW"] == expected
    assert result["tiers"][1]["site_prices"]["TW"] == expected


def test_parse_price_matrix_header_paste_fills_remaining_tiers_from_last_row():
    text = "商品\tTW 售价\tTW 原价\tPH 售价\tPH 原价\nstandard\t315\t525\t696\t1160"
    result = parse_price_matrix(
        text,
        tiers=[{"id": "standard"}, {"id": "large"}],
        target_sites=["TW", "PH"],
    )

    assert result["errors"] == []
    assert result["warnings"] == ["tier_row_count:1:2"]
    assert result["tiers"][1]["site_prices"]["PH"] == {"sale_price": 696.0, "original_price": 1160.0}


def test_parse_price_matrix_empty_data_rows_with_header_still_errors():
    text = "商品\tTW 售价\tTW 原价\tPH 售价\tPH 原价"
    result = parse_price_matrix(
        text,
        tiers=[{"id": "standard"}, {"id": "large"}],
        target_sites=["TW", "PH"],
    )

    assert "tier_row_count:0:2" in result["errors"]
    assert result["tiers"][0]["site_prices"] == {}


def test_parse_price_matrix_headerless_follows_target_site_order():
    # Column pairs map positionally onto target_sites in the order given, so
    # the caller controls recognition order (CNSC page order vs editor order).
    text = "19\t32\t315\t525"
    result = parse_price_matrix(
        text,
        tiers=[{"id": "tier_1"}],
        target_sites=["SG", "TW"],
    )

    assert result["errors"] == []
    assert result["tiers"][0]["site_prices"]["SG"] == {"sale_price": 19.0, "original_price": 32.0}
    assert result["tiers"][0]["site_prices"]["TW"] == {"sale_price": 315.0, "original_price": 525.0}


def test_parse_price_matrix_accepts_space_separated_numeric_rows():
    # A paste that lost its tabs (single space-separated line) still parses.
    text = "315 525 696 1160 19 32 348 580 48 81 331 551 290595 363000 92 153"
    targets = ["TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR"]

    result = parse_price_matrix(
        text,
        tiers=[{"id": "tier_1"}, {"id": "tier_2"}],
        target_sites=targets,
    )

    assert result["errors"] == []
    assert result["warnings"] == ["tier_row_count:1:2"]
    assert result["tiers"][0]["site_prices"]["TW"] == {"sale_price": 315.0, "original_price": 525.0}


def test_parse_price_matrix_keeps_spaced_header_cells_intact():
    # Header cells like "TW 售价" contain spaces and must stay single cells
    # even though bare-numeric lines get whitespace-split.
    text = (
        "商品\tTW 售价\tTW 原价\tPH 售价\tPH 原价\n"
        "dress\t746\t1243\t999\t1500"
    )
    result = parse_price_matrix(
        text,
        tiers=[{"id": "dress"}],
        target_sites=["TW", "PH"],
    )

    assert result["errors"] == []
    assert result["tiers"][0]["site_prices"]["TW"] == {"sale_price": 746.0, "original_price": 1243.0}


def test_cnsc_price_matrix_preview_endpoint_accepts_post() -> None:
    # The frontend previews the pasted matrix via POST; this guards the route
    # against being registered on the wrong HTTP method again.
    server, base = _start_api()
    try:
        req = urlrequest.Request(
            f"{base}/api/cnsc-price-matrix/preview",
            data=json.dumps({
                "text": "商品\tPH 售价\tPH 原价\ndress\t746\t1243",
                "tiers": [{"id": "dress", "sizes": [], "colors": ["衣服"]}],
                "target_sites": ["PH"],
            }).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlrequest.urlopen(req, timeout=10) as response:
            assert response.status == 200
            result = json.loads(response.read().decode())
    finally:
        server.shutdown()
        server.server_close()

    assert result["errors"] == []
    assert result["tiers"][0]["colors"] == ["衣服"]
    assert result["tiers"][0]["site_prices"]["PH"] == {"sale_price": 746.0, "original_price": 1243.0}
