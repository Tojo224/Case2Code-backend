"""W3: size limits on chat, assistant prompts, images, batches, XMI uploads and WS frames."""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core import limits

PATCH_TARGET = "app.presentation.routes.diagrams.senior_colleague_orchestrator.process_colleague_interaction"


def _new_diagram(client):
    return client.post("/api/diagrams", json={"name": "Limits"}).json()["id"]


def test_assistant_prompt_over_limit_is_rejected(client):
    diagram_id = _new_diagram(client)
    resp = client.post(
        f"/api/diagrams/{diagram_id}/assistant",
        json={"prompt": "x" * (limits.MAX_ASSISTANT_PROMPT_CHARS + 1)},
    )
    assert resp.status_code == 422


def test_assistant_rejects_unsupported_image_mime(client):
    diagram_id = _new_diagram(client)
    resp = client.post(
        f"/api/diagrams/{diagram_id}/assistant",
        json={"prompt": "hola", "image_base64": "AAAA", "image_mime_type": "application/x-evil"},
    )
    assert resp.status_code == 400


def test_assistant_image_over_limit_is_rejected(client):
    diagram_id = _new_diagram(client)
    resp = client.post(
        f"/api/diagrams/{diagram_id}/assistant",
        json={"prompt": "hola", "image_base64": "A" * (limits.MAX_IMAGE_BASE64_CHARS + 1)},
    )
    assert resp.status_code == 422


def test_batch_over_limit_is_rejected(client):
    diagram_id = _new_diagram(client)
    command = {"command_type": "CREATE_CLASS", "class_id": "c", "name": "A", "position": {"x": 0, "y": 0}}
    resp = client.post(
        f"/api/diagrams/{diagram_id}/commands/batch",
        json={"commands": [command] * (limits.MAX_BATCH_COMMANDS + 1)},
    )
    assert resp.status_code == 422


def test_xmi_upload_over_limit_returns_413(client, monkeypatch):
    monkeypatch.setattr("app.presentation.routes.diagrams.MAX_XMI_UPLOAD_BYTES", 100)
    resp = client.post(
        "/api/diagrams/import/xmi",
        files={"file": ("big.xmi", b"<x>" + b"a" * 500 + b"</x>", "text/xml")},
    )
    assert resp.status_code == 413


def test_ws_chat_over_limit_is_rejected_and_not_stored_or_sent_to_the_ai(client):
    diagram_id = _new_diagram(client)
    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws") as ws:
            json.loads(ws.receive_text())  # ROOM_STATE
            ws.send_text(
                json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai " + "x" * limits.MAX_CHAT_MESSAGE_CHARS})
            )
            reply = json.loads(ws.receive_text())
    assert reply["type"] == "MESSAGE_REJECTED"
    assert reply["reason"] == "too_long"
    mock_ai.assert_not_called()
    body = client.get(f"/api/diagrams/{diagram_id}/chat").json()
    messages = body["messages"] if isinstance(body, dict) else body
    assert all("x" * 50 not in m["content"] for m in messages)


def test_ws_oversized_frame_closes_the_socket(client):
    diagram_id = _new_diagram(client)
    with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws") as ws:
        json.loads(ws.receive_text())
        ws.send_text("x" * (limits.MAX_WS_FRAME_CHARS + 1))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1009
