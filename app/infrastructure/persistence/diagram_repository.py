from typing import List, Optional
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.application.ports.diagram_repository import DiagramRepositoryPort
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.infrastructure.persistence.models import DiagramCollaboratorModel, DiagramModel


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
            if document.owner_id is not None:
                existing.owner_id = document.owner_id
            existing.data = doc_dict
            existing.updated_at = document.updated_at
        else:
            new_record = DiagramModel(
                id=document.id,
                name=document.name,
                description=document.description,
                version=document.version,
                owner_id=document.owner_id,
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
        doc = CanonicalUmlDocument.model_validate(record.data)
        doc.owner_id = record.owner_id
        return doc

    def list_all(self) -> List[CanonicalUmlDocument]:
        records = self.db.query(DiagramModel).order_by(DiagramModel.updated_at.desc()).all()
        result = []
        for r in records:
            doc = CanonicalUmlDocument.model_validate(r.data)
            doc.owner_id = r.owner_id
            result.append(doc)
        return result

    def list_public(self) -> List[CanonicalUmlDocument]:
        records = (
            self.db.query(DiagramModel)
            .filter(DiagramModel.owner_id.is_(None))
            .order_by(DiagramModel.updated_at.desc())
            .all()
        )
        result = []
        for r in records:
            doc = CanonicalUmlDocument.model_validate(r.data)
            doc.owner_id = r.owner_id
            result.append(doc)
        return result

    def list_for_user(self, user_id: str) -> List[CanonicalUmlDocument]:
        collab_diagram_ids = (
            select(DiagramCollaboratorModel.diagram_id)
            .where(DiagramCollaboratorModel.user_id == user_id)
        )
        records = (
            self.db.query(DiagramModel)
            .filter(
                or_(
                    DiagramModel.owner_id == user_id,
                    DiagramModel.id.in_(collab_diagram_ids),
                )
            )
            .order_by(DiagramModel.updated_at.desc())
            .all()
        )
        result = []
        for r in records:
            doc = CanonicalUmlDocument.model_validate(r.data)
            doc.owner_id = r.owner_id
            result.append(doc)
        return result

    def delete(self, diagram_id: str) -> bool:
        record = self.db.query(DiagramModel).filter(DiagramModel.id == diagram_id).first()
        if not record:
            return False
        self.db.delete(record)
        self.db.commit()
        return True
