import uuid
from datetime import datetime, timezone
from typing import Optional

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    Position,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
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
from app.domain.services.uml_validator import (
    AttributeNotFoundException,
    RelationshipNotFoundException,
    UmlValidator,
)


def _generate_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _touch_document(document: CanonicalUmlDocument) -> None:
    document.version += 1
    document.updated_at = datetime.now(timezone.utc)


def handle_create_class(document: CanonicalUmlDocument, cmd: CreateClassCommand) -> CanonicalUmlDocument:
    UmlValidator.validate_create_class(document, cmd)
    class_id = cmd.class_id or _generate_id("class")
    position = cmd.position or Position(x=100.0, y=100.0)

    # By default, add a primary key 'id: Long' if no attributes provided yet
    default_pk = UmlAttribute(
        id=_generate_id("attr"),
        name="id",
        type="Long",
        primary_key=True,
        nullable=False,
    )

    new_class = UmlClass(
        id=class_id,
        name=cmd.name.strip(),
        position=position,
        attributes=[default_pk],
        is_abstract=cmd.is_abstract,
        stereotype=cmd.stereotype,
    )

    document.classes.append(new_class)
    _touch_document(document)
    return document


def handle_rename_class(document: CanonicalUmlDocument, cmd: RenameClassCommand) -> CanonicalUmlDocument:
    target_class = UmlValidator.validate_rename_class(document, cmd)
    target_class.name = cmd.new_name.strip()
    _touch_document(document)
    return document


def handle_delete_class(document: CanonicalUmlDocument, cmd: DeleteClassCommand) -> CanonicalUmlDocument:
    target_class = UmlValidator.get_class_or_fail(document, cmd.class_id)
    
    # Remove class
    document.classes = [c for c in document.classes if c.id != target_class.id]

    # Cascade remove any relationship involving this class (Referential Integrity)
    document.relationships = [
        r
        for r in document.relationships
        if r.source_class_id != target_class.id and r.target_class_id != target_class.id
    ]

    _touch_document(document)
    return document


def handle_move_class(document: CanonicalUmlDocument, cmd: MoveClassCommand) -> CanonicalUmlDocument:
    target_class = UmlValidator.get_class_or_fail(document, cmd.class_id)
    target_class.position = cmd.position
    _touch_document(document)
    return document


def handle_add_attribute(document: CanonicalUmlDocument, cmd: AddAttributeCommand) -> CanonicalUmlDocument:
    target_class = UmlValidator.validate_add_attribute(document, cmd)
    attr_id = cmd.attribute_id or _generate_id("attr")

    # If this attribute is marked primary key, remove PK flag from others
    if cmd.primary_key:
        for a in target_class.attributes:
            a.primary_key = False

    new_attr = UmlAttribute(
        id=attr_id,
        name=cmd.name.strip(),
        type=cmd.type.strip(),
        primary_key=cmd.primary_key,
        nullable=cmd.nullable,
        default_value=cmd.default_value,
        visibility=cmd.visibility,
    )
    target_class.attributes.append(new_attr)
    _touch_document(document)
    return document


def handle_update_attribute(document: CanonicalUmlDocument, cmd: UpdateAttributeCommand) -> CanonicalUmlDocument:
    target_class = UmlValidator.validate_update_attribute(document, cmd)
    attr = next(a for a in target_class.attributes if a.id == cmd.attribute_id)

    if cmd.name is not None:
        attr.name = cmd.name.strip()
    if cmd.type is not None:
        attr.type = cmd.type.strip()
    if cmd.primary_key is not None:
        if cmd.primary_key:
            for a in target_class.attributes:
                a.primary_key = False
        attr.primary_key = cmd.primary_key
    if cmd.nullable is not None:
        attr.nullable = cmd.nullable
    if cmd.default_value is not None:
        attr.default_value = cmd.default_value
    if cmd.visibility is not None:
        attr.visibility = cmd.visibility

    _touch_document(document)
    return document


def handle_delete_attribute(document: CanonicalUmlDocument, cmd: DeleteAttributeCommand) -> CanonicalUmlDocument:
    target_class = UmlValidator.get_class_or_fail(document, cmd.class_id)
    existing_len = len(target_class.attributes)
    target_class.attributes = [a for a in target_class.attributes if a.id != cmd.attribute_id]
    if len(target_class.attributes) == existing_len:
        raise AttributeNotFoundException(cmd.class_id, cmd.attribute_id)

    _touch_document(document)
    return document


def handle_create_relationship(document: CanonicalUmlDocument, cmd: CreateRelationshipCommand) -> CanonicalUmlDocument:
    UmlValidator.validate_create_relationship(document, cmd)
    rel_id = cmd.relationship_id or _generate_id("rel")

    new_rel = UmlRelationship(
        id=rel_id,
        name=cmd.name,
        type=cmd.type,
        source_class_id=cmd.source_class_id,
        target_class_id=cmd.target_class_id,
        source_cardinality=cmd.source_cardinality,
        target_cardinality=cmd.target_cardinality,
        source_role=cmd.source_role,
        target_role=cmd.target_role,
    )
    document.relationships.append(new_rel)
    _touch_document(document)
    return document


def handle_update_relationship(document: CanonicalUmlDocument, cmd: UpdateRelationshipCommand) -> CanonicalUmlDocument:
    rel = UmlValidator.validate_update_relationship(document, cmd)
    if cmd.name is not None:
        rel.name = cmd.name
    if cmd.type is not None:
        rel.type = cmd.type
    if cmd.source_cardinality is not None:
        rel.source_cardinality = cmd.source_cardinality
    if cmd.target_cardinality is not None:
        rel.target_cardinality = cmd.target_cardinality
    if cmd.source_role is not None:
        rel.source_role = cmd.source_role
    if cmd.target_role is not None:
        rel.target_role = cmd.target_role

    _touch_document(document)
    return document


def handle_delete_relationship(document: CanonicalUmlDocument, cmd: DeleteRelationshipCommand) -> CanonicalUmlDocument:
    existing_len = len(document.relationships)
    document.relationships = [r for r in document.relationships if r.id != cmd.relationship_id]
    if len(document.relationships) == existing_len:
        raise RelationshipNotFoundException(cmd.relationship_id)

    _touch_document(document)
    return document
