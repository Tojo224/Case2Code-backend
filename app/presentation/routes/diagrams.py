import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.application.command_bus import command_bus
from app.core.database import get_db
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.services.uml_validator import UmlValidationError
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.presentation.schemas.diagram_schemas import (
    CommandExecutionResponse,
    CreateDiagramRequest,
    ExecuteCommandRequest,
)

router = APIRouter(prefix="/diagrams", tags=["Diagrams"])


def get_repository(db: Session = Depends(get_db)) -> SqlAlchemyDiagramRepository:
    return SqlAlchemyDiagramRepository(db)


@router.post("", response_model=CanonicalUmlDocument, status_code=status.HTTP_201_CREATED)
def create_diagram(
    payload: CreateDiagramRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
):
    diagram_id = f"diag-{uuid.uuid4().hex[:8]}"
    document = CanonicalUmlDocument(
        id=diagram_id,
        name=payload.name,
        description=payload.description,
        version=1,
        classes=[],
        relationships=[],
    )
    repo.save(document)
    return document


@router.get("", response_model=List[CanonicalUmlDocument])
def list_diagrams(repo: SqlAlchemyDiagramRepository = Depends(get_repository)):
    return repo.list_all()


@router.get("/{diagram_id}", response_model=CanonicalUmlDocument)
def get_diagram(
    diagram_id: str,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
):
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )
    return document


@router.delete("/{diagram_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_diagram(
    diagram_id: str,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
):
    deleted = repo.delete(diagram_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )
    return None


@router.post("/{diagram_id}/commands", response_model=CommandExecutionResponse)
def execute_command(
    diagram_id: str,
    payload: ExecuteCommandRequest,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
):
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )

    try:
        updated_doc = command_bus.dispatch(document, payload.command)
        repo.save(updated_doc)
        return CommandExecutionResponse(
            success=True,
            version=updated_doc.version,
            document=updated_doc,
            error=None,
        )
    except UmlValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal error processing command: {str(e)}",
        )

