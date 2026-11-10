"""Per-diagram AI conversational memory. CONTRACT (agent B implements the repository)."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class AiRoomMemoryModel(Base):
    __tablename__ = "ai_room_memory"

    diagram_id = Column(
        String(64),
        ForeignKey("diagrams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_topic = Column(String(64), nullable=True)
    last_prompt = Column(Text, nullable=True)
    # Last proposal (hologram) the AI made: list of UmlCommand dicts + the document
    # version it was computed against, so "hacelo" can replay it safely.
    pending_commands = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    pending_base_version = Column(Integer, nullable=True)
    pending_requested_by = Column(String(64), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
