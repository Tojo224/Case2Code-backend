from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SenderType(str, Enum):
    USER = "USER"
    AI = "AI"


class ChatMessage(BaseModel):
    id: str
    diagram_id: str
    sender_id: str
    sender_name: str
    sender_avatar_color: str = "#3B82F6"
    sender_type: SenderType = SenderType.USER
    content: str
    addressed_to: Optional[str] = None
    ai_engine: Optional[str] = None
    executed_commands: Optional[List[Dict[str, Any]]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatHistoryResponse(BaseModel):
    diagram_id: str
    messages: List[ChatMessage]
