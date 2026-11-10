import json
import uuid
from unittest.mock import AsyncMock, patch

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage, SenderType
from app.infrastructure.ai.senior_colleague import ColleagueResult
from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository

PATCH_TARGET = "app.presentation.routes.diagrams.senior_colleague_orchestrator.process_colleague_interaction"


def _open_diagram(diagram_repo):
    doc = CanonicalUmlDocument(id=f"diag-wsai-{uuid.uuid4().hex[:8]}", name="Room", version=1)
    diagram_repo.save(doc)
    return doc


def _result(doc):
    return ColleagueResult(reply_text="@Guest, listo.", addressed_to="Guest", updated_doc=doc, engine="Fake")


def _drain_until_pong(ws):
    ws.send_text(json.dumps({"type": "PING"}))
    seen = []
    while True:
        msg = json.loads(ws.receive_text())
        if msg["type"] == "PONG":
            return seen
        seen.append(msg)


def test_human_chat_does_not_wake_the_ai(client, diagram_repo):
    doc = _open_diagram(diagram_repo)
    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        with client.websocket_connect(f"/api/diagrams/{doc.id}/ws") as ws:
            json.loads(ws.receive_text())  # ROOM_STATE
            for text in ["¿cómo lo hacemos?", "hola, qué tal", "el senior de ayer estuvo bien", "creá la clase Cliente"]:
                ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": text}))
            seen = _drain_until_pong(ws)
    assert [m["type"] for m in seen] == ["CHAT_MESSAGE"] * 4
    mock_ai.assert_not_called()


def test_mention_wakes_the_ai_for_an_editor(client, diagram_repo):
    doc = _open_diagram(diagram_repo)
    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = _result(doc)
        with client.websocket_connect(f"/api/diagrams/{doc.id}/ws") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai hola", "ai_mode": "ADVISORY"}))
            types = [json.loads(ws.receive_text())["type"] for _ in range(4)]
    assert types == ["CHAT_MESSAGE", "TYPING_INDICATOR", "CHAT_MESSAGE", "TYPING_INDICATOR"]
    assert mock_ai.await_args.kwargs["can_edit"] is True


def test_force_ai_flag_wakes_the_ai_without_a_mention(client, diagram_repo):
    doc = _open_diagram(diagram_repo)
    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = _result(doc)
        with client.websocket_connect(f"/api/diagrams/{doc.id}/ws") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "agregá un campo", "force_ai": True}))
            for _ in range(4):
                ws.receive_text()
    mock_ai.assert_awaited_once()


def test_ai_turn_does_not_block_the_senders_websocket(client, diagram_repo):
    """While the AI is slow, the same socket still gets PONG (the loop is not blocked)."""
    import asyncio

    doc = _open_diagram(diagram_repo)

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.5)
        return _result(doc)

    with patch(PATCH_TARGET, new=slow):
        with client.websocket_connect(f"/api/diagrams/{doc.id}/ws") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai hola"}))
            ws.send_text(json.dumps({"type": "PING"}))
            order = [json.loads(ws.receive_text())["type"] for _ in range(3)]
            assert order == ["CHAT_MESSAGE", "TYPING_INDICATOR", "PONG"]
            for _ in range(2):  # AI chat + typing off, so the turn ends cleanly
                ws.receive_text()


def test_viewer_is_marked_as_unable_to_edit(client):
    owner = client.post("/api/auth/register", json={
        "email": f"o_{uuid.uuid4().hex[:6]}@case2code.io", "password": "Password123!", "name": "Dueña"}).json()
    viewer_email = f"v_{uuid.uuid4().hex[:6]}@case2code.io"
    viewer = client.post("/api/auth/register", json={
        "email": viewer_email, "password": "Password123!", "name": "Observador"}).json()
    created = client.post("/api/diagrams", headers={"Authorization": f"Bearer {owner['access_token']}"},
                          json={"name": "Privado"}).json()
    client.post(f"/api/diagrams/{created['id']}/collaborators",
                headers={"Authorization": f"Bearer {owner['access_token']}"},
                json={"email": viewer_email, "role": "VIEWER"})

    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = _result(CanonicalUmlDocument(id=created["id"], name="Privado"))
        with client.websocket_connect(f"/api/diagrams/{created['id']}/ws?token={viewer['access_token']}") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai creá la clase X"}))
            for _ in range(4):
                ws.receive_text()
    assert mock_ai.await_args.kwargs["can_edit"] is False


def test_chat_repository_round_trips_suggestions(db_session):
    doc_id = f"diag-chat-{uuid.uuid4().hex[:8]}"
    repo = SqlAlchemyChatRepository(db_session)
    repo.add_message(ChatMessage(
        id=f"msg-{uuid.uuid4().hex[:8]}", diagram_id=doc_id, sender_id="ai-senior", sender_name="Senior AI",
        sender_type=SenderType.AI, content="Propuesta",
        suggested_commands=[{"command_type": "CREATE_CLASS", "name": "Producto"}],
        suggested_operations=[{"op": "RESTORE_VERSION", "version_number": 3, "reason": "undo"}],
    ))
    db_session.expire_all()
    [msg] = repo.list_messages(doc_id)
    assert msg.suggested_commands == [{"command_type": "CREATE_CLASS", "name": "Producto"}]
    assert msg.suggested_operations == [{"op": "RESTORE_VERSION", "version_number": 3, "reason": "undo"}]
