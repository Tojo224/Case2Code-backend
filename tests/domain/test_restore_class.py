import pytest
from pydantic import TypeAdapter

from app.application.command_bus import command_bus
from app.application.replay import replay_commands
from app.domain.models.commands import (
    CreateClassCommand,
    CreateRelationshipCommand,
    DeleteClassCommand,
    RestoreClassCommand,
    UmlCommand,
)
from app.domain.services.uml_validator import UmlValidationError


def _class_data(cid="c-1", name="Cliente"):
    return {
        "id": cid,
        "name": name,
        "position": {"x": 5, "y": 6},
        "attributes": [
            {"id": "a-1", "name": "dni", "type": "String", "primary_key": True, "nullable": False,
             "default_value": None, "visibility": "PRIVATE"}
        ],
        "is_abstract": False,
        "stereotype": None,
    }


def _rel(rid, src, tgt):
    return {
        "id": rid, "name": None, "type": "ONE_TO_MANY", "source_class_id": src, "target_class_id": tgt,
        "source_cardinality": "1", "target_cardinality": "*", "source_role": None, "target_role": None,
        "source_handle": None, "target_handle": None,
    }


def _cmd(cid="c-1", name="Cliente", rels=()):
    return TypeAdapter(UmlCommand).validate_python(
        {"command_type": "RESTORE_CLASS", "class_data": _class_data(cid, name), "relationships": list(rels)}
    )


def test_restore_after_delete_keeps_ids(sample_document):
    doc = command_bus.dispatch(sample_document, CreateClassCommand(class_id="c-2", name="Pedido"))
    doc = command_bus.dispatch(doc, _cmd())
    doc = command_bus.dispatch(
        doc,
        CreateRelationshipCommand(relationship_id="r-1", type="ONE_TO_MANY", source_class_id="c-1", target_class_id="c-2"),
    )
    rel_json = doc.relationships[0].model_dump(mode="json")
    class_json = next(c for c in doc.classes if c.id == "c-1").model_dump(mode="json")
    doc = command_bus.dispatch(doc, DeleteClassCommand(class_id="c-1"))
    assert [c.id for c in doc.classes] == ["c-2"] and doc.relationships == []
    version = doc.version

    cmd = TypeAdapter(UmlCommand).validate_python(
        {"command_type": "RESTORE_CLASS", "class_data": class_json, "relationships": [rel_json]}
    )
    doc = command_bus.dispatch(doc, cmd)
    restored = next(c for c in doc.classes if c.id == "c-1")
    assert restored.attributes[0].id == "a-1"
    assert doc.relationships[0].id == "r-1"
    assert doc.version == version + 1


def test_restore_fails_on_existing_id_or_name(sample_document):
    doc = command_bus.dispatch(sample_document, _cmd())
    with pytest.raises(UmlValidationError):
        command_bus.dispatch(doc, _cmd(cid="c-1", name="Otro"))
    with pytest.raises(UmlValidationError):
        command_bus.dispatch(doc, _cmd(cid="c-9", name="cliente"))


def test_restore_skips_dangling_relationships(sample_document):
    rels = [_rel("r-ok", "c-1", "c-1"), _rel("r-bad", "c-1", "ghost")]
    doc = command_bus.dispatch(sample_document, _cmd(rels=rels))
    assert [r.id for r in doc.relationships] == ["r-ok"]


def test_restore_serialization_and_replay(sample_document):
    cmd = _cmd(rels=[_rel("r-ok", "c-1", "c-1")])
    dumped = cmd.model_dump(mode="json")
    assert dumped["command_type"] == "RESTORE_CLASS"
    assert isinstance(TypeAdapter(UmlCommand).validate_python(dumped), RestoreClassCommand)
    outcome = replay_commands(sample_document, [cmd])
    assert outcome is not None


def test_restore_via_endpoint(client):
    diagram_id = client.post("/api/diagrams", json={"name": "D"}).json()["id"]
    url = f"/api/diagrams/{diagram_id}/commands"
    r = client.post(url, json={"command": {"command_type": "CREATE_CLASS", "name": "Otro", "class_id": "c-2"}})
    assert r.status_code == 200
    body = {"command": {"command_type": "RESTORE_CLASS", "class_data": _class_data(),
                        "relationships": [_rel("r-1", "c-1", "c-2")]}}
    r = client.post(url, json=body)
    assert r.status_code == 200, r.text
    doc = r.json()["document"]
    c1 = next(c for c in doc["classes"] if c["id"] == "c-1")
    assert c1["attributes"][0]["id"] == "a-1"
    assert doc["relationships"][0]["id"] == "r-1"
    assert client.post(url, json=body).status_code == 400
