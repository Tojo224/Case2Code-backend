"""SQLAlchemy table for diagram versions. CONTRACT (agent A implements the repository logic)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class DiagramVersionModel(Base):
    __tablename__ = "diagram_versions"
    __table_args__ = (UniqueConstraint("diagram_id", "number", name="uq_diagram_version_number"),)

    id = Column(String(64), primary_key=True, index=True)
    diagram_id = Column(
        String(64),
        ForeignKey("diagrams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number = Column(Integer, nullable=False)
    kind = Column(String(32), nullable=False)
    label = Column(String(255), nullable=True)
    message = Column(Text, nullable=True)
    author_id = Column(String(64), nullable=False)
    author_name = Column(String(255), nullable=False)
    author_type = Column(String(16), nullable=False, default="USER")
    document_version = Column(Integer, nullable=False)
    class_count = Column(Integer, nullable=False, default=0)
    relationship_count = Column(Integer, nullable=False, default=0)
    snapshot = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
