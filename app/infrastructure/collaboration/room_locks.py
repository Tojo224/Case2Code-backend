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


_ai_locks: Dict[str, asyncio.Lock] = {}


def get_room_ai_lock(diagram_id: str) -> asyncio.Lock:
    """Serializes AI turns of one room.

    Unlike get_room_lock this one is held for the WHOLE AI turn (including the
    LLM wait), so two AI turns never interleave. It must never be needed by a
    human edit path: humans only ever take get_room_lock, and only briefly.
    Lock order when both are needed: AI lock first, then the room lock.
    """
    lock = _ai_locks.get(diagram_id)
    if lock is None:
        lock = asyncio.Lock()
        _ai_locks[diagram_id] = lock
    return lock
