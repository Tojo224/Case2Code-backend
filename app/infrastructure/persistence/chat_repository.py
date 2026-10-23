from typing import List
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
            executed_commands=message.executed_commands,
            created_at=message.created_at,
        )
        self.db.add(record)
        self.db.commit()
        return message

    def list_messages(self, diagram_id: str, limit: int = 100) -> List[ChatMessage]:
        records = (
            self.db.query(DiagramChatMessageModel)
            .filter(DiagramChatMessageModel.diagram_id == diagram_id)
            .order_by(DiagramChatMessageModel.created_at.asc())
            .limit(limit)
            .all()
        )
        return [
            ChatMessage(
                id=r.id,
                diagram_id=r.diagram_id,
                sender_id=r.sender_id,
                sender_name=r.sender_name,
                sender_avatar_color=r.sender_avatar_color,
                sender_type=SenderType(r.sender_type) if r.sender_type in ("USER", "AI") else SenderType.USER,
                content=r.content,
                addressed_to=r.addressed_to,
                executed_commands=r.executed_commands,
                created_at=r.created_at,
            )
            for r in records
        ]

    def clear_messages(self, diagram_id: str) -> None:
        self.db.query(DiagramChatMessageModel).filter(
            DiagramChatMessageModel.diagram_id == diagram_id
        ).delete()
        self.db.commit()
