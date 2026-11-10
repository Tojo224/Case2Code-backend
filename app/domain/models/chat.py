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
    suggested_commands: Optional[List[Dict[str, Any]]] = None
    # Non-UML operations the AI proposes and a human must approve, e.g.
    # {"op": "RESTORE_VERSION", "version_number": 5, "reason": "..."} or
    # {"op": "CREATE_CHECKPOINT", "label": "...", "reason": "..."}
    suggested_operations: Optional[List[Dict[str, Any]]] = None
    # Outcome of each proposal, decided server-side. Keys: "hologram" and "op:<index>".
    # Value: {"status": "APPLIED"|"DISCARDED", "by_id", "by_name", "at" (iso), and optionally
    # "detail", "applied_count", "skipped_count"}.
    resolutions: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatHistoryResponse(BaseModel):
    diagram_id: str
    messages: List[ChatMessage]
