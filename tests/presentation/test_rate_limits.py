"""W3: per-user rate limits for chat flooding and LLM-triggering requests."""

import json
from unittest.mock import AsyncMock, patch

from app.core import rate_limit
from app.core.rate_limit import SlidingWindowLimiter

PATCH_TARGET = "app.presentation.routes.diagrams.senior_colleague_orchestrator.process_colleague_interaction"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_limiter_blocks_over_budget_and_recovers_with_the_window():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(max_events=3, window_s=10, clock=clock)
    assert [limiter.hit("k") for _ in range(3)] == [0, 0, 0]
    retry = limiter.hit("k")
    assert 0 < retry <= 10
    assert limiter.hit("other") == 0  # keys are independent
    clock.now = 10.5
    assert limiter.hit("k") == 0


def _new_diagram(client):
    return client.post("/api/diagrams", json={"name": "Rate"}).json()["id"]


def test_ws_chat_flood_is_rejected_after_the_window_budget(client, monkeypatch):
    monkeypatch.setattr(rate_limit, "chat_limiter", SlidingWindowLimiter(3, 60))
    monkeypatch.setattr("app.presentation.routes.diagrams.chat_limiter", rate_limit.chat_limiter)
    diagram_id = _new_diagram(client)
    with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws") as ws:
        json.loads(ws.receive_text())
        for i in range(4):
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": f"hola {i}"}))
        seen = []
        for _ in range(4):
            seen.append(json.loads(ws.receive_text()))
    types = [m["type"] for m in seen]
    assert types.count("CHAT_MESSAGE") == 3
    rejected = [m for m in seen if m["type"] == "MESSAGE_REJECTED"]
    assert len(rejected) == 1 and rejected[0]["reason"] == "rate_limited"
    assert rejected[0]["retry_after"] > 0


def test_ws_ai_requests_are_limited_but_the_human_message_still_posts(client, monkeypatch):
    limiter = SlidingWindowLimiter(1, 60)
    monkeypatch.setattr("app.presentation.routes.diagrams.ai_limiter", limiter)
    diagram_id = _new_diagram(client)
    with patch("app.presentation.routes.diagrams.schedule_ai_turn") as schedule:
        with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai primera"}))
            first = json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai segunda"}))
            second = json.loads(ws.receive_text())
            notice = json.loads(ws.receive_text())
    assert first["type"] == "CHAT_MESSAGE" and second["type"] == "CHAT_MESSAGE"
    assert notice["type"] == "MESSAGE_REJECTED" and notice["reason"] == "ai_rate_limited"
    assert schedule.call_count == 1


def test_rest_assistant_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr("app.presentation.routes.diagrams.ai_limiter", SlidingWindowLimiter(1, 60))
    diagram_id = _new_diagram(client)
    ok = client.post(f"/api/diagrams/{diagram_id}/assistant", json={"prompt": "crea la clase Cliente"})
    assert ok.status_code == 200
    blocked = client.post(f"/api/diagrams/{diagram_id}/assistant", json={"prompt": "crea la clase Pedido"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
