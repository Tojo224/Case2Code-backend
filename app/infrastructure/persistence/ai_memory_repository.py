"""Per-diagram AI memory on top of AiRoomMemoryModel (replaces the old JSON file)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.infrastructure.persistence.room_memory_models import AiRoomMemoryModel

PENDING_TTL = timedelta(minutes=30)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class AiRoomMemory:
    diagram_id: str
    last_topic: Optional[str]
    last_prompt: Optional[str]
    pending_commands: Optional[List[Dict[str, Any]]]
    pending_base_version: Optional[int]
    pending_requested_by: Optional[str]
    updated_at: Optional[datetime]

    def has_live_pending(self, now: Optional[datetime] = None, ttl: timedelta = PENDING_TTL) -> bool:
        """A pending proposal exists and is younger than the TTL.

        updated_at doubles as "pending created at": save_topic never touches it
        while a proposal is pending, so topic chatter cannot extend the TTL.
        """
        if not self.pending_commands:
            return False
        stamp = _aware(self.updated_at)
        if stamp is None:
            return False
        return (now or datetime.now(timezone.utc)) - stamp <= ttl


class SqlAlchemyAiMemoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def _row(self, diagram_id: str) -> Optional[AiRoomMemoryModel]:
        return self.db.get(AiRoomMemoryModel, diagram_id)

    @staticmethod
    def _to_memory(row: AiRoomMemoryModel) -> AiRoomMemory:
        return AiRoomMemory(
            diagram_id=row.diagram_id,
            last_topic=row.last_topic,
            last_prompt=row.last_prompt,
            pending_commands=row.pending_commands or None,
            pending_base_version=row.pending_base_version,
            pending_requested_by=row.pending_requested_by,
            updated_at=row.updated_at,
        )

    def get(self, diagram_id: str) -> Optional[AiRoomMemory]:
        row = self._row(diagram_id)
        return self._to_memory(row) if row else None

    def _get_or_create(self, diagram_id: str) -> AiRoomMemoryModel:
        row = self._row(diagram_id)
        if row is None:
            row = AiRoomMemoryModel(diagram_id=diagram_id)
            self.db.add(row)
        return row

    def save_topic(self, diagram_id: str, topic: str, prompt: Optional[str] = None) -> AiRoomMemory:
        row = self._get_or_create(diagram_id)
        row.last_topic = topic
        if prompt is not None:
            row.last_prompt = prompt
        if not row.pending_commands:
            row.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        return self._to_memory(row)

    def save_pending(
        self,
        diagram_id: str,
        commands: List[Dict[str, Any]],
        base_version: Optional[int],
        requested_by: Optional[str],
    ) -> AiRoomMemory:
        row = self._get_or_create(diagram_id)
        row.pending_commands = commands
        row.pending_base_version = base_version
        row.pending_requested_by = requested_by
        row.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        return self._to_memory(row)

    def clear_pending(self, diagram_id: str) -> None:
        row = self._row(diagram_id)
        if row is None:
            return
        row.pending_commands = None
        row.pending_base_version = None
        row.pending_requested_by = None
        row.updated_at = datetime.now(timezone.utc)
        self.db.commit()
