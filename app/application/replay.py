"""Replays a list of UML commands on a document with batch semantics.

Shared by the AI room runner, the REST batch endpoint and the server-side
resolution of AI proposals: every caller wants "apply what still fits, report
what does not, never abort the batch".
"""

import logging
from dataclasses import dataclass, field
from typing import List, Tuple

from app.application.command_bus import command_bus
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.commands import UmlCommand

logger = logging.getLogger(__name__)


@dataclass
class ReplayOutcome:
    document: CanonicalUmlDocument
    applied: List[UmlCommand] = field(default_factory=list)
    skipped: List[Tuple[UmlCommand, str]] = field(default_factory=list)


def replay_commands(document: CanonicalUmlDocument, commands: List[UmlCommand]) -> ReplayOutcome:
    """Replays planned commands on `document` (which is never mutated).

    Each command is tried on a private copy; a command that fails (validation
    error, missing class, ...) is skipped and reported while the rest continue.
    The command handlers bump the document version themselves, so nothing extra
    is added here.
    """
    current = document.model_copy(deep=True)
    outcome = ReplayOutcome(document=current)
    for cmd in commands:
        trial = current.model_copy(deep=True)
        try:
            current = command_bus.dispatch(trial, cmd)
            outcome.applied.append(cmd)
        except Exception as e:  # UmlValidationError, ValueError, KeyError, handler bugs
            logger.warning("Skipping command %s: %s", getattr(cmd, "command_type", cmd), e)
            outcome.skipped.append((cmd, str(e)))
    outcome.document = current
    return outcome
