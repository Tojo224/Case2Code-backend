from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class CollaboratorRole(str, Enum):
    OWNER = "OWNER"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"


class User(BaseModel):
    id: str
    email: str
    name: str
    avatar_color: str = "#3B82F6"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProjectCollaborator(BaseModel):
    id: str
    diagram_id: str
    user_id: str
    role: CollaboratorRole = CollaboratorRole.EDITOR
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    avatar_color: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserPresence(BaseModel):
    user_id: str
    name: str
    email: str
    avatar_color: str
    cursor_x: Optional[float] = None
    cursor_y: Optional[float] = None
    selected_class_id: Optional[str] = None
    last_active: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
