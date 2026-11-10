"""Diagram version control service.

CONTRACT: this file is the interface agreed between agent A (implements it) and
agent B (calls it from the AI room). Agent A replaces the NotImplementedError
bodies; signatures and semantics below must not change.

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

from typing import List, Optional

from sqlalchemy.orm import Session

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.versioning import (
    DiagramVersion,
    DiagramVersionDetail,
    VersionAuthorType,
    VersionDiff,
    VersionKind,
)


class DiagramVersionService:
    def __init__(self, db: Session):
        self.db = db

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
        raise NotImplementedError

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
        raise NotImplementedError

    def list_versions(self, diagram_id: str, limit: int = 50) -> List[DiagramVersion]:
        """Newest first."""
        raise NotImplementedError

    def get_version(self, diagram_id: str, number: int) -> DiagramVersionDetail:
        """Raises VersionNotFoundError."""
        raise NotImplementedError

    def diff(
        self,
        diagram_id: str,
        from_number: int,
        to_number: Optional[int] = None,
    ) -> VersionDiff:
        """Raises VersionNotFoundError."""
        raise NotImplementedError

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
        raise NotImplementedError
