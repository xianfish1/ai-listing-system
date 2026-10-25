from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CredentialCrypto:
    def __init__(self, master_key: str) -> None:
        value = str(master_key or "").strip()
        try:
            self._fernet = Fernet(value.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ValueError("AI_CREDENTIALS_MASTER_KEY 必须是有效的 Fernet 密钥") from exc

    def encrypt(self, value: str) -> str:
        secret = str(value or "")
        if not secret:
            raise ValueError("API Key 不能为空")
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(str(value or "").encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError) as exc:
            raise ValueError("无法解密 AI API Key，请检查 AI_CREDENTIALS_MASTER_KEY") from exc


def redact_connection(connection: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in connection.items()
        if key not in {"api_key", "encrypted_api_key"}
    }
    public["api_key_configured"] = bool(connection.get("encrypted_api_key"))
    return public
