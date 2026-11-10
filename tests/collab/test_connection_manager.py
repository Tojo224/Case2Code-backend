import asyncio
import json
import time

import pytest

from app.domain.models.user import User
from app.infrastructure.collaboration import connection_manager as cm_module
from app.infrastructure.collaboration.connection_manager import ConnectionManager

pytestmark = pytest.mark.asyncio

ROOM = "room-1"


class FakeWS:
    def __init__(self, mode="ok"):
        self.mode = mode  # ok | hang | fail
        self.sent = []
        self.closed = False
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        if self.mode == "hang":
            await asyncio.Event().wait()
        if self.mode == "fail":
            raise RuntimeError("socket closed")
        self.sent.append(json.loads(text))

    async def close(self, *a, **k):
        self.closed = True

    def types(self, t):
        return [m for m in self.sent if m["type"] == t]


def _user(uid):
    return User(id=uid, email=f"{uid}@x.io", name=uid.upper(), avatar_color="#fff")


@pytest.fixture(autouse=True)
def fast_timeout(monkeypatch):
    monkeypatch.setattr(cm_module, "BROADCAST_SEND_TIMEOUT_S", 0.1)


async def _join(mgr, uid, mode="ok"):
    ws = FakeWS()
    await mgr.connect(ws, ROOM, _user(uid))
    ws.mode = mode
    return ws


async def test_stalled_client_does_not_delay_others_beyond_timeout():
    mgr = ConnectionManager()
    healthy = [await _join(mgr, f"u{i}") for i in range(3)]
    stalled = await _join(mgr, "slow", "hang")
    start = time.monotonic()
    await mgr.broadcast(ROOM, {"type": "PING"})
    elapsed = time.monotonic() - start
    # Sequential sends would hang forever; concurrent+timeout is bounded.
    assert elapsed < 1.0
    assert all(ws.types("PING") for ws in healthy)
    assert stalled not in mgr.get_room(ROOM)
    assert stalled.closed


async def test_dead_socket_emits_user_left_exactly_once():
    mgr = ConnectionManager()
    peer = await _join(mgr, "peer")
    dead = await _join(mgr, "gone", "fail")
    await mgr.broadcast(ROOM, {"type": "PING"})
    left = peer.types("USER_LEFT")
    assert len(left) == 1 and left[0]["user_id"] == "gone"
    # A later disconnect() of the same socket must not emit again.
    await mgr.disconnect(dead, ROOM)
    assert len(peer.types("USER_LEFT")) == 1


async def test_same_user_two_sockets_left_only_when_last_gone():
    mgr = ConnectionManager()
    peer = await _join(mgr, "peer")
    a = await _join(mgr, "dup", "fail")
    b = await _join(mgr, "dup")
    await mgr.broadcast(ROOM, {"type": "PING"})
    assert peer.types("USER_LEFT") == []
    b.mode = "fail"
    await mgr.broadcast(ROOM, {"type": "PING2"})
    assert [m["user_id"] for m in peer.types("USER_LEFT")] == ["dup"]
    assert a not in mgr.get_room(ROOM)


async def test_both_sockets_of_same_user_dead_in_one_broadcast_emit_once():
    mgr = ConnectionManager()
    peer = await _join(mgr, "peer")
    await _join(mgr, "dup", "fail")
    await _join(mgr, "dup", "fail")
    await mgr.broadcast(ROOM, {"type": "PING"})
    assert len(peer.types("USER_LEFT")) == 1


async def test_connect_failure_does_not_leak_socket():
    mgr = ConnectionManager()
    ws = FakeWS("fail")
    with pytest.raises(RuntimeError):
        await mgr.connect(ws, ROOM, _user("x"))
    assert ws.closed
    assert ROOM not in mgr._rooms


async def test_connect_failure_keeps_other_sockets():
    mgr = ConnectionManager()
    peer = await _join(mgr, "peer")
    ws = FakeWS("hang")
    with pytest.raises(asyncio.TimeoutError):
        await mgr.connect(ws, ROOM, _user("x"))
    assert list(mgr.get_room(ROOM)) == [peer]
    assert peer.types("USER_JOINED") == []
