from enum import Enum
from typing import Annotated, Literal, Optional, Union
from pydantic import BaseModel, Field

from app.domain.models.canonical_uml import (
    Position,
    RelationshipTypeEnum,
    VisibilityEnum,
)


class CommandTypeEnum(str, Enum):
    CREATE_CLASS = "CREATE_CLASS"
    RENAME_CLASS = "RENAME_CLASS"
    DELETE_CLASS = "DELETE_CLASS"
    MOVE_CLASS = "MOVE_CLASS"
    ADD_ATTRIBUTE = "ADD_ATTRIBUTE"
    UPDATE_ATTRIBUTE = "UPDATE_ATTRIBUTE"
    DELETE_ATTRIBUTE = "DELETE_ATTRIBUTE"
    CREATE_RELATIONSHIP = "CREATE_RELATIONSHIP"
    UPDATE_RELATIONSHIP = "UPDATE_RELATIONSHIP"
    DELETE_RELATIONSHIP = "DELETE_RELATIONSHIP"


class BaseUmlCommand(BaseModel):
    command_type: CommandTypeEnum


class CreateClassCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.CREATE_CLASS] = CommandTypeEnum.CREATE_CLASS
    class_id: Optional[str] = None
    name: str
    position: Optional[Position] = None
    is_abstract: bool = False
    stereotype: Optional[str] = None


class RenameClassCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.RENAME_CLASS] = CommandTypeEnum.RENAME_CLASS
    class_id: str
    new_name: str


class DeleteClassCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.DELETE_CLASS] = CommandTypeEnum.DELETE_CLASS
    class_id: str


class MoveClassCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.MOVE_CLASS] = CommandTypeEnum.MOVE_CLASS
    class_id: str
    position: Position


class AddAttributeCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.ADD_ATTRIBUTE] = CommandTypeEnum.ADD_ATTRIBUTE
    class_id: str
    attribute_id: Optional[str] = None
    name: str
    type: str = "String"
    primary_key: bool = False
    nullable: bool = True
    default_value: Optional[str] = None
    visibility: VisibilityEnum = VisibilityEnum.PRIVATE


class UpdateAttributeCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.UPDATE_ATTRIBUTE] = CommandTypeEnum.UPDATE_ATTRIBUTE
    class_id: str
    attribute_id: str
    name: Optional[str] = None
    type: Optional[str] = None
    primary_key: Optional[bool] = None
    nullable: Optional[bool] = None
    default_value: Optional[str] = None
    visibility: Optional[VisibilityEnum] = None


class DeleteAttributeCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.DELETE_ATTRIBUTE] = CommandTypeEnum.DELETE_ATTRIBUTE
    class_id: str
    attribute_id: str


class CreateRelationshipCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.CREATE_RELATIONSHIP] = CommandTypeEnum.CREATE_RELATIONSHIP
    relationship_id: Optional[str] = None
    name: Optional[str] = None
    type: RelationshipTypeEnum
    source_class_id: str
    target_class_id: str
    source_cardinality: str = "1"
    target_cardinality: str = "1"
    source_role: Optional[str] = None
    target_role: Optional[str] = None


class UpdateRelationshipCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.UPDATE_RELATIONSHIP] = CommandTypeEnum.UPDATE_RELATIONSHIP
    relationship_id: str
    name: Optional[str] = None
    type: Optional[RelationshipTypeEnum] = None
    source_cardinality: Optional[str] = None
    target_cardinality: Optional[str] = None
    source_role: Optional[str] = None
    target_role: Optional[str] = None


class DeleteRelationshipCommand(BaseUmlCommand):
    command_type: Literal[CommandTypeEnum.DELETE_RELATIONSHIP] = CommandTypeEnum.DELETE_RELATIONSHIP
    relationship_id: str


UmlCommand = Annotated[
    Union[
        CreateClassCommand,
        RenameClassCommand,
        DeleteClassCommand,
        MoveClassCommand,
        AddAttributeCommand,
        UpdateAttributeCommand,
        DeleteAttributeCommand,
        CreateRelationshipCommand,
        UpdateRelationshipCommand,
        DeleteRelationshipCommand,
    ],
    Field(discriminator="command_type"),
]
