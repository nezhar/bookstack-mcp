"""Client-side rate limit: a token bucket shared by all requests of one client.

BookStack's own limit defaults to 180 requests per minute per user (``API_REQUESTS_PER_MIN``);
the default of 150 leaves headroom for people using the same account. A request waits for a
token rather than failing.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

DEFAULT_REQUESTS_PER_MINUTE = 150
DEFAULT_BURST = 10

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """Holds up to ``burst`` tokens, refilled continuously at ``requests_per_minute``."""

    def __init__(
        self,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        *,
        burst: int = DEFAULT_BURST,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self.rate_per_s = requests_per_minute / 60.0
        self.burst = burst
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(burst)
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self, min_elapsed: float = 0.0) -> None:
        now = self._clock()
        # After a sleep, count at least the time slept: a clock with coarse resolution (or a
        # test clock that does not move) must not make the wait loop spin.
        elapsed = max(min_elapsed, now - self._updated)
        self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate_per_s)
        self._updated = now

    async def acquire(self) -> float:
        """Take one token, waiting for it if the bucket is empty. Returns the seconds waited."""
        waited = 0.0
        async with self._lock:  # first come, first served
            self._refill()
            while self._tokens < 1:
                wait = (1 - self._tokens) / self.rate_per_s
                await self._sleep(wait)
                waited += wait
                self._refill(min_elapsed=wait)
            self._tokens -= 1
        return waited
