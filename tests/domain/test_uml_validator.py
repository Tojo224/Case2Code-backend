import pytest
from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    Position,
    RelationshipTypeEnum,
    UmlClass,
    UmlRelationship,
)
from app.domain.models.commands import (
    AddAttributeCommand,
    CreateClassCommand,
    CreateRelationshipCommand,
    RenameClassCommand,
)
from app.domain.services.uml_validator import (
    ClassNotFoundException,
    UmlValidationError,
    UmlValidator,
)


def test_validate_identifier():
    UmlValidator.validate_identifier("Cliente")
    UmlValidator.validate_identifier("id_cliente")
    UmlValidator.validate_identifier("_private")

    with pytest.raises(UmlValidationError):
        UmlValidator.validate_identifier("123Cliente")
    with pytest.raises(UmlValidationError):
        UmlValidator.validate_identifier("Cliente-Name")
    with pytest.raises(UmlValidationError):
        UmlValidator.validate_identifier("Cliente Name")


def test_validate_duplicate_class(sample_document):
    cmd = CreateClassCommand(name="Cliente")
    UmlValidator.validate_create_class(sample_document, cmd)

    sample_document.classes.append(UmlClass(id="c1", name="Cliente"))

    # Case insensitive duplicate check
    with pytest.raises(UmlValidationError):
        UmlValidator.validate_create_class(sample_document, CreateClassCommand(name="cliente"))


def test_validate_rename_class(sample_document):
    sample_document.classes.append(UmlClass(id="c1", name="Cliente"))
    sample_document.classes.append(UmlClass(id="c2", name="Reserva"))

    # Valid rename
    UmlValidator.validate_rename_class(sample_document, RenameClassCommand(class_id="c1", new_name="Usuario"))

    # Collision with existing class
    with pytest.raises(UmlValidationError):
        UmlValidator.validate_rename_class(sample_document, RenameClassCommand(class_id="c1", new_name="Reserva"))

    # Non-existent class
    with pytest.raises(ClassNotFoundException):
        UmlValidator.validate_rename_class(sample_document, RenameClassCommand(class_id="c999", new_name="X"))


def test_validate_duplicate_attribute(sample_document):
    c1 = UmlClass(id="c1", name="Cliente")
    sample_document.classes.append(c1)

    cmd = AddAttributeCommand(class_id="c1", name="telefono", type="String")
    UmlValidator.validate_add_attribute(sample_document, cmd)

    cmd2 = AddAttributeCommand(class_id="c1", name="telefono", type="String")
    # Add first
    from app.domain.models.canonical_uml import UmlAttribute
    c1.attributes.append(UmlAttribute(id="a1", name="telefono", type="String"))

    with pytest.raises(UmlValidationError):
        UmlValidator.validate_add_attribute(sample_document, cmd2)


def test_validate_relationship_endpoints(sample_document):
    sample_document.classes.append(UmlClass(id="c1", name="Cliente"))
    sample_document.classes.append(UmlClass(id="c2", name="Reserva"))

    # Valid
    cmd = CreateRelationshipCommand(
        source_class_id="c1",
        target_class_id="c2",
        type=RelationshipTypeEnum.ONE_TO_MANY,
    )
    UmlValidator.validate_create_relationship(sample_document, cmd)

    # Invalid source
    with pytest.raises(ClassNotFoundException):
        UmlValidator.validate_create_relationship(
            sample_document,
            CreateRelationshipCommand(
                source_class_id="non-existent",
                target_class_id="c2",
                type=RelationshipTypeEnum.ONE_TO_MANY,
            ),
        )
