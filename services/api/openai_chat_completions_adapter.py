from __future__ import annotations

import json
from typing import Any, Callable
from urllib import error, request as urllib_request

from services.api.ai_retry import AI_CALL_ATTEMPTS, call_with_retry, should_retry_status
from services.api.model_gateway import GenerationRequest, GenerationResult, ModelConnection


class OpenAIChatCompletionsAdapter:
    """Adapter for OpenAI Chat Completions and compatible endpoints.

    ``connection.base_url`` is treated as an API root; ``/chat/completions``
    is appended (adding ``/v1`` when the root has no version segment), unless
    the URL already ends in ``/chat/completions`` in which case it is used
    as-is. ``chat_completions`` connections route here too.
    """

    def __init__(self, *, urlopen: Callable[..., Any] = urllib_request.urlopen) -> None:
        self.urlopen = urlopen

    def generate(
        self,
        *,
        connection: ModelConnection,
        api_key: str,
        request: GenerationRequest,
    ) -> GenerationResult:
        # Text-only requests keep the plain string content; vision requests use the
        # OpenAI-compatible array-of-parts format with `image_url` data URLs.
        if request.images:
            user_content: Any = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.media_type};base64,{img.data_base64}"},
                }
                for img in request.images
            ]
            user_content.append({"type": "text", "text": request.user})
        else:
            user_content = request.user
        body = {
            "model": connection.model_id,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": int(request.max_tokens),
            "temperature": float(request.temperature),
        }
        http_request = urllib_request.Request(
            _chat_completions_url(connection.base_url),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                # Some OpenAI-compatible gateways front their API with Cloudflare,
                # which 403/error-1010 blocks the default urllib User-Agent.
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            },
        )
        timeout_seconds = request.timeout_override_seconds or connection.timeout_seconds

        class _AiCallError(RuntimeError):
            """带"是否值得重试"标记的调用错误。"""

            def __init__(self, message: str, *, retryable: bool) -> None:
                super().__init__(message)
                self.retryable = retryable

        def _perform():
            try:
                with self.urlopen(http_request, timeout=timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                detail = _safe_error(raw)
                if exc.code == 403 and "1010" in raw:
                    detail = f"{detail}（接口被防火墙拦截，请检查网关地址/密钥或联系服务商）"
                # 5xx/429 是服务商侧抖动（现场踩过 503 error code: 1102），值得退避重试
                raise _AiCallError(
                    f"AI 服务返回 HTTP {exc.code}: {detail}",
                    retryable=should_retry_status(exc.code),
                ) from exc
            except (error.URLError, TimeoutError) as exc:
                # 超时不重试：视觉请求单次可达 180s，重试会把等待时间翻倍
                raise _AiCallError(
                    "AI 服务连接失败" if not isinstance(exc, TimeoutError) else f"AI 服务响应超时（{timeout_seconds}s）",
                    retryable=not isinstance(exc, TimeoutError),
                ) from exc

        retried: list[int] = []
        try:
            payload = call_with_retry(
                _perform,
                attempts=AI_CALL_ATTEMPTS,
                retryable=lambda exc: bool(getattr(exc, "retryable", False)),
                on_retry=lambda attempt, exc, delay: retried.append(attempt),
            )
        except _AiCallError as exc:
            # 重试过就把次数写进消息：操作员一眼能看出是服务商抖动，不是配置问题
            if retried:
                raise RuntimeError(f"{exc}（已退避重试 {len(retried)} 次）") from exc
            raise RuntimeError(str(exc)) from exc
        choices = payload.get("choices") if isinstance(payload, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, dict) else {}
        text = _message_text(message)
        if not text:
            raise RuntimeError("AI 服务没有返回文本内容")

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return GenerationResult(
            text=text,
            model_id=str(payload.get("model") or connection.model_id),
            stop_reason=str(first.get("finish_reason") or "") if isinstance(first, dict) else "",
            usage={
                "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            },
        )


def _chat_completions_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("AI 接口地址不能为空")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        ).strip()
    return ""


def _safe_error(raw: str) -> str:
    text = " ".join(str(raw or "").split())
    return text[:240] if text else "请求失败"
