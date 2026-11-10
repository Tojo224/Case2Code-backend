from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage
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


class AssistantPromptRequest(BaseModel):
    prompt: Optional[str] = "Replicar este diseño en el diagrama."
    image_base64: Optional[str] = None
    image_mime_type: Optional[str] = "image/png"
    gemini_api_key: Optional[str] = None


class AssistantPromptResponse(BaseModel):
    success: bool
    reply: str
    executed_commands: list[UmlCommand]
    document: CanonicalUmlDocument
    error: Optional[str] = None



class AiConfigUpdateRequest(BaseModel):
    """Runtime LLM settings. Semantic validation (mode, URL host) is done by the route."""

    provider_mode: Optional[str] = Field(default=None, max_length=16)
    local_base_url: Optional[str] = Field(default=None, max_length=300)
    local_model: Optional[str] = Field(default=None, max_length=120)


class BatchCommandsRequest(BaseModel):
    commands: List[UmlCommand]


class SkippedCommand(BaseModel):
    command: Any
    reason: str


class BatchCommandsResponse(BaseModel):
    document: CanonicalUmlDocument
    applied: List[UmlCommand]
    skipped: List[SkippedCommand]


class ResolveProposalRequest(BaseModel):
    """Plain strings on purpose: bad input must answer 400, not FastAPI's 422."""

    target: str
    index: Optional[int] = None
    action: str
    expected_document_version: Optional[int] = None


class ResolveProposalResponse(BaseModel):
    message: ChatMessage
    document: Optional[CanonicalUmlDocument] = None
    applied_count: Optional[int] = None
    skipped: Optional[List[Dict[str, Any]]] = None
