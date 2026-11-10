import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage, SenderType
from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def room(diagram_repo):
    doc = CanonicalUmlDocument(id=f"diag-chat-{uuid.uuid4().hex[:8]}", name="Room", version=1)
    diagram_repo.save(doc)
    return doc


def _msg(room, idx, created_at=None, mid=None):
    return ChatMessage(
        id=mid or f"m-{idx:03d}",
        diagram_id=room.id,
        sender_id="u1",
        sender_name="Ana",
        sender_type=SenderType.USER,
        content=f"msg {idx}",
        created_at=created_at or T0 + timedelta(seconds=idx),
    )


def test_list_messages_returns_the_latest_n_in_chronological_order(room, db_session):
    repo = SqlAlchemyChatRepository(db_session)
    for i in range(30):
        repo.add_message(_msg(room, i))

    got = repo.list_messages(room.id, limit=12)

    assert [m.content for m in got] == [f"msg {i}" for i in range(18, 30)]


def test_list_messages_ties_on_timestamp_are_broken_by_id(room, db_session):
    repo = SqlAlchemyChatRepository(db_session)
    for i in range(5):
        repo.add_message(_msg(room, i, created_at=T0))

    got = repo.list_messages(room.id, limit=3)

    assert [m.id for m in got] == ["m-002", "m-003", "m-004"]


def test_composite_index_exists_on_chat_messages(db_session):
    indexes = inspect(db_session.get_bind()).get_indexes("diagram_chat_messages")
    assert any(ix["column_names"] == ["diagram_id", "created_at"] for ix in indexes)
