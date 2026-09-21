from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from app.domain.models.user import CollaboratorRole, User


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="Password must be at least 6 characters")
    name: str = Field(..., min_length=1, description="Full name or nickname")
    avatar_color: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    dev_token: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=6, description="New password must be at least 6 characters")


class ResetPasswordResponse(BaseModel):
    message: str
    success: bool = True


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: User


class AddCollaboratorRequest(BaseModel):
    email: str
    role: CollaboratorRole = CollaboratorRole.EDITOR
