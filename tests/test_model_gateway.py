from __future__ import annotations

import json
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet

from services.api.anthropic_messages_adapter import AnthropicMessagesAdapter
from services.api.credential_crypto import CredentialCrypto
from services.api.model_gateway import GenerationRequest, ModelConnection, ModelGateway
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


class _FakeUrlOpen:
    def __init__(self) -> None:
        self.requests = []

    def __call__(self, request, timeout=0):
        self.requests.append((request, timeout))
        return _Response(
            {
                "model": json.loads(request.data.decode("utf-8"))["model"],
                "content": [
                    {"type": "text", "text": "generated "},
                    {"type": "text", "text": "description"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 8},
            }
        )


def _connection(model_id: str = "model-a") -> ModelConnection:
    return ModelConnection(
        id="conn-1",
        name="Anthropic 兼容主模型",
        protocol="anthropic_messages",
        base_url="https://ai.example/v1",
        model_id=model_id,
        api_version="2023-06-01",
        timeout_seconds=30,
        encrypted_api_key="",
    )


def test_anthropic_adapter_uses_messages_contract() -> None:
    fake_urlopen = _FakeUrlOpen()
    adapter = AnthropicMessagesAdapter(urlopen=fake_urlopen)

    result = adapter.generate(
        connection=_connection(),
        api_key="secret-key",
        request=GenerationRequest(system="system", user="product", max_tokens=300, temperature=0.4),
    )

    request, timeout = fake_urlopen.requests[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://ai.example/v1/messages"
    assert headers["x-api-key"] == "secret-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["user-agent"].startswith("Mozilla/5.0")
    assert body["model"] == "model-a"
    assert body["messages"] == [{"role": "user", "content": "product"}]
    assert timeout == 30
    assert result.text == "generated description"
    assert result.model_id == "model-a"


def test_anthropic_adapter_annotates_cloudflare_403_1010() -> None:
    from urllib import error

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
        "https://ai.example/v1/messages", 403, "Forbidden", {}, _RawBodyResponse()
    )

    def failing_urlopen(request, timeout=0):
        raise cloudflare_error

    with pytest.raises(RuntimeError) as exc_info:
        AnthropicMessagesAdapter(urlopen=failing_urlopen).generate(
            connection=_connection(),
            api_key="bad",
            request=GenerationRequest(system="system", user="product", max_tokens=64),
        )
    message = str(exc_info.value)
    assert "HTTP 403" in message
    assert "1010" in message
    assert "防火墙" in message


def test_gateway_can_switch_model_connections_without_changing_request() -> None:
    fake_urlopen = _FakeUrlOpen()
    crypto = CredentialCrypto(Fernet.generate_key().decode("ascii"))
    gateway = ModelGateway(
        crypto=crypto,
        adapters={"anthropic_messages": AnthropicMessagesAdapter(urlopen=fake_urlopen)},
    )
    first = replace(_connection("model-a"), encrypted_api_key=crypto.encrypt("key-a"))
    second = replace(_connection("model-b"), encrypted_api_key=crypto.encrypt("key-b"))
    request = GenerationRequest(system="system", user="same product", max_tokens=100, temperature=0.2)

    first_result = gateway.generate(first, request)
    second_result = gateway.generate(second, request)

    assert first_result.model_id == "model-a"
    assert second_result.model_id == "model-b"
    assert [json.loads(item[0].data.decode("utf-8"))["model"] for item in fake_urlopen.requests] == [
        "model-a",
        "model-b",
    ]


def test_gateway_routes_openai_chat_completions_connection() -> None:
    requests = []

    def fake_urlopen(request, timeout=0):
        requests.append((request, timeout))
        return _Response(
            {
                "model": "deepseek-chat",
                "choices": [{"message": {"content": "generated description"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }
        )

    crypto = CredentialCrypto(Fernet.generate_key().decode("ascii"))
    gateway = ModelGateway(
        crypto=crypto,
        adapters={"openai_chat_completions": OpenAIChatCompletionsAdapter(urlopen=fake_urlopen)},
    )
    connection = ModelConnection(
        id="conn-openai",
        name="DeepSeek",
        protocol="openai_chat_completions",
        base_url="https://api.example/v1",
        model_id="deepseek-chat",
        api_version="",
        timeout_seconds=10,
        encrypted_api_key=crypto.encrypt("openai-key"),
    )

    result = gateway.generate(connection, GenerationRequest(system="system", user="product"))

    headers = {key.lower(): value for key, value in requests[0][0].header_items()}
    assert headers["authorization"] == "Bearer openai-key"
    assert result.text == "generated description"
    assert result.usage == {"input_tokens": 12, "output_tokens": 8}


def test_gateway_routes_chat_completions_literal_endpoint_connection() -> None:
    requests = []

    def fake_urlopen(request, timeout=0):
        requests.append((request, timeout))
        return _Response(
            {
                "model": "z-ai/glm-5.3-flash",
                "choices": [{"message": {"content": "generated description"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }
        )

    crypto = CredentialCrypto(Fernet.generate_key().decode("ascii"))
    gateway = ModelGateway(
        crypto=crypto,
        adapters={"chat_completions": OpenAIChatCompletionsAdapter(urlopen=fake_urlopen)},
    )
    connection = ModelConnection(
        id="conn-chat",
        name="CommandCode",
        protocol="chat_completions",
        base_url="https://api.commandcode.ai/provider/v1/chat/completions",
        model_id="z-ai/glm-5.3-flash",
        api_version="",
        timeout_seconds=10,
        encrypted_api_key=crypto.encrypt("cc-key"),
    )

    result = gateway.generate(connection, GenerationRequest(system="system", user="product"))

    request, _timeout = requests[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.full_url == "https://api.commandcode.ai/provider/v1/chat/completions"
    assert headers["authorization"] == "Bearer cc-key"
    assert result.text == "generated description"
    assert result.model_id == "z-ai/glm-5.3-flash"
