import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.application.services.version_service import DiagramVersionService
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage, SenderType
from app.domain.models.versioning import VersionKind
from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository

CREATE_A = {"command_type": "CREATE_CLASS", "name": "Alpha"}
CREATE_B = {"command_type": "CREATE_CLASS", "name": "Beta"}
RENAME_GHOST = {"command_type": "RENAME_CLASS", "class_id": "cls-ghost", "new_name": "Nope"}


@pytest.fixture
def room(diagram_repo):
    doc = CanonicalUmlDocument(id=f"diag-res-{uuid.uuid4().hex[:8]}", name="Resolve", version=1)
    diagram_repo.save(doc)
    return doc


def _ai_message(db_session, diagram_id, *, commands=None, operations=None, age_minutes=0):
    msg = ChatMessage(
        id=f"msg-{uuid.uuid4().hex[:8]}",
        diagram_id=diagram_id,
        sender_id="ai-senior",
        sender_name="Senior AI",
        sender_type=SenderType.AI,
        content="Te propongo esto.",
        suggested_commands=commands,
        suggested_operations=operations,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )
    SqlAlchemyChatRepository(db_session).add_message(msg)
    return msg


def _resolve(client, diagram_id, message_id, body, **kwargs):
    return client.post(f"/api/diagrams/{diagram_id}/chat/{message_id}/resolve", json=body, **kwargs)


HOLOGRAM_APPLY = {"target": "hologram", "action": "APPLY"}


def test_hologram_apply_replays_the_stored_commands_and_records_who_did_it(client, room, db_session, recorded_broadcasts):
    msg = _ai_message(db_session, room.id, commands=[CREATE_A, RENAME_GHOST, CREATE_B])

    # Client-sent commands must be ignored: the server replays what it stored.
    body = {**HOLOGRAM_APPLY, "commands": [{"command_type": "CREATE_CLASS", "name": "Injected"}]}
    resp = _resolve(client, room.id, msg.id, body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_count"] == 2
    assert len(data["skipped"]) == 1
    assert [c["name"] for c in data["document"]["classes"]] == ["Alpha", "Beta"]
    resolution = data["message"]["resolutions"]["hologram"]
    assert resolution["status"] == "APPLIED"
    assert resolution["applied_count"] == 2 and resolution["skipped_count"] == 1
    assert resolution["by_id"] == "guest" and resolution["at"]


def test_second_resolve_of_the_same_target_is_409_with_the_message(client, room, db_session):
    msg = _ai_message(db_session, room.id, commands=[CREATE_A])
    assert _resolve(client, room.id, msg.id, HOLOGRAM_APPLY).status_code == 200

    again = _resolve(client, room.id, msg.id, HOLOGRAM_APPLY)

    assert again.status_code == 409
    body = again.json()
    assert body["detail"]
    assert body["message"]["resolutions"]["hologram"]["status"] == "APPLIED"
    # and it was not applied twice
    assert len(client.get(f"/api/diagrams/{room.id}").json()["classes"]) == 1
    # a resolved proposal cannot be discarded afterwards either
    assert _resolve(client, room.id, msg.id, {"target": "hologram", "action": "DISCARD"}).status_code == 409


def test_discard_only_records_the_resolution(client, room, db_session, recorded_broadcasts):
    msg = _ai_message(db_session, room.id, commands=[CREATE_A])

    resp = _resolve(client, room.id, msg.id, {"target": "hologram", "action": "DISCARD"})

    assert resp.status_code == 200
    assert resp.json()["message"]["resolutions"]["hologram"]["status"] == "DISCARDED"
    assert resp.json()["document"] is None
    assert client.get(f"/api/diagrams/{room.id}").json()["classes"] == []
    assert [m["type"] for m in recorded_broadcasts] == ["CHAT_MESSAGE_UPDATED"]


def test_viewers_and_outsiders_cannot_apply_or_discard(client, private_room, db_session):
    msg = _ai_message(db_session, private_room.diagram_id, commands=[CREATE_A])
    for headers in (private_room.viewer, private_room.outsider):
        for action in ("APPLY", "DISCARD"):
            resp = _resolve(client, private_room.diagram_id, msg.id, {"target": "hologram", "action": action}, headers=headers)
            assert resp.status_code == 403
    assert _resolve(client, private_room.diagram_id, msg.id, HOLOGRAM_APPLY).status_code == 401
    assert _resolve(client, private_room.diagram_id, msg.id, HOLOGRAM_APPLY, headers=private_room.editor).status_code == 200


def test_proposals_older_than_30_minutes_are_gone_for_apply_but_can_be_discarded(client, room, db_session):
    msg = _ai_message(db_session, room.id, commands=[CREATE_A], age_minutes=31)

    expired = _resolve(client, room.id, msg.id, HOLOGRAM_APPLY)

    assert expired.status_code == 410
    assert client.get(f"/api/diagrams/{room.id}").json()["classes"] == []
    assert _resolve(client, room.id, msg.id, {"target": "hologram", "action": "DISCARD"}).status_code == 200


def test_hologram_that_applies_nothing_stays_unresolved(client, room, db_session, recorded_broadcasts):
    msg = _ai_message(db_session, room.id, commands=[RENAME_GHOST])

    resp = _resolve(client, room.id, msg.id, HOLOGRAM_APPLY)

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_count"] == 0
    assert len(data["skipped"]) == 1
    assert data["message"]["resolutions"] == {}
    assert recorded_broadcasts == []
    # still resolvable later
    assert _resolve(client, room.id, msg.id, {"target": "hologram", "action": "DISCARD"}).status_code == 200


def test_restore_operation_applies_the_version_and_broadcasts_document_and_message(client, room, db_session, recorded_broadcasts):
    service = DiagramVersionService(db_session)
    service.create_snapshot(room, author_id="u", author_name="U", kind=VersionKind.CHECKPOINT)  # empty diagram = v1
    edited = client.post(f"/api/diagrams/{room.id}/commands", json={"command": CREATE_A}).json()["document"]
    msg = _ai_message(db_session, room.id, operations=[{"op": "RESTORE_VERSION", "version_number": 1, "reason": "volver"}])
    recorded_broadcasts.clear()

    resp = _resolve(
        client, room.id, msg.id,
        {"target": "operation", "index": 0, "action": "APPLY", "expected_document_version": edited["version"]},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["document"]["classes"] == []
    assert data["message"]["resolutions"]["op:0"]["status"] == "APPLIED"
    assert [m["type"] for m in recorded_broadcasts] == ["DOCUMENT_UPDATED", "CHAT_MESSAGE_UPDATED"]
    updated = recorded_broadcasts[1]["message"]
    assert updated["id"] == msg.id and updated["resolutions"]["op:0"]["status"] == "APPLIED"


def test_restore_with_a_stale_document_version_is_409_and_stays_unresolved(client, room, db_session):
    DiagramVersionService(db_session).create_snapshot(room, author_id="u", author_name="U", kind=VersionKind.CHECKPOINT)
    client.post(f"/api/diagrams/{room.id}/commands", json={"command": CREATE_A})
    msg = _ai_message(db_session, room.id, operations=[{"op": "RESTORE_VERSION", "version_number": 1}])

    resp = _resolve(client, room.id, msg.id, {"target": "operation", "index": 0, "action": "APPLY", "expected_document_version": 1})

    assert resp.status_code == 409
    assert client.get(f"/api/diagrams/{room.id}").json()["classes"] != []  # untouched
    chat = client.get(f"/api/diagrams/{room.id}/chat").json()["messages"]
    assert chat[0]["resolutions"] == {}


def test_checkpoint_operation_creates_a_checkpoint(client, room, db_session):
    msg = _ai_message(db_session, room.id, operations=[{"op": "CREATE_CHECKPOINT", "label": "Hito", "reason": "antes de seguir"}])

    resp = _resolve(client, room.id, msg.id, {"target": "operation", "index": 0, "action": "APPLY"})

    assert resp.status_code == 200
    assert resp.json()["document"] is None
    versions = DiagramVersionService(db_session).list_versions(room.id)
    assert [(v.kind, v.label) for v in versions] == [(VersionKind.CHECKPOINT, "Hito")]


def test_resolutions_are_persisted_and_returned_by_get_chat(client, room, db_session):
    msg = _ai_message(
        db_session, room.id, commands=[CREATE_A],
        operations=[{"op": "CREATE_CHECKPOINT"}, {"op": "CREATE_CHECKPOINT"}],
    )
    _resolve(client, room.id, msg.id, HOLOGRAM_APPLY)
    _resolve(client, room.id, msg.id, {"target": "operation", "index": 1, "action": "DISCARD"})

    chat = client.get(f"/api/diagrams/{room.id}/chat").json()["messages"]

    resolutions = chat[0]["resolutions"]
    assert set(resolutions) == {"hologram", "op:1"}
    assert resolutions["hologram"]["status"] == "APPLIED"
    assert resolutions["op:1"]["status"] == "DISCARDED"


def test_unknown_targets_are_404_and_bad_input_is_400(client, room, db_session):
    plain = _ai_message(db_session, room.id)
    holo = _ai_message(db_session, room.id, commands=[CREATE_A], operations=[{"op": "CREATE_CHECKPOINT"}])

    assert _resolve(client, room.id, "msg-missing", HOLOGRAM_APPLY).status_code == 404
    assert _resolve(client, room.id, plain.id, HOLOGRAM_APPLY).status_code == 404  # nothing proposed
    assert _resolve(client, room.id, holo.id, {"target": "operation", "index": 5, "action": "APPLY"}).status_code == 404
    assert _resolve(client, "diag-nope", holo.id, HOLOGRAM_APPLY).status_code == 404
    assert _resolve(client, room.id, holo.id, {"target": "hologram", "action": "NUKE"}).status_code == 400
    assert _resolve(client, room.id, holo.id, {"target": "banana", "action": "APPLY"}).status_code == 400
    assert _resolve(client, room.id, holo.id, {"target": "operation", "action": "APPLY"}).status_code == 400


def test_a_message_of_another_diagram_cannot_be_resolved_through_this_one(client, room, db_session, diagram_repo):
    other = CanonicalUmlDocument(id=f"diag-other-{uuid.uuid4().hex[:6]}", name="Other", version=1)
    diagram_repo.save(other)
    msg = _ai_message(db_session, other.id, commands=[CREATE_A])

    assert _resolve(client, room.id, msg.id, HOLOGRAM_APPLY).status_code == 404
