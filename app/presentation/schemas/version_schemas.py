from typing import Optional

from pydantic import BaseModel, Field


class CreateVersionRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=255)
    message: Optional[str] = Field(default=None, max_length=2000)
    # Only used for guests on open diagrams; authenticated users use their own name.
    author_name: Optional[str] = Field(default=None, max_length=100)


class RestoreVersionRequest(BaseModel):
    expected_document_version: Optional[int] = None
