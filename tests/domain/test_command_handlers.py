import pytest
from app.application.command_bus import command_bus
from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    Position,
    RelationshipTypeEnum,
)
from app.domain.models.commands import (
    AddAttributeCommand,
    CreateClassCommand,
    CreateRelationshipCommand,
    DeleteAttributeCommand,
    DeleteClassCommand,
    DeleteRelationshipCommand,
    MoveClassCommand,
    RenameClassCommand,
    UpdateAttributeCommand,
    UpdateRelationshipCommand,
)
from app.domain.services.uml_validator import AttributeNotFoundException, ClassNotFoundException


def test_command_create_and_rename_class(sample_document):
    cmd_create = CreateClassCommand(name="Cliente", position=Position(x=10.0, y=20.0))
    doc = command_bus.dispatch(sample_document, cmd_create)

    assert len(doc.classes) == 1
    assert doc.classes[0].name == "Cliente"
    assert doc.classes[0].position.x == 10.0
    # Check default id attribute was created
    assert len(doc.classes[0].attributes) == 1
    assert doc.classes[0].attributes[0].name == "id"
    assert doc.classes[0].attributes[0].primary_key is True
    assert doc.version == 2

    class_id = doc.classes[0].id
    cmd_rename = RenameClassCommand(class_id=class_id, new_name="ClienteVip")
    doc = command_bus.dispatch(doc, cmd_rename)
    assert doc.classes[0].name == "ClienteVip"
    assert doc.version == 3


def test_command_attribute_lifecycle(sample_document):
    cmd_create = CreateClassCommand(name="Producto")
    doc = command_bus.dispatch(sample_document, cmd_create)
    class_id = doc.classes[0].id

    # Add attribute
    cmd_add = AddAttributeCommand(class_id=class_id, name="precio", type="BigDecimal")
    doc = command_bus.dispatch(doc, cmd_add)
    assert len(doc.classes[0].attributes) == 2
    attr_precio = doc.classes[0].attributes[1]
    assert attr_precio.name == "precio"
    assert attr_precio.type == "BigDecimal"

    # Update attribute
    cmd_update = UpdateAttributeCommand(
        class_id=class_id,
        attribute_id=attr_precio.id,
        name="precioFinal",
        type="Double",
    )
    doc = command_bus.dispatch(doc, cmd_update)
    assert doc.classes[0].attributes[1].name == "precioFinal"
    assert doc.classes[0].attributes[1].type == "Double"

    # Delete attribute
    cmd_delete = DeleteAttributeCommand(class_id=class_id, attribute_id=attr_precio.id)
    doc = command_bus.dispatch(doc, cmd_delete)
    assert len(doc.classes[0].attributes) == 1

    # Deleting non-existent raises
    with pytest.raises(AttributeNotFoundException):
        command_bus.dispatch(doc, DeleteAttributeCommand(class_id=class_id, attribute_id="non-existent"))


def test_command_relationship_and_cascade_delete(sample_document):
    # 1. Create two classes
    doc = command_bus.dispatch(sample_document, CreateClassCommand(name="Cliente"))
    doc = command_bus.dispatch(doc, CreateClassCommand(name="Reserva"))
    cliente_id = doc.classes[0].id
    reserva_id = doc.classes[1].id

    # 2. Create relationship 1:N
    cmd_rel = CreateRelationshipCommand(
        source_class_id=cliente_id,
        target_class_id=reserva_id,
        type=RelationshipTypeEnum.ONE_TO_MANY,
        source_cardinality="1",
        target_cardinality="*",
    )
    doc = command_bus.dispatch(doc, cmd_rel)
    assert len(doc.relationships) == 1
    rel_id = doc.relationships[0].id
    assert doc.relationships[0].source_cardinality == "1"
    assert doc.relationships[0].target_cardinality == "*"

    # 3. Update relationship
    cmd_update_rel = UpdateRelationshipCommand(
        relationship_id=rel_id,
        target_cardinality="0..*",
    )
    doc = command_bus.dispatch(doc, cmd_update_rel)
    assert doc.relationships[0].target_cardinality == "0..*"

    # 4. REFERENTIAL INTEGRITY: Delete Cliente -> Relationship must be cascade removed!
    cmd_delete_class = DeleteClassCommand(class_id=cliente_id)
    doc = command_bus.dispatch(doc, cmd_delete_class)

    assert len(doc.classes) == 1
    assert doc.classes[0].id == reserva_id
    # Assert relationship was automatically removed!
    assert len(doc.relationships) == 0


def test_command_move_class(sample_document):
    doc = command_bus.dispatch(sample_document, CreateClassCommand(name="Item"))
    class_id = doc.classes[0].id

    cmd_move = MoveClassCommand(class_id=class_id, position=Position(x=250.0, y=350.0))
    doc = command_bus.dispatch(doc, cmd_move)
    assert doc.classes[0].position.x == 250.0
    assert doc.classes[0].position.y == 350.0
