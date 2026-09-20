from typing import List, Optional
from sqlalchemy.orm import Session

from app.application.ports.diagram_repository import DiagramRepositoryPort
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.infrastructure.persistence.models import DiagramModel


class SqlAlchemyDiagramRepository(DiagramRepositoryPort):
    def __init__(self, db: Session):
        self.db = db

    def save(self, document: CanonicalUmlDocument) -> CanonicalUmlDocument:
        # Convert document to JSON-compatible dict for JSONB storage
        doc_dict = document.model_dump(mode="json")
        
        existing = self.db.query(DiagramModel).filter(DiagramModel.id == document.id).first()
        if existing:
            existing.name = document.name
            existing.description = document.description
            existing.version = document.version
            existing.data = doc_dict
            existing.updated_at = document.updated_at
        else:
            new_record = DiagramModel(
                id=document.id,
                name=document.name,
                description=document.description,
                version=document.version,
                data=doc_dict,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
            self.db.add(new_record)
        
        self.db.commit()
        return document

    def get_by_id(self, diagram_id: str) -> Optional[CanonicalUmlDocument]:
        record = self.db.query(DiagramModel).filter(DiagramModel.id == diagram_id).first()
        if not record:
            return None
        return CanonicalUmlDocument.model_validate(record.data)

    def list_all(self) -> List[CanonicalUmlDocument]:
        records = self.db.query(DiagramModel).order_by(DiagramModel.updated_at.desc()).all()
        return [CanonicalUmlDocument.model_validate(r.data) for r in records]

    def delete(self, diagram_id: str) -> bool:
        record = self.db.query(DiagramModel).filter(DiagramModel.id == diagram_id).first()
        if not record:
            return False
        self.db.delete(record)
        self.db.commit()
        return True

