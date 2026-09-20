import re
from typing import Set

from app.domain.models.canonical_uml import CanonicalUmlDocument, UmlClass, UmlRelationship
from app.domain.models.commands import (
    AddAttributeCommand,
    CreateClassCommand,
    CreateRelationshipCommand,
    RenameClassCommand,
    UpdateAttributeCommand,
    UpdateRelationshipCommand,
)


class UmlValidationError(Exception):
    """Raised when a UML domain rule is violated."""
    pass


class ClassNotFoundException(UmlValidationError):
    def __init__(self, class_id: str):
        super().__init__(f"Class with ID '{class_id}' does not exist in diagram.")


class AttributeNotFoundException(UmlValidationError):
    def __init__(self, class_id: str, attribute_id: str):
        super().__init__(f"Attribute with ID '{attribute_id}' does not exist in class '{class_id}'.")


class RelationshipNotFoundException(UmlValidationError):
    def __init__(self, relationship_id: str):
        super().__init__(f"Relationship with ID '{relationship_id}' does not exist in diagram.")


class UmlValidator:
    IDENTIFIER_REGEX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    @classmethod
    def validate_identifier(cls, name: str, field_name: str = "Name") -> None:
        if not name or not cls.IDENTIFIER_REGEX.match(name.strip()):
            raise UmlValidationError(
                f"{field_name} '{name}' is invalid. Must be a valid alphanumeric identifier starting with a letter or underscore."
            )

    @classmethod
    def validate_create_class(cls, document: CanonicalUmlDocument, cmd: CreateClassCommand) -> None:
        cls.validate_identifier(cmd.name, "Class name")
        for cls_item in document.classes:
            if cls_item.name.lower() == cmd.name.strip().lower():
                raise UmlValidationError(f"Class '{cmd.name}' already exists in diagram.")

    @classmethod
    def validate_rename_class(cls, document: CanonicalUmlDocument, cmd: RenameClassCommand) -> UmlClass:
        target_class = cls.get_class_or_fail(document, cmd.class_id)
        cls.validate_identifier(cmd.new_name, "Class name")
        for cls_item in document.classes:
            if cls_item.id != cmd.class_id and cls_item.name.lower() == cmd.new_name.strip().lower():
                raise UmlValidationError(f"Class name '{cmd.new_name}' is already taken by another class.")
        return target_class

    @classmethod
    def validate_add_attribute(cls, document: CanonicalUmlDocument, cmd: AddAttributeCommand) -> UmlClass:
        target_class = cls.get_class_or_fail(document, cmd.class_id)
        cls.validate_identifier(cmd.name, "Attribute name")
        for attr in target_class.attributes:
            if attr.name.lower() == cmd.name.strip().lower():
                raise UmlValidationError(
                    f"Attribute '{cmd.name}' already exists in class '{target_class.name}'."
                )
        return target_class

    @classmethod
    def validate_update_attribute(cls, document: CanonicalUmlDocument, cmd: UpdateAttributeCommand) -> UmlClass:
        target_class = cls.get_class_or_fail(document, cmd.class_id)
        attr = next((a for a in target_class.attributes if a.id == cmd.attribute_id), None)
        if not attr:
            raise AttributeNotFoundException(cmd.class_id, cmd.attribute_id)

        if cmd.name:
            cls.validate_identifier(cmd.name, "Attribute name")
            for other_attr in target_class.attributes:
                if other_attr.id != cmd.attribute_id and other_attr.name.lower() == cmd.name.strip().lower():
                    raise UmlValidationError(
                        f"Attribute name '{cmd.name}' is already used by another attribute in class '{target_class.name}'."
                    )
        return target_class

    @classmethod
    def validate_create_relationship(cls, document: CanonicalUmlDocument, cmd: CreateRelationshipCommand) -> None:
        cls.get_class_or_fail(document, cmd.source_class_id)
        cls.get_class_or_fail(document, cmd.target_class_id)

        # Check for duplicate identical relationship
        for rel in document.relationships:
            if (
                rel.source_class_id == cmd.source_class_id
                and rel.target_class_id == cmd.target_class_id
                and rel.type == cmd.type
                and rel.source_role == cmd.source_role
                and rel.target_role == cmd.target_role
            ):
                raise UmlValidationError(
                    f"Identical relationship of type '{cmd.type}' already exists between source '{cmd.source_class_id}' and target '{cmd.target_class_id}'."
                )

    @classmethod
    def validate_update_relationship(cls, document: CanonicalUmlDocument, cmd: UpdateRelationshipCommand) -> UmlRelationship:
        rel = next((r for r in document.relationships if r.id == cmd.relationship_id), None)
        if not rel:
            raise RelationshipNotFoundException(cmd.relationship_id)
        return rel

    @classmethod
    def get_class_or_fail(cls, document: CanonicalUmlDocument, class_id: str) -> UmlClass:
        for cls_item in document.classes:
            if cls_item.id == class_id:
                return cls_item
        raise ClassNotFoundException(class_id)

    @classmethod
    def validate_document_integrity(cls, document: CanonicalUmlDocument) -> None:
        """Verifies overall consistency of document (unique class names, valid relation endpoints)."""
        class_ids: Set[str] = set()
        class_names: Set[str] = set()

        for c in document.classes:
            if c.id in class_ids:
                raise UmlValidationError(f"Duplicate class ID '{c.id}' found in document.")
            if c.name.lower() in class_names:
                raise UmlValidationError(f"Duplicate class name '{c.name}' found in document.")
            class_ids.add(c.id)
            class_names.add(c.name.lower())

            # Validate attributes uniqueness
            attr_names: Set[str] = set()
            for attr in c.attributes:
                if attr.name.lower() in attr_names:
                    raise UmlValidationError(f"Duplicate attribute '{attr.name}' in class '{c.name}'.")
                attr_names.add(attr.name.lower())

        for rel in document.relationships:
            if rel.source_class_id not in class_ids:
                raise UmlValidationError(
                    f"Relationship '{rel.id}' references non-existent source class '{rel.source_class_id}'."
                )
            if rel.target_class_id not in class_ids:
                raise UmlValidationError(
                    f"Relationship '{rel.id}' references non-existent target class '{rel.target_class_id}'."
                )
