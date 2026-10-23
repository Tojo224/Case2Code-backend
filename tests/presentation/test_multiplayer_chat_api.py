import json
from unittest.mock import AsyncMock, patch
import pytest
from app.domain.models.canonical_uml import CanonicalUmlDocument, UmlClass
from app.domain.models.chat import ChatMessage, SenderType
from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository


def test_chat_history_rest_endpoint(client, diagram_repo, db_session):
    # 1. Create diagram
    doc = CanonicalUmlDocument(
        id="diag-chat-test",
        name="Chat Test Diagram",
        version=1,
        classes=[],
        relationships=[],
    )
    diagram_repo.save(doc)

    # 2. Add sample chat messages
    chat_repo = SqlAlchemyChatRepository(db_session)
    msg1 = ChatMessage(
        id="msg-1",
        diagram_id=doc.id,
        sender_id="user-juan",
        sender_name="Juan",
        sender_avatar_color="#3B82F6",
        sender_type=SenderType.USER,
        content="Hola equipo, ¿cómo arrancamos el diseño?",
    )
    msg2 = ChatMessage(
        id="msg-2",
        diagram_id=doc.id,
        sender_id="ai-senior",
        sender_name="Senior AI",
        sender_avatar_color="#8B5CF6",
        sender_type=SenderType.AI,
        content="@Juan, te sugiero arrancar definiendo las entidades principales del dominio.",
        addressed_to="Juan",
    )
    chat_repo.add_message(msg1)
    chat_repo.add_message(msg2)

    # 3. GET /api/diagrams/{id}/chat
    resp = client.get(f"/api/diagrams/{doc.id}/chat")
    assert resp.status_code == 200
    data = resp.json()
    assert data["diagram_id"] == doc.id
    assert len(data["messages"]) == 2
    assert data["messages"][0]["sender_name"] == "Juan"
    assert data["messages"][1]["sender_name"] == "Senior AI"
    assert data["messages"][1]["addressed_to"] == "Juan"


def test_websocket_chat_and_senior_ai_response(client, diagram_repo):
    # 1. Create diagram with a class
    doc = CanonicalUmlDocument(
        id="diag-ws-chat-1",
        name="Multiplayer Room",
        version=1,
        classes=[
            UmlClass(id="cls-1", name="Usuario", attributes=[], position={"x": 100, "y": 100})
        ],
        relationships=[],
    )
    diagram_repo.save(doc)

    # 2. Connect WebSocket
    with patch(
        "app.presentation.routes.diagrams.senior_colleague_orchestrator.process_colleague_interaction",
        new_callable=AsyncMock,
    ) as mock_ai:
        mock_ai.return_value = (
            "@Guest-1234, para modelar los roles te recomiendo una entidad Rol con relación N:M.",
            "Guest-1234",
            [],
            doc,
        )

        with client.websocket_connect(f"/api/diagrams/{doc.id}/ws") as ws:
            # First message is ROOM_STATE
            init_msg = json.loads(ws.receive_text())
            assert init_msg["type"] == "ROOM_STATE"

            # 3. Send typing indicator
            ws.send_text(json.dumps({"type": "SET_TYPING", "is_typing": True}))

            # 4. Send chat message asking Senior AI for advice
            ws.send_text(
                json.dumps({
                    "type": "SEND_CHAT_MESSAGE",
                    "content": "@Senior ¿cómo modelamos los roles de los usuarios?",
                    "ai_mode": "ADVISORY",
                })
            )

            # Receive broadcast of the user's message
            user_broadcast = json.loads(ws.receive_text())
            assert user_broadcast["type"] == "CHAT_MESSAGE"
            assert "@Senior" in user_broadcast["message"]["content"]

            # Receive TYPING_INDICATOR (AI typing: True)
            ai_typing_start = json.loads(ws.receive_text())
            assert ai_typing_start["type"] == "TYPING_INDICATOR"
            assert ai_typing_start["user_id"] == "ai-senior"
            assert ai_typing_start["is_typing"] is True

            # Receive AI response broadcast
            ai_msg_broadcast = json.loads(ws.receive_text())
            assert ai_msg_broadcast["type"] == "CHAT_MESSAGE"
            ai_msg = ai_msg_broadcast["message"]
            assert ai_msg["sender_type"] == "AI"
            assert ai_msg["sender_name"] == "Senior AI"
            # Must address the user
            assert "@Guest" in ai_msg["content"] or "Guest" in (ai_msg.get("addressed_to") or "")

            # Receive TYPING_INDICATOR (AI typing: False)
            ai_typing_end = json.loads(ws.receive_text())
            assert ai_typing_end["type"] == "TYPING_INDICATOR"
            assert ai_typing_end["is_typing"] is False
