from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class VisibilityEnum(str, Enum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    PROTECTED = "PROTECTED"
    PACKAGE = "PACKAGE"


class RelationshipTypeEnum(str, Enum):
    ONE_TO_ONE = "ONE_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"
    MANY_TO_ONE = "MANY_TO_ONE"
    MANY_TO_MANY = "MANY_TO_MANY"
    INHERITANCE = "INHERITANCE"
    AGGREGATION = "AGGREGATION"
    COMPOSITION = "COMPOSITION"
    REALIZATION = "REALIZATION"
    DEPENDENCY = "DEPENDENCY"


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class UmlAttribute(BaseModel):
    id: str
    name: str
    type: str = "String"
    primary_key: bool = False
    nullable: bool = True
    default_value: Optional[str] = None
    visibility: VisibilityEnum = VisibilityEnum.PRIVATE


class UmlClass(BaseModel):
    id: str
    name: str
    position: Position = Field(default_factory=Position)
    attributes: List[UmlAttribute] = Field(default_factory=list)
    is_abstract: bool = False
    stereotype: Optional[str] = None


class UmlRelationship(BaseModel):
    id: str
    name: Optional[str] = None
    type: RelationshipTypeEnum
    source_class_id: str
    target_class_id: str
    source_cardinality: str = "1"
    target_cardinality: str = "1"
    source_role: Optional[str] = None
    target_role: Optional[str] = None
    source_handle: Optional[str] = None
    target_handle: Optional[str] = None


class CanonicalUmlDocument(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    version: int = 1
    classes: List[UmlClass] = Field(default_factory=list)
    relationships: List[UmlRelationship] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

