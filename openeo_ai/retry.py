# ABOUTME: One async retry/backoff decorator for I/O boundaries.
# ABOUTME: Retries typed-transient errors with capped exponential backoff; terminal errors propagate.
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Awaitable, Callable, Tuple, Type, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def retryable(
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 8.0,
    retry_on: Tuple[Type[BaseException], ...],
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Decorate an async callable to retry transient failures.

    Only exceptions in ``retry_on`` are retried; everything else propagates
    immediately (terminal). Backoff is exponential, capped at
    ``max_delay_seconds``. After ``max_attempts`` the last transient error
    is re-raised.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    def decorator(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> _T:
            attempt = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except retry_on as exc:  # type: ignore[misc]
                    attempt += 1
                    if attempt >= max_attempts:
                        logger.warning(
                            "[retry] %s exhausted after %d attempts: %s",
                            getattr(func, "__name__", "call"), attempt, exc,
                        )
                        raise
                    delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
                    logger.info(
                        "[retry] %s attempt %d failed (%s); retrying in %.2fs",
                        getattr(func, "__name__", "call"), attempt, exc, delay,
                    )
                    await asyncio.sleep(delay)

        return wrapper

    return decorator
