from datetime import date

from services.api import title_selection as selection


def skill(**kw):
    return {"id": "s", "name": "男装", "version": 1, "category": "tshirt", "audience": "men", "library_id": "l", "enabled": True, "is_default": True, "max_age_days": 30, **kw}


def batch(id, day, **kw):
    return {"id": id, "library_id": "l", "data_date": day, "uploaded_at": day, "enabled": True, "source": "export.xlsx", "source_type": "measured", "scope_categories": ["tshirt"], "scope_audiences": ["men"], **kw}


def row(word, **kw):
    return {"id": word, "word": word, "category": "tshirt", "audience": "men", "status": "enabled", "search_volume": 500, **kw}


PRODUCT = {"id": "p", "title_category": "tshirt", "target_audience": "men", "recognized_attributes": {"Material": "Polyester"}}


def test_newest_data_date_wins_and_never_fills_missing_words_from_history():
    batches = [batch("old", "2026-09-20", uploaded_at="2026-10-25"), batch("new", "2026-10-20")]
    rows = {"old": [row("vintage tshirt for men")], "new": [row("tshirt for men")]}
    ctx = selection.build_context(PRODUCT, [skill()], batches, rows.get, today=date(2026, 10, 25))
    assert ctx["batch"]["id"] == "new"
    assert [r["word"] for r in ctx["candidates"]] == ["tshirt for men"]
    assert not ctx["stale"]


def test_old_data_remains_reference_and_unknown_audience_does_not_use_mens_words():
    ctx = selection.build_context(PRODUCT, [skill()], [batch("old", "2026-09-20")], lambda _: [row("tshirt for men")], today=date(2026, 10, 25))
    assert ctx["stale"] and ctx["age_days"] == 35
    unknown = selection.build_context({"title_category": "tshirt"}, [skill(audience="all")], [batch("all", "2026-10-20", scope_audiences=["all"])], lambda _: [row("tshirt for men"), row("tshirt", audience="all")])
    assert [r["word"] for r in unknown["candidates"]] == ["tshirt"]
    assert unknown["warnings"]


def test_scope_and_facts_precede_volume():
    rows = [row("tshirt for men"), row("tshirt for kids", audience="kids", search_volume=99999), row("cotton tshirt for men"), row("spiderman tshirt for men"), row("tshirt", audience="all", status="pending")]
    ctx = selection.build_context(PRODUCT, [skill()], [batch("b", "2026-10-20")], lambda _: rows)
    assert [r["word"] for r in ctx["candidates"]] == ["tshirt for men"]
    assert len(ctx["rejected"]) == 4


def test_explicit_rule_must_match_product_and_disabled_batch_rejected():
    import pytest
    with pytest.raises(ValueError, match="范围"):
        selection.build_context(PRODUCT, [skill(audience="kids")], [], lambda _: [], overrides={"skill_id": "s"})
    with pytest.raises(ValueError, match="批次"):
        selection.build_context(PRODUCT, [skill()], [batch("b", "2026-10-20", enabled=False)], lambda _: [], overrides={"batch_id": "b"})


def test_title_validation_does_not_truncate_and_checks_material_and_banned_terms():
    ctx = selection.build_context(PRODUCT, [skill(banned_terms=["official"], title_max_chars=50)], [], lambda _: [])
    result = selection.validate_title("Official 100% Cotton tshirt for men " + "long " * 20, ctx)
    assert result["status"] == "BLOCKED"
    assert len(result["title"]) > 50
    assert any("材质" in w for w in result["warnings"])
    assert any("禁用" in w for w in result["warnings"])


def test_explicit_rule_errors_name_the_actual_cause():
    import pytest
    with pytest.raises(ValueError, match="不存在"):
        selection.resolve_skill(PRODUCT, [skill()], {"skill_id": "missing"})
    with pytest.raises(ValueError, match="已停用"):
        selection.resolve_skill(PRODUCT, [skill(enabled=False)], {"skill_id": "s"})


def test_scoped_defaults_and_ordinary_phrase_boundaries():
    selected = selection.resolve_skill(PRODUCT, [skill(id="general", category="all", audience="all"), skill()])
    assert selected["id"] == "s"
    assert not selection.contains_phrase("Cartoon tshirt", "art")
    assert selection.contains_phrase("Graphic T-Shirt for Men", "t shirt for men")


def test_boys_and_girls_are_distinct_audiences():
    boys = {"id": "b", "title_category": "tshirt", "recognized_attributes": {"Gender": "Boy"}}
    girls = {"id": "g", "title_category": "tshirt", "recognized_attributes": {"Gender": "Girl"}}
    assert selection.product_scope(boys)["audience"] == "boys"
    assert selection.product_scope(girls)["audience"] == "girls"
    # 兼容旧 target_audience 值
    assert selection.product_scope({"target_audience": "boy"})["audience"] == "boys"
    assert selection.product_scope({"target_audience": "girl"})["audience"] == "girls"
    # 泛童装关键词/规则仍可用于男童、女童
    assert selection.audience_matches("kids", "boys")
    assert selection.audience_matches("kids", "girls")
    assert not selection.audience_matches("boys", "girls")


def test_audience_words_in_titles_are_no_longer_blocking():
    # 2026-11-03 起人群由 AI 识别款式后匹配规则，标题文本里的人群/性别词不再拦截：
    # 「Spider-Man」拆出的 man 曾把男童标题全部判死。
    boys_ctx = {"scope": {"audience": "boys", "category": "tshirt"}, "skill": skill(audience="boys"), "facts": ["boy", "tshirt"], "candidates": []}
    assert selection.validate_title("Cartoon Print tshirt for girls", boys_ctx)["status"] != "BLOCKED"
    assert selection.validate_title("Cartoon Print tshirt for men", boys_ctx)["status"] != "BLOCKED"
    assert "人群" not in "".join(selection.validate_title("Cartoon Print tshirt for girls", boys_ctx)["errors"])

    girls_ctx = {"scope": {"audience": "girls", "category": "tshirt"}, "skill": skill(audience="girls"), "facts": ["girl", "tshirt"], "candidates": []}
    assert selection.validate_title("Cartoon Print tshirt for boys", girls_ctx)["status"] != "BLOCKED"


def test_spiderman_print_does_not_block_boys_title():
    boys_ctx = {"scope": {"audience": "boys", "category": "tshirt"}, "skill": skill(audience="boys"), "facts": ["boy", "tshirt", "spider"], "candidates": []}
    result = selection.validate_title("Cartoon damit Spider-Man t shirt for kids boy short-sleeve printed", boys_ctx)
    assert result["status"] != "BLOCKED"
    assert result["errors"] == []


def test_product_line_counts_as_category_evidence():
    # AI 属性还没生成（或生成失败）时，商品只有产品线这一条品类线索；
    # 不带进来会让 tshirt 规则匹配不上、静默退回「通用标题」。
    product = {"id": "p", "category": "POD Mens T-Shirt", "title_audience": "boys"}
    assert selection.product_scope(product)["category"] == "tshirt"
    assert selection.resolve_skill(product, [skill(audience="boys")])["id"] == "s"


def test_product_line_does_not_override_known_hoodie_path():
    product = {"id": "p", "category": "POD Mens T-Shirt", "marketplace_category_path": "Men Clothes > Hoodies & Sweatshirts > Hoodies"}
    assert selection.product_scope(product)["category"] == "hoodie"


def test_boys_fallback_preference_wins_for_unknown_imports():
    boys = skill(id="boys", audience="boys")
    girls = skill(id="girls", audience="girls")
    girls["created_at"] = girls["updated_at"] = "2099-01-01T00:00:00+00:00"
    selected = selection.resolve_skill({"id": "unknown"}, [girls, boys])
    assert selected["id"] == "boys"


def test_new_audience_values_are_valid_scope_overrides():
    ctx = selection.build_context(
        {"id": "p", "title_category": "tshirt"},
        [skill(id="girls", audience="girls")],
        [batch("b", "2026-10-20", scope_audiences=["girls"])],
        lambda _: [row("tshirt for girls", audience="girls")],
        overrides={"audience": "girls"},
    )
    assert ctx["scope"]["audience"] == "girls"
    assert ctx["skill"]["id"] == "girls"
