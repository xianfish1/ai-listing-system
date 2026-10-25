"""AI 调用的退避重试策略。

现场（2026-09-28 19:21:30，基础商品 HN0015）：服务商返回一次瞬时 503
（`error code: 1102`），「AI 生成」与「颜色识别」当场都失败；90 秒后同一连接
（commandcode / deepseek-v4.1-flash）测试就恢复正常 —— 是服务商侧抖动。

原来的重试是**无间隔连发两次**，1~2 秒的抖动直接穿透。这里统一给可重试错误
加指数退避：

- 可重试：429 / 500 / 502 / 503 / 504、以及连接类错误（URLError：重置、DNS 等）
- 不重试：4xx（鉴权、额度不足、参数错误——重试只会更慢），以及**超时**
  （视觉请求单次可达 180 秒，重试会把等待时间翻倍）
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, TypeVar

#: HTTP 状态码 → 值得重试
RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

#: 默认总尝试次数（含第一次）
AI_CALL_ATTEMPTS = 3
#: 退避基数与上限（秒）
AI_RETRY_BASE_SECONDS = 1.5
AI_RETRY_CAP_SECONDS = 12.0

T = TypeVar("T")


def should_retry_status(status: int) -> bool:
    """这个 HTTP 状态码是否值得重试。"""

    return int(status) in RETRYABLE_HTTP_STATUS


def retry_delay_seconds(attempt: int, *, base: float = AI_RETRY_BASE_SECONDS, cap: float = AI_RETRY_CAP_SECONDS) -> float:
    """第 attempt 次失败后的等待时长（指数退避 + 抖动）。"""

    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    return round(delay * (0.85 + random.random() * 0.3), 3)


def call_with_retry(
    operation: Callable[[], T],
    *,
    attempts: int = AI_CALL_ATTEMPTS,
    retryable: Callable[[BaseException], bool],
    sleeper: Callable[[float], Any] | None = None,
    on_retry: Callable[[int, BaseException, float], Any] | None = None,
) -> T:
    """执行 operation；可重试错误按退避重试，最后一次失败原样抛出。

    ``retryable(exc)`` 由调用方决定（适配器把自己的 HTTP/连接错误标成可重试）。
    ``sleeper`` 在调用时解析默认的 ``time.sleep``（不做默认参数绑定），这样测试里
    monkeypatch 才生效、也不会真的等退避时间。
    """

    sleep = sleeper or time.sleep
    total = max(1, int(attempts))
    last: BaseException | None = None
    for attempt in range(1, total + 1):
        try:
            return operation()
        except BaseException as exc:  # noqa: BLE001 - 由 retryable 判定是否继续
            last = exc
            if attempt >= total or not retryable(exc):
                raise
            delay = retry_delay_seconds(attempt)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            sleep(delay)
    assert last is not None  # pragma: no cover - 循环必然 return 或 raise
    raise last
