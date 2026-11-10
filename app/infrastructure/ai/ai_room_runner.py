"""Runs one AI turn of a collaborative room without blocking anybody.

Concurrency design
- The turn is an asyncio task, so the sender's websocket loop keeps serving
  cursor/selection/chat while the LLM thinks.
- Turns of one room are serialized by get_room_ai_lock (held for the whole turn).
- The LLM is awaited WITHOUT the document lock. Only the short
  "reload -> replay -> save -> broadcast" step takes get_room_lock, and it
  replays the planned commands on a FRESH document, so a human edit made while
  the AI was thinking is preserved (commands that no longer apply are skipped
  and reported, never abort the batch).
- Each turn opens its own short-lived DB session: the websocket's session lives
  for the whole connection (stale identity map) and must not be shared with a
  concurrently running task.
"""

import asyncio
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.application.replay import ReplayOutcome, replay_commands  # noqa: F401  (re-exported)
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage, SenderType
from app.domain.models.commands import UmlCommand
from app.domain.models.user import User
from app.infrastructure.ai import version_tools
from app.infrastructure.ai.senior_colleague import (
    SeniorColleagueOrchestrator,
    senior_colleague_orchestrator,
)
from app.infrastructure.collaboration.connection_manager import connection_manager
from app.infrastructure.collaboration.blocking import run_blocking
from app.infrastructure.collaboration.room_locks import get_room_ai_lock, get_room_lock
from app.infrastructure.persistence.ai_memory_repository import SqlAlchemyAiMemoryRepository
from app.infrastructure.persistence.chat_repository import SqlAlchemyChatRepository
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository

logger = logging.getLogger(__name__)

AI_USER_ID = "ai-senior"
AI_NAME = "Senior AI"
AI_AVATAR_COLOR = "#8B5CF6"
MAX_SKIP_REASONS_SHOWN = 3


def _describe_skips(outcome: ReplayOutcome, total: int) -> str:
    reasons = "; ".join(reason for _, reason in outcome.skipped[:MAX_SKIP_REASONS_SHOWN])
    more = len(outcome.skipped) - MAX_SKIP_REASONS_SHOWN
    if more > 0:
        reasons += f"; y {more} más"
    return (
        f"⚠️ No pude aplicar {len(outcome.skipped)} de {total} cambios porque ya no encajan con el "
        f"diagrama actual (alguien lo modificó mientras yo pensaba). Detalle: {reasons}."
    )


def _default_session_factory() -> Session:
    # Resolved at call time on purpose: tests monkeypatch SessionLocal.
    import app.core.database as db_module

    return db_module.SessionLocal()


async def run_ai_turn(
    *,
    diagram_id: str,
    content: str,
    speaker: User,
    can_edit: bool,
    ai_mode: str,
    orchestrator: Optional[SeniorColleagueOrchestrator] = None,
    session_factory: Optional[Callable[[], Session]] = None,
    version_service_factory: Optional[version_tools.VersionServiceFactory] = None,
    hub=None,
) -> Optional[ChatMessage]:
    """One full AI turn: infer, apply safely, persist and broadcast the reply."""
    orchestrator = orchestrator or senior_colleague_orchestrator
    hub = hub or connection_manager
    make_session = session_factory or _default_session_factory
    make_versions = version_service_factory or version_tools.default_version_service_factory

    async with get_room_ai_lock(diagram_id):
        session = make_session()
        try:
            return await _turn_body(
                session=session,
                diagram_id=diagram_id,
                content=content,
                speaker=speaker,
                can_edit=can_edit,
                ai_mode=ai_mode,
                orchestrator=orchestrator,
                version_service_factory=make_versions,
                hub=hub,
            )
        except Exception:
            logger.exception("AI turn failed in room %s", diagram_id)
            try:
                session.rollback()
            except Exception:
                logger.warning("Rollback after failed AI turn also failed", exc_info=True)
            return await _post_failure_reply(
                diagram_id=diagram_id, speaker=speaker, hub=hub, session=session, make_session=make_session
            )
        finally:
            session.close()


async def _post_failure_reply(*, diagram_id: str, speaker: User, hub, session: Session, make_session) -> Optional[ChatMessage]:
    """Tells the room the turn failed. Must never raise."""
    ai_msg = ChatMessage(
        id=f"msg-{uuid.uuid4().hex[:8]}",
        diagram_id=diagram_id,
        sender_id=AI_USER_ID,
        sender_name=AI_NAME,
        sender_avatar_color=AI_AVATAR_COLOR,
        sender_type=SenderType.AI,
        content=(
            f"@{speaker.name}, no pude completar tu pedido por un error interno. "
            "No modifiqué nada que no te haya confirmado; probá de nuevo en un momento."
        ),
        addressed_to=speaker.name,
    )
    try:
        try:
            await run_blocking(SqlAlchemyChatRepository(session).add_message, ai_msg)
        except Exception:
            # The failed session may be unusable: retry once on a fresh one.
            logger.warning("Could not persist AI failure reply on the turn session", exc_info=True)
            fresh = make_session()
            try:
                await run_blocking(SqlAlchemyChatRepository(fresh).add_message, ai_msg)
            finally:
                fresh.close()
        await hub.broadcast_chat_message(diagram_id, ai_msg.model_dump(mode="json"))
        return ai_msg
    except Exception:
        logger.error("Could not deliver AI failure reply", exc_info=True)
        return None


async def _turn_body(
    *,
    session: Session,
    diagram_id: str,
    content: str,
    speaker: User,
    can_edit: bool,
    ai_mode: str,
    orchestrator: SeniorColleagueOrchestrator,
    version_service_factory: version_tools.VersionServiceFactory,
    hub,
) -> Optional[ChatMessage]:
    make_versions = version_service_factory
    diag_repo = SqlAlchemyDiagramRepository(session)
    chat_repo = SqlAlchemyChatRepository(session)
    memory = SqlAlchemyAiMemoryRepository(session)
    version_service = make_versions(session)

    def load_context():
        doc = diag_repo.get_by_id(diagram_id)
        if doc is None:
            return None, []
        msgs = chat_repo.list_messages(diagram_id, limit=12)
        return doc, msgs

    # One contiguous DB section off the event loop (the session is not used elsewhere meanwhile).
    document, history = await run_blocking(load_context)
    if document is None:
        return None
    if history and history[-1].sender_id == speaker.id and history[-1].content.strip() == content.strip():
        history = history[:-1]  # the message being answered is passed as the prompt itself

    # No document lock here: this is the slow part (LLM).
    result = await orchestrator.process_colleague_interaction(
        prompt=content,
        speaker=speaker,
        active_users=hub.get_active_presences(diagram_id),
        document=document,
        chat_history=history,
        ai_mode=ai_mode,
        can_edit=can_edit,
        memory=memory,
        version_service=version_service,
    )

    reply = result.reply_text
    applied: List[UmlCommand] = []
    if result.executed_commands and can_edit:
        reply, applied = await apply_planned_commands(
            session=session,
            diag_repo=diag_repo,
            version_service=version_service,
            hub=hub,
            diagram_id=diagram_id,
            prompt=content,
            planned=result.executed_commands,
            reply=reply,
            speaker_name=speaker.name,
        )

    if result.consumed_pending and applied:
        # Only a successful apply consumes the stored proposal; a failed one keeps it.
        def clear_pending():
            try:
                memory.clear_pending(diagram_id)
            except Exception:
                logger.warning("Could not clear the applied pending proposal", exc_info=True)
                session.rollback()

        await run_blocking(clear_pending)

    ai_msg = ChatMessage(
        id=f"msg-{uuid.uuid4().hex[:8]}",
        diagram_id=diagram_id,
        sender_id=AI_USER_ID,
        sender_name=AI_NAME,
        sender_avatar_color=AI_AVATAR_COLOR,
        sender_type=SenderType.AI,
        content=reply,
        addressed_to=result.addressed_to or speaker.name,
        ai_engine=result.engine or None,
        executed_commands=[c.model_dump(mode="json") for c in applied] or None,
        suggested_commands=[c.model_dump(mode="json") for c in result.suggested_commands] or None,
        suggested_operations=list(result.suggested_operations) or None,
    )
    await run_blocking(chat_repo.add_message, ai_msg)
    await hub.broadcast_chat_message(diagram_id, ai_msg.model_dump(mode="json"))
    return ai_msg


async def apply_planned_commands(
    *,
    session: Session,
    diag_repo: SqlAlchemyDiagramRepository,
    version_service,
    hub,
    diagram_id: str,
    prompt: str,
    planned: List[UmlCommand],
    reply: str,
    speaker_name: str,
) -> Tuple[str, List[UmlCommand]]:
    """Reload fresh under the room lock, replay, snapshot, save once, broadcast.

    Returns (reply_with_notes, applied_commands). Shared by the websocket turn and
    the REST assistant endpoint."""
    def reload_replay_save():
        session.expire_all()  # never trust the identity map: humans saved in the meantime
        fresh = diag_repo.get_by_id(diagram_id)
        if fresh is None:
            return None, None, None
        outcome = replay_commands(fresh, planned)
        if not outcome.applied:
            return fresh, outcome, None
        snapshot = version_tools.create_pre_ai_snapshot(version_service, fresh, prompt)
        diag_repo.save(outcome.document)
        return fresh, outcome, snapshot

    async with get_room_lock(diagram_id):
        # The whole reload -> replay -> snapshot -> save section is ONE blocking call, so the
        # event loop keeps serving every room's websockets while the DB works.
        fresh, outcome, snapshot = await run_blocking(reload_replay_save)
        if fresh is None:
            return f"@{speaker_name}, el diagrama ya no existe, no apliqué nada.", []
        if not outcome.applied:
            return f"@{speaker_name}, {_describe_skips(outcome, len(planned))}", []
        await hub.broadcast_document_update(
            diagram_id=diagram_id,
            document_dict=outcome.document.model_dump(mode="json"),
        )

    if outcome.skipped:
        reply += f"\n\n{_describe_skips(outcome, len(planned))}"
    if snapshot is None and version_service is not None:
        reply += (
            "\n\nℹ️ No pude guardar la versión de respaldo previa al cambio, "
            "así que no vas a tener un punto exacto al cual volver para deshacerlo."
        )
    return reply, outcome.applied


# --- scheduling: strong references, error logging, typing indicator ---

_background_tasks: Set["asyncio.Task[Any]"] = set()
_active_turns: Dict[str, int] = {}


async def _set_typing(hub, diagram_id: str, is_typing: bool) -> None:
    try:
        await hub.broadcast_typing(
            diagram_id=diagram_id, user_id=AI_USER_ID, name=AI_NAME, is_typing=is_typing
        )
    except Exception as e:
        logger.warning("Could not broadcast AI typing=%s: %s", is_typing, e)


async def _guarded_turn(diagram_id: str, hub, **kwargs) -> None:
    _active_turns[diagram_id] = _active_turns.get(diagram_id, 0) + 1
    await _set_typing(hub, diagram_id, True)
    try:
        await run_ai_turn(diagram_id=diagram_id, hub=hub, **kwargs)
    finally:
        _active_turns[diagram_id] -= 1
        if _active_turns[diagram_id] <= 0:
            _active_turns.pop(diagram_id, None)
            await _set_typing(hub, diagram_id, False)


def _on_task_done(task: "asyncio.Task[Any]") -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("AI turn failed: %s", exc, exc_info=exc)


def schedule_ai_turn(*, diagram_id: str, hub=None, **kwargs) -> "asyncio.Task[Any]":
    """Starts the AI turn in the background. The event loop only weakly
    references tasks, so we keep them in a set until they finish."""
    task = asyncio.create_task(_guarded_turn(diagram_id, hub or connection_manager, **kwargs))
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task
