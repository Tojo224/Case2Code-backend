from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    Position,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
    VisibilityEnum,
)


def test_canonical_uml_document_serialization():
    doc = CanonicalUmlDocument(
        id="diag-1",
        name="Hotel System",
        classes=[
            UmlClass(
                id="class-cliente",
                name="Cliente",
                position=Position(x=100.0, y=150.0),
                attributes=[
                    UmlAttribute(
                        id="attr-1",
                        name="id",
                        type="Long",
                        primary_key=True,
                        nullable=False,
                    ),
                    UmlAttribute(
                        id="attr-2",
                        name="nombre",
                        type="String",
                        primary_key=False,
                        nullable=False,
                        visibility=VisibilityEnum.PRIVATE,
                    ),
                ],
            ),
            UmlClass(
                id="class-reserva",
                name="Reserva",
                position=Position(x=400.0, y=150.0),
                attributes=[
                    UmlAttribute(
                        id="attr-3",
                        name="id",
                        type="Long",
                        primary_key=True,
                        nullable=False,
                    ),
                ],
            ),
        ],
        relationships=[
            UmlRelationship(
                id="rel-1",
                name="reservas_cliente",
                type=RelationshipTypeEnum.ONE_TO_MANY,
                source_class_id="class-cliente",
                target_class_id="class-reserva",
                source_cardinality="1",
                target_cardinality="*",
            )
        ],
    )

    data = doc.model_dump(mode="json")
    assert data["id"] == "diag-1"
    assert len(data["classes"]) == 2
    assert len(data["relationships"]) == 1
    assert data["classes"][0]["attributes"][0]["name"] == "id"
    assert data["relationships"][0]["type"] == "ONE_TO_MANY"

    # Verify round-trip deserialization
    reconstructed = CanonicalUmlDocument.model_validate(data)
    assert reconstructed.id == doc.id
    assert reconstructed.classes[0].name == "Cliente"
    assert reconstructed.relationships[0].source_class_id == "class-cliente"
