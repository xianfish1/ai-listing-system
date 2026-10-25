"""Fetch remaining AI credit for provider accounts that expose a balance endpoint.

Only DeepSeek (via the Anthropic/OpenAI-compatible base_url ``https://api.deepseek.com``)
is wired up today — it exposes ``GET /user/balance`` returning::

    {
      "is_available": true,
      "balance_infos": [
        {"currency": "USD", "total_balance": "10.00",
         "granted_balance": "0.00", "topped_up_balance": "10.00"},
        {"currency": "CNY", "total_balance": "..." }
      ]
    }

Callers should treat every failure as non-fatal — this is a courtesy indicator, never
part of an automation flow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request as urllib_request


DEEPSEEK_HOSTS = ("api.deepseek.com", "api.deepseek.ai")


@dataclass(frozen=True)
class BalanceResult:
    provider: str  # "deepseek" | "unsupported"
    is_available: bool
    balances: tuple[dict[str, str], ...]  # one entry per currency; strings preserve provider precision
    fetched_at: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "is_available": self.is_available,
            "balances": [dict(b) for b in self.balances],
            "fetched_at": self.fetched_at,
            "error": self.error,
        }


def _is_deepseek(base_url: str) -> bool:
    host = str(base_url or "").lower()
    return any(candidate in host for candidate in DEEPSEEK_HOSTS)


def supports_balance(base_url: str, protocol: str = "") -> bool:
    """True if the given connection points at a provider we know how to query."""
    return _is_deepseek(base_url)


def _deepseek_balance_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("AI 接口地址不能为空")
    # DeepSeek's balance endpoint sits at /user/balance regardless of whether the
    # user configured base_url as https://api.deepseek.com or …/v1. Strip the /v1
    # suffix when present.
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/user/balance"


def _parse_deepseek(payload: dict[str, Any]) -> tuple[bool, tuple[dict[str, str], ...]]:
    is_available = bool(payload.get("is_available"))
    raw_balances = payload.get("balance_infos") or []
    if not isinstance(raw_balances, list):
        return is_available, ()
    normalized: list[dict[str, str]] = []
    for item in raw_balances:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "currency": str(item.get("currency") or "").strip().upper(),
            "total_balance": str(item.get("total_balance") or "0"),
            "granted_balance": str(item.get("granted_balance") or "0"),
            "topped_up_balance": str(item.get("topped_up_balance") or "0"),
        })
    return is_available, tuple(normalized)


def fetch_balance(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = 15.0,
    now: str = "",
    urlopen: Callable[..., Any] = urllib_request.urlopen,
) -> BalanceResult:
    """Best-effort balance fetch. Returns provider='unsupported' + an empty result
    when we don't know how to query the endpoint; never raises."""

    if not _is_deepseek(base_url):
        return BalanceResult(provider="unsupported", is_available=False, balances=(), fetched_at=now)
    try:
        url = _deepseek_balance_url(base_url)
    except ValueError as exc:
        return BalanceResult(provider="deepseek", is_available=False, balances=(), fetched_at=now, error=str(exc))
    req = urllib_request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return BalanceResult(
            provider="deepseek", is_available=False, balances=(), fetched_at=now,
            error=f"HTTP {exc.code}: {detail[:200]}",
        )
    except (error.URLError, TimeoutError) as exc:
        return BalanceResult(
            provider="deepseek", is_available=False, balances=(), fetched_at=now,
            error=f"网络错误: {exc}",
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return BalanceResult(
            provider="deepseek", is_available=False, balances=(), fetched_at=now,
            error=f"响应不是 JSON: {exc}",
        )
    if not isinstance(payload, dict):
        return BalanceResult(
            provider="deepseek", is_available=False, balances=(), fetched_at=now,
            error="响应格式异常（不是对象）",
        )
    is_available, balances = _parse_deepseek(payload)
    return BalanceResult(
        provider="deepseek", is_available=is_available, balances=balances,
        fetched_at=now, error="",
    )
