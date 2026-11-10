"""Diagram version control service.

CONTRACT: this file is the interface agreed between agent A (implements it) and
agent B (calls it from the AI room). Signatures and semantics below must not change.

Semantics
- All methods are synchronous and use the SQLAlchemy session given in __init__.
- The service never takes the room lock. Callers that mutate documents
  (restore, AI edits) must hold `get_room_lock(diagram_id)` themselves.
- `number` is 1-based and strictly increasing per diagram.
- `restore` first stores a PRE_RESTORE snapshot of the CURRENT state, then
  replaces classes/relationships of the live document with the snapshot's,
  keeps id/owner_id/name, sets version = current.version + 1, saves it and
  returns the saved document. It never deletes history.
- `diff(from_number, to_number)`: to_number=None compares against the current
  live document; from_number is required.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.versioning import (
    DiagramVersion,
    DiagramVersionDetail,
    VersionAuthorType,
    VersionConflictError,
    VersionDiff,
    VersionKind,
    VersionNotFoundError,
)
from app.domain.services.diagram_diff import build_summary, diff_documents
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.infrastructure.persistence.version_models import DiagramVersionModel

MAX_AUTO_VERSIONS = 50
_NUMBER_RACE_RETRIES = 1
_VOLATILE_FIELDS = ("version", "updated_at")


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _content(snapshot: dict) -> dict:
    """Document content ignoring fields that change on every save."""
    return {k: v for k, v in snapshot.items() if k not in _VOLATILE_FIELDS}


def _to_version(row: DiagramVersionModel) -> DiagramVersion:
    return DiagramVersion(
        id=row.id,
        diagram_id=row.diagram_id,
        number=row.number,
        kind=VersionKind(row.kind),
        label=row.label,
        message=row.message,
        author_id=row.author_id,
        author_name=row.author_name,
        author_type=VersionAuthorType(row.author_type),
        document_version=row.document_version,
        class_count=row.class_count,
        relationship_count=row.relationship_count,
        created_at=_as_utc(row.created_at),
    )


def _to_detail(row: DiagramVersionModel) -> DiagramVersionDetail:
    return DiagramVersionDetail(
        **_to_version(row).model_dump(),
        snapshot=CanonicalUmlDocument.model_validate(row.snapshot),
    )


class DiagramVersionService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ helpers
    def _query(self, diagram_id: str):
        return self.db.query(DiagramVersionModel).filter(DiagramVersionModel.diagram_id == diagram_id)

    def _latest(self, diagram_id: str) -> Optional[DiagramVersionModel]:
        return self._query(diagram_id).order_by(DiagramVersionModel.number.desc()).first()

    def _row(self, diagram_id: str, number: int) -> DiagramVersionModel:
        row = self._query(diagram_id).filter(DiagramVersionModel.number == number).first()
        if row is None:
            raise VersionNotFoundError(f"Version {number} of diagram '{diagram_id}' not found.")
        return row

    def _live_document(self, diagram_id: str) -> CanonicalUmlDocument:
        # Callers may hold long-lived sessions: never trust cached rows.
        self.db.expire_all()
        document = SqlAlchemyDiagramRepository(self.db).get_by_id(diagram_id)
        if document is None:
            raise VersionNotFoundError(f"Diagram '{diagram_id}' not found.")
        return document

    # --------------------------------------------------------------- operations
    def create_snapshot(
        self,
        document: CanonicalUmlDocument,
        *,
        author_id: str,
        author_name: str,
        author_type: VersionAuthorType = VersionAuthorType.USER,
        kind: VersionKind = VersionKind.CHECKPOINT,
        label: Optional[str] = None,
        message: Optional[str] = None,
    ) -> DiagramVersion:
        snapshot = document.model_dump(mode="json")
        for attempt in range(_NUMBER_RACE_RETRIES + 1):
            max_number = (
                self.db.query(func.max(DiagramVersionModel.number))
                .filter(DiagramVersionModel.diagram_id == document.id)
                .scalar()
            )
            row = DiagramVersionModel(
                id=f"ver-{uuid.uuid4().hex[:12]}",
                diagram_id=document.id,
                number=(max_number or 0) + 1,
                kind=kind.value,
                label=label,
                message=message,
                author_id=author_id,
                author_name=author_name,
                author_type=author_type.value,
                document_version=document.version,
                class_count=len(document.classes),
                relationship_count=len(document.relationships),
                snapshot=snapshot,
                created_at=datetime.now(timezone.utc),
            )
            self.db.add(row)
            try:
                self.db.commit()
            except IntegrityError:
                # Another writer took the same number: retry once with a fresh max.
                self.db.rollback()
                if attempt >= _NUMBER_RACE_RETRIES:
                    raise
                continue
            return _to_version(row)
        raise RuntimeError("unreachable")  # pragma: no cover

    def maybe_auto_snapshot(
        self,
        document: CanonicalUmlDocument,
        *,
        author_id: str,
        author_name: str,
        author_type: VersionAuthorType = VersionAuthorType.USER,
        min_interval_seconds: int = 600,
    ) -> Optional[DiagramVersion]:
        """Create an AUTO snapshot only if the last snapshot is older than
        min_interval_seconds AND the document changed since it. Else return None."""
        last = self._latest(document.id)
        if last is not None:
            age = datetime.now(timezone.utc) - _as_utc(last.created_at)
            if age < timedelta(seconds=min_interval_seconds):
                return None
            if _content(last.snapshot) == _content(document.model_dump(mode="json")):
                return None
        created = self.create_snapshot(
            document,
            author_id=author_id,
            author_name=author_name,
            author_type=author_type,
            kind=VersionKind.AUTO,
        )
        self._prune_auto(document.id)
        return created

    def _prune_auto(self, diagram_id: str) -> None:
        stale = (
            self._query(diagram_id)
            .filter(DiagramVersionModel.kind == VersionKind.AUTO.value)
            .order_by(DiagramVersionModel.number.desc())
            .offset(MAX_AUTO_VERSIONS)
            .all()
        )
        if not stale:
            return
        for row in stale:
            self.db.delete(row)
        self.db.commit()

    def list_versions(self, diagram_id: str, limit: int = 50) -> List[DiagramVersion]:
        """Newest first."""
        rows = (
            self._query(diagram_id)
            .order_by(DiagramVersionModel.number.desc())
            .limit(limit)
            .all()
        )
        return [_to_version(r) for r in rows]

    def get_version(self, diagram_id: str, number: int) -> DiagramVersionDetail:
        """Raises VersionNotFoundError."""
        return _to_detail(self._row(diagram_id, number))

    def diff(
        self,
        diagram_id: str,
        from_number: int,
        to_number: Optional[int] = None,
    ) -> VersionDiff:
        """Raises VersionNotFoundError."""
        old = CanonicalUmlDocument.model_validate(self._row(diagram_id, from_number).snapshot)
        if to_number is None:
            new = self._live_document(diagram_id)
        else:
            new = CanonicalUmlDocument.model_validate(self._row(diagram_id, to_number).snapshot)
        changes = diff_documents(old, new)
        return VersionDiff(
            diagram_id=diagram_id,
            from_number=from_number,
            to_number=to_number,
            changes=changes,
            summary=build_summary(changes),
        )

    def restore(
        self,
        diagram_id: str,
        number: int,
        *,
        author_id: str,
        author_name: str,
        author_type: VersionAuthorType = VersionAuthorType.USER,
        expected_document_version: Optional[int] = None,
    ) -> CanonicalUmlDocument:
        """Raises VersionNotFoundError, or VersionConflictError when
        expected_document_version is given and differs from the live version."""
        target = _to_detail(self._row(diagram_id, number))
        current = self._live_document(diagram_id)
        if expected_document_version is not None and expected_document_version != current.version:
            raise VersionConflictError(
                f"El diagrama cambió (versión {current.version}, se esperaba {expected_document_version})."
            )

        self.create_snapshot(
            current,
            author_id=author_id,
            author_name=author_name,
            author_type=author_type,
            kind=VersionKind.PRE_RESTORE,
            label=f"Antes de restaurar la versión {number}",
        )

        restored = current.model_copy(
            update={
                "classes": [c.model_copy(deep=True) for c in target.snapshot.classes],
                "relationships": [r.model_copy(deep=True) for r in target.snapshot.relationships],
                "version": current.version + 1,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return SqlAlchemyDiagramRepository(self.db).save(restored)
