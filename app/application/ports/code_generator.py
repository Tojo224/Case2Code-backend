from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from app.domain.models.canonical_uml import CanonicalUmlDocument


class SqlDialect(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"
    SQLSERVER = "sqlserver"


class CodeGeneratorPort(ABC):
    @abstractmethod
    def generate(self, document: CanonicalUmlDocument, output_dir: Path) -> Path:
        """Generates a complete, runnable Spring Boot project from a CanonicalUmlDocument.
        Returns the path to the generated project directory.
        """
        pass


class SqlGeneratorPort(ABC):
    @abstractmethod
    def generate_sql(self, document: CanonicalUmlDocument, dialect: SqlDialect = SqlDialect.POSTGRESQL) -> str:
        """Generates a complete SQL DDL script for the given dialect from a CanonicalUmlDocument."""
        pass


class MobileGeneratorPort(ABC):
    @abstractmethod
    def generate_mobile(self, document: CanonicalUmlDocument, output_dir: Path) -> Path:
        """Generates a complete, runnable Flutter mobile project with a generic metadata-driven frontend.
        Returns the path to the generated project directory.
        """
        pass



