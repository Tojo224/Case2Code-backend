"""Run blocking (synchronous SQLAlchemy / CPU heavy) work off the event loop.

CONTRACT: async routes and AI turns must not call the synchronous session
directly while the loop serves every room's websockets. Wrap each contiguous
DB section in ``await run_blocking(fn, ...)``.

Rules
- Never run two blocking sections on the SAME Session concurrently (await one
  before starting the next). A Session may be used from different threads as
  long as the uses are sequential.
- Keep the asyncio room lock held around the whole reload -> replay -> save
  section: ``async with get_room_lock(id): result = await run_blocking(work)``,
  then broadcast.
- All project engines are created with check_same_thread=False, so SQLite is safe.
"""

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")


async def run_blocking(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    return await asyncio.to_thread(fn, *args, **kwargs)
