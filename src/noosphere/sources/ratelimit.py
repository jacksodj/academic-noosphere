"""Async token-bucket rate limiting for scholarly API clients."""

import asyncio
import time


class RateLimiter:
    """Token bucket: at most `rate_per_sec` acquisitions per second, sustained.

    Waiters are served in FIFO order (asyncio.Lock is fair), so concurrent
    callers proceed in the order they called acquire().
    """

    def __init__(self, rate_per_sec: float, burst: float = 1.0) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self._rate: float = float(rate_per_sec)
        self._capacity: float = max(1.0, float(burst))
        self._tokens: float = self._capacity
        self._updated: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self._rate)
