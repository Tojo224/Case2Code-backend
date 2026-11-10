"""REST API for diagram version history (list, checkpoint, detail, diff, restore)."""

from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.application.services.version_service import DiagramVersionService
from app.core.database import get_db
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.user import CollaboratorRole, User
from app.domain.models.versioning import (
    DiagramVersion,
    DiagramVersionDetail,
    VersionAuthorType,
    VersionConflictError,
    VersionDiff,
    VersionKind,
    VersionNotFoundError,
)
from app.infrastructure.collaboration.connection_manager import connection_manager
from app.infrastructure.collaboration.room_locks import get_room_lock
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
from app.presentation.routes.auth import get_current_user_optional, get_user_repo
from app.presentation.schemas.version_schemas import CreateVersionRequest, RestoreVersionRequest

router = APIRouter(prefix="/diagrams/{diagram_id}/versions", tags=["Versions"])

GUEST_AUTHOR_ID = "guest"
GUEST_AUTHOR_NAME = "Invitado"


def get_version_service(db: Session = Depends(get_db)) -> DiagramVersionService:
    return DiagramVersionService(db)


def _authorize(
    diagram_id: str,
    db: Session,
    user_repo: SqlAlchemyUserRepository,
    current_user: Optional[User],
    *,
    write: bool,
) -> CanonicalUmlDocument:
    document = SqlAlchemyDiagramRepository(db).get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )
    if document.owner_id:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Iniciá sesión para acceder al historial de este proyecto.",
            )
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if not role and document.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tenés permiso para acceder a este proyecto.",
            )
        if write and role == CollaboratorRole.VIEWER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Los visores no pueden crear ni restaurar versiones.",
            )
    return document


def _author(current_user: Optional[User], guest_name: Optional[str] = None) -> Tuple[str, str]:
    if current_user:
        return current_user.id, current_user.name
    name = (guest_name or "").strip() or GUEST_AUTHOR_NAME
    return GUEST_AUTHOR_ID, name


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("", response_model=List[DiagramVersion])
def list_versions(
    diagram_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    service: DiagramVersionService = Depends(get_version_service),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _authorize(diagram_id, db, user_repo, current_user, write=False)
    return service.list_versions(diagram_id, limit=limit)


@router.post("", response_model=DiagramVersion, status_code=status.HTTP_201_CREATED)
def create_version(
    diagram_id: str,
    payload: CreateVersionRequest,
    db: Session = Depends(get_db),
    service: DiagramVersionService = Depends(get_version_service),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = _authorize(diagram_id, db, user_repo, current_user, write=True)
    author_id, author_name = _author(current_user, payload.author_name)
    return service.create_snapshot(
        document,
        author_id=author_id,
        author_name=author_name,
        author_type=VersionAuthorType.USER,
        kind=VersionKind.CHECKPOINT,
        label=(payload.label or "").strip() or None,
        message=(payload.message or "").strip() or None,
    )


@router.get("/{number}", response_model=DiagramVersionDetail)
def get_version(
    diagram_id: str,
    number: int,
    db: Session = Depends(get_db),
    service: DiagramVersionService = Depends(get_version_service),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _authorize(diagram_id, db, user_repo, current_user, write=False)
    try:
        return service.get_version(diagram_id, number)
    except VersionNotFoundError as exc:
        raise _not_found(exc)


@router.get("/{number}/diff", response_model=VersionDiff)
def diff_version(
    diagram_id: str,
    number: int,
    against: str = Query(default="current"),
    db: Session = Depends(get_db),
    service: DiagramVersionService = Depends(get_version_service),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _authorize(diagram_id, db, user_repo, current_user, write=False)
    to_number: Optional[int] = None
    if against != "current":
        try:
            to_number = int(against)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El parámetro against debe ser current o un número de versión.",
            )
    try:
        return service.diff(diagram_id, number, to_number)
    except VersionNotFoundError as exc:
        raise _not_found(exc)


@router.post("/{number}/restore", response_model=CanonicalUmlDocument)
async def restore_version(
    diagram_id: str,
    number: int,
    payload: Optional[RestoreVersionRequest] = None,
    db: Session = Depends(get_db),
    service: DiagramVersionService = Depends(get_version_service),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    _authorize(diagram_id, db, user_repo, current_user, write=True)
    author_id, author_name = _author(current_user)
    expected = payload.expected_document_version if payload else None
    async with get_room_lock(diagram_id):
        try:
            restored = service.restore(
                diagram_id,
                number,
                author_id=author_id,
                author_name=author_name,
                author_type=VersionAuthorType.USER,
                expected_document_version=expected,
            )
        except VersionNotFoundError as exc:
            raise _not_found(exc)
        except VersionConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        await connection_manager.broadcast_document_update(
            diagram_id=diagram_id,
            document_dict=restored.model_dump(mode="json"),
        )
    return restored
