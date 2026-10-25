"""Unit tests for services.api.ai_balance.

These tests use urlopen fakes instead of hitting the real DeepSeek endpoint.
"""

from __future__ import annotations

import io
import json
from urllib import error

from services.api import ai_balance


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _happy_urlopen(body: dict[str, object]):
    payload = json.dumps(body).encode("utf-8")

    def _fake(_req, timeout=None):  # noqa: ARG001 - urllib signature
        return _FakeResponse(payload)

    return _fake


def test_supports_balance_only_for_deepseek() -> None:
    assert ai_balance.supports_balance("https://api.deepseek.com")
    assert ai_balance.supports_balance("https://api.deepseek.com/v1")
    assert ai_balance.supports_balance("https://api.deepseek.ai/v1")
    assert not ai_balance.supports_balance("https://api.anthropic.com/v1")
    assert not ai_balance.supports_balance("https://api.openai.com/v1")


def test_fetch_balance_unsupported_provider_short_circuits() -> None:
    result = ai_balance.fetch_balance(
        base_url="https://api.anthropic.com/v1",
        api_key="never-used",
        now="2026-08-03T00:00:00Z",
    )
    assert result.provider == "unsupported"
    assert result.balances == ()
    assert result.error == ""


def test_fetch_balance_parses_deepseek_response() -> None:
    result = ai_balance.fetch_balance(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        now="2026-08-03T00:00:00Z",
        urlopen=_happy_urlopen({
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "12.34", "granted_balance": "0.00", "topped_up_balance": "12.34"},
                {"currency": "CNY", "total_balance": "88.00", "granted_balance": "0.00", "topped_up_balance": "88.00"},
            ],
        }),
    )
    assert result.provider == "deepseek"
    assert result.is_available is True
    assert result.balances[0]["currency"] == "USD"
    assert result.balances[0]["total_balance"] == "12.34"
    assert result.balances[1]["currency"] == "CNY"
    assert result.error == ""


def test_fetch_balance_strips_v1_suffix_when_building_url() -> None:
    captured: list[str] = []

    def _capture(req, timeout=None):  # noqa: ARG001
        captured.append(req.full_url)
        return _FakeResponse(json.dumps({"is_available": True, "balance_infos": []}).encode("utf-8"))

    ai_balance.fetch_balance(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        urlopen=_capture,
    )
    assert captured == ["https://api.deepseek.com/user/balance"]


def test_fetch_balance_records_http_error_without_raising() -> None:
    def _http_error(_req, timeout=None):  # noqa: ARG001
        raise error.HTTPError(
            url="https://api.deepseek.com/user/balance",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"invalid_api_key"}'),
        )

    result = ai_balance.fetch_balance(
        base_url="https://api.deepseek.com",
        api_key="wrong",
        urlopen=_http_error,
    )
    assert result.provider == "deepseek"
    assert result.is_available is False
    assert "HTTP 401" in result.error


def test_fetch_balance_records_network_error_without_raising() -> None:
    def _url_error(_req, timeout=None):  # noqa: ARG001
        raise error.URLError("Name or service not known")

    result = ai_balance.fetch_balance(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        urlopen=_url_error,
    )
    assert result.error.startswith("网络错误")


def test_balance_result_to_dict_round_trips() -> None:
    result = ai_balance.BalanceResult(
        provider="deepseek",
        is_available=True,
        balances=({"currency": "USD", "total_balance": "5.00", "granted_balance": "0", "topped_up_balance": "5.00"},),
        fetched_at="2026-08-03T10:00:00Z",
    )
    data = result.to_dict()
    assert data["provider"] == "deepseek"
    assert data["balances"][0]["currency"] == "USD"
    assert data["error"] == ""
