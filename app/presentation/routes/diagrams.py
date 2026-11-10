import json
import logging
import re
import uuid
from typing import List, Optional
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.orm import Session

from app.application.command_bus import command_bus
from app.application.replay import replay_commands
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.commands import UmlCommand
from app.domain.models.chat import ChatHistoryResponse, ChatMessage, SenderType
from app.domain.models.user import CollaboratorRole, ProjectCollaborator, User
from app.domain.services.uml_validator import UmlValidationError
from app.infrastructure.ai import ai_uml_interpreter
from app.application.services.version_service import DiagramVersionService
from app.infrastructure.ai.ai_policy import (
    AI_PROVIDER_MODES,
    can_user_edit_via_ai,
    check_llm_base_url,
    is_addressed_to_ai,
)
from app.infrastructure.ai.ai_room_runner import (
    apply_planned_commands,
    schedule_ai_turn,
)
from app.infrastructure.ai.senior_colleague import senior_colleague_orchestrator
from app.infrastructure.collaboration.blocking import run_blocking
from app.infrastructure.collaboration.room_locks import get_room_ai_lock, get_room_lock
from app.infrastructure.collaboration.connection_manager import connection_manager
from app.infrastructure.interchange.xmi_exporter import XmiExporter
from app.infrastructure.interchange.xmi_importer import XmiImporter
from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
from app.presentation.routes.auth import get_current_user, get_current_user_optional, get_user_repo
from app.presentation.schemas.auth_schemas import AddCollaboratorRequest
from app.presentation.schemas.diagram_schemas import (
    AssistantPromptRequest,
    AiConfigUpdateRequest,
    AssistantPromptResponse,
    BatchCommandsRequest,
    BatchCommandsResponse,
    CommandExecutionResponse,
    CreateDiagramRequest,
    ExecuteCommandRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagrams", tags=["Diagrams"])


def get_repository(db: Session = Depends(get_db)) -> SqlAlchemyDiagramRepository:
    return SqlAlchemyDiagramRepository(db)


def get_chat_repo(db: Session = Depends(get_db)) -> SqlAlchemyChatRepository:
    return SqlAlchemyChatRepository(db)


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


@router.post("/import/xmi", response_model=CanonicalUmlDocument, status_code=status.HTTP_201_CREATED)
async def import_new_diagram_xmi(
    file: UploadFile = File(...),
    name: Optional[str] = Query(None),
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    diagram_id = f"diag-{uuid.uuid4().hex[:8]}"
    owner_id = current_user.id if current_user else None
    fallback_name = file.filename.rsplit(".", 1)[0] if file.filename else "Imported Model"
    diag_name = name.strip() if name and name.strip() else fallback_name

    empty_doc = CanonicalUmlDocument(
        id=diagram_id,
        name=diag_name,
        description="Imported from Enterprise Architect XMI 2.1",
        version=1,
        owner_id=owner_id,
        classes=[],
        relationships=[],
    )

    content_bytes = await file.read()
    try:
        xml_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        xml_str = content_bytes.decode("latin-1")

    try:
        updated_document = XmiImporter.import_xmi(xml_str, empty_doc)
    except Exception as e:
        logger.error(f"Error parsing XMI file: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error parsing XMI file: {str(e)}",
        )

    repo.save(updated_document)
    if current_user:
        user_repo.add_collaborator(diagram_id, current_user.id, CollaboratorRole.OWNER)
    return updated_document


@router.get("", response_model=List[CanonicalUmlDocument])
def list_diagrams(
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    if current_user:
        return repo.list_for_user(current_user.id)
    return repo.list_public()


@router.get("/ai/config")
async def get_ai_provider_config():
    """Returns current Local LLM / Cloud AI status and runtime settings."""
    return await senior_colleague_orchestrator.get_status()


@router.post("/ai/config")
async def update_ai_provider_config(
    payload: AiConfigUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    """Updates runtime Local LLM / Cloud AI provider settings (authenticated users only).

    The LLM URL is process-wide, so it is restricted to allowlisted hosts to keep
    room prompts from being redirected to an arbitrary server."""
    mode = payload.provider_mode.strip().lower() if payload.provider_mode else None
    if mode and mode not in AI_PROVIDER_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Modo de proveedor inválido. Usá: local, hybrid o gemini.",
        )
    if payload.local_base_url and payload.local_base_url.strip():
        error = check_llm_base_url(
            payload.local_base_url,
            settings.AI_ALLOWED_LLM_HOSTS,
            settings.LOCAL_LLM_BASE_URL,
        )
        if error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    senior_colleague_orchestrator.update_runtime_config(
        provider_mode=mode,
        local_base_url=payload.local_base_url,
        local_model=payload.local_model,
    )
    return await senior_colleague_orchestrator.get_status()


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
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )

    # Only OWNER can delete an owned diagram
    if document.owner_id:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Inicia sesión para eliminar este proyecto.",
            )
        if document.owner_id != current_user.id:
            role = user_repo.get_user_role(diagram_id, current_user.id)
            if role != CollaboratorRole.OWNER:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Solo el propietario del diagrama puede eliminarlo.",
                )

    deleted = repo.delete(diagram_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )
    return None


def _auto_snapshot_before_edit(
    repo: SqlAlchemyDiagramRepository,
    document: CanonicalUmlDocument,
    user: Optional[User],
) -> None:
    """Throttled safety snapshot of the state BEFORE a human edit.

    Never blocks the edit: a failure here is logged and ignored.
    """
    try:
        DiagramVersionService(repo.db).maybe_auto_snapshot(
            document,
            author_id=user.id if user else "guest",
            author_name=user.name if user else "Invitado",
        )
    except Exception as e:
        logger.warning("Auto snapshot skipped for diagram %s: %s", document.id, e)


_NOT_FOUND = "Diagram '{}' not found."


def _check_edit_access(
    document: CanonicalUmlDocument,
    diagram_id: str,
    user_repo: SqlAlchemyUserRepository,
    current_user: Optional[User],
    *,
    unauthenticated: str,
    forbidden: str,
    viewer: str,
) -> None:
    """Edit rules shared by every mutating endpoint of a private diagram."""
    if not document.owner_id:
        return
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=unauthenticated)
    role = user_repo.get_user_role(diagram_id, current_user.id)
    if not role and document.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=forbidden)
    if role == CollaboratorRole.VIEWER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=viewer)


def _load_for_edit(
    repo: SqlAlchemyDiagramRepository,
    user_repo: SqlAlchemyUserRepository,
    diagram_id: str,
    current_user: Optional[User],
    **messages: str,
) -> CanonicalUmlDocument:
    """Blocking section: load the diagram and check the caller may edit it."""
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND.format(diagram_id),
        )
    _check_edit_access(document, diagram_id, user_repo, current_user, **messages)
    return document


_COMMAND_MESSAGES = dict(
    unauthenticated="Authentication required to modify this diagram.",
    forbidden="You do not have permission to modify this diagram.",
    viewer="Viewers cannot make changes to this diagram.",
)


def _reload_fresh(repo: SqlAlchemyDiagramRepository, diagram_id: str) -> CanonicalUmlDocument:
    repo.db.expire_all()  # humans/AI saved in the meantime: never trust the identity map
    fresh = repo.get_by_id(diagram_id)
    if not fresh:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND.format(diagram_id),
        )
    return fresh


def _run_single_command(
    repo: SqlAlchemyDiagramRepository,
    diagram_id: str,
    command: UmlCommand,
    user: Optional[User],
) -> CanonicalUmlDocument:
    """Blocking section (call under the room lock): reload, snapshot, dispatch, save."""
    fresh = _reload_fresh(repo, diagram_id)
    _auto_snapshot_before_edit(repo, fresh, user)
    updated_doc = command_bus.dispatch(fresh, command)
    repo.save(updated_doc)
    return updated_doc


def _run_batch(
    repo: SqlAlchemyDiagramRepository,
    diagram_id: str,
    commands: List[UmlCommand],
    user: Optional[User],
):
    """Blocking section (call under the room lock): reload, replay (skipping failures),
    snapshot the pre-state and save ONCE. Nothing is saved when nothing applied."""
    fresh = _reload_fresh(repo, diagram_id)
    outcome = replay_commands(fresh, commands)
    if outcome.applied:
        _auto_snapshot_before_edit(repo, fresh, user)
        repo.save(outcome.document)
    return outcome


@router.post("/{diagram_id}/commands", response_model=CommandExecutionResponse)
async def execute_command(
    diagram_id: str,
    payload: ExecuteCommandRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    await run_blocking(_load_for_edit, repo, user_repo, diagram_id, current_user, **_COMMAND_MESSAGES)

    try:
        # Reload-apply-save under the room lock so a concurrent AI/restore edit is never overwritten.
        # The whole DB section is one blocking call; the broadcast stays on the loop.
        async with get_room_lock(diagram_id):
            updated_doc = await run_blocking(
                _run_single_command, repo, diagram_id, payload.command, current_user
            )

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
    except HTTPException:
        raise
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


@router.post("/{diagram_id}/commands/batch", response_model=BatchCommandsResponse)
async def execute_command_batch(
    diagram_id: str,
    payload: BatchCommandsRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Applies many commands: failing ones are skipped and reported, the document is
    saved once and broadcast once."""
    if not payload.commands:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The commands list must not be empty.",
        )
    await run_blocking(_load_for_edit, repo, user_repo, diagram_id, current_user, **_COMMAND_MESSAGES)

    async with get_room_lock(diagram_id):
        outcome = await run_blocking(_run_batch, repo, diagram_id, payload.commands, current_user)
        if outcome.applied:
            await connection_manager.broadcast_document_update(
                diagram_id=diagram_id,
                document_dict=outcome.document.model_dump(mode="json"),
            )

    return BatchCommandsResponse(
        document=outcome.document,
        applied=outcome.applied,
        skipped=[
            {"command": cmd.model_dump(mode="json"), "reason": reason}
            for cmd, reason in outcome.skipped
        ],
    )


@router.post("/{diagram_id}/assistant", response_model=AssistantPromptResponse)
async def process_assistant_prompt(
    diagram_id: str,
    payload: AssistantPromptRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = await run_blocking(
        _load_for_edit,
        repo,
        user_repo,
        diagram_id,
        current_user,
        unauthenticated="Debes iniciar sesión para usar el asistente en este proyecto.",
        forbidden="No tienes permiso para modificar este proyecto con el asistente.",
        viewer="Los observadores (viewers) no pueden aplicar cambios con el asistente.",
    )

    image_data = None
    if payload.image_base64:
        image_data = {
            "data": payload.image_base64,
            "mime_type": payload.image_mime_type or "image/png",
        }

    prompt_text = payload.prompt or "Replicar este diseño de base de datos en el diagrama."

    # Serialize AI turns of the room; infer WITHOUT the document lock on a private copy,
    # then reload fresh and replay the executed commands under the room lock.
    async with get_room_ai_lock(diagram_id):
        planned_doc, planned_commands, reply = await ai_uml_interpreter.interpret_and_execute(
            prompt_text,
            document.model_copy(deep=True),
            image_data=image_data,
            api_key_override=payload.gemini_api_key,
        )
        if not planned_commands:
            return AssistantPromptResponse(
                success=False,
                reply=reply,
                executed_commands=[],
                document=planned_doc,
                error=None,
            )

        reply, executed_commands = await apply_planned_commands(
            session=repo.db,
            diag_repo=repo,
            version_service=DiagramVersionService(repo.db),
            hub=connection_manager,
            diagram_id=diagram_id,
            prompt=prompt_text,
            planned=planned_commands,
            reply=reply,
            speaker_name=current_user.name if current_user else "equipo",
        )
        def reload_final():
            repo.db.expire_all()
            return repo.get_by_id(diagram_id)

        final_doc = await run_blocking(reload_final) or planned_doc

    return AssistantPromptResponse(
        success=bool(executed_commands),
        reply=reply,
        executed_commands=executed_commands,
        document=final_doc,
        error=None,
    )


# --- Enterprise Architect XMI Interchange Endpoints ---

@router.get("/{diagram_id}/export/xmi")
def export_diagram_xmi(
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

    xmi_content = XmiExporter.export(document)
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", document.name.strip()) or "model"
    return Response(
        content=xmi_content,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.xmi"',
        },
    )


@router.post("/{diagram_id}/import/xmi", response_model=CanonicalUmlDocument)
async def import_diagram_xmi(
    diagram_id: str,
    file: UploadFile = File(...),
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    await run_blocking(
        _load_for_edit,
        repo,
        user_repo,
        diagram_id,
        current_user,
        unauthenticated="Inicia sesión para modificar este proyecto.",
        forbidden="No tienes permiso para modificar este proyecto.",
        viewer="Los observadores (viewers) no pueden importar diagramas.",
    )

    content_bytes = await file.read()
    try:
        xml_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        xml_str = content_bytes.decode("latin-1")

    def import_on_fresh() -> CanonicalUmlDocument:
        # Read-modify-write on a FRESH document (the one loaded for the permission check may
        # be stale by now): parse, snapshot the pre-state so the import is undoable, save.
        fresh = _reload_fresh(repo, diagram_id)
        try:
            updated = XmiImporter.import_xmi(xml_str, fresh.model_copy(deep=True))
        except Exception as e:
            logger.error(f"Error parsing XMI file: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Error parsing XMI file: {str(e)}",
            )
        _auto_snapshot_before_edit(repo, fresh, current_user)
        updated.version += 1
        repo.save(updated)
        return updated

    async with get_room_lock(diagram_id):
        updated_document = await run_blocking(import_on_fresh)

        # Broadcast update to all active web sockets
        await connection_manager.broadcast_document_update(
            diagram_id=diagram_id,
            document_dict=updated_document.model_dump(mode="json"),
        )

    return updated_document


# --- Collaborator Endpoints ---

@router.get("/{diagram_id}/collaborators", response_model=List[ProjectCollaborator])
def get_collaborators(
    diagram_id: str,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
):
    doc = repo.get_by_id(diagram_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Diagram '{diagram_id}' not found.")
    return user_repo.list_collaborators(diagram_id)


@router.post("/{diagram_id}/collaborators", response_model=ProjectCollaborator)
def add_collaborator(
    diagram_id: str,
    payload: AddCollaboratorRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: User = Depends(get_current_user),
):
    doc = repo.get_by_id(diagram_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Diagram '{diagram_id}' not found.")

    if doc.owner_id and doc.owner_id != current_user.id:
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if role != CollaboratorRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el propietario del proyecto puede invitar colaboradores.",
            )

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
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: User = Depends(get_current_user),
):
    doc = repo.get_by_id(diagram_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Diagram not found.")

    if doc.owner_id and doc.owner_id != current_user.id and current_user.id != user_id:
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if role != CollaboratorRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el propietario puede remover colaboradores.",
            )

    removed = user_repo.remove_collaborator(diagram_id, user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Collaborator not found.")
    return None


# --- Team Chat Endpoints ---

@router.get("/{diagram_id}/chat", response_model=ChatHistoryResponse)
def get_diagram_chat_history(
    diagram_id: str,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    chat_repo: SqlAlchemyChatRepository = Depends(get_chat_repo),
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
                detail="Inicia sesión para ver el chat de este proyecto.",
            )
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if not role and document.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para acceder a este proyecto.",
            )

    messages = chat_repo.list_messages(diagram_id)
    return ChatHistoryResponse(diagram_id=diagram_id, messages=messages)


# --- WebSocket Multiplayer Hub ---

@router.websocket("/{diagram_id}/ws")
async def diagram_websocket(
    websocket: WebSocket,
    diagram_id: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    diag_repo = SqlAlchemyDiagramRepository(db)
    user_repo = SqlAlchemyUserRepository(db)
    chat_repo = SqlAlchemyChatRepository(db)

    document = diag_repo.get_by_id(diagram_id)
    if not document:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user: Optional[User] = None
    if token:
        payload = decode_access_token(token)
        if payload and "sub" in payload:
            db_user = user_repo.get_by_id(payload["sub"])
            if db_user:
                user = User(
                    id=db_user.id,
                    email=db_user.email,
                    name=db_user.name,
                    avatar_color=db_user.avatar_color,
                    created_at=db_user.created_at,
                )

    # Restrict WebSocket access on private diagrams
    if document.owner_id:
        if not user:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        role = user_repo.get_user_role(diagram_id, user.id)
        if not role and document.owner_id != user.id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

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
                elif msg_type == "SEND_CHAT_MESSAGE":
                    content = str(msg.get("content", "")).strip()
                    ai_mode = str(msg.get("ai_mode", "PAIR_PROGRAMMER"))
                    if content:
                        # Access can change while the socket is open (collaborator removed,
                        # role downgraded): decide from fresh data on EVERY chat message, so a
                        # removed member can neither post nor wake the AI.
                        def recheck_access():
                            db.expire_all()
                            fresh_doc = diag_repo.get_by_id(diagram_id)
                            if fresh_doc is None:
                                return None
                            sender_role = user_repo.get_user_role(diagram_id, user.id)
                            return can_user_edit_via_ai(fresh_doc.owner_id, user.id, sender_role)

                        access = await run_blocking(recheck_access)
                        if access is None:
                            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                            break
                        in_room, can_edit = access
                        if not in_room:
                            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                            break

                        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                        user_msg = ChatMessage(
                            id=msg_id,
                            diagram_id=diagram_id,
                            sender_id=user.id,
                            sender_name=user.name,
                            sender_avatar_color=user.avatar_color,
                            sender_type=SenderType.USER,
                            content=content,
                            addressed_to=msg.get("addressed_to"),
                        )
                        await run_blocking(chat_repo.add_message, user_msg)
                        await connection_manager.broadcast_chat_message(
                            diagram_id,
                            user_msg.model_dump(mode="json"),
                        )

                        # The AI only answers to an explicit mention (@ai, @senior, @bot, /ai)
                        # or when the client forces it (assistant panel). Human-to-human chat
                        # never wakes it.
                        if is_addressed_to_ai(content, bool(msg.get("force_ai", False))):
                            # Runs in the background: this loop keeps serving cursors/selection/chat.
                            schedule_ai_turn(
                                diagram_id=diagram_id,
                                content=content,
                                speaker=user,
                                can_edit=can_edit,
                                ai_mode=ai_mode,
                            )
                elif msg_type == "SET_TYPING":
                    is_typing = bool(msg.get("is_typing", False))
                    await connection_manager.broadcast_typing(
                        diagram_id=diagram_id,
                        user_id=user.id,
                        name=user.name,
                        is_typing=is_typing,
                        exclude_ws=websocket,
                    )
                elif msg_type == "PING":
                    await websocket.send_text(json.dumps({"type": "PONG"}))
            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.error(f"Error handling websocket message: {e}", exc_info=True)
    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket, diagram_id)
    except Exception as e:
        logger.warning(f"WebSocket connection exception: {e}")
        await connection_manager.disconnect(websocket, diagram_id)
