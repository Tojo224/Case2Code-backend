from datetime import datetime, timedelta, timezone

import uuid

import pytest

from app.application.services.version_service import MAX_AUTO_VERSIONS, DiagramVersionService
from app.domain.models.canonical_uml import CanonicalUmlDocument, UmlAttribute, UmlClass
from app.domain.models.versioning import (
    VersionAuthorType,
    VersionConflictError,
    VersionKind,
    VersionNotFoundError,
)
from app.infrastructure.persistence.version_models import DiagramVersionModel

AUTHOR = {"author_id": "u1", "author_name": "Ana"}


@pytest.fixture
def service(db_session):
    return DiagramVersionService(db_session)


@pytest.fixture
def doc(diagram_repo):
    d = CanonicalUmlDocument(
        id=f"diag-ver-{uuid.uuid4().hex[:8]}",
        name="Tienda",
        owner_id=None,
        classes=[UmlClass(id="c1", name="Producto", attributes=[UmlAttribute(id="a1", name="id")])],
    )
    diagram_repo.save(d)
    return d


def _backdate(db_session, diagram_id, seconds):
    for row in db_session.query(DiagramVersionModel).filter_by(diagram_id=diagram_id):
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    db_session.commit()


def test_numbering_and_metadata(service, doc):
    v1 = service.create_snapshot(doc, **AUTHOR, label="Inicial")
    v2 = service.create_snapshot(doc, **AUTHOR, kind=VersionKind.PRE_AI, author_type=VersionAuthorType.AI)
    assert (v1.number, v2.number) == (1, 2)
    assert v1.kind == VersionKind.CHECKPOINT and v1.label == "Inicial"
    assert v1.class_count == 1 and v1.relationship_count == 0 and v1.document_version == 1
    assert v2.author_type == VersionAuthorType.AI
    assert [v.number for v in service.list_versions(doc.id)] == [2, 1]
    assert service.get_version(doc.id, 1).snapshot.classes[0].name == "Producto"


def test_number_race_retries_once(service, doc, db_session, monkeypatch):
    service.create_snapshot(doc, **AUTHOR)
    real = db_session.query
    calls = {"n": 0}

    # First max() lookup returns a stale value, forcing a unique-constraint conflict.
    from sqlalchemy import func as sa_func

    class StaleQuery:
        def __init__(self, q):
            self.q = q

        def filter(self, *a, **k):
            return self

        def scalar(self):
            calls["n"] += 1
            return 0 if calls["n"] == 1 else 1

    def fake_query(*entities):
        if len(entities) == 1 and str(entities[0]) == str(sa_func.max(DiagramVersionModel.number)):
            return StaleQuery(None)
        return real(*entities)

    monkeypatch.setattr(db_session, "query", fake_query)
    created = service.create_snapshot(doc, **AUTHOR)
    assert created.number == 2
    assert calls["n"] == 2


def test_get_version_not_found(service, doc):
    with pytest.raises(VersionNotFoundError):
        service.get_version(doc.id, 99)
    with pytest.raises(VersionNotFoundError):
        service.diff(doc.id, 99)


def test_auto_snapshot_created_when_none_exists(service, doc):
    v = service.maybe_auto_snapshot(doc, **AUTHOR)
    assert v is not None and v.kind == VersionKind.AUTO


def test_auto_snapshot_throttled_by_interval(service, doc, db_session):
    service.create_snapshot(doc, **AUTHOR)
    changed = doc.model_copy(deep=True)
    changed.classes.append(UmlClass(id="c2", name="Cliente"))
    assert service.maybe_auto_snapshot(changed, **AUTHOR, min_interval_seconds=600) is None
    _backdate(db_session, doc.id, 700)
    assert service.maybe_auto_snapshot(changed, **AUTHOR, min_interval_seconds=600) is not None


def test_auto_snapshot_skipped_when_unchanged(service, doc, db_session):
    service.create_snapshot(doc, **AUTHOR)
    _backdate(db_session, doc.id, 700)
    bumped = doc.model_copy(deep=True)
    bumped.version += 5
    bumped.updated_at = datetime.now(timezone.utc) + timedelta(hours=1)
    assert service.maybe_auto_snapshot(bumped, **AUTHOR, min_interval_seconds=600) is None


def test_auto_versions_are_pruned_but_checkpoints_kept(service, doc):
    service.create_snapshot(doc, **AUTHOR, label="Hito")
    for _ in range(MAX_AUTO_VERSIONS + 5):
        service.create_snapshot(doc, **AUTHOR, kind=VersionKind.AUTO)
    # Trigger pruning through the public path.
    changed = doc.model_copy(deep=True)
    changed.name = "Otra"
    service.db.query(DiagramVersionModel).update({"created_at": datetime.now(timezone.utc) - timedelta(days=1)})
    service.db.commit()
    assert service.maybe_auto_snapshot(changed, **AUTHOR, min_interval_seconds=1) is not None

    versions = service.list_versions(doc.id, limit=200)
    autos = [v for v in versions if v.kind == VersionKind.AUTO]
    assert len(autos) == MAX_AUTO_VERSIONS
    assert any(v.kind == VersionKind.CHECKPOINT and v.label == "Hito" for v in versions)
    assert versions[0].number == MAX_AUTO_VERSIONS + 7  # newest kept


def test_diff_against_current_and_between_versions(service, doc, diagram_repo):
    service.create_snapshot(doc, **AUTHOR)
    live = doc.model_copy(deep=True)
    live.classes.append(UmlClass(id="c2", name="Cliente"))
    live.version = 2
    diagram_repo.save(live)
    service.create_snapshot(live, **AUTHOR)

    against_current = service.diff(doc.id, 1)
    assert against_current.to_number is None
    assert [c.kind for c in against_current.changes] == ["class_added"]
    assert against_current.summary == "1 clase agregada"

    between = service.diff(doc.id, 1, 2)
    assert between.to_number == 2 and len(between.changes) == 1


def test_restore_creates_pre_restore_and_bumps_version(service, doc, diagram_repo):
    service.create_snapshot(doc, **AUTHOR, label="Base")
    live = doc.model_copy(deep=True)
    live.classes = [UmlClass(id="c9", name="Otra")]
    live.version = 4
    diagram_repo.save(live)

    restored = service.restore(doc.id, 1, **AUTHOR)

    assert restored.version == 5
    assert [c.name for c in restored.classes] == ["Producto"]
    assert (restored.id, restored.name, restored.owner_id) == (doc.id, doc.name, doc.owner_id)
    assert diagram_repo.get_by_id(doc.id).version == 5

    versions = service.list_versions(doc.id)
    assert [v.number for v in versions] == [2, 1]
    pre = versions[0]
    assert pre.kind == VersionKind.PRE_RESTORE and pre.document_version == 4
    assert service.get_version(doc.id, 2).snapshot.classes[0].name == "Otra"


def test_restore_uses_deep_copies(service, doc, diagram_repo):
    service.create_snapshot(doc, **AUTHOR)
    restored = service.restore(doc.id, 1, **AUTHOR)
    restored.classes[0].name = "Mutada"
    assert service.get_version(doc.id, 1).snapshot.classes[0].name == "Producto"


def test_restore_conflict_leaves_no_side_effects(service, doc):
    service.create_snapshot(doc, **AUTHOR)
    with pytest.raises(VersionConflictError):
        service.restore(doc.id, 1, **AUTHOR, expected_document_version=99)
    assert len(service.list_versions(doc.id)) == 1


def test_restore_with_matching_expected_version(service, doc):
    service.create_snapshot(doc, **AUTHOR)
    restored = service.restore(doc.id, 1, **AUTHOR, expected_document_version=1)
    assert restored.version == 2


def test_restore_not_found(service, doc):
    with pytest.raises(VersionNotFoundError):
        service.restore(doc.id, 7, **AUTHOR)
