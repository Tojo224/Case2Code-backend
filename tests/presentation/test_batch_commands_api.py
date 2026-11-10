import uuid

import pytest

from app.application.services.version_service import DiagramVersionService
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository

CREATE_A = {"command_type": "CREATE_CLASS", "name": "Alpha"}
CREATE_B = {"command_type": "CREATE_CLASS", "name": "Beta"}
RENAME_GHOST = {"command_type": "RENAME_CLASS", "class_id": "cls-ghost", "new_name": "Nope"}


@pytest.fixture
def open_room(diagram_repo):
    doc = CanonicalUmlDocument(id=f"diag-batch-{uuid.uuid4().hex[:8]}", name="Batch", version=1)
    diagram_repo.save(doc)
    return doc


def _post(client, diagram_id, commands, **kwargs):
    return client.post(f"/api/diagrams/{diagram_id}/commands/batch", json={"commands": commands}, **kwargs)


def test_batch_applies_in_order_and_skips_failing_commands_without_aborting(client, open_room):
    resp = _post(client, open_room.id, [CREATE_A, RENAME_GHOST, CREATE_B])

    assert resp.status_code == 200
    data = resp.json()
    assert [c["name"] for c in data["document"]["classes"]] == ["Alpha", "Beta"]
    assert [c["name"] for c in data["applied"]] == ["Alpha", "Beta"]
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["command"]["command_type"] == "RENAME_CLASS"
    assert data["skipped"][0]["reason"]


def test_batch_saves_once_and_broadcasts_once(client, open_room, recorded_broadcasts, monkeypatch):
    saves = []
    original = SqlAlchemyDiagramRepository.save

    def counting_save(self, document):
        saves.append(document.version)
        return original(self, document)

    monkeypatch.setattr(SqlAlchemyDiagramRepository, "save", counting_save)

    resp = _post(client, open_room.id, [CREATE_A, CREATE_B, RENAME_GHOST])

    assert resp.status_code == 200
    assert len(saves) == 1
    updates = [m for m in recorded_broadcasts if m["type"] == "DOCUMENT_UPDATED"]
    assert len(updates) == 1
    assert len(updates[0]["document"]["classes"]) == 2


def test_batch_takes_one_auto_snapshot_of_the_pre_state(client, open_room, db_session):
    _post(client, open_room.id, [CREATE_A, CREATE_B])

    versions = DiagramVersionService(db_session).list_versions(open_room.id)
    assert len(versions) == 1
    snapshot = DiagramVersionService(db_session).get_version(open_room.id, versions[0].number).snapshot
    assert snapshot.classes == []  # the state BEFORE the batch


def test_batch_where_nothing_applies_neither_saves_nor_broadcasts(client, open_room, recorded_broadcasts, db_session):
    resp = _post(client, open_room.id, [RENAME_GHOST])

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"] == []
    assert len(data["skipped"]) == 1
    assert data["document"]["version"] == open_room.version
    assert recorded_broadcasts == []
    assert DiagramVersionService(db_session).list_versions(open_room.id) == []


def test_batch_rejects_an_empty_list(client, open_room):
    assert _post(client, open_room.id, []).status_code == 400


def test_batch_on_unknown_diagram_is_404(client):
    assert _post(client, "diag-nope", [CREATE_A]).status_code == 404


def test_batch_permissions_follow_the_commands_rules(client, private_room):
    assert _post(client, private_room.diagram_id, [CREATE_A]).status_code == 401
    assert _post(client, private_room.diagram_id, [CREATE_A], headers=private_room.outsider).status_code == 403
    assert _post(client, private_room.diagram_id, [CREATE_A], headers=private_room.viewer).status_code == 403
    assert _post(client, private_room.diagram_id, [CREATE_A], headers=private_room.editor).status_code == 200
