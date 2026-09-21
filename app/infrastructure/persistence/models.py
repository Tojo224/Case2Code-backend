from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class UserModel(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    avatar_color = Column(String(32), nullable=False, default="#3B82F6")
    
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    reset_token = Column(String(255), nullable=True, index=True)
    reset_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    collaborations = relationship(
        "DiagramCollaboratorModel",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class DiagramModel(Base):
    __tablename__ = "diagrams"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    
    # Ownership
    owner_id = Column(String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # JSONB in PostgreSQL, standard JSON in SQLite
    data = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    collaborators = relationship(
        "DiagramCollaboratorModel",
        back_populates="diagram",
        cascade="all, delete-orphan",
    )


class DiagramCollaboratorModel(Base):
    __tablename__ = "diagram_collaborators"

    id = Column(String(64), primary_key=True, index=True)
    diagram_id = Column(
        String(64),
        ForeignKey("diagrams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(32), nullable=False, default="EDITOR")
    
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    user = relationship("UserModel", back_populates="collaborations")
    diagram = relationship("DiagramModel", back_populates="collaborators")
