from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from services.api.credential_crypto import CredentialCrypto


@dataclass(frozen=True)
class GenerationImage:
    """A single image passed alongside the text prompt. `data_base64` is the raw base64
    payload without a `data:` prefix; `media_type` is the MIME type (e.g. `image/jpeg`)."""

    media_type: str
    data_base64: str


@dataclass(frozen=True)
class GenerationRequest:
    system: str
    user: str
    max_tokens: int = 600
    temperature: float = 0.4
    images: tuple[GenerationImage, ...] = ()
    # Per-request timeout; 0 = use the connection's configured timeout_seconds.
    # Vision generation with reasoning models routinely runs past 120s.
    timeout_override_seconds: int = 0


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model_id: str
    stop_reason: str
    usage: dict[str, int]


@dataclass(frozen=True)
class ModelConnection:
    id: str
    name: str
    protocol: str
    base_url: str
    model_id: str
    api_version: str
    timeout_seconds: int
    encrypted_api_key: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ModelConnection":
        return cls(
            id=str(value.get("id") or ""),
            name=str(value.get("name") or ""),
            protocol=str(value.get("protocol") or ""),
            base_url=str(value.get("base_url") or ""),
            model_id=str(value.get("model_id") or ""),
            api_version=str(value.get("api_version") or "2023-06-01"),
            timeout_seconds=int(value.get("timeout_seconds") or 30),
            encrypted_api_key=str(value.get("encrypted_api_key") or ""),
        )


class ModelAdapter(Protocol):
    def generate(
        self,
        *,
        connection: ModelConnection,
        api_key: str,
        request: GenerationRequest,
    ) -> GenerationResult: ...


class ModelGateway:
    def __init__(self, *, crypto: CredentialCrypto, adapters: dict[str, ModelAdapter]) -> None:
        self.crypto = crypto
        self.adapters = dict(adapters)

    def generate(
        self,
        connection: ModelConnection | dict[str, Any],
        request: GenerationRequest,
    ) -> GenerationResult:
        resolved = connection if isinstance(connection, ModelConnection) else ModelConnection.from_dict(connection)
        adapter = self.adapters.get(resolved.protocol)
        if adapter is None:
            raise ValueError(f"不支持的 AI 协议：{resolved.protocol}")
        api_key = self.crypto.decrypt(resolved.encrypted_api_key)
        return adapter.generate(connection=resolved, api_key=api_key, request=request)
