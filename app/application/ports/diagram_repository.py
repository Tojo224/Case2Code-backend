from abc import ABC, abstractmethod
from typing import List, Optional
from app.domain.models.canonical_uml import CanonicalUmlDocument


class DiagramRepositoryPort(ABC):
    @abstractmethod
    def save(self, document: CanonicalUmlDocument) -> CanonicalUmlDocument:
        """Save or update a canonical UML document."""
        pass

    @abstractmethod
    def get_by_id(self, diagram_id: str) -> Optional[CanonicalUmlDocument]:
        """Retrieve a canonical UML document by ID."""
        pass

    @abstractmethod
    def list_all(self) -> List[CanonicalUmlDocument]:
        """List all canonical UML documents."""
        pass

    @abstractmethod
    def list_for_user(self, user_id: str) -> List[CanonicalUmlDocument]:
        """List all canonical UML documents owned by or shared with a user."""
        pass

    @abstractmethod
    def delete(self, diagram_id: str) -> bool:
        """Delete a canonical UML document by ID."""
        pass

