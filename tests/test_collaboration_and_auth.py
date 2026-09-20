import uuid
import pytest
from fastapi.testclient import TestClient
from app.core.database import init_db
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.main import app


@pytest.fixture
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def test_security_hashing():
    pw = "SuperSecurePassword123!"
    hashed = hash_password(pw)
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password(pw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_security_jwt():
    payload = {"sub": "usr-123", "email": "test@case2code.io", "name": "Test User"}
    token = create_access_token(payload)
    assert isinstance(token, str)
    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "usr-123"
    assert decoded["email"] == "test@case2code.io"


def test_auth_endpoints(client):
    # Test demo users endpoint
    resp = client.get("/api/auth/demo-users")
    assert resp.status_code == 200
    demo_users = resp.json()
    assert len(demo_users) >= 3
    prof = next(u for u in demo_users if u["user"]["email"] == "profesor@case2code.io")
    assert prof["user"]["name"] == "Prof. Carlos Mendoza"
    token = prof["access_token"]

    # Test /api/auth/me
    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "profesor@case2code.io"

    # Test custom registration
    test_email = f"user_{uuid.uuid4().hex[:6]}@case2code.io"
    reg_resp = client.post(
        "/api/auth/register",
        json={
            "email": test_email,
            "password": "Password123!",
            "name": "Nuevo Arquitecto",
            "avatar_color": "#8B5CF6",
        },
    )
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert reg_data["user"]["email"] == test_email

    # Test login
    login_resp = client.post(
        "/api/auth/login",
        json={
            "email": test_email,
            "password": "Password123!",
        },
    )
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()


def test_diagram_ownership_and_collaborators(client):
    # Get tokens for Profesor and Estudiante A
    demo_resp = client.get("/api/auth/demo-users").json()
    prof = next(u for u in demo_resp if u["user"]["email"] == "profesor@case2code.io")
    est_a = next(u for u in demo_resp if u["user"]["email"] == "estudiante.a@case2code.io")

    prof_token = prof["access_token"]
    est_a_token = est_a["access_token"]

    # Profesor creates a diagram
    create_resp = client.post(
        "/api/diagrams",
        headers={"Authorization": f"Bearer {prof_token}"},
        json={"name": "Proyecto Sistema Bancario", "description": "Diagrama de arquitectura"},
    )
    assert create_resp.status_code == 201
    diag = create_resp.json()
    diag_id = diag["id"]
    assert diag["owner_id"] == prof["user"]["id"]

    # Check collaborators: Profesor is automatically OWNER
    collab_resp = client.get(f"/api/diagrams/{diag_id}/collaborators")
    assert collab_resp.status_code == 200
    collabs = collab_resp.json()
    assert any(c["user_id"] == prof["user"]["id"] and c["role"] == "OWNER" for c in collabs)

    # Profesor invites Estudiante A
    invite_resp = client.post(
        f"/api/diagrams/{diag_id}/collaborators",
        headers={"Authorization": f"Bearer {prof_token}"},
        json={"email": "estudiante.a@case2code.io", "role": "EDITOR"},
    )
    assert invite_resp.status_code == 200
    assert invite_resp.json()["user_id"] == est_a["user"]["id"]
    assert invite_resp.json()["role"] == "EDITOR"

    # Estudiante A lists diagrams and should see "Proyecto Sistema Bancario"
    est_list_resp = client.get("/api/diagrams", headers={"Authorization": f"Bearer {est_a_token}"})
    assert est_list_resp.status_code == 200
    est_diagrams = est_list_resp.json()
    assert any(d["id"] == diag_id for d in est_diagrams)


def test_websocket_collaboration(client):
    demo_resp = client.get("/api/auth/demo-users").json()
    prof = demo_resp[0]
    prof_token = prof["access_token"]

    # Create diagram
    create_resp = client.post(
        "/api/diagrams",
        headers={"Authorization": f"Bearer {prof_token}"},
        json={"name": "WS Test Diagram"},
    )
    diag_id = create_resp.json()["id"]

    # Connect WebSocket client
    with client.websocket_connect(f"/api/diagrams/{diag_id}/ws?token={prof_token}") as ws:
        # First message should be ROOM_STATE
        initial = ws.receive_json()
        assert initial["type"] == "ROOM_STATE"
        assert initial["diagram_id"] == diag_id
        assert len(initial["users"]) >= 1

        # Send PING
        ws.send_json({"type": "PING"})
        pong = ws.receive_json()
        assert pong["type"] == "PONG"
