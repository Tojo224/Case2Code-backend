import uuid
from typing import List, Optional
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.domain.models.user import CollaboratorRole, ProjectCollaborator, User
from app.infrastructure.persistence.models import DiagramCollaboratorModel, DiagramModel, UserModel


class SqlAlchemyUserRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_user(
        self,
        email: str,
        name: str,
        password: str,
        avatar_color: str = "#3B82F6",
        user_id: Optional[str] = None,
    ) -> User:
        uid = user_id or f"usr-{uuid.uuid4().hex[:8]}"
        hashed = hash_password(password)
        db_user = UserModel(
            id=uid,
            email=email.strip().lower(),
            name=name.strip(),
            hashed_password=hashed,
            avatar_color=avatar_color,
        )
        self.db.add(db_user)
        self.db.commit()
        self.db.refresh(db_user)
        return User(
            id=db_user.id,
            email=db_user.email,
            name=db_user.name,
            avatar_color=db_user.avatar_color,
            created_at=db_user.created_at,
        )

    def get_by_id(self, user_id: str) -> Optional[UserModel]:
        return self.db.query(UserModel).filter(UserModel.id == user_id).first()

    def get_by_email(self, email: str) -> Optional[UserModel]:
        return self.db.query(UserModel).filter(UserModel.email == email.strip().lower()).first()

    def list_users(self) -> List[User]:
        users = self.db.query(UserModel).all()
        return [
            User(
                id=u.id,
                email=u.email,
                name=u.name,
                avatar_color=u.avatar_color,
                created_at=u.created_at,
            )
            for u in users
        ]

    def add_collaborator(
        self,
        diagram_id: str,
        user_id: str,
        role: CollaboratorRole = CollaboratorRole.EDITOR,
    ) -> ProjectCollaborator:
        existing = (
            self.db.query(DiagramCollaboratorModel)
            .filter(
                DiagramCollaboratorModel.diagram_id == diagram_id,
                DiagramCollaboratorModel.user_id == user_id,
            )
            .first()
        )
        if existing:
            existing.role = role.value if isinstance(role, CollaboratorRole) else str(role)
            self.db.commit()
            self.db.refresh(existing)
            db_collab = existing
        else:
            collab_id = f"collab-{uuid.uuid4().hex[:8]}"
            db_collab = DiagramCollaboratorModel(
                id=collab_id,
                diagram_id=diagram_id,
                user_id=user_id,
                role=role.value if isinstance(role, CollaboratorRole) else str(role),
            )
            self.db.add(db_collab)
            self.db.commit()
            self.db.refresh(db_collab)

        user = self.get_by_id(user_id)
        return ProjectCollaborator(
            id=db_collab.id,
            diagram_id=db_collab.diagram_id,
            user_id=db_collab.user_id,
            role=CollaboratorRole(db_collab.role),
            user_email=user.email if user else None,
            user_name=user.name if user else None,
            avatar_color=user.avatar_color if user else None,
            created_at=db_collab.created_at,
        )

    def list_collaborators(self, diagram_id: str) -> List[ProjectCollaborator]:
        collabs = (
            self.db.query(DiagramCollaboratorModel)
            .filter(DiagramCollaboratorModel.diagram_id == diagram_id)
            .all()
        )
        result = []
        for c in collabs:
            u = c.user
            result.append(
                ProjectCollaborator(
                    id=c.id,
                    diagram_id=c.diagram_id,
                    user_id=c.user_id,
                    role=CollaboratorRole(c.role),
                    user_email=u.email if u else None,
                    user_name=u.name if u else None,
                    avatar_color=u.avatar_color if u else None,
                    created_at=c.created_at,
                )
            )
        return result

    def remove_collaborator(self, diagram_id: str, user_id: str) -> bool:
        collab = (
            self.db.query(DiagramCollaboratorModel)
            .filter(
                DiagramCollaboratorModel.diagram_id == diagram_id,
                DiagramCollaboratorModel.user_id == user_id,
            )
            .first()
        )
        if not collab:
            return False
        self.db.delete(collab)
        self.db.commit()
        return True

    def get_user_role(self, diagram_id: str, user_id: str) -> Optional[CollaboratorRole]:
        # Check if user is owner
        diagram = self.db.query(DiagramModel).filter(DiagramModel.id == diagram_id).first()
        if diagram and diagram.owner_id == user_id:
            return CollaboratorRole.OWNER

        collab = (
            self.db.query(DiagramCollaboratorModel)
            .filter(
                DiagramCollaboratorModel.diagram_id == diagram_id,
                DiagramCollaboratorModel.user_id == user_id,
            )
            .first()
        )
        if collab:
            return CollaboratorRole(collab.role)
        return None
