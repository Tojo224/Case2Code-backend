import uuid
from datetime import datetime, timedelta, timezone

from app.infrastructure.persistence.ai_memory_repository import SqlAlchemyAiMemoryRepository

CMDS = [{"command_type": "CREATE_CLASS", "name": "Producto"}]


def _repo(db_session):
    return SqlAlchemyAiMemoryRepository(db_session), f"diag-mem-{uuid.uuid4().hex[:8]}"


def test_get_missing_returns_none(db_session):
    repo, did = _repo(db_session)
    assert repo.get(did) is None


def test_save_topic_then_pending_round_trip(db_session):
    repo, did = _repo(db_session)
    repo.save_topic(did, "hotel", "armá un hotel")
    repo.save_pending(did, CMDS, base_version=4, requested_by="usr-1")

    mem = repo.get(did)
    assert mem.last_topic == "hotel" and mem.last_prompt == "armá un hotel"
    assert mem.pending_commands == CMDS
    assert mem.pending_base_version == 4 and mem.pending_requested_by == "usr-1"
    assert mem.has_live_pending()


def test_clear_pending_keeps_topic(db_session):
    repo, did = _repo(db_session)
    repo.save_topic(did, "hotel")
    repo.save_pending(did, CMDS, 1, "usr-1")
    repo.clear_pending(did)

    mem = repo.get(did)
    assert mem.last_topic == "hotel"
    assert mem.pending_commands is None and not mem.has_live_pending()


def test_pending_expires_after_ttl(db_session):
    repo, did = _repo(db_session)
    mem = repo.save_pending(did, CMDS, 1, "usr-1")
    stamp = mem.updated_at if mem.updated_at.tzinfo else mem.updated_at.replace(tzinfo=timezone.utc)
    assert not mem.has_live_pending(now=stamp + timedelta(minutes=31))
    assert mem.has_live_pending(now=datetime.now(timezone.utc))


def test_topic_chatter_does_not_extend_pending_ttl(db_session):
    repo, did = _repo(db_session)
    before = repo.save_pending(did, CMDS, 1, "usr-1").updated_at
    after = repo.save_topic(did, "hospital", "otra cosa").updated_at
    assert after == before
