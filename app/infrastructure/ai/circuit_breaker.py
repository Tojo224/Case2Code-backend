"""Tiny circuit breaker so a dead LLM provider is not retried on every message.

States: CLOSED (calls allowed) -> OPEN after `failure_threshold` consecutive failures
(calls skipped for `cooldown_s`) -> HALF-OPEN (exactly one probe call allowed) ->
CLOSED on success / OPEN again on failure. Not thread safe on purpose: it is only
touched from the event loop.
"""

import time
from typing import Callable


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at < self.cooldown_s:
            return False
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True  # half-open: a single probe
        return True

    def release_probe(self) -> None:
        """The probe never ran (caller ran out of budget): allow another one, count nothing."""
        self._probe_in_flight = False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self._failures += 1
        if self._opened_at is not None or self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
