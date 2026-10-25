from __future__ import annotations

import pytest

from services.api.attribute_recommendation import recommend_attribute_values


class _FakeGateway:
    """Returns a canned generation result; records the last request."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_user = ""

    def generate(self, connection, request):  # noqa: ANN001, ANN202
        self.last_user = request.user
        return type("Generated", (), {"text": self.reply})()


def _field(field_id: str, *, required: bool = True, options: list[dict] | None = None, field_type: str = "single_select") -> dict:
    return {"field_id": field_id, "label": f"字段{field_id}", "required": required, "type": field_type, "options": options or []}


def test_enum_values_are_kept_only_when_in_options() -> None:
    gateway = _FakeGateway('{"values": {"100134": "1149", "100162": "999999"}}')
    result = recommend_attribute_values(
        gateway,
        {"model_id": "test"},
        [_field("100134", options=[{"value": "1149", "label": "棉花"}]), _field("100162", options=[{"value": "1428", "label": "蜡染"}])],
    )
    # 枚举外的值丢弃（宁缺勿错，自选值由操作员手动加）
    assert result["values"] == {"100134": "1149"}
    assert result["skipped"] == ["100162"]


def test_multi_select_returns_filtered_array() -> None:
    gateway = _FakeGateway('{"values": {"100162": ["1561", "1428", "bad"]}}')
    result = recommend_attribute_values(
        gateway,
        {"model_id": "test"},
        [_field("100162", field_type="multi_select", options=[{"value": "1561", "label": "a"}, {"value": "1428", "label": "b"}])],
    )
    assert result["values"] == {"100162": ["1561", "1428"]}


def test_text_fields_accept_short_text_and_strip_fences() -> None:
    gateway = _FakeGateway('```json\n{"values": {"101639": "POD-Tee-001", "100037": "Philippines"}}\n```')
    result = recommend_attribute_values(
        gateway,
        {"model_id": "test"},
        [_field("101639", options=None, field_type="text"), _field("100037", options=None, field_type="text")],
    )
    assert result["values"] == {"101639": "POD-Tee-001", "100037": "Philippines"}


def test_unknown_fields_and_missing_values_are_ignored() -> None:
    gateway = _FakeGateway('{"values": {"999999": "x", "100134": "", "100162": "   "}}')
    result = recommend_attribute_values(
        gateway,
        {"model_id": "test"},
        [_field("100134", options=None, field_type="text"), _field("100162", options=None, field_type="text")],
    )
    assert result["values"] == {}
    assert sorted(result["skipped"]) == ["100134", "100162"]


def test_empty_reply_raises_after_retry() -> None:
    gateway = _FakeGateway("")
    with pytest.raises(ValueError):
        recommend_attribute_values(gateway, {"model_id": "test"}, [_field("100134", options=None, field_type="text")])


def test_prompt_context_keeps_title_description_and_recognized_product_facts() -> None:
    gateway = _FakeGateway('{"values": {"100134": "1149"}}')
    recommend_attribute_values(
        gateway,
        {"model_id": "test"},
        [_field("100134", options=[{"value": "1149", "label": "棉花"}])],
        {
            "product_name": "Lightning McQueen Tshirt",
            "description": "100% cotton cartoon tee",
            "recognized_attributes": {"Material": "Cotton", "Pattern Type": "Cartoon"},
            "recognized_colors": ["Blue", "Red"],
        },
    )
    assert "Lightning McQueen Tshirt" in gateway.last_user
    assert "100% cotton cartoon tee" in gateway.last_user
    assert "Pattern Type" in gateway.last_user
    assert "Blue" in gateway.last_user
