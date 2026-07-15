import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx

from app.config import Settings
from app.utils.errors import GatewayError
from app.utils.logging import logger

T = TypeVar("T")


def build_client(
    settings: Settings,
    timeout: float | None = None,
    *,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout or settings.request_timeout_seconds),
        follow_redirects=follow_redirects,
    )


async def timed_call(name: str, func: Callable[[], Awaitable[T]]) -> T:
    """记录 provider 调用耗时，便于后续定位慢接口。"""
    start = time.perf_counter()
    try:
        return await func()
    except httpx.TimeoutException as exc:
        logger.warning("{} 调用超时", name)
        raise GatewayError(f"{name} 调用超时", status_code=504) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.warning("{} 返回 HTTP {}", name, status)
        detail = {"status": status}
        retry_after = retry_after_seconds(exc.response)
        if retry_after is not None:
            detail["retry_after_seconds"] = retry_after
        raise GatewayError(f"{name} 调用失败", status_code=502, detail=detail) from exc
    except httpx.HTTPError as exc:
        logger.warning("{} 网络异常: {}", name, type(exc).__name__)
        raise GatewayError(
            f"{name} 网络异常",
            status_code=502,
            detail={"error_type": type(exc).__name__},
        ) from exc
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("{} 调用耗时 {:.1f}ms", name, elapsed_ms)


def retry_after_seconds(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after", "").strip()
    if not raw:
        return None
    try:
        return max(1, min(86400, int(raw)))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(raw)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        return max(1, min(86400, round((target - datetime.now(UTC)).total_seconds())))
    except (TypeError, ValueError, OverflowError):
        return None
