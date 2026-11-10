import asyncio

import pytest
from pydantic import TypeAdapter

from app.domain.models.canonical_uml import CanonicalUmlDocument, Position, UmlClass
from app.domain.models.commands import UmlCommand
from app.infrastructure.ai import ai_room_runner
from app.infrastructure.ai.ai_room_runner import AI_USER_ID, _cursor_stops, schedule_ai_cursor

_adapter = TypeAdapter(UmlCommand)


def _cmds(*raw):
    return [_adapter.validate_python(r) for r in raw]


def _doc():
    return CanonicalUmlDocument(
        id="d-cur",
        name="D",
        classes=[
            UmlClass(id="c1", name="Cliente", position=Position(x=100, y=100)),
            UmlClass(id="c2", name="Pedido", position=Position(x=400, y=200)),
            UmlClass(id="c3", name="Producto", position=Position(x=700, y=50)),
        ],
    )


def test_stops_follow_the_touched_classes_in_order_without_repeats():
    cmds = _cmds(
        {"command_type": "CREATE_CLASS", "class_id": "c2", "name": "Pedido"},
        {"command_type": "ADD_ATTRIBUTE", "class_id": "Pedido", "name": "fecha", "type": "LocalDate"},
        {"command_type": "ADD_ATTRIBUTE", "class_id": "c1", "name": "email", "type": "String"},
    )
    stops = _cursor_stops(_doc(), cmds)
    assert len(stops) == 2  # Pedido once (by id and by name), then Cliente
    assert stops[0][0] > 400 and stops[1][0] > 100


def test_unknown_classes_and_empty_commands_produce_no_stops():
    assert _cursor_stops(_doc(), []) == []
    assert _cursor_stops(_doc(), _cmds({"command_type": "DELETE_CLASS", "class_id": "zzz"})) == []


class RecordingHub:
    def __init__(self):
        self.events = []

    async def broadcast_ai_cursor(self, diagram_id, user_id, name, color, x, y):
        self.events.append(("move", user_id, round(x), round(y)))

    async def hide_cursor(self, diagram_id, user_id, name):
        self.events.append(("hide", user_id))


@pytest.mark.asyncio
async def test_ai_cursor_walks_then_hides(monkeypatch):
    monkeypatch.setattr(ai_room_runner, "CURSOR_STEP_S", 0.0)
    monkeypatch.setattr(ai_room_runner, "CURSOR_LINGER_S", 0.0)
    hub = RecordingHub()
    cmds = _cmds(
        {"command_type": "ADD_ATTRIBUTE", "class_id": "c1", "name": "email", "type": "String"},
        {"command_type": "ADD_ATTRIBUTE", "class_id": "c3", "name": "precio", "type": "Double"},
    )
    schedule_ai_cursor(hub, "d-cur", _doc(), cmds)
    await asyncio.sleep(0.05)
    assert [e[0] for e in hub.events] == ["move", "move", "hide"]
    assert all(e[1] == AI_USER_ID for e in hub.events)
    assert "d-cur" not in ai_room_runner._cursor_tasks


@pytest.mark.asyncio
async def test_a_hub_without_cursor_support_is_ignored():
    class PlainHub:
        pass

    schedule_ai_cursor(PlainHub(), "d-cur", _doc(), _cmds({"command_type": "ADD_ATTRIBUTE", "class_id": "c1", "name": "x", "type": "String"}))
    assert "d-cur" not in ai_room_runner._cursor_tasks
