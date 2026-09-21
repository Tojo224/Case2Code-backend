from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_reset_token,
    decode_access_token,
    hash_password,
    verify_password,
    verify_reset_token,
)
from app.domain.models.user import User
from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
from app.presentation.schemas.auth_schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def get_user_repo(db: Session = Depends(get_db)) -> SqlAlchemyUserRepository:
    return SqlAlchemyUserRepository(db)


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    repo: SqlAlchemyUserRepository = Depends(get_user_repo),
) -> Optional[User]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user_model = repo.get_by_id(user_id)
    if not user_model:
        return None
    return User(
        id=user_model.id,
        email=user_model.email,
        name=user_model.name,
        avatar_color=user_model.avatar_color,
        created_at=user_model.created_at,
    )


def get_current_user(
    authorization: Optional[str] = Header(None),
    repo: SqlAlchemyUserRepository = Depends(get_user_repo),
) -> User:
    user = get_current_user_optional(authorization, repo)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide a valid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    repo: SqlAlchemyUserRepository = Depends(get_user_repo),
):
    existing = repo.get_by_email(payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists.",
        )

    color = payload.avatar_color or "#6366F1"
    user = repo.create_user(
        email=payload.email,
        name=payload.name,
        password=payload.password,
        avatar_color=color,
    )

    token = create_access_token({"sub": user.id, "email": user.email, "name": user.name})
    return AuthResponse(access_token=token, token_type="bearer", user=user)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    repo: SqlAlchemyUserRepository = Depends(get_user_repo),
):
    user_model = repo.get_by_email(payload.email)
    if not user_model or not verify_password(payload.password, user_model.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    user = User(
        id=user_model.id,
        email=user_model.email,
        name=user_model.name,
        avatar_color=user_model.avatar_color,
        created_at=user_model.created_at,
    )
    token = create_access_token({"sub": user.id, "email": user.email, "name": user.name})
    return AuthResponse(access_token=token, token_type="bearer", user=user)


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    repo: SqlAlchemyUserRepository = Depends(get_user_repo),
):
    user = repo.get_by_email(payload.email)
    msg = "Si el correo está registrado, recibirás las instrucciones para restablecer tu contraseña."
    if not user:
        return ForgotPasswordResponse(message=msg, dev_token=None)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=15)
    token = create_reset_token(user.email, expires_delta=timedelta(minutes=15))
    repo.set_reset_token(user.email, token, expires_at)

    logger.info(f"Password reset token generated for {user.email}: {token}")
    return ForgotPasswordResponse(
        message=msg,
        dev_token=token,  # Included for dev/testing ease
    )


@router.post("/reset-password", response_model=ResetPasswordResponse)
def reset_password(
    payload: ResetPasswordRequest,
    repo: SqlAlchemyUserRepository = Depends(get_user_repo),
):
    email = verify_reset_token(payload.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token de restablecimiento inválido o expirado.",
        )

    success = repo.reset_password(payload.token, payload.new_password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El token es inválido, ya fue utilizado o ha expirado.",
        )

    return ResetPasswordResponse(
        message="Contraseña actualizada exitosamente. Ya podés iniciar sesión con tu nueva contraseña.",
        success=True,
    )


@router.get("/me", response_model=User)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
