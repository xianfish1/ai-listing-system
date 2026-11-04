"""AI 先识别款式人群、再据此匹配标题规则（2026-11-03）。"""
from __future__ import annotations

from services.api import main, title_selection, vision_listing


def _context(audience: str = "unknown") -> dict:
    return {"skill": {"id": "default", "audience": "boys"}, "scope": {"audience": audience, "category": "tshirt"}, "warnings": []}


def test_unknown_audience_is_detected_and_rule_is_rebuilt(monkeypatch):
    overrides_seen = []

    def fake_build_context(product, skills, batches, rows_for_batch, *, overrides=None):
        overrides_seen.append(dict(overrides or {}))
        return {"skill": {"id": "girls-rule", "audience": overrides.get("audience")}, "scope": {"audience": overrides.get("audience"), "category": "tshirt"}, "warnings": []}

    monkeypatch.setattr(title_selection, "build_context", fake_build_context)
    monkeypatch.setattr(vision_listing, "detect_product_audience", lambda **kw: ("girls", ""))

    context, detected, error = main._apply_ai_audience_to_title_context(
        _context(), {"title_category": "tshirt"}, [], object(), {"id": "c"}, {}
    )

    assert (detected, error) == ("girls", "")
    assert overrides_seen[-1]["audience"] == "girls"
    assert context["skill"]["id"] == "girls-rule"
    assert "女童" in context["warnings"][0]


def test_operator_audience_wins_and_skips_detection(monkeypatch):
    calls = []
    monkeypatch.setattr(vision_listing, "detect_product_audience", lambda **kw: calls.append(kw) or ("girls", ""))

    context = _context("men")
    out, detected, error = main._apply_ai_audience_to_title_context(
        context, {"title_category": "tshirt"}, [], object(), {"id": "c"}, {"audience": "men"}
    )

    assert (out, detected, error) == (context, "", "")
    assert calls == []


def test_confirmed_attributes_skip_detection(monkeypatch):
    calls = []
    monkeypatch.setattr(vision_listing, "detect_product_audience", lambda **kw: calls.append(kw) or ("girls", ""))

    context = _context("girls")
    out, detected, error = main._apply_ai_audience_to_title_context(
        context, {"title_category": "tshirt", "recognized_attributes": {"Gender": "Girl"}}, [], object(), {"id": "c"}, {}
    )

    assert (out, detected, error) == (context, "", "")
    assert calls == []


def test_detection_failure_keeps_default_context(monkeypatch):
    monkeypatch.setattr(vision_listing, "detect_product_audience", lambda **kw: ("", "没有可用的产品图片"))

    context = _context()
    out, detected, error = main._apply_ai_audience_to_title_context(
        context, {"title_category": "tshirt"}, [], object(), {"id": "c"}, {}
    )

    assert out is context
    assert detected == ""
    assert "图片" in error
