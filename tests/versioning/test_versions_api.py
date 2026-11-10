import uuid

import pytest

from app.domain.models.canonical_uml import CanonicalUmlDocument, UmlClass


def _register(client, name):
    email = f"{name}_{uuid.uuid4().hex[:6]}@case2code.io"
    data = client.post(
        "/api/auth/register",
        json={"email": email, "password": "Password123!", "name": name},
    ).json()
    return {"headers": {"Authorization": f"Bearer {data['access_token']}"}, "email": email, "id": data["user"]["id"]}


@pytest.fixture
def team(client):
    owner = _register(client, "Duenio")
    editor = _register(client, "Editor")
    viewer = _register(client, "Visor")
    stranger = _register(client, "Ajeno")
    diag = client.post("/api/diagrams", headers=owner["headers"], json={"name": "Privado"}).json()
    for member, role in ((editor, "EDITOR"), (viewer, "VIEWER")):
        resp = client.post(
            f"/api/diagrams/{diag['id']}/collaborators",
            headers=owner["headers"],
            json={"email": member["email"], "role": role},
        )
        assert resp.status_code == 200
    return {"diagram_id": diag["id"], "owner": owner, "editor": editor, "viewer": viewer, "stranger": stranger}


def _url(team, suffix=""):
    return f"/api/diagrams/{team['diagram_id']}/versions{suffix}"


def test_owner_creates_lists_and_reads_checkpoint(client, team):
    created = client.post(_url(team), headers=team["owner"]["headers"], json={"label": "  Hito 1 ", "message": "ok"})
    assert created.status_code == 201
    body = created.json()
    assert body["number"] == 1 and body["kind"] == "CHECKPOINT" and body["label"] == "Hito 1"
    assert body["author_name"] == "Duenio" and body["author_type"] == "USER"

    listing = client.get(_url(team), headers=team["owner"]["headers"])
    assert listing.status_code == 200 and [v["number"] for v in listing.json()] == [1]

    detail = client.get(_url(team, "/1"), headers=team["owner"]["headers"])
    assert detail.status_code == 200 and detail.json()["snapshot"]["id"] == team["diagram_id"]


def test_roles(client, team):
    # Viewer can read but not create.
    assert client.get(_url(team), headers=team["viewer"]["headers"]).status_code == 200
    assert client.post(_url(team), headers=team["viewer"]["headers"], json={}).status_code == 403
    # Editor can create.
    assert client.post(_url(team), headers=team["editor"]["headers"], json={}).status_code == 201
    # Strangers and anonymous callers are rejected.
    assert client.get(_url(team), headers=team["stranger"]["headers"]).status_code == 403
    assert client.get(_url(team)).status_code == 401
    assert client.post(_url(team), json={}).status_code == 401


def test_restore_roles(client, team):
    client.post(_url(team), headers=team["owner"]["headers"], json={})
    assert client.post(_url(team, "/1/restore"), headers=team["viewer"]["headers"], json={}).status_code == 403
    assert client.post(_url(team, "/1/restore"), headers=team["stranger"]["headers"], json={}).status_code == 403
    assert client.post(_url(team, "/1/restore")).status_code == 401
    assert client.post(_url(team, "/1/restore"), headers=team["editor"]["headers"], json={}).status_code == 200


def test_not_found(client, team):
    headers = team["owner"]["headers"]
    assert client.get(_url(team, "/9"), headers=headers).status_code == 404
    assert client.get(_url(team, "/9/diff"), headers=headers).status_code == 404
    assert client.post(_url(team, "/9/restore"), headers=headers, json={}).status_code == 404
    assert client.get("/api/diagrams/nope/versions", headers=headers).status_code == 404


def test_diff_endpoint(client, team):
    headers = team["owner"]["headers"]
    client.post(_url(team), headers=headers, json={})
    client.post(
        f"/api/diagrams/{team['diagram_id']}/commands",
        headers=headers,
        json={"command": {"command_type": "CREATE_CLASS", "class_id": "c1", "name": "Producto"}},
    )
    diff = client.get(_url(team, "/1/diff?against=current"), headers=headers)
    assert diff.status_code == 200
    data = diff.json()
    assert data["to_number"] is None and data["summary"] == "1 clase agregada"
    assert data["changes"][0]["description"] == "Se agregó la clase Producto"

    client.post(_url(team), headers=headers, json={})
    assert client.get(_url(team, "/1/diff?against=2"), headers=headers).json()["to_number"] == 2
    assert client.get(_url(team, "/1/diff?against=abc"), headers=headers).status_code == 422


def test_restore_endpoint_restores_and_broadcasts(client, team, diagram_repo, monkeypatch):
    headers = team["owner"]["headers"]
    client.post(_url(team), headers=headers, json={})
    live = diagram_repo.get_by_id(team["diagram_id"])
    live.classes.append(UmlClass(id="c1", name="Nueva"))
    live.version = 3
    diagram_repo.save(live)

    broadcasts = []

    async def fake_broadcast(diagram_id, document_dict, **kwargs):
        broadcasts.append((diagram_id, document_dict))

    from app.presentation.routes import versions as versions_routes

    monkeypatch.setattr(versions_routes.connection_manager, "broadcast_document_update", fake_broadcast)

    resp = client.post(_url(team, "/1/restore"), headers=headers, json={"expected_document_version": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 4 and body["classes"] == [] and body["owner_id"] == team["owner"]["id"]
    assert broadcasts == [(team["diagram_id"], body)]

    kinds = [v["kind"] for v in client.get(_url(team), headers=headers).json()]
    assert kinds == ["PRE_RESTORE", "CHECKPOINT"]


def test_restore_version_conflict_returns_409(client, team):
    headers = team["owner"]["headers"]
    client.post(_url(team), headers=headers, json={})
    resp = client.post(_url(team, "/1/restore"), headers=headers, json={"expected_document_version": 42})
    assert resp.status_code == 409
    assert len(client.get(_url(team), headers=headers).json()) == 1


def test_open_diagram_allows_guests(client, diagram_repo):
    diagram_repo.save(CanonicalUmlDocument(id="diag-open", name="Abierto"))
    created = client.post("/api/diagrams/diag-open/versions", json={"label": "Mío", "author_name": "Lucía"})
    assert created.status_code == 201
    assert created.json()["author_name"] == "Lucía" and created.json()["author_id"] == "guest"

    anonymous = client.post("/api/diagrams/diag-open/versions", json={})
    assert anonymous.json()["author_name"] == "Invitado"
    assert client.get("/api/diagrams/diag-open/versions").status_code == 200
    assert client.post("/api/diagrams/diag-open/versions/1/restore", json={}).status_code == 200
