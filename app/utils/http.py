import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from app.config import Settings
from app.utils.errors import GatewayError
from app.utils.logging import logger

T = TypeVar("T")


def build_client(settings: Settings, timeout: float | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout or settings.request_timeout_seconds))


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
        text = exc.response.text[:500]
        logger.warning("{} 返回 HTTP {}: {}", name, status, text)
        raise GatewayError(f"{name} 调用失败", status_code=502, detail={"status": status}) from exc
    except httpx.HTTPError as exc:
        logger.warning("{} 网络异常: {} {}", name, type(exc).__name__, repr(exc))
        raise GatewayError(
            f"{name} 网络异常",
            status_code=502,
            detail={"error_type": type(exc).__name__},
        ) from exc
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("{} 调用耗时 {:.1f}ms", name, elapsed_ms)
