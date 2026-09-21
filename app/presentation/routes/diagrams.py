import json
import logging
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.application.command_bus import command_bus
from app.core.database import get_db
from app.core.security import decode_access_token
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.user import CollaboratorRole, ProjectCollaborator, User
from app.domain.services.uml_validator import UmlValidationError
from app.infrastructure.ai import ai_uml_interpreter
from app.infrastructure.collaboration.connection_manager import connection_manager
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
from app.presentation.routes.auth import get_current_user_optional, get_user_repo
from app.presentation.schemas.auth_schemas import AddCollaboratorRequest
from app.presentation.schemas.diagram_schemas import (
    AssistantPromptRequest,
    AssistantPromptResponse,
    CommandExecutionResponse,
    CreateDiagramRequest,
    ExecuteCommandRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagrams", tags=["Diagrams"])


def get_repository(db: Session = Depends(get_db)) -> SqlAlchemyDiagramRepository:
    return SqlAlchemyDiagramRepository(db)


@router.post("", response_model=CanonicalUmlDocument, status_code=status.HTTP_201_CREATED)
def create_diagram(
    payload: CreateDiagramRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    diagram_id = f"diag-{uuid.uuid4().hex[:8]}"
    owner_id = current_user.id if current_user else None
    document = CanonicalUmlDocument(
        id=diagram_id,
        name=payload.name,
        description=payload.description,
        version=1,
        owner_id=owner_id,
        classes=[],
        relationships=[],
    )
    repo.save(document)
    if current_user:
        user_repo.add_collaborator(diagram_id, current_user.id, CollaboratorRole.OWNER)
    return document


@router.get("", response_model=List[CanonicalUmlDocument])
def list_diagrams(
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    if current_user:
        return repo.list_for_user(current_user.id)
    return repo.list_public()


@router.get("/{diagram_id}", response_model=CanonicalUmlDocument)
def get_diagram(
    diagram_id: str,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )
    if document.owner_id:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Inicia sesión para acceder a este proyecto.",
            )
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if not role and document.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para acceder a este proyecto.",
            )
    return document


@router.delete("/{diagram_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_diagram(
    diagram_id: str,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
):
    deleted = repo.delete(diagram_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )
    return None


@router.post("/{diagram_id}/commands", response_model=CommandExecutionResponse)
async def execute_command(
    diagram_id: str,
    payload: ExecuteCommandRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )

    if document.owner_id:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to modify this diagram.",
            )
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if not role and document.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify this diagram.",
            )
        if role == CollaboratorRole.VIEWER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Viewers cannot make changes to this diagram.",
            )

    try:
        updated_doc = command_bus.dispatch(document, payload.command)
        repo.save(updated_doc)

        # Broadcast update to connected peers in real time
        await connection_manager.broadcast_document_update(
            diagram_id=diagram_id,
            document_dict=updated_doc.model_dump(mode="json"),
            command_dict=payload.command.model_dump(mode="json"),
        )

        return CommandExecutionResponse(
            success=True,
            version=updated_doc.version,
            document=updated_doc,
            error=None,
        )
    except UmlValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal error processing command: {str(e)}",
        )


@router.post("/{diagram_id}/assistant", response_model=AssistantPromptResponse)
async def process_assistant_prompt(
    diagram_id: str,
    payload: AssistantPromptRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
):
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )

    image_data = None
    if payload.image_base64:
        image_data = {
            "data": payload.image_base64,
            "mime_type": payload.image_mime_type or "image/png",
        }

    prompt_text = payload.prompt or "Replicar este diseño de base de datos en el diagrama."
    updated_doc, executed_commands, reply = await ai_uml_interpreter.interpret_and_execute(
        prompt_text, document, image_data=image_data
    )
    if executed_commands:
        repo.save(updated_doc)
        # Broadcast AI-assisted updates to all collaborators
        await connection_manager.broadcast_document_update(
            diagram_id=diagram_id,
            document_dict=updated_doc.model_dump(mode="json"),
        )

    return AssistantPromptResponse(
        success=bool(executed_commands),
        reply=reply,
        executed_commands=executed_commands,
        document=updated_doc,
        error=None,
    )


# --- Collaborator Endpoints ---

@router.get("/{diagram_id}/collaborators", response_model=List[ProjectCollaborator])
def get_collaborators(
    diagram_id: str,
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
):
    return user_repo.list_collaborators(diagram_id)


@router.post("/{diagram_id}/collaborators", response_model=ProjectCollaborator)
def add_collaborator(
    diagram_id: str,
    payload: AddCollaboratorRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
):
    doc = repo.get_by_id(diagram_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Diagram '{diagram_id}' not found.")

    target_user = user_repo.get_by_email(payload.email)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email '{payload.email}' not found. Ask them to register first or use a demo user.",
        )

    collab = user_repo.add_collaborator(diagram_id, target_user.id, payload.role)
    return collab


@router.delete("/{diagram_id}/collaborators/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_collaborator(
    diagram_id: str,
    user_id: str,
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
):
    removed = user_repo.remove_collaborator(diagram_id, user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Collaborator not found.")
    return None


# --- WebSocket Multiplayer Hub ---

@router.websocket("/{diagram_id}/ws")
async def diagram_websocket(
    websocket: WebSocket,
    diagram_id: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    user: Optional[User] = None
    if token:
        payload = decode_access_token(token)
        if payload and "sub" in payload:
            repo = SqlAlchemyUserRepository(db)
            db_user = repo.get_by_id(payload["sub"])
            if db_user:
                user = User(
                    id=db_user.id,
                    email=db_user.email,
                    name=db_user.name,
                    avatar_color=db_user.avatar_color,
                    created_at=db_user.created_at,
                )

    if not user:
        anon_id = f"guest-{uuid.uuid4().hex[:6]}"
        user = User(
            id=anon_id,
            email=f"{anon_id}@guest.local",
            name=f"Guest-{anon_id[-4:]}",
            avatar_color="#9CA3AF",
        )

    await connection_manager.connect(websocket, diagram_id, user)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type")
                if msg_type == "CURSOR_MOVE":
                    await connection_manager.broadcast_cursor(
                        diagram_id,
                        websocket,
                        msg.get("x", 0.0),
                        msg.get("y", 0.0),
                    )
                elif msg_type == "SELECTION_CHANGE":
                    await connection_manager.broadcast_selection(
                        diagram_id,
                        websocket,
                        msg.get("selected_class_id"),
                    )
                elif msg_type == "PING":
                    await websocket.send_text(json.dumps({"type": "PONG"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket, diagram_id)
    except Exception as e:
        logger.warning(f"WebSocket connection exception: {e}")
        await connection_manager.disconnect(websocket, diagram_id)
