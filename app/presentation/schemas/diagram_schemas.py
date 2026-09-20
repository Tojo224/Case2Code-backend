from typing import Optional
from pydantic import BaseModel, Field

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.commands import UmlCommand


class CreateDiagramRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class ExecuteCommandRequest(BaseModel):
    command: UmlCommand


class CommandExecutionResponse(BaseModel):
    success: bool
    version: int
    document: CanonicalUmlDocument
    error: Optional[str] = None
