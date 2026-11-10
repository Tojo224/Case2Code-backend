"""W1-4 (runtime AI config hardening) and W1-7 (fresh role check on every AI turn)."""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from starlette.websockets import WebSocketDisconnect

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.infrastructure.ai.senior_colleague import ColleagueResult

PATCH_TARGET = "app.presentation.routes.diagrams.senior_colleague_orchestrator.process_colleague_interaction"


def _register(client, name):
    email = f"{name.lower()}_{uuid.uuid4().hex[:6]}@case2code.io"
    data = client.post(
        "/api/auth/register", json={"email": email, "password": "Password123!", "name": name}
    ).json()
    return email, data["access_token"], data["user"]["id"]


def _result(diagram_id):
    doc = CanonicalUmlDocument(id=diagram_id, name="Privado")
    return ColleagueResult(reply_text="@Colab, listo.", addressed_to="Colab", updated_doc=doc, engine="Fake")


def _private_room_with_collaborator(client, role):
    _, owner_token, _ = _register(client, "Duena")
    collab_email, collab_token, collab_id = _register(client, "Colab")
    owner_h = {"Authorization": f"Bearer {owner_token}"}
    created = client.post("/api/diagrams", headers=owner_h, json={"name": "Privado"}).json()
    client.post(
        f"/api/diagrams/{created['id']}/collaborators",
        headers=owner_h,
        json={"email": collab_email, "role": role},
    )
    return created["id"], owner_h, collab_token, collab_id, collab_email


# --- W1-7 ---


def test_removed_collaborator_cannot_wake_the_ai_through_an_open_socket(client):
    diagram_id, owner_h, collab_token, collab_id, _ = _private_room_with_collaborator(client, "EDITOR")
    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws?token={collab_token}") as ws:
            json.loads(ws.receive_text())
            resp = client.delete(f"/api/diagrams/{diagram_id}/collaborators/{collab_id}", headers=owner_h)
            assert resp.status_code == 204
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai creá la clase X"}))
            with pytest.raises(WebSocketDisconnect) as exc:
                while True:
                    ws.receive_text()
            assert exc.value.code == 1008
    mock_ai.assert_not_called()


@pytest.mark.parametrize("role,expected", [("VIEWER", False), ("EDITOR", True)])
def test_collaborator_role_decides_can_edit(client, role, expected):
    diagram_id, _, collab_token, _, _ = _private_room_with_collaborator(client, role)
    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = _result(diagram_id)
        with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws?token={collab_token}") as ws:
            json.loads(ws.receive_text())
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai creá la clase X"}))
            for _ in range(4):
                ws.receive_text()
    assert mock_ai.await_args.kwargs["can_edit"] is expected


def test_viewer_downgraded_while_connected_loses_edit_rights(client):
    diagram_id, owner_h, collab_token, collab_id, collab_email = _private_room_with_collaborator(client, "EDITOR")
    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = _result(diagram_id)
        with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws?token={collab_token}") as ws:
            json.loads(ws.receive_text())
            resp = client.post(
                f"/api/diagrams/{diagram_id}/collaborators",
                headers=owner_h,
                json={"email": collab_email, "role": "VIEWER"},
            )
            assert resp.status_code in (200, 201), resp.text
            ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "@ai creá la clase X"}))
            for _ in range(4):
                ws.receive_text()
    assert mock_ai.await_args.kwargs["can_edit"] is False


# --- W1-4 ---


@pytest.fixture
def restore_ai_config():
    from app.core.config import settings
    from app.infrastructure.ai.senior_colleague import senior_colleague_orchestrator as orch

    saved = (
        orch.provider_mode, orch.local_provider.base_url, orch.local_provider.model,
        settings.AI_PROVIDER_MODE, settings.LOCAL_LLM_BASE_URL, settings.LOCAL_LLM_MODEL,
    )
    yield
    (
        orch.provider_mode, orch.local_provider.base_url, orch.local_provider.model,
        settings.AI_PROVIDER_MODE, settings.LOCAL_LLM_BASE_URL, settings.LOCAL_LLM_MODEL,
    ) = saved


def _auth(client):
    _, token, _ = _register(client, "Cfg")
    return {"Authorization": f"Bearer {token}"}


def test_ai_config_update_requires_authentication(client, restore_ai_config):
    resp = client.post("/api/diagrams/ai/config", json={"local_base_url": "http://localhost:11434"})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"local_base_url": "ftp://localhost:11434"},
        {"local_base_url": "http://evil.example.com:11434"},
        {"local_base_url": "http://localhost@evil.example.com"},
        {"provider_mode": "skynet"},
    ],
)
def test_ai_config_update_rejects_bad_input(client, restore_ai_config, body):
    resp = client.post("/api/diagrams/ai/config", json=body, headers=_auth(client))
    assert resp.status_code == 400


def test_ai_config_bad_host_mentions_env_var_and_changes_nothing(client, restore_ai_config):
    from app.infrastructure.ai.senior_colleague import senior_colleague_orchestrator as orch

    before = orch.local_provider.base_url
    resp = client.post(
        "/api/diagrams/ai/config", json={"local_base_url": "http://evil.example.com"}, headers=_auth(client)
    )
    assert resp.status_code == 400 and "AI_ALLOWED_LLM_HOSTS" in resp.json()["detail"]
    assert orch.local_provider.base_url == before


def test_ai_config_update_allows_allowlisted_host(client, restore_ai_config):
    with patch(
        "app.infrastructure.ai.ai_provider.LocalLlmAiProvider.check_health",
        new_callable=AsyncMock,
        return_value={"online": False},
    ):
        resp = client.post(
            "/api/diagrams/ai/config",
            json={"provider_mode": "local", "local_base_url": "http://ollama:11434/v1/"},
            headers=_auth(client),
        )
    assert resp.status_code == 200
    assert resp.json()["local_base_url"] == "http://ollama:11434"


def test_removed_collaborator_cannot_post_plain_chat_messages(client):
    """The access re-check must gate every chat message, not only AI requests."""
    diagram_id, owner_h, collab_token, collab_id, _ = _private_room_with_collaborator(client, "EDITOR")
    with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws?token={collab_token}") as ws:
        json.loads(ws.receive_text())
        resp = client.delete(f"/api/diagrams/{diagram_id}/collaborators/{collab_id}", headers=owner_h)
        assert resp.status_code == 204
        ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "hola equipo"}))
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                ws.receive_text()
        assert exc.value.code == 1008
    history = client.get(f"/api/diagrams/{diagram_id}/chat", headers=owner_h).json()
    assert history["messages"] == []


def test_viewer_can_still_chat_without_waking_the_ai(client):
    diagram_id, owner_h, collab_token, _, _ = _private_room_with_collaborator(client, "VIEWER")
    owner_token = owner_h["Authorization"].split(" ", 1)[1]
    # With 2+ people in the room human chat is the default and the AI needs a mention. (A lone
    # user wakes it automatically, which would make this assertion race the background turn.)
    with patch(PATCH_TARGET, new_callable=AsyncMock) as mock_ai:
        with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws?token={owner_token}") as owner_ws:
            json.loads(owner_ws.receive_text())
            with client.websocket_connect(f"/api/diagrams/{diagram_id}/ws?token={collab_token}") as ws:
                json.loads(ws.receive_text())
                ws.send_text(json.dumps({"type": "SEND_CHAT_MESSAGE", "content": "hola equipo"}))
                echoed = json.loads(ws.receive_text())
    assert echoed["type"] == "CHAT_MESSAGE" and echoed["message"]["content"] == "hola equipo"
    mock_ai.assert_not_called()
