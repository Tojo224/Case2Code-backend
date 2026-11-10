from datetime import datetime, timedelta, timezone

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.versioning import VersionAuthorType, VersionKind
from app.infrastructure.ai import version_tools as vt
from tests.ai.fakes import FakeVersionService, make_version

DOC = CanonicalUmlDocument(id="d", name="n")


def test_second_ai_checkpoint_within_cooldown_is_refused():
    svc = FakeVersionService()
    first = vt.create_ai_checkpoint(svc, DOC, "Ana")
    assert "checkpoint v1" in first.reply
    second = vt.create_ai_checkpoint(svc, DOC, "Ana")
    assert "Esperá" in second.reply
    assert len(svc.snapshots) == 1


def test_ai_checkpoint_allowed_again_after_cooldown():
    old = make_version(3, kind=VersionKind.CHECKPOINT, author_type=VersionAuthorType.AI)
    old.created_at = datetime.now(timezone.utc) - timedelta(seconds=vt.AI_CHECKPOINT_COOLDOWN_S + 5)
    svc = FakeVersionService([old])
    res = vt.create_ai_checkpoint(svc, DOC, "Ana")
    assert "checkpoint v4" in res.reply


def test_user_checkpoints_and_other_kinds_do_not_trigger_the_cooldown():
    svc = FakeVersionService(
        [
            make_version(2, kind=VersionKind.PRE_AI, author_type=VersionAuthorType.AI),
            make_version(1, kind=VersionKind.CHECKPOINT, author_type=VersionAuthorType.USER),
        ]
    )
    assert "checkpoint v3" in vt.create_ai_checkpoint(svc, DOC, "Ana").reply


def test_cooldown_is_per_diagram_with_the_real_service(db_session, diagram_repo):
    from app.application.services.version_service import DiagramVersionService

    a = CanonicalUmlDocument(id="cool-a", name="A")
    b = CanonicalUmlDocument(id="cool-b", name="B")
    diagram_repo.save(a)
    diagram_repo.save(b)
    service = DiagramVersionService(db_session)
    assert "checkpoint v1" in vt.create_ai_checkpoint(service, a, "Ana").reply
    assert "Esperá" in vt.create_ai_checkpoint(service, a, "Ana").reply
    assert "checkpoint v1" in vt.create_ai_checkpoint(service, b, "Ana").reply
