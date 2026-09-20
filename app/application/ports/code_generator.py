from abc import ABC, abstractmethod
from pathlib import Path
from app.domain.models.canonical_uml import CanonicalUmlDocument


class CodeGeneratorPort(ABC):
    @abstractmethod
    def generate(self, document: CanonicalUmlDocument, output_dir: Path) -> Path:
        """Generates a complete, runnable Spring Boot project from a CanonicalUmlDocument.
        Returns the path to the generated project directory.
        """
        pass

