import uuid
from dataclasses import dataclass
from typing import Dict

import pytest

from app.infrastructure.collaboration.connection_manager import connection_manager


@dataclass
class PrivateRoom:
    diagram_id: str
    owner: Dict[str, str]
    editor: Dict[str, str]
    viewer: Dict[str, str]
    outsider: Dict[str, str]


def _register(client, label: str) -> Dict[str, str]:
    email = f"{label}-{uuid.uuid4().hex[:8]}@case2code.io"
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "name": label.capitalize()},
    )
    assert resp.status_code in (200, 201), resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    headers["X-Test-Email"] = email  # not sent by the server logic, only used by tests
    return headers


@pytest.fixture
def private_room(client) -> PrivateRoom:
    """A private diagram with an owner, an EDITOR, a VIEWER and an outsider."""
    owner = _register(client, "owner")
    editor = _register(client, "editor")
    viewer = _register(client, "viewer")
    outsider = _register(client, "outsider")
    created = client.post("/api/diagrams", json={"name": "Private"}, headers=owner)
    assert created.status_code == 201
    diagram_id = created.json()["id"]
    for headers, role in ((editor, "EDITOR"), (viewer, "VIEWER")):
        resp = client.post(
            f"/api/diagrams/{diagram_id}/collaborators",
            json={"email": headers["X-Test-Email"], "role": role},
            headers=owner,
        )
        assert resp.status_code == 200, resp.text
    return PrivateRoom(diagram_id, owner, editor, viewer, outsider)


@pytest.fixture
def recorded_broadcasts(monkeypatch):
    """Captures every message the hub would push to websockets."""
    sent = []

    async def fake_broadcast(diagram_id, message, exclude_ws=None):
        sent.append(message)

    monkeypatch.setattr(connection_manager, "broadcast", fake_broadcast)
    return sent
