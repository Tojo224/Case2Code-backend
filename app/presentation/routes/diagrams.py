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
from app.core.database import get_db
from app.core.security import decode_access_token
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatHistoryResponse, ChatMessage, SenderType
from app.domain.models.user import CollaboratorRole, ProjectCollaborator, User
from app.domain.services.uml_validator import UmlValidationError
from app.infrastructure.ai import ai_uml_interpreter
from app.infrastructure.ai.senior_colleague import senior_colleague_orchestrator
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
    AssistantPromptResponse,
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
                detail="Debes iniciar sesión para usar el asistente en este proyecto.",
            )
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if not role and document.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para modificar este proyecto con el asistente.",
            )
        if role == CollaboratorRole.VIEWER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Los observadores (viewers) no pueden aplicar cambios con el asistente.",
            )

    image_data = None
    if payload.image_base64:
        image_data = {
            "data": payload.image_base64,
            "mime_type": payload.image_mime_type or "image/png",
        }

    prompt_text = payload.prompt or "Replicar este diseño de base de datos en el diagrama."
    updated_doc, executed_commands, reply = await ai_uml_interpreter.interpret_and_execute(
        prompt_text, document, image_data=image_data, api_key_override=payload.gemini_api_key
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
                detail="Inicia sesión para modificar este proyecto.",
            )
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if not role and document.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para modificar este proyecto.",
            )
        if role == CollaboratorRole.VIEWER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Los observadores (viewers) no pueden importar diagramas.",
            )

    content_bytes = await file.read()
    try:
        xml_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        xml_str = content_bytes.decode("latin-1")

    try:
        updated_document = XmiImporter.import_xmi(xml_str, document)
    except Exception as e:
        logger.error(f"Error parsing XMI file: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error parsing XMI file: {str(e)}",
        )

    updated_document.version += 1
    repo.save(updated_document)

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
                        chat_repo.add_message(user_msg)
                        await connection_manager.broadcast_chat_message(
                            diagram_id,
                            user_msg.model_dump(mode="json"),
                        )

                        # Trigger AI Senior Developer if addressed to AI or in active room mode
                        content_lower = content.lower()
                        should_trigger_ai = (
                            "@ai" in content_lower
                            or "@senior" in content_lower
                            or "@bot" in content_lower
                            or "senior" in content_lower
                            or "asistente" in content_lower
                            or msg.get("force_ai", False)
                            or bool(re.search(r"^(¿|\?|crea|agrega|elimina|relaciona|cómo|como|que|qué|ayuda)", content_lower))
                        )

                        if should_trigger_ai:
                            # Notify room that Senior AI is typing
                            await connection_manager.broadcast_typing(
                                diagram_id=diagram_id,
                                user_id="ai-senior",
                                name="Senior AI",
                                is_typing=True,
                            )

                            try:
                                active_presences = connection_manager.get_active_presences(diagram_id)
                                current_doc = diag_repo.get_by_id(diagram_id) or document
                                recent_history = chat_repo.list_messages(diagram_id, limit=12)

                                reply_text, addressed_to, executed_commands, updated_doc = (
                                    await senior_colleague_orchestrator.process_colleague_interaction(
                                        prompt=content,
                                        speaker=user,
                                        active_users=active_presences,
                                        document=current_doc,
                                        chat_history=recent_history,
                                        ai_mode=ai_mode,
                                    )
                                )

                                if executed_commands:
                                    updated_doc.version += 1
                                    diag_repo.save(updated_doc)
                                    document = updated_doc
                                    await connection_manager.broadcast_document_update(
                                        diagram_id=diagram_id,
                                        document_dict=updated_doc.model_dump(mode="json"),
                                    )

                                ai_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                                ai_msg = ChatMessage(
                                    id=ai_msg_id,
                                    diagram_id=diagram_id,
                                    sender_id="ai-senior",
                                    sender_name="Senior AI",
                                    sender_avatar_color="#8B5CF6",
                                    sender_type=SenderType.AI,
                                    content=reply_text,
                                    addressed_to=addressed_to or user.name,
                                    executed_commands=[c.model_dump(mode="json") for c in executed_commands] if executed_commands else None,
                                )
                                chat_repo.add_message(ai_msg)

                                await connection_manager.broadcast_chat_message(
                                    diagram_id,
                                    ai_msg.model_dump(mode="json"),
                                )
                            except Exception as e:
                                logger.error(f"Error executing AI senior colleague: {e}", exc_info=True)
                            finally:
                                await connection_manager.broadcast_typing(
                                    diagram_id=diagram_id,
                                    user_id="ai-senior",
                                    name="Senior AI",
                                    is_typing=False,
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
