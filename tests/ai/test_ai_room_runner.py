import asyncio
import uuid

import pytest

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.user import User
from app.domain.models.versioning import VersionAuthorType, VersionKind
from app.infrastructure.ai import ai_room_runner
from app.infrastructure.ai.ai_room_runner import run_ai_turn, schedule_ai_turn
from app.infrastructure.ai.senior_colleague import SeniorColleagueOrchestrator
from app.infrastructure.collaboration.room_locks import get_room_lock
from tests.ai.fakes import FakeHub, FakeProvider, FakeVersionService

pytestmark = pytest.mark.asyncio

CREATE_PRODUCTO = {"command_type": "CREATE_CLASS", "name": "Producto"}
CREATE_CLIENTE = {"command_type": "CREATE_CLASS", "name": "Cliente"}
SPEAKER = User(id="usr-1", email="ana@test.com", name="Ana", avatar_color="#10B981")


@pytest.fixture
def room(diagram_repo):
    doc = CanonicalUmlDocument(id=f"diag-run-{uuid.uuid4().hex[:8]}", name="Room", version=1)
    diagram_repo.save(doc)
    return doc


def _turn(room, provider, hub, versions=None, can_edit=True, content="@ai crea la clase Producto", mode="PAIR_PROGRAMMER"):
    return run_ai_turn(
        diagram_id=room.id,
        content=content,
        speaker=SPEAKER,
        can_edit=can_edit,
        ai_mode=mode,
        orchestrator=SeniorColleagueOrchestrator(provider=provider),
        version_service_factory=lambda _session: versions or FakeVersionService(),
        hub=hub,
    )


async def _human_adds_class(diagram_repo, diagram_id, name):
    """Same read-modify-write a REST /commands call does: under the room lock."""
    from app.application.command_bus import command_bus
    from app.domain.models.commands import CreateClassCommand

    async with get_room_lock(diagram_id):
        diagram_repo.db.expire_all()
        doc = diagram_repo.get_by_id(diagram_id)
        diagram_repo.save(command_bus.dispatch(doc, CreateClassCommand(name=name)))


async def test_human_edit_during_ai_thinking_survives(room, diagram_repo):
    gate, entered = asyncio.Event(), asyncio.Event()
    provider = FakeProvider(commands=[CREATE_PRODUCTO], gate=gate, entered=entered)
    hub = FakeHub()

    turn = asyncio.create_task(_turn(room, provider, hub))
    await asyncio.wait_for(entered.wait(), 5)  # the AI is "thinking" and holds no document lock

    await asyncio.wait_for(_human_adds_class(diagram_repo, room.id, "Cliente"), 5)
    gate.set()
    await asyncio.wait_for(turn, 5)

    diagram_repo.db.expire_all()
    final = diagram_repo.get_by_id(room.id)
    assert {c.name for c in final.classes} == {"Cliente", "Producto"}
    assert final.version == 3  # 1 + human edit + AI edit, no extra bump
    assert hub.documents and len(hub.documents[-1]["classes"]) == 2


async def test_commands_that_no_longer_apply_are_skipped_and_reported(room, diagram_repo):
    gate, entered = asyncio.Event(), asyncio.Event()
    provider = FakeProvider(commands=[CREATE_PRODUCTO, CREATE_CLIENTE], gate=gate, entered=entered)
    hub = FakeHub()

    turn = asyncio.create_task(_turn(room, provider, hub))
    await asyncio.wait_for(entered.wait(), 5)
    await _human_adds_class(diagram_repo, room.id, "Producto")  # makes the AI's first command invalid
    gate.set()
    ai_msg = await asyncio.wait_for(turn, 5)

    diagram_repo.db.expire_all()
    names = sorted(c.name for c in diagram_repo.get_by_id(room.id).classes)
    assert names == ["Cliente", "Producto"]
    assert "No pude aplicar 1 de 2" in ai_msg.content
    assert len(ai_msg.executed_commands) == 1


async def test_pre_ai_snapshot_is_taken_before_the_ai_change(room, diagram_repo):
    versions = FakeVersionService()
    await _turn(room, FakeProvider(commands=[CREATE_PRODUCTO]), FakeHub(), versions=versions)

    assert len(versions.snapshots) == 1
    snap = versions.snapshots[0]
    assert snap["kind"] == VersionKind.PRE_AI
    assert snap["author_type"] == VersionAuthorType.AI
    assert snap["author_name"] == "Senior AI"
    assert snap["class_names"] == []  # state BEFORE the AI change


async def test_failed_snapshot_does_not_block_the_edit_and_is_mentioned(room, diagram_repo):
    versions = FakeVersionService(fail_snapshot=True)
    ai_msg = await _turn(room, FakeProvider(commands=[CREATE_PRODUCTO]), FakeHub(), versions=versions)

    diagram_repo.db.expire_all()
    assert [c.name for c in diagram_repo.get_by_id(room.id).classes] == ["Producto"]
    assert "versión de respaldo" in ai_msg.content


async def test_viewer_cannot_make_the_ai_mutate(room, diagram_repo):
    provider = FakeProvider(commands=[CREATE_PRODUCTO])
    hub = FakeHub()
    ai_msg = await _turn(room, provider, hub, can_edit=False)

    diagram_repo.db.expire_all()
    assert diagram_repo.get_by_id(room.id).classes == []
    assert hub.documents == []
    assert "observador" in ai_msg.content
    assert ai_msg.executed_commands is None


async def test_turns_of_one_room_are_serialized(room):
    gate = asyncio.Event()
    provider = FakeProvider(commands=[CREATE_PRODUCTO], gate=gate)
    hub = FakeHub()
    first = asyncio.create_task(_turn(room, provider, hub))
    second = asyncio.create_task(_turn(room, provider, hub, content="@ai crea la clase Cliente"))
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert provider.max_running == 1


async def test_typing_is_turned_off_even_when_the_turn_fails(room):
    class Boom(SeniorColleagueOrchestrator):
        async def process_colleague_interaction(self, *a, **k):
            raise RuntimeError("llm exploded")

    hub = FakeHub()
    task = schedule_ai_turn(
        diagram_id=room.id,
        hub=hub,
        content="@ai hola",
        speaker=SPEAKER,
        can_edit=True,
        ai_mode="ADVISORY",
        orchestrator=Boom(),
    )
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)
    assert hub.typing == [True, False]
    assert task not in ai_room_runner._background_tasks
