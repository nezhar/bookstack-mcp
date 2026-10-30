"""Retry policy for BookStack API calls.

Taken from pnocera/bookstack-mcp-server (MIT), ``src/api/client.ts``: at most 4 attempts,
exponential backoff from 500 ms with up to 25% jitter, at most 30 s of waiting per call, and a
server-directed wait (``Retry-After`` or ``X-RateLimit-Reset``) wins over the backoff.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from bookstack_mcp.client.errors import (
    BookStackError,
    BookStackRateLimited,
    BookStackServerError,
    BookStackUnavailable,
)

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods that are safe to resend after a server error, connection error or timeout."""

RETRYABLE_SERVER_STATUSES = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """When and how long to wait before trying a failed request again."""

    max_attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    jitter: float = 0.25
    budget_s: float = 30.0
    server_margin_s: float = 0.25
    random: Callable[[], float] = field(default=random.random, repr=False, compare=False)

    def is_retryable(self, method: str, error: BookStackError) -> bool:
        """429 for any method (the throttle rejects before the route runs); 5xx, connection
        errors and timeouts only for GET, HEAD and OPTIONS (a POST or PUT may have applied)."""
        if isinstance(error, BookStackRateLimited):
            return True
        if method.upper() not in IDEMPOTENT_METHODS:
            return False
        if isinstance(error, BookStackUnavailable):
            return True
        return isinstance(error, BookStackServerError) and error.status in RETRYABLE_SERVER_STATUSES

    def backoff(self, attempt: int) -> float:
        """Seconds to wait after failed attempt ``attempt`` (1-based): 0.5, 1, 2, 4, 8, 8, ...
        plus up to ``jitter`` (25%) on top."""
        step = min(self.base_delay_s * 2.0 ** (attempt - 1), self.max_delay_s)
        return step * (1 + self.jitter * self.random())

    def delay(
        self, method: str, error: BookStackError, attempt: int, waited_s: float
    ) -> float | None:
        """Seconds to wait before the next attempt, or ``None`` to give up and raise ``error``.

        ``attempt`` is the number of the attempt that just failed; ``waited_s`` is how long this
        call has already spent waiting between attempts.
        """
        if attempt >= self.max_attempts or not self.is_retryable(method, error):
            return None
        remaining = self.budget_s - waited_s
        if remaining <= 0:
            return None
        server_wait = (
            error.retry_after_s
            if isinstance(error, BookStackRateLimited | BookStackServerError)
            else None
        )
        if server_wait is not None:
            return min(server_wait + self.server_margin_s, remaining)
        wait = self.backoff(attempt)
        if wait > remaining:
            return None
        return wait
