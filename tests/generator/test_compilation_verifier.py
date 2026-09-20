import os
import shutil
from pathlib import Path
import pytest

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    UmlAttribute,
    UmlClass,
)
from app.infrastructure.codegen.compilation_verifier import CompilationVerifier
from app.infrastructure.codegen.spring_boot_generator import SpringBootGenerator


@pytest.mark.slow
def test_generated_project_mvnw_test_and_package():
    """Generates a minimal Cliente project and executes mvnw.cmd test and mvnw.cmd package.
    Ensures zero errors and real successful compilation and artifact generation.
    """
    doc = CanonicalUmlDocument(
        id="diag-verify-cliente",
        name="Peluqueria",
        classes=[
            UmlClass(
                id="c-cliente",
                name="Cliente",
                attributes=[
                    UmlAttribute(
                        id="a-1",
                        name="id",
                        type="Long",
                        primary_key=True,
                        nullable=False,
                    ),
                    UmlAttribute(
                        id="a-2",
                        name="nombre",
                        type="String",
                        nullable=False,
                    ),
                    UmlAttribute(
                        id="a-3",
                        name="telefono",
                        type="String",
                        nullable=True,
                    ),
                ],
            )
        ],
    )

    output_dir = Path("temp_output") / "peluqueria_verify"
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        generator = SpringBootGenerator()
        generator.generate(doc, output_dir)

        # Verify using mvnw.cmd test and mvnw.cmd package
        success, message = CompilationVerifier.verify(output_dir, timeout_seconds=300)
        assert success is True
        assert "BUILD SUCCESS" in message

        # Verify the jar was built in target/
        target_dir = output_dir / "target"
        assert target_dir.exists()
        jar_files = list(target_dir.glob("*.jar"))
        assert len(jar_files) >= 1
    finally:
        # Cleanup
        shutil.rmtree(output_dir, ignore_errors=True)

