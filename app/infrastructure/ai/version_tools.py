"""Version-control abilities of the senior AI.

The AI may READ history (deterministic Spanish answers, no LLM), SAVE a
checkpoint (non-destructive) and take a PRE_AI snapshot before its own edits.
It never restores: a restore is only ever proposed as a suggested operation
that a human approves in the UI.

The version service is reached through an injectable factory so tests (and the
room runner) never depend on the real implementation.
"""

import logging
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.application.services.version_service import DiagramVersionService
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.versioning import (
    DiagramVersion,
    VersionAuthorType,
    VersionKind,
)
from app.infrastructure.ai.ai_policy import fold, strip_mention

logger = logging.getLogger(__name__)

AI_AUTHOR_ID = "ai-senior"
AI_AUTHOR_NAME = "Senior AI"
# Each checkpoint is a full JSON copy of the diagram: don't let chat spam it.
AI_CHECKPOINT_COOLDOWN_S = 60

VersionServiceFactory = Callable[[Session], DiagramVersionService]


def default_version_service_factory(db: Session) -> DiagramVersionService:
    return DiagramVersionService(db)


class VersionIntentKind(str, Enum):
    HISTORY = "HISTORY"
    CHECKPOINT = "CHECKPOINT"
    RESTORE = "RESTORE"


class RestoreTarget(str, Enum):
    NUMBER = "NUMBER"
    PREVIOUS = "PREVIOUS"
    LAST_AI = "LAST_AI"


@dataclass(frozen=True)
class VersionIntent:
    kind: VersionIntentKind
    target: Optional[RestoreTarget] = None
    version_number: Optional[int] = None


@dataclass
class VersionToolResult:
    reply: str
    operations: List[Dict[str, Any]] = field(default_factory=list)


_RESTORE_VERB = re.compile(
    r"\b(volve\w*|vuelve|volv\w+|restaur\w+|restablec\w+|revert\w+|retroced\w+|retorn\w+|desh[ae]c\w*)\b"
)
_VERSION_NUMBER = re.compile(r"(?:\bversion\s*(?:n\s*[°o.]?\s*)?#?\s*(\d+)|\bv\s*(\d+)\b|#(\d+))")
_PREVIOUS = re.compile(r"\b(?:la|el)\s+(?:version\s+)?anterior\b|\bultima\s+version\b|\bpunto\s+anterior\b")
_UNDO_AI = re.compile(
    r"\blo\s+ultimo\s+que\s+(?:hiciste|hizo|hicieron|cambiaste)\b"
    r"|\blo\s+que\s+(?:hiciste|hizo)\b"
    r"|\btu\s+ultimo\s+cambio\b|\bultimo\s+cambio\s+de\s+la\s+ia\b|\bcambios?\s+de\s+la\s+ia\b"
)
_CHECKPOINT = re.compile(
    r"\b(?:guard\w+|cre\w+|hac\w+|haz\w*|gener\w+|marc\w+)\b.*"
    r"\b(?:checkpoint|snapshot|punto\s+de\s+restauracion|punto\s+de\s+control|copia\s+de\s+seguridad)\b"
    r"|\bguard\w+\b.*\bversion\b"
)
_CHECKPOINT_BARE = re.compile(
    r"^\W*(?:un\s+|el\s+)?(?:checkpoint|snapshot|punto\s+de\s+restauracion)\W*$"
)
# "historial" alone is version history, but "historial clinico" / "historial de
# reservas" are domain nouns.
_HISTORY = re.compile(
    r"\bque\s+cambio\b|\bque\s+cambios\b"
    r"|\bhistorial\b(?!\s+(?:clinic\w*|medic\w*|academic\w*|de\s+(?!versiones\b|cambios\b)\w+))"
    r"|\bversiones\b|\bque\s+se\s+modifico\b"
    r"|\bultimos\s+cambios\b|\bcambios\s+recientes\b|\bque\s+hicieron\b|\bque\s+paso\b"
)

# Words of a modelling request: the message is about the DIAGRAM CONTENT, not its history.
_MODEL_ENTITY = re.compile(r"\b(?:atributos?|clases?|tablas?|columnas?|campos?|entidad(?:es)?|relacion(?:es)?)\b")
_CREATION_VERB = re.compile(
    r"\b(?:crea\w*|agreg\w+|anad\w+|add|create|new|nueva|nuevo|hac\w+|haz\w*|arma\w*|disena\w*"
    r"|genera\w*|modela\w*|constru\w+|elimin\w+|borra\w*|quita\w*)\b"
)
# Version-specific anchors that still win when the message also sounds like modelling.
_STRONG_ANCHOR = re.compile(
    r"\b(?:checkpoint|snapshot|punto\s+de\s+(?:restauracion|control)|copia\s+de\s+seguridad)\b"
    r"|\bversion\s*(?:n\s*[°o.]?\s*)?#?\s*\d+|\bv\s*\d+\b|#\d+"
    r"|\bversion\s+anterior\b|\bultima\s+version\b"
    r"|\bversiones\s+(?:del?|de\s+(?:la|el|este|esta))\s+(?:diagrama|proyecto|modelo)\b"
    r"|\bhistorial\s+de\s+(?:versiones|cambios)\b"
    r"|\blo\s+ultimo\s+que\s+(?:hiciste|hizo|hicieron|cambiaste)\b|\bcambios?\s+de\s+la\s+ia\b"
)


def _is_modelling_request(text: str) -> bool:
    return bool(_MODEL_ENTITY.search(text) or _CREATION_VERB.search(text))


def detect_version_intent(prompt: str) -> Optional[VersionIntent]:
    """Deterministic intent detection on the (accent-folded) prompt. RESTORE wins
    over CHECKPOINT, which wins over HISTORY.

    A message that creates/edits model content or names model entities ("crea la
    clase Historial", "guarda la version del libro como atributo") is only a
    version request when it carries a version-specific anchor (checkpoint,
    "version 3", "historial de versiones", ...)."""
    text = fold(strip_mention(prompt))

    if _is_modelling_request(text) and not _STRONG_ANCHOR.search(text):
        return None

    if _RESTORE_VERB.search(text):
        match = _VERSION_NUMBER.search(text)
        if match:
            number = int(next(g for g in match.groups() if g))
            return VersionIntent(VersionIntentKind.RESTORE, RestoreTarget.NUMBER, number)
        if _UNDO_AI.search(text):
            return VersionIntent(VersionIntentKind.RESTORE, RestoreTarget.LAST_AI)
        if _PREVIOUS.search(text):
            return VersionIntent(VersionIntentKind.RESTORE, RestoreTarget.PREVIOUS)
        if re.search(r"\bdesh[ae]c\w*\b", text):
            return VersionIntent(VersionIntentKind.RESTORE, RestoreTarget.LAST_AI)

    if _CHECKPOINT.search(text) or _CHECKPOINT_BARE.search(text):
        return VersionIntent(VersionIntentKind.CHECKPOINT)

    if _HISTORY.search(text):
        return VersionIntent(VersionIntentKind.HISTORY)
    return None


def _describe(version: DiagramVersion) -> str:
    kind = {
        VersionKind.CHECKPOINT: "checkpoint",
        VersionKind.AUTO: "automática",
        VersionKind.PRE_AI: "antes de un cambio de la IA",
        VersionKind.PRE_RESTORE: "antes de una restauración",
    }.get(version.kind, str(version.kind))
    label = f" «{version.label}»" if version.label else ""
    return f"v{version.number}{label} ({kind}, {version.author_name}, {version.class_count} clases)"


def summarize_recent_versions(
    service: Optional[DiagramVersionService], diagram_id: str, limit: int = 5
) -> str:
    """Compact list of the latest versions for the LLM context. "" on any failure."""
    if service is None:
        return ""
    try:
        versions = service.list_versions(diagram_id, limit=limit)
    except Exception as e:
        logger.debug("Could not list versions for AI context: %s", e)
        return ""
    return "\n".join(f"  - {_describe(v)}" for v in versions[:limit])


def answer_history(service: DiagramVersionService, diagram_id: str, max_changes: int = 8) -> str:
    """"Qué cambió": diff between the newest saved version and the live diagram."""
    try:
        versions = service.list_versions(diagram_id, limit=5)
        if not versions:
            return (
                "Todavía no hay versiones guardadas de este diagrama. "
                "Pedime `@ai guardá un checkpoint` y arranco el historial."
            )
        latest = versions[0]
        diff = service.diff(diagram_id, latest.number, None)
    except Exception as e:
        logger.warning("Could not build history answer: %s", e)
        return "No pude consultar el historial de versiones en este momento. Probá de nuevo en unos segundos."

    lines = [f"Última versión guardada: {_describe(latest)}."]
    if not diff.changes:
        lines.append("Desde entonces el diagrama no tuvo cambios.")
    else:
        lines.append(f"Desde entonces cambió esto ({len(diff.changes)} cambios):")
        lines.extend(f"- {c.description}" for c in diff.changes[:max_changes])
        if len(diff.changes) > max_changes:
            lines.append(f"- ... y {len(diff.changes) - max_changes} más.")
    if len(versions) > 1:
        lines.append("Versiones anteriores: " + "; ".join(_describe(v) for v in versions[1:]) + ".")
    return "\n".join(lines)


def _checkpoint_cooldown_remaining(service: DiagramVersionService, diagram_id: str) -> int:
    """Seconds left before the AI may save another checkpoint (0 = allowed).
    A failing lookup never blocks the checkpoint."""
    try:
        versions = service.list_versions(diagram_id, limit=50)
    except Exception as e:
        logger.debug("Could not check AI checkpoint cooldown: %s", e)
        return 0
    last = next(
        (
            v
            for v in versions
            if v.kind == VersionKind.CHECKPOINT and v.author_type == VersionAuthorType.AI
        ),
        None,
    )
    if last is None:
        return 0
    created = last.created_at if last.created_at.tzinfo else last.created_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - created).total_seconds()
    return max(0, int(AI_CHECKPOINT_COOLDOWN_S - elapsed) + 1) if elapsed < AI_CHECKPOINT_COOLDOWN_S else 0


def create_ai_checkpoint(
    service: DiagramVersionService, document: CanonicalUmlDocument, requested_by: str
) -> VersionToolResult:
    """Direct, safe action: save a CHECKPOINT authored by the AI."""
    wait = _checkpoint_cooldown_remaining(service, document.id)
    if wait > 0:
        return VersionToolResult(
            "Ya guardé un checkpoint hace instantes. "
            f"Esperá unos {wait} segundos antes de pedirme otro."
        )
    try:
        version = service.create_snapshot(
            document,
            author_id=AI_AUTHOR_ID,
            author_name=AI_AUTHOR_NAME,
            author_type=VersionAuthorType.AI,
            kind=VersionKind.CHECKPOINT,
            label=f"Checkpoint pedido por {requested_by}",
            message="Guardado por la IA a pedido del equipo.",
        )
    except Exception as e:
        logger.warning("AI checkpoint failed: %s", e)
        return VersionToolResult("No pude guardar el checkpoint por un error interno. Probá de nuevo.")
    return VersionToolResult(
        f"Listo, guardé el checkpoint v{version.number} con el estado actual "
        f"({version.class_count} clases, {version.relationship_count} relaciones). "
        "Podés volver a él cuando quieras desde el historial."
    )


def create_pre_ai_snapshot(
    service: Optional[DiagramVersionService], document: CanonicalUmlDocument, prompt: str
) -> Optional[DiagramVersion]:
    """Snapshot taken right before the AI mutates the diagram. Never raises:
    a failure is logged and the caller continues (and mentions it in the reply)."""
    if service is None:
        return None
    try:
        return service.create_snapshot(
            document,
            author_id=AI_AUTHOR_ID,
            author_name=AI_AUTHOR_NAME,
            author_type=VersionAuthorType.AI,
            kind=VersionKind.PRE_AI,
            label="Antes de un cambio de la IA",
            message=(prompt or "")[:200] or None,
        )
    except Exception as e:
        logger.warning("PRE_AI snapshot failed, continuing without it: %s", e)
        return None


def build_restore_operation(
    service: DiagramVersionService, diagram_id: str, intent: VersionIntent
) -> VersionToolResult:
    """NEVER restores. Emits a RESTORE_VERSION suggested operation for a human to approve."""
    try:
        versions = service.list_versions(diagram_id, limit=50)
    except Exception as e:
        logger.warning("Could not list versions for restore proposal: %s", e)
        return VersionToolResult("No pude consultar el historial de versiones, así que no puedo proponerte la restauración.")

    if not versions:
        return VersionToolResult("Todavía no hay versiones guardadas a las que volver.")

    target: Optional[DiagramVersion] = None
    reason = ""
    if intent.target == RestoreTarget.NUMBER:
        target = next((v for v in versions if v.number == intent.version_number), None)
        if target is None:
            return VersionToolResult(f"No encontré la versión {intent.version_number} en el historial de este diagrama.")
        reason = f"Pedido del equipo: volver a la versión {target.number}."
    elif intent.target == RestoreTarget.LAST_AI:
        target = next((v for v in versions if v.kind == VersionKind.PRE_AI), None)
        if target is None:
            return VersionToolResult("No encuentro una versión previa a un cambio mío, así que no tengo a qué volver para deshacerlo.")
        reason = "Deshacer el último cambio hecho por la IA."
    else:
        target = versions[0]
        reason = "Volver a la última versión guardada."

    operation = {"op": "RESTORE_VERSION", "version_number": target.number, "reason": reason}
    reply = (
        f"Puedo proponerte volver a {_describe(target)}. "
        "Yo no restauro por mi cuenta: aprobá la tarjeta de abajo si estás de acuerdo. "
        "Antes de restaurar se guarda automáticamente el estado actual, así que se puede deshacer."
    )
    return VersionToolResult(reply, [operation])
