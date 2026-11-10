"""In-memory sliding-window rate limiter (per process).

Good enough to stop a single client from flooding a room or burning LLM budget;
with several workers each one enforces its own window. Event-loop only, not thread safe.
"""

import time
from collections import deque
from typing import Callable, Deque, Dict

from app.core.limits import (
    AI_REQUESTS_PER_WINDOW,
    AI_WINDOW_S,
    CHAT_MESSAGES_PER_WINDOW,
    CHAT_WINDOW_S,
)


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.max_events = max_events
        self.window_s = window_s
        self._clock = clock
        self._events: Dict[str, Deque[float]] = {}

    def hit(self, key: str) -> float:
        """Registers one event. Returns 0 when allowed, else the seconds until it would be."""
        now = self._clock()
        events = self._events.setdefault(key, deque())
        while events and now - events[0] >= self.window_s:
            events.popleft()
        if len(events) >= self.max_events:
            return max(0.0, self.window_s - (now - events[0]))
        events.append(now)
        if len(self._events) > 5000:  # bound memory: drop keys with nothing recent
            self._prune(now)
        return 0.0

    def _prune(self, now: float) -> None:
        for k in [k for k, ev in self._events.items() if not ev or now - ev[-1] >= self.window_s]:
            del self._events[k]

    def reset(self) -> None:
        self._events.clear()


chat_limiter = SlidingWindowLimiter(CHAT_MESSAGES_PER_WINDOW, CHAT_WINDOW_S)
ai_limiter = SlidingWindowLimiter(AI_REQUESTS_PER_WINDOW, AI_WINDOW_S)
