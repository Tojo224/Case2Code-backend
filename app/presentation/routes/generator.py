import shutil
import tempfile
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.infrastructure.codegen.compilation_verifier import (
    CompilationError,
    CompilationVerifier,
)
from app.infrastructure.codegen.spring_boot_generator import SpringBootGenerator
from app.infrastructure.persistence.diagram_repository import SqlAlchemyDiagramRepository

router = APIRouter(prefix="/diagrams", tags=["Code Generator"])


def get_repository(db: Session = Depends(get_db)) -> SqlAlchemyDiagramRepository:
    return SqlAlchemyDiagramRepository(db)


@router.post("/{diagram_id}/generate")
def generate_backend(
    diagram_id: str,
    verify: bool = Query(default=False, description="Run mvnw test and mvnw package before returning"),
    repo: SqlAlchemyDiagramRepository = Depends(get_repository),
):
    document = repo.get_by_id(diagram_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagram '{diagram_id}' not found.",
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

