from typing import Optional
from pydantic import BaseModel
from app.domain.models.user import CollaboratorRole, User


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    avatar_color: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: User


class AddCollaboratorRequest(BaseModel):
    email: str
    role: CollaboratorRole = CollaboratorRole.EDITOR
