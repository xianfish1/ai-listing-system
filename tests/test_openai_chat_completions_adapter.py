from __future__ import annotations

import json
from urllib import error

import pytest

from services.api.model_gateway import GenerationRequest, ModelConnection
from services.api.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def close(self) -> None:
        return None


class _FakeUrlOpen:
    def __init__(self, payload: dict | None = None, exc: Exception | None = None) -> None:
        self.payload = payload or {}
        self.exc = exc
        self.requests = []

    def __call__(self, request, timeout=0):
        self.requests.append((request, timeout))
        if self.exc:
            raise self.exc
        return _Response(self.payload)


def _connection(base_url: str = "https://api.example/v1") -> ModelConnection:
    return ModelConnection(
        id="conn-1",
        name="OpenAI 兼容主模型",
        protocol="openai_chat_completions",
        base_url=base_url,
        model_id="gpt-test",
        api_version="",
        timeout_seconds=12,
        encrypted_api_key="",
    )


def _request() -> GenerationRequest:
    return GenerationRequest(system="system prompt", user="product data", max_tokens=250, temperature=0.3)


def test_openai_adapter_uses_chat_completions_contract_and_parses_usage() -> None:
    fake_urlopen = _FakeUrlOpen(
        {
            "id": "chatcmpl-1",
            "model": "gpt-test-2026",
            "choices": [{"message": {"role": "assistant", "content": "generated description"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 14, "completion_tokens": 9, "total_tokens": 23},
        }
    )
    adapter = OpenAIChatCompletionsAdapter(urlopen=fake_urlopen)

    result = adapter.generate(connection=_connection(), api_key="secret-key", request=_request())

    http_request, timeout = fake_urlopen.requests[0]
    headers = {key.lower(): value for key, value in http_request.header_items()}
    body = json.loads(http_request.data.decode("utf-8"))
    assert http_request.full_url == "https://api.example/v1/chat/completions"
    assert headers["authorization"] == "Bearer secret-key"
    assert headers["content-type"] == "application/json"
    assert headers["user-agent"].startswith("Mozilla/5.0")
    assert body == {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "product data"},
        ],
        "max_tokens": 250,
        "temperature": 0.3,
    }
    assert timeout == 12
    assert result.text == "generated description"
    assert result.model_id == "gpt-test-2026"
    assert result.stop_reason == "stop"
    assert result.usage == {"input_tokens": 14, "output_tokens": 9}


def test_openai_adapter_normalizes_base_url_without_v1() -> None:
    fake_urlopen = _FakeUrlOpen({"choices": [{"message": {"content": "ok"}}]})
    adapter = OpenAIChatCompletionsAdapter(urlopen=fake_urlopen)

    adapter.generate(connection=_connection("https://api.example"), api_key="key", request=_request())

    assert fake_urlopen.requests[0][0].full_url == "https://api.example/v1/chat/completions"


def test_openai_adapter_reports_http_and_connection_failures() -> None:
    http_error = error.HTTPError(
        "https://api.example/v1/chat/completions", 401, "Unauthorized", {}, _Response({"error": {"message": "bad key"}})
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        OpenAIChatCompletionsAdapter(urlopen=_FakeUrlOpen(exc=http_error)).generate(
            connection=_connection(), api_key="bad", request=_request()
        )

    # 超时单独报"响应超时"（更利于判断是请求太大还是网关不通），而且**不重试**
    timeout_opener = _FakeUrlOpen(exc=TimeoutError())
    with pytest.raises(RuntimeError, match="响应超时"):
        OpenAIChatCompletionsAdapter(urlopen=timeout_opener).generate(
            connection=_connection(), api_key="key", request=_request()
        )
    assert len(timeout_opener.requests) == 1, "超时不重试（视觉请求单次可达 180s）"


def test_openai_adapter_annotates_cloudflare_403_1010() -> None:
    class _RawBodyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def close(self) -> None:
            return None

        def read(self) -> bytes:
            return b"error code: 1010\n"

    cloudflare_error = error.HTTPError(
        "https://api.example/v1/chat/completions", 403, "Forbidden", {}, _RawBodyResponse()
    )
    with pytest.raises(RuntimeError) as exc_info:
        OpenAIChatCompletionsAdapter(urlopen=_FakeUrlOpen(exc=cloudflare_error)).generate(
            connection=_connection(), api_key="bad", request=_request()
        )
    message = str(exc_info.value)
    assert "HTTP 403" in message
    assert "1010" in message
    assert "防火墙" in message


def test_openai_adapter_rejects_empty_completion() -> None:
    adapter = OpenAIChatCompletionsAdapter(urlopen=_FakeUrlOpen({"choices": [{"message": {"content": ""}}]}))

    with pytest.raises(RuntimeError, match="没有返回文本内容"):
        adapter.generate(connection=_connection(), api_key="key", request=_request())


def _http_error(status: int, message: str):
    body = _Response({"error": {"message": message}})
    return error.HTTPError("https://api.example/v1/chat/completions", status, message, {}, body)


def test_openai_adapter_retries_transient_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务商瞬时 503（现场 error code: 1102）应被退避重试吸收，最终成功。"""

    attempts: list[int] = []

    class _FlakyUrlOpen:
        def __init__(self) -> None:
            self.calls = 0
            self.requests = []

        def __call__(self, request, timeout=0):
            self.calls += 1
            self.requests.append((request, timeout))
            if self.calls == 1:
                raise _http_error(503, "error code: 1102")
            return _Response({"choices": [{"message": {"content": "ok after retry"}}], "model": "gpt-test"})

    sleeper_calls: list[float] = []
    monkeypatch.setattr("services.api.ai_retry.time.sleep", lambda seconds: sleeper_calls.append(seconds))

    opener = _FlakyUrlOpen()
    result = OpenAIChatCompletionsAdapter(urlopen=opener).generate(
        connection=_connection(), api_key="key", request=_request()
    )

    assert result.text == "ok after retry"
    assert opener.calls == 2, "第一次 503 后应重试一次"
    assert sleeper_calls and sleeper_calls[0] > 0, "重试前要退避等待"


def test_openai_adapter_reports_retries_when_503_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    """持续 503 时：重试到上限并把重试次数写进消息（区分服务商抖动与配置问题）。"""

    monkeypatch.setattr("services.api.ai_retry.time.sleep", lambda seconds: None)

    class _Always503:
        def __init__(self) -> None:
            self.calls = 0
            self.requests = []

        def __call__(self, request, timeout=0):
            self.calls += 1
            self.requests.append((request, timeout))
            raise _http_error(503, "error code: 1102")

    opener = _Always503()
    with pytest.raises(RuntimeError, match="HTTP 503") as excinfo:
        OpenAIChatCompletionsAdapter(urlopen=opener).generate(
            connection=_connection(), api_key="key", request=_request()
        )

    assert opener.calls == 3, "默认重试到 3 次上限"
    assert "已退避重试 2 次" in str(excinfo.value)
    assert "error code: 1102" in str(excinfo.value)


def test_openai_adapter_does_not_retry_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """4xx（鉴权/额度/参数）不重试：重试只会更慢。"""

    monkeypatch.setattr("services.api.ai_retry.time.sleep", lambda seconds: None)

    class _QuotaExceeded:
        def __init__(self) -> None:
            self.calls = 0
            self.requests = []

        def __call__(self, request, timeout=0):
            self.calls += 1
            self.requests.append((request, timeout))
            raise _http_error(403, "insufficient_user_quota")

    opener = _QuotaExceeded()
    with pytest.raises(RuntimeError, match="HTTP 403"):
        OpenAIChatCompletionsAdapter(urlopen=opener).generate(
            connection=_connection(), api_key="key", request=_request()
        )

    assert opener.calls == 1, "403 不重试"
