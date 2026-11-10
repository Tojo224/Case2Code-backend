"""Server-side resolution of AI proposals (hologram commands and suggested operations).

The server is the single source of truth: a hologram is applied from the commands
STORED on the chat message (never from client-sent ones), every proposal is resolved
at most once, and the outcome is persisted in ``ChatMessage.resolutions`` so it
survives reloads and is shown to every peer.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from app.application.replay import replay_commands
from app.application.services.version_service import DiagramVersionService
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage
from app.domain.models.commands import UmlCommand
from app.domain.models.user import User
from app.domain.models.versioning import (
    VersionAuthorType,
    VersionConflictError,
    VersionKind,
    VersionNotFoundError,
)
from app.infrastructure.collaboration.blocking import run_blocking
from app.infrastructure.collaboration.connection_manager import connection_manager
from app.infrastructure.collaboration.room_locks import get_room_lock
from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
from app.presentation.routes.auth import get_current_user_optional, get_user_repo
from app.presentation.routes.diagrams import (
    _auto_snapshot_before_edit,
    _load_for_edit,
    _reload_fresh,
    get_chat_repo,
    get_repository,
)
from app.presentation.schemas.diagram_schemas import (
    ResolveProposalRequest,
    ResolveProposalResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagrams", tags=["Proposals"])

PROPOSAL_TTL = timedelta(minutes=30)
GUEST_ID = "guest"
GUEST_NAME = "Invitado"

_command_adapter: TypeAdapter = TypeAdapter(UmlCommand)


class _ResolveFailure(Exception):
    """Carries the HTTP answer out of the blocking section."""

    def __init__(self, status_code: int, detail: str, message: Optional[ChatMessage] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.message = message


@dataclass
class _Resolution:
    message: ChatMessage
    document: Optional[CanonicalUmlDocument] = None
    applied_count: Optional[int] = None
    skipped: Optional[List[Dict[str, Any]]] = None
    recorded: bool = False  # a resolution was persisted (-> CHAT_MESSAGE_UPDATED)
    document_changed: bool = False  # the live document changed (-> DOCUMENT_UPDATED)


def _stamp(user: Optional[User], status_value: str, **extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "status": status_value,
        "by_id": user.id if user else GUEST_ID,
        "by_name": user.name if user else GUEST_NAME,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    data.update({k: v for k, v in extra.items() if v is not None})
    return data


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _record(chat_repo: SqlAlchemyChatRepository, message: ChatMessage, key: str, resolution: Dict[str, Any]) -> ChatMessage:
    updated = chat_repo.update_resolutions(message.id, key, resolution)
    if updated is None:  # lost a race against another resolver
        current = chat_repo.get_message(message.id)
        raise _ResolveFailure(status.HTTP_409_CONFLICT, "Esta propuesta ya fue resuelta.", current)
    return updated


def _parse_stored_commands(raw: List[Dict[str, Any]]) -> Tuple[List[Any], List[Dict[str, Any]]]:
    commands: List[Any] = []
    unreadable: List[Dict[str, Any]] = []
    for item in raw:
        try:
            commands.append(_command_adapter.validate_python(item))
        except Exception as e:
            unreadable.append({"command": item, "reason": f"Comando inválido: {e}"})
    return commands, unreadable


def _resolve_sync(
    repo: SqlAlchemyDiagramRepository,
    chat_repo: SqlAlchemyChatRepository,
    diagram_id: str,
    message_id: str,
    payload: ResolveProposalRequest,
    user: Optional[User],
) -> _Resolution:
    """Blocking section; the caller holds the room lock around it."""
    action = (payload.action or "").upper()
    if action not in ("APPLY", "DISCARD"):
        raise _ResolveFailure(status.HTTP_400_BAD_REQUEST, "action debe ser APPLY o DISCARD.")
    target = payload.target
    if target not in ("hologram", "operation"):
        raise _ResolveFailure(status.HTTP_400_BAD_REQUEST, "target debe ser 'hologram' u 'operation'.")
    if target == "operation" and payload.index is None:
        raise _ResolveFailure(status.HTTP_400_BAD_REQUEST, "index es obligatorio para target 'operation'.")

    message = chat_repo.get_message(message_id)
    if message is None or message.diagram_id != diagram_id:
        raise _ResolveFailure(status.HTTP_404_NOT_FOUND, "Mensaje no encontrado.")

    operation: Optional[Dict[str, Any]] = None
    if target == "hologram":
        key = "hologram"
        if not message.suggested_commands:
            raise _ResolveFailure(status.HTTP_404_NOT_FOUND, "El mensaje no tiene un holograma propuesto.")
    else:
        ops = message.suggested_operations or []
        if payload.index < 0 or payload.index >= len(ops):
            raise _ResolveFailure(status.HTTP_404_NOT_FOUND, "Operación propuesta no encontrada.")
        key = f"op:{payload.index}"
        operation = ops[payload.index]

    if key in message.resolutions:
        raise _ResolveFailure(status.HTTP_409_CONFLICT, "Esta propuesta ya fue resuelta.", message)

    if action == "DISCARD":
        updated = _record(chat_repo, message, key, _stamp(user, "DISCARDED"))
        return _Resolution(message=updated, recorded=True)

    if datetime.now(timezone.utc) - _as_utc(message.created_at) > PROPOSAL_TTL:
        raise _ResolveFailure(
            status.HTTP_410_GONE,
            "La propuesta venció (más de 30 minutos). Pedile a la IA que la genere de nuevo.",
            message,
        )

    if target == "hologram":
        return _apply_hologram(repo, chat_repo, diagram_id, message, user)
    return _apply_operation(repo, chat_repo, diagram_id, message, key, operation or {}, payload, user)


def _apply_hologram(
    repo: SqlAlchemyDiagramRepository,
    chat_repo: SqlAlchemyChatRepository,
    diagram_id: str,
    message: ChatMessage,
    user: Optional[User],
) -> _Resolution:
    commands, unreadable = _parse_stored_commands(message.suggested_commands or [])
    fresh = _reload_fresh(repo, diagram_id)
    outcome = replay_commands(fresh, commands)
    skipped = unreadable + [
        {"command": cmd.model_dump(mode="json"), "reason": reason} for cmd, reason in outcome.skipped
    ]
    if not outcome.applied:
        # Nothing fits the current diagram: leave the proposal unresolved.
        return _Resolution(message=message, document=fresh, applied_count=0, skipped=skipped)

    _auto_snapshot_before_edit(repo, fresh, user)
    repo.save(outcome.document)
    updated = _record(
        chat_repo,
        message,
        "hologram",
        _stamp(user, "APPLIED", applied_count=len(outcome.applied), skipped_count=len(skipped)),
    )
    return _Resolution(
        message=updated,
        document=outcome.document,
        applied_count=len(outcome.applied),
        skipped=skipped,
        recorded=True,
        document_changed=True,
    )


def _apply_operation(
    repo: SqlAlchemyDiagramRepository,
    chat_repo: SqlAlchemyChatRepository,
    diagram_id: str,
    message: ChatMessage,
    key: str,
    operation: Dict[str, Any],
    payload: ResolveProposalRequest,
    user: Optional[User],
) -> _Resolution:
    op_name = operation.get("op")
    author_id = user.id if user else GUEST_ID
    author_name = user.name if user else GUEST_NAME
    service = DiagramVersionService(repo.db)

    if op_name == "RESTORE_VERSION":
        number = operation.get("version_number")
        if not isinstance(number, int):
            raise _ResolveFailure(status.HTTP_400_BAD_REQUEST, "La operación no indica una versión válida.")
        try:
            restored = service.restore(
                diagram_id,
                number,
                author_id=author_id,
                author_name=author_name,
                author_type=VersionAuthorType.USER,
                expected_document_version=payload.expected_document_version,
            )
        except VersionNotFoundError as e:
            raise _ResolveFailure(status.HTTP_404_NOT_FOUND, str(e), message)
        except VersionConflictError as e:
            raise _ResolveFailure(status.HTTP_409_CONFLICT, str(e), message)
        updated = _record(
            chat_repo, message, key, _stamp(user, "APPLIED", detail=f"Restauró la versión {number}")
        )
        return _Resolution(message=updated, document=restored, recorded=True, document_changed=True)

    if op_name == "CREATE_CHECKPOINT":
        fresh = _reload_fresh(repo, diagram_id)
        version = service.create_snapshot(
            fresh,
            author_id=author_id,
            author_name=author_name,
            author_type=VersionAuthorType.USER,
            kind=VersionKind.CHECKPOINT,
            label=(operation.get("label") or "").strip() or None,
            message=(operation.get("reason") or "").strip() or None,
        )
        updated = _record(
            chat_repo, message, key, _stamp(user, "APPLIED", detail=f"Checkpoint v{version.number} creado")
        )
        return _Resolution(message=updated, recorded=True)

    raise _ResolveFailure(status.HTTP_400_BAD_REQUEST, f"Operación no soportada: {op_name!r}.")


@router.post("/{diagram_id}/chat/{message_id}/resolve", response_model=ResolveProposalResponse)
async def resolve_proposal(
    diagram_id: str,
    message_id: str,
    payload: ResolveProposalRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    chat_repo: SqlAlchemyChatRepository = Depends(get_chat_repo),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # Viewers and non-members may neither apply nor discard: both change shared state.
    await run_blocking(
        _load_for_edit,
        repo,
        user_repo,
        diagram_id,
        current_user,
        unauthenticated="Iniciá sesión para resolver propuestas de este proyecto.",
        forbidden="No tenés permiso para resolver propuestas de este proyecto.",
        viewer="Los observadores (viewers) no pueden aplicar ni descartar propuestas.",
    )

    async with get_room_lock(diagram_id):
        try:
            result = await run_blocking(
                _resolve_sync, repo, chat_repo, diagram_id, message_id, payload, current_user
            )
        except _ResolveFailure as failure:
            body: Dict[str, Any] = {"detail": failure.detail}
            if failure.message is not None:
                body["message"] = failure.message.model_dump(mode="json")
            return JSONResponse(status_code=failure.status_code, content=body)

        if result.document_changed and result.document is not None:
            await connection_manager.broadcast_document_update(
                diagram_id=diagram_id,
                document_dict=result.document.model_dump(mode="json"),
            )
        if result.recorded:
            await connection_manager.broadcast(
                diagram_id,
                {
                    "type": "CHAT_MESSAGE_UPDATED",
                    "diagram_id": diagram_id,
                    "message": result.message.model_dump(mode="json"),
                },
            )

    return ResolveProposalResponse(
        message=result.message,
        document=result.document,
        applied_count=result.applied_count,
        skipped=result.skipped,
    )
