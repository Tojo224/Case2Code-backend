import pytest


def test_register_validation(client):
    # 1. Short password (< 6 chars) rejected with 422
    resp_short = client.post(
        "/api/auth/register",
        json={"email": "short@test.com", "password": "123", "name": "Short User"},
    )
    assert resp_short.status_code == 422

    # 2. Invalid email rejected with 422
    resp_invalid_email = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "securepassword123", "name": "Bad Email"},
    )
    assert resp_invalid_email.status_code == 422

    # 3. Valid registration succeeds with 201
    resp_ok = client.post(
        "/api/auth/register",
        json={"email": "alice@case2code.io", "password": "password123", "name": "Alice"},
    )
    assert resp_ok.status_code == 201
    data = resp_ok.json()
    assert "access_token" in data
    assert data["user"]["email"] == "alice@case2code.io"

    # 4. Duplicate email rejected with 400
    resp_dup = client.post(
        "/api/auth/register",
        json={"email": "alice@case2code.io", "password": "password456", "name": "Alice Clone"},
    )
    assert resp_dup.status_code == 400
    assert "already exists" in resp_dup.json()["detail"]


def test_login_flow(client):
    # Register user
    client.post(
        "/api/auth/register",
        json={"email": "bob@case2code.io", "password": "secretbob123", "name": "Bob"},
    )

    # Invalid password
    resp_bad = client.post(
        "/api/auth/login",
        json={"email": "bob@case2code.io", "password": "wrongpassword"},
    )
    assert resp_bad.status_code == 401

    # Valid login
    resp_ok = client.post(
        "/api/auth/login",
        json={"email": "bob@case2code.io", "password": "secretbob123"},
    )
    assert resp_ok.status_code == 200
    assert "access_token" in resp_ok.json()


def test_forgot_and_reset_password(client):
    email = "charlie@case2code.io"
    client.post(
        "/api/auth/register",
        json={"email": email, "password": "initialpassword123", "name": "Charlie"},
    )

    # 1. Request forgot password
    resp_forgot = client.post("/api/auth/forgot-password", json={"email": email})
    assert resp_forgot.status_code == 200
    forgot_data = resp_forgot.json()
    token = forgot_data.get("dev_token")
    assert token is not None

    # 2. Reset with bad token fails
    resp_bad_reset = client.post(
        "/api/auth/reset-password",
        json={"token": "invalid-token-xyz", "new_password": "newpassword123"},
    )
    assert resp_bad_reset.status_code == 400

    # 3. Reset with valid token succeeds
    resp_reset = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "newpassword123"},
    )
    assert resp_reset.status_code == 200
    assert resp_reset.json()["success"] is True

    # 4. Old password no longer works
    resp_old_login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "initialpassword123"},
    )
    assert resp_old_login.status_code == 401

    # 5. New password works
    resp_new_login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "newpassword123"},
    )
    assert resp_new_login.status_code == 200

    # 6. Token is single-use and cannot be reused
    resp_reuse = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "anotherpassword123"},
    )
    assert resp_reuse.status_code == 400


def test_diagram_isolation_and_rbac(client):
    # 1. Register User A and User B
    resp_a = client.post(
        "/api/auth/register",
        json={"email": "usera@case2code.io", "password": "passwordA123", "name": "User A"},
    )
    token_a = resp_a.json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    resp_b = client.post(
        "/api/auth/register",
        json={"email": "userb@case2code.io", "password": "passwordB123", "name": "User B"},
    )
    token_b = resp_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 2. User A creates private Diagram A
    resp_create = client.post(
        "/api/diagrams",
        json={"name": "Proyecto Privado de A", "description": "Solo para User A"},
        headers=headers_a,
    )
    assert resp_create.status_code == 201
    diag_id = resp_create.json()["id"]

    # 3. User B lists diagrams -> MUST NOT SEE Diagram A (ISOLATION GUARANTEE)
    resp_list_b = client.get("/api/diagrams", headers=headers_b)
    assert resp_list_b.status_code == 200
    diagrams_b = resp_list_b.json()
    assert not any(d["id"] == diag_id for d in diagrams_b)

    # 4. User B attempts unauthorized access to Diagram A
    # View diagram
    resp_view = client.get(f"/api/diagrams/{diag_id}", headers=headers_b)
    assert resp_view.status_code == 403

    # Execute command
    resp_cmd = client.post(
        f"/api/diagrams/{diag_id}/commands",
        json={"command": {"command_type": "CREATE_CLASS", "name": "InjectedClass"}},
        headers=headers_b,
    )
    assert resp_cmd.status_code == 403

    # Delete diagram
    resp_del_unauth = client.delete(f"/api/diagrams/{diag_id}", headers=headers_b)
    assert resp_del_unauth.status_code == 403

    # 5. User A invites User B as EDITOR
    resp_invite = client.post(
        f"/api/diagrams/{diag_id}/collaborators",
        json={"email": "userb@case2code.io", "role": "EDITOR"},
        headers=headers_a,
    )
    assert resp_invite.status_code == 200

    # 6. User B now lists diagrams -> Diagram A is visible!
    resp_list_b_collab = client.get("/api/diagrams", headers=headers_b)
    assert any(d["id"] == diag_id for d in resp_list_b_collab.json())

    # 7. User B can now edit Diagram A
    resp_cmd_collab = client.post(
        f"/api/diagrams/{diag_id}/commands",
        json={"command": {"command_type": "CREATE_CLASS", "name": "CollabClass"}},
        headers=headers_b,
    )
    assert resp_cmd_collab.status_code == 200
    assert resp_cmd_collab.json()["success"] is True

    # 8. User B (as EDITOR) still CANNOT delete Diagram A
    resp_del_collab = client.delete(f"/api/diagrams/{diag_id}", headers=headers_b)
    assert resp_del_collab.status_code == 403

    # 9. User A (as OWNER) can delete Diagram A
    resp_del_owner = client.delete(f"/api/diagrams/{diag_id}", headers=headers_a)
    assert resp_del_owner.status_code == 204

    # 10. Confirm diagram is deleted
    assert client.get(f"/api/diagrams/{diag_id}", headers=headers_a).status_code == 404
