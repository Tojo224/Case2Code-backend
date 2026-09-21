import shutil
import tempfile
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.models.user import User
from app.infrastructure.codegen.compilation_verifier import (
    CompilationError,
    CompilationVerifier,
)
from app.infrastructure.codegen.spring_boot_generator import SpringBootGenerator
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository
from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
from app.presentation.routes.auth import get_current_user_optional, get_user_repo

router = APIRouter(prefix="/diagrams", tags=["Code Generator"])


def get_repository(db: Session = Depends(get_db)) -> SqlAlchemyDiagramRepository:
    return SqlAlchemyDiagramRepository(db)


@router.post("/{diagram_id}/generate")
def generate_backend(
    diagram_id: str,
    verify: bool = Query(default=False, description="Run mvnw test and mvnw package before returning"),
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repo),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
        )

    # Verify authorization for private projects
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

    if not document.classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Diagram must contain at least one class to generate backend code.",
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="case2code_gen_"))
    generator = SpringBootGenerator()
    project_dir = generator.generate(document, temp_dir)

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
    )
