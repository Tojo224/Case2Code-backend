"""Test doubles for the AI room: provider, hub and version service."""

import asyncio
import copy
from typing import Any, Dict, List, Optional

from app.domain.models.versioning import (
    DiagramChange,
    DiagramVersion,
    VersionAuthorType,
    VersionDiff,
    VersionKind,
)
from app.infrastructure.ai.ai_provider import AiProviderPort


class FakeProvider(AiProviderPort):
    def __init__(
        self,
        name: str = "Fake LLM",
        commands: Optional[List[Dict[str, Any]]] = None,
        chat_reply: str = "Mi consejo: normalizá.",
        gate: Optional[asyncio.Event] = None,
        entered: Optional[asyncio.Event] = None,
    ):
        self._name = name
        self.commands = commands if commands is not None else []
        self.chat_reply = chat_reply
        self.gate = gate
        self.entered = entered
        self.command_calls = 0
        self.chat_calls: List[Dict[str, str]] = []
        self.running = 0
        self.max_running = 0

    @property
    def provider_name(self) -> str:
        return self._name

    async def generate_commands(self, prompt, current_uml, image_data=None):
        self.command_calls += 1
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.entered is not None:
                self.entered.set()
            if self.gate is not None:
                await self.gate.wait()
            return {"commands": copy.deepcopy(self.commands), "question": None}
        finally:
            self.running -= 1

    async def generate_chat_response(self, system_prompt: str, user_message: str) -> Optional[str]:
        self.chat_calls.append({"system": system_prompt, "user": user_message})
        return self.chat_reply


class FakeHub:
    def __init__(self):
        self.documents: List[Dict[str, Any]] = []
        self.chats: List[Dict[str, Any]] = []
        self.typing: List[bool] = []

    def get_active_presences(self, diagram_id):
        return []

    async def broadcast_document_update(self, diagram_id, document_dict, command_dict=None):
        self.documents.append(document_dict)

    async def broadcast_chat_message(self, diagram_id, message):
        self.chats.append(message)

    async def broadcast_typing(self, diagram_id, user_id, name, is_typing, exclude_ws=None):
        self.typing.append(is_typing)


class FakeVersionService:
    def __init__(self, versions: Optional[List[DiagramVersion]] = None, fail_snapshot: bool = False):
        self.versions = versions or []
        self.fail_snapshot = fail_snapshot
        self.snapshots: List[Dict[str, Any]] = []
        self.diff_changes: List[DiagramChange] = []

    def create_snapshot(self, document, *, author_id, author_name, author_type=VersionAuthorType.USER,
                        kind=VersionKind.CHECKPOINT, label=None, message=None):
        if self.fail_snapshot:
            raise RuntimeError("snapshot boom")
        number = max([v.number for v in self.versions], default=0) + 1
        version = make_version(number, kind=kind, author_name=author_name, author_type=author_type,
                               diagram_id=document.id, label=label)
        self.versions.insert(0, version)
        self.snapshots.append({
            "kind": kind, "author_type": author_type, "author_name": author_name,
            "label": label, "class_names": [c.name for c in document.classes],
        })
        return version

    def list_versions(self, diagram_id, limit=50):
        return self.versions[:limit]

    def diff(self, diagram_id, from_number, to_number=None):
        return VersionDiff(diagram_id=diagram_id, from_number=from_number, to_number=to_number,
                           changes=self.diff_changes, summary="")


def make_version(number, kind=VersionKind.CHECKPOINT, author_name="Ana",
                 author_type=VersionAuthorType.USER, diagram_id="d", label=None) -> DiagramVersion:
    return DiagramVersion(
        id=f"ver-{number}", diagram_id=diagram_id, number=number, kind=kind, label=label,
        author_id="u1", author_name=author_name, author_type=author_type,
        document_version=number, class_count=2, relationship_count=1,
    )
