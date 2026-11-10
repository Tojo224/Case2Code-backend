"""The event loop must keep ticking while the DB works, and the XMI import must hold the room lock."""

import asyncio
import io
import time
import uuid

import httpx
import pytest
import pytest_asyncio

from app.application.command_bus import command_bus
from app.application.services.version_service import DiagramVersionService
from app.core.database import get_db
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.commands import CreateClassCommand
from app.domain.models.user import User
from app.infrastructure.ai.ai_room_runner import run_ai_turn
from app.infrastructure.ai.senior_colleague import SeniorColleagueOrchestrator
from app.infrastructure.collaboration.room_locks import get_room_lock
from app.infrastructure.interchange.xmi_exporter import XmiExporter
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.main import app
from app.presentation.routes import diagrams as diagrams_routes
from tests.ai.fakes import FakeHub, FakeProvider, FakeVersionService

pytestmark = pytest.mark.asyncio

SLOW_SECONDS = 0.4
TICK = 0.01


@pytest.fixture
def room(diagram_repo):
    doc = CanonicalUmlDocument(id=f"diag-async-{uuid.uuid4().hex[:8]}", name="Async", version=1)
    diagram_repo.save(doc)
    return doc


@pytest_asyncio.fixture
async def async_client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


class Ticker:
    """A coroutine that can only tick while the event loop is free."""

    def __init__(self):
        self.ticks = 0
        self._task = None

    async def _run(self):
        while True:
            await asyncio.sleep(TICK)
            self.ticks += 1

    def __enter__(self):
        self._task = asyncio.create_task(self._run())
        return self

    def __exit__(self, *exc):
        self._task.cancel()


def _slow(original):
    def wrapper(*args, **kwargs):
        time.sleep(SLOW_SECONDS)  # a slow DB, as seen by the loop
        return original(*args, **kwargs)

    return wrapper


MIN_TICKS = int(SLOW_SECONDS / TICK / 3)  # generous: a blocked loop would give ~0


async def test_rest_command_does_not_block_the_event_loop(async_client, room, monkeypatch):
    monkeypatch.setattr(
        diagrams_routes, "_auto_snapshot_before_edit", _slow(diagrams_routes._auto_snapshot_before_edit)
    )

    with Ticker() as ticker:
        resp = await async_client.post(
            f"/api/diagrams/{room.id}/commands",
            json={"command": {"command_type": "CREATE_CLASS", "name": "Alpha"}},
        )

    assert resp.status_code == 200
    assert ticker.ticks >= MIN_TICKS


async def test_ai_turn_save_does_not_block_the_event_loop(room, monkeypatch):
    monkeypatch.setattr(SqlAlchemyDiagramRepository, "save", _slow(SqlAlchemyDiagramRepository.save))
    provider = FakeProvider(commands=[{"command_type": "CREATE_CLASS", "name": "Producto"}])
    speaker = User(id="usr-1", email="ana@test.com", name="Ana", avatar_color="#10B981")

    with Ticker() as ticker:
        msg = await run_ai_turn(
            diagram_id=room.id,
            content="@ai crea la clase Producto",
            speaker=speaker,
            can_edit=True,
            ai_mode="PAIR_PROGRAMMER",
            orchestrator=SeniorColleagueOrchestrator(provider=provider),
            version_service_factory=lambda _s: FakeVersionService(),
            hub=FakeHub(),
        )

    assert msg is not None and msg.executed_commands
    assert ticker.ticks >= MIN_TICKS


async def test_xmi_import_waits_for_the_room_lock_and_does_not_overwrite_a_concurrent_edit(
    async_client, room, db_session, diagram_repo
):
    source = CanonicalUmlDocument(id="diag-xmi-src", name="Src", version=1)
    source = command_bus.dispatch(source, CreateClassCommand(name="Imported"))
    xmi = XmiExporter.export(source).encode("utf-8")

    async with get_room_lock(room.id):
        upload = asyncio.create_task(
            async_client.post(
                f"/api/diagrams/{room.id}/import/xmi",
                files={"file": ("m.xmi", io.BytesIO(xmi), "application/xml")},
            )
        )
        await asyncio.sleep(0.3)
        assert not upload.done()  # the import is queued behind the lock holder

        # A concurrent edit lands while the import waits (same RMW a /commands call does).
        db_session.expire_all()
        live = diagram_repo.get_by_id(room.id)
        diagram_repo.save(command_bus.dispatch(live, CreateClassCommand(name="Concurrente")))
        version_after_edit = diagram_repo.get_by_id(room.id).version

    resp = await asyncio.wait_for(upload, 10)

    assert resp.status_code == 200
    # Built on the FRESH document (version continues from the concurrent edit), not the stale one.
    assert resp.json()["version"] > version_after_edit
    # The pre-import state, including the concurrent edit, was snapshotted first.
    service = DiagramVersionService(db_session)
    versions = service.list_versions(room.id)
    assert len(versions) == 1
    snapshot = service.get_version(room.id, versions[0].number).snapshot
    assert [c.name for c in snapshot.classes] == ["Concurrente"]
