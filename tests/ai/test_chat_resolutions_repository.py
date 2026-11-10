import uuid

import pytest
from sqlalchemy import inspect

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage, SenderType
from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository


@pytest.fixture
def stored(diagram_repo, db_session):
    doc = CanonicalUmlDocument(id=f"diag-resrepo-{uuid.uuid4().hex[:8]}", name="R", version=1)
    diagram_repo.save(doc)
    repo = SqlAlchemyChatRepository(db_session)
    msg = ChatMessage(
        id=f"m-{uuid.uuid4().hex[:6]}", diagram_id=doc.id, sender_id="ai", sender_name="AI",
        sender_type=SenderType.AI, content="x",
    )
    repo.add_message(msg)
    return repo, msg


def test_resolutions_column_exists_and_defaults_to_empty(stored, db_session):
    repo, msg = stored
    columns = {c["name"] for c in inspect(db_session.get_bind()).get_columns("diagram_chat_messages")}
    assert "resolutions" in columns
    assert repo.get_message(msg.id).resolutions == {}


def test_update_resolutions_is_one_shot_per_key(stored):
    repo, msg = stored

    first = repo.update_resolutions(msg.id, "hologram", {"status": "APPLIED"})
    second = repo.update_resolutions(msg.id, "hologram", {"status": "DISCARDED"})
    other_key = repo.update_resolutions(msg.id, "op:0", {"status": "DISCARDED"})

    assert first.resolutions == {"hologram": {"status": "APPLIED"}}
    assert second is None
    assert set(other_key.resolutions) == {"hologram", "op:0"}
    assert repo.get_message(msg.id).resolutions["hologram"]["status"] == "APPLIED"
    assert repo.list_messages(msg.diagram_id)[0].resolutions == other_key.resolutions


def test_get_message_and_update_of_unknown_id(stored):
    repo, _ = stored
    assert repo.get_message("nope") is None
    assert repo.update_resolutions("nope", "hologram", {"status": "APPLIED"}) is None


def test_replay_commands_is_still_importable_from_the_ai_room_runner():
    from app.application import replay
    from app.infrastructure.ai import ai_room_runner

    assert ai_room_runner.replay_commands is replay.replay_commands
    assert ai_room_runner.ReplayOutcome is replay.ReplayOutcome
