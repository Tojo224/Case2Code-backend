"""One asyncio lock per room (diagram).

CONTRACT: every code path that does read-modify-write on a diagram document
(REST /commands, AI edits, version restore) must hold this lock while it
reloads the fresh document, applies changes, saves and broadcasts.
Never hold it while waiting for the LLM: infer first, then lock and replay.
"""

import asyncio
from typing import Dict

_locks: Dict[str, asyncio.Lock] = {}


def get_room_lock(diagram_id: str) -> asyncio.Lock:
    lock = _locks.get(diagram_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[diagram_id] = lock
    return lock
