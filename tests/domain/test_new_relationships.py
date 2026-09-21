import pytest
from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlClass,
    UmlRelationship,
)
from app.domain.models.commands import CreateRelationshipCommand
from app.application.command_bus import command_bus
from app.domain.services.uml_validator import UmlValidationError


def test_create_all_relationship_types_with_handles():
    doc = CanonicalUmlDocument(
        id="doc-test",
        name="TestDoc",
        classes=[
            UmlClass(id="c1", name="Persona"),
            UmlClass(id="c2", name="Cliente"),
            UmlClass(id="c3", name="Pedido"),
            UmlClass(id="c4", name="DetallePedido"),
        ],
    )

    # 1. Inheritance: Cliente extends Persona with side handles
    cmd_inh = CreateRelationshipCommand(
        type=RelationshipTypeEnum.INHERITANCE,
        source_class_id="c2",
        target_class_id="c1",
        source_handle="top-source",
        target_handle="bottom-target",
    )
    doc = command_bus.dispatch(doc, cmd_inh)
    assert len(doc.relationships) == 1
    rel1 = doc.relationships[0]
    assert rel1.type == RelationshipTypeEnum.INHERITANCE
    assert rel1.source_handle == "top-source"
    assert rel1.target_handle == "bottom-target"

    # 2. Composition: Pedido owns DetallePedido with lateral handles
    cmd_comp = CreateRelationshipCommand(
        type=RelationshipTypeEnum.COMPOSITION,
        source_class_id="c3",
        target_class_id="c4",
        source_handle="right-source",
        target_handle="left-target",
        source_cardinality="1",
        target_cardinality="*",
    )
    doc = command_bus.dispatch(doc, cmd_comp)
    assert len(doc.relationships) == 2
    rel2 = doc.relationships[1]
    assert rel2.type == RelationshipTypeEnum.COMPOSITION
    assert rel2.source_handle == "right-source"
    assert rel2.target_handle == "left-target"


def test_prevent_circular_and_self_inheritance():
    doc = CanonicalUmlDocument(
        id="doc-circ",
        name="CircularDoc",
        classes=[
            UmlClass(id="c1", name="Animal"),
            UmlClass(id="c2", name="Mamifero"),
        ],
    )

    # Self inheritance must fail
    with pytest.raises(UmlValidationError, match="A class cannot inherit from itself"):
        command_bus.dispatch(
            doc,
            CreateRelationshipCommand(
                type=RelationshipTypeEnum.INHERITANCE,
                source_class_id="c1",
                target_class_id="c1",
            ),
        )

    # Mamifero extends Animal
    doc = command_bus.dispatch(
        doc,
        CreateRelationshipCommand(
            type=RelationshipTypeEnum.INHERITANCE,
            source_class_id="c2",
            target_class_id="c1",
        ),
    )

    # Animal extends Mamifero (circular!) must fail
    with pytest.raises(UmlValidationError, match="Circular inheritance detected"):
        command_bus.dispatch(
            doc,
            CreateRelationshipCommand(
                type=RelationshipTypeEnum.INHERITANCE,
                source_class_id="c1",
                target_class_id="c2",
            ),
        )


def test_self_referencing_recursive_relationship():
    doc = CanonicalUmlDocument(
        id="doc-self",
        name="SelfRefDoc",
        classes=[
            UmlClass(id="c1", name="Empleado"),
        ],
    )

    # Empleado references Empleado (recursive 1:N)
    cmd_self = CreateRelationshipCommand(
        type=RelationshipTypeEnum.ONE_TO_MANY,
        source_class_id="c1",
        target_class_id="c1",
        source_handle="right-source",
        target_handle="bottom-target",
        source_cardinality="1",
        target_cardinality="*",
        source_role="subordinados",
        target_role="supervisor",
    )
    doc = command_bus.dispatch(doc, cmd_self)
    assert len(doc.relationships) == 1
    rel = doc.relationships[0]
    assert rel.source_class_id == "c1"
    assert rel.target_class_id == "c1"
    assert rel.source_role == "subordinados"
    assert rel.target_role == "supervisor"

