import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from app.application.ports.code_generator import SqlDialect
from app.core.database import get_db
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.user import User
from app.infrastructure.codegen.compilation_verifier import (
    CompilationError,
    CompilationVerifier,
)
from app.infrastructure.codegen.flutter_generator import FlutterGenerator
from app.infrastructure.codegen.spring_boot_generator import SpringBootGenerator
from app.infrastructure.codegen.sql_generator import SqlGenerator
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
from app.presentation.routes.auth import get_current_user_optional, get_user_repo

router = APIRouter(prefix="/diagrams", tags=["Code Generator"])


def _delete_file(path: str) -> None:
    """Remove a generated archive once it has been sent."""
    Path(path).unlink(missing_ok=True)


def get_repository(db: Session = Depends(get_db)) -> SqlAlchemyDiagramRepository:
    return SqlAlchemyDiagramRepository(db)


def _get_authorized_diagram(
    diagram_id: str,
    repo: SqlAlchemyDiagramRepository,
    user_repo: SqlAlchemyUserRepository,
    current_user: Optional[User],
) -> CanonicalUmlDocument:
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )

    if document.owner_id:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Inicia sesión para generar código de este proyecto.",
            )
        role = user_repo.get_user_role(diagram_id, current_user.id)
        if not role and document.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para acceder al código de este proyecto.",
            )

    return document


@router.get("/{diagram_id}/sql")
def preview_sql_script(
    diagram_id: str,
    dialect: SqlDialect = Query(
        default=SqlDialect.POSTGRESQL,
        description="Target SQL dialect (postgresql, mysql, sqlite, sqlserver)",
    ),
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = _get_authorized_diagram(diagram_id, repo, user_repo, current_user)
    sql_generator = SqlGenerator()
    sql_content = sql_generator.generate_sql(document, dialect=dialect)
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", (document.name or "schema").strip()) or "schema"

    return {
        "diagram_id": diagram_id,
        "diagram_name": document.name,
        "dialect": dialect.value,
        "filename": f"{safe_name}_{dialect.value}.sql",
        "sql": sql_content,
    }


@router.get("/{diagram_id}/export/sql")
def export_sql_script(
    diagram_id: str,
    dialect: SqlDialect = Query(
        default=SqlDialect.POSTGRESQL,
        description="Target SQL dialect (postgresql, mysql, sqlite, sqlserver)",
    ),
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = _get_authorized_diagram(diagram_id, repo, user_repo, current_user)
    if not document.classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Diagram must contain at least one class to export SQL.",
        )

    sql_generator = SqlGenerator()
    sql_content = sql_generator.generate_sql(document, dialect=dialect)
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", (document.name or "schema").strip()) or "schema"
    filename = f"{safe_name}_{dialect.value}.sql"

    return Response(
        content=sql_content,
        media_type="application/sql; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/{diagram_id}/generate")
def generate_backend(
    diagram_id: str,
    verify: bool = Query(default=False, description="Run mvnw test and mvnw package before returning"),
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = _get_authorized_diagram(diagram_id, repo, user_repo, current_user)

    if not document.classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Diagram must contain at least one class to generate backend code.",
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="case2code_gen_"))
    generator = SpringBootGenerator()
    project_dir = generator.generate(document, temp_dir)

    # Also bundle generated PostgreSQL schema.sql in the root of the project
    sql_generator = SqlGenerator()
    (project_dir / "schema.sql").write_text(
        sql_generator.generate_sql(document, dialect=SqlDialect.POSTGRESQL),
        encoding="utf-8",
    )

    if verify:
        try:
            CompilationVerifier.verify(project_dir)
        except CompilationError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Generated code failed compilation verification: {str(e)}\nStdout:\n{e.stdout}\nStderr:\n{e.stderr}",
            )
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Compilation verification error: {str(e)}",
            )

    # Create zip archive of the generated project
    zip_base_name = temp_dir.parent / f"{document.name or 'case2code'}_backend"
    zip_file = shutil.make_archive(str(zip_base_name), "zip", project_dir)

    # Clean up unzipped directory
    shutil.rmtree(temp_dir, ignore_errors=True)

    return FileResponse(
        zip_file,
        media_type="application/zip",
        filename=f"{Path(zip_file).name}",
        background=BackgroundTask(_delete_file, zip_file),
    )


@router.post("/{diagram_id}/generate/mobile")
def generate_mobile_app(
    diagram_id: str,
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = _get_authorized_diagram(diagram_id, repo, user_repo, current_user)

    if not document.classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Diagram must contain at least one class to generate mobile application code.",
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="case2code_mobile_"))
    generator = FlutterGenerator()
    project_dir = generator.generate_mobile(document, temp_dir)

    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", (document.name or "case2code").strip()) or "case2code"
    zip_base_name = temp_dir.parent / f"{safe_name}_flutter_mobile"
    zip_file = shutil.make_archive(str(zip_base_name), "zip", project_dir)

    shutil.rmtree(temp_dir, ignore_errors=True)

    return FileResponse(
        zip_file,
        media_type="application/zip",
        filename=f"{Path(zip_file).name}",
        background=BackgroundTask(_delete_file, zip_file),
    )

