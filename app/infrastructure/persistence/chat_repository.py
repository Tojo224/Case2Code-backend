from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.domain.models.chat import ChatMessage, SenderType
from app.infrastructure.persistence.models import DiagramChatMessageModel


class SqlAlchemyChatRepository:
    def __init__(self, db: Session):
        self.db = db

    def add_message(self, message: ChatMessage) -> ChatMessage:
        record = DiagramChatMessageModel(
            id=message.id,
            diagram_id=message.diagram_id,
            sender_id=message.sender_id,
            sender_name=message.sender_name,
            sender_avatar_color=message.sender_avatar_color,
            sender_type=message.sender_type.value if hasattr(message.sender_type, "value") else str(message.sender_type),
            content=message.content,
            addressed_to=message.addressed_to,
            ai_engine=message.ai_engine,
            executed_commands=message.executed_commands,
            suggested_commands=message.suggested_commands,
            suggested_operations=message.suggested_operations,
            resolutions=message.resolutions or None,
            created_at=message.created_at,
        )
        self.db.add(record)
        self.db.commit()
        return message

    def list_messages(self, diagram_id: str, limit: int = 100) -> List[ChatMessage]:
        records = (
            self.db.query(DiagramChatMessageModel)
            .filter(DiagramChatMessageModel.diagram_id == diagram_id)
            .order_by(DiagramChatMessageModel.created_at.desc(), DiagramChatMessageModel.id.desc())
            .limit(limit)
            .all()
        )
        # Newest N were fetched; hand them back oldest-first.
        records.reverse()
        return [self._to_message(r) for r in records]

    @staticmethod
    def _to_message(r: DiagramChatMessageModel) -> ChatMessage:
        return ChatMessage(
            id=r.id,
            diagram_id=r.diagram_id,
            sender_id=r.sender_id,
            sender_name=r.sender_name,
            sender_avatar_color=r.sender_avatar_color,
            sender_type=SenderType(r.sender_type) if r.sender_type in ("USER", "AI") else SenderType.USER,
            content=r.content,
            addressed_to=r.addressed_to,
            ai_engine=r.ai_engine,
            executed_commands=r.executed_commands,
            suggested_commands=r.suggested_commands,
            suggested_operations=r.suggested_operations,
            resolutions=dict(r.resolutions or {}),
            created_at=r.created_at,
        )

    def get_message(self, message_id: str) -> Optional[ChatMessage]:
        """Fresh read: never serves a stale identity-map row."""
        self.db.expire_all()
        record = self.db.get(DiagramChatMessageModel, message_id)
        return self._to_message(record) if record else None

    def update_resolutions(
        self,
        message_id: str,
        key: str,
        resolution: Dict[str, Any],
        *,
        only_if_absent: bool = True,
    ) -> Optional[ChatMessage]:
        """Atomically records one proposal outcome.

        Re-reads the row (locked with FOR UPDATE where the dialect supports it) and
        writes only if `key` is still unresolved. Returns the updated message, or None
        when the key was already resolved (or the message does not exist). The JSON dict
        is replaced, not mutated, so the change is always detected.
        """
        record = (
            self.db.query(DiagramChatMessageModel)
            .filter(DiagramChatMessageModel.id == message_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if record is None:
            return None
        current = dict(record.resolutions or {})
        if only_if_absent and key in current:
            self.db.commit()  # nothing to write: just release the row lock
            return None
        current[key] = resolution
        record.resolutions = current
        self.db.commit()
        return self._to_message(record)

    def clear_messages(self, diagram_id: str) -> None:
        self.db.query(DiagramChatMessageModel).filter(
            DiagramChatMessageModel.diagram_id == diagram_id
        ).delete()
        self.db.commit()
