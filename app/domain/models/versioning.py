"""Domain models for diagram version control.

CONTRACT: shared by the versioning service (agent A) and the AI room (agent B).
Do not change field names without updating both sides.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.models.canonical_uml import CanonicalUmlDocument


class VersionAuthorType(str, Enum):
    USER = "USER"
    AI = "AI"
    SYSTEM = "SYSTEM"


class VersionKind(str, Enum):
    CHECKPOINT = "CHECKPOINT"      # explicit, named by a user or by the AI
    AUTO = "AUTO"                  # throttled automatic snapshot while editing
    PRE_AI = "PRE_AI"              # taken right before the AI mutates the diagram
    PRE_RESTORE = "PRE_RESTORE"    # taken right before a restore, so restore is undoable


class DiagramVersion(BaseModel):
    id: str
    diagram_id: str
    number: int  # 1-based, strictly increasing per diagram
    kind: VersionKind
    label: Optional[str] = None
    message: Optional[str] = None
    author_id: str
    author_name: str
    author_type: VersionAuthorType = VersionAuthorType.USER
    document_version: int  # CanonicalUmlDocument.version at snapshot time
    class_count: int
    relationship_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DiagramVersionDetail(DiagramVersion):
    snapshot: CanonicalUmlDocument


class DiagramChange(BaseModel):
    # class_added | class_removed | class_renamed | attribute_added | attribute_removed
    # | attribute_changed | relationship_added | relationship_removed | relationship_changed
    kind: str
    target_id: str
    description: str  # human readable, Spanish, e.g. "Se agregó la clase Producto"


class VersionDiff(BaseModel):
    diagram_id: str
    from_number: Optional[int] = None  # None never means "current" for from_number
    to_number: Optional[int] = None    # None means "current state of the diagram"
    changes: List[DiagramChange] = Field(default_factory=list)
    summary: str = ""


class VersionNotFoundError(Exception):
    pass


class VersionConflictError(Exception):
    """Raised when expected_document_version does not match the current document."""
