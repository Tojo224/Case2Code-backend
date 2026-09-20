import shutil
from pathlib import Path
import pytest

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
)
from app.infrastructure.codegen.compilation_verifier import CompilationVerifier
from app.infrastructure.codegen.spring_boot_generator import SpringBootGenerator


@pytest.mark.slow
def test_relationship_project_mvnw_test_and_package():
    """Generates a multi-entity project (Cliente 1:N Reserva) with foreign key mappings
    and executes mvnw.cmd test and mvnw.cmd package to verify complete relational integrity.
    """
    doc = CanonicalUmlDocument(
        id="diag-rel-compile",
        name="PeluqueriaMulti",
        classes=[
            UmlClass(
                id="c-cliente",
                name="Cliente",
                attributes=[
                    UmlAttribute(id="a-1", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="a-2", name="nombre", type="String", nullable=False),
                    UmlAttribute(id="a-3", name="telefono", type="String"),
                ],
            ),
            UmlClass(
                id="c-reserva",
                name="Reserva",
                attributes=[
                    UmlAttribute(id="a-4", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="a-5", name="fecha", type="String", nullable=False),
                    UmlAttribute(id="a-6", name="hora", type="String"),
                ],
            ),
        ],
        relationships=[
            UmlRelationship(
                id="rel-1",
                name="reservas",
                type=RelationshipTypeEnum.ONE_TO_MANY,
                source_class_id="c-cliente",
                target_class_id="c-reserva",
                source_cardinality="1",
                target_cardinality="*",
            )
        ],
    )

    output_dir = Path("temp_output") / "peluqueria_rel_verify"
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        generator = SpringBootGenerator()
        generator.generate(doc, output_dir)

        # Execute compilation, unit tests, relationship integration test, and packaging
        success, message = CompilationVerifier.verify(output_dir, timeout_seconds=600)
        assert success is True
        assert "BUILD SUCCESS" in message

        # Verify packaged artifact exists
        target_dir = output_dir / "target"
        assert target_dir.exists()
        jar_files = list(target_dir.glob("*.jar"))
        assert len(jar_files) >= 1
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
