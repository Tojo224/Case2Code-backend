import shutil
import tempfile
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


@pytest.fixture
def business_document() -> CanonicalUmlDocument:
    cliente = UmlClass(
        id="c-1",
        name="Cliente",
        attributes=[
            UmlAttribute(id="a-1", name="id", type="Long", primary_key=True, nullable=False),
            UmlAttribute(id="a-2", name="nombre", type="String", primary_key=False, nullable=False),
            UmlAttribute(id="a-3", name="telefono", type="String", primary_key=False, nullable=True),
        ],
    )
    reserva = UmlClass(
        id="c-2",
        name="Reserva",
        attributes=[
            UmlAttribute(id="a-4", name="id", type="Long", primary_key=True, nullable=False),
            UmlAttribute(id="a-5", name="fecha", type="LocalDate", primary_key=False, nullable=False),
        ],
    )
    rel = UmlRelationship(
        id="r-1",
        type=RelationshipTypeEnum.ONE_TO_MANY,
        source_class_id="c-1",
        target_class_id="c-2",
        source_cardinality="1",
        target_cardinality="*",
    )
    return CanonicalUmlDocument(
        id="diag-asst-1",
        name="PeluqueriaBusiness",
        description="Peluqueria test system with Business Assistant",
        version=1,
        classes=[cliente, reserva],
        relationships=[rel],
    )


def test_business_assistant_structure(business_document):
    generator = SpringBootGenerator()
    output_dir = Path(tempfile.mkdtemp(prefix="test_asst_struct_"))

    try:
        generator.generate(business_document, output_dir)

        asst_pkg = output_dir / "src" / "main" / "java" / "com" / "case2code" / "app" / "assistant"
        assert (asst_pkg / "model" / "BusinessAction.java").exists()
        assert (asst_pkg / "model" / "BusinessCommand.java").exists()
        assert (asst_pkg / "model" / "AssistantRequest.java").exists()
        assert (asst_pkg / "model" / "AssistantResponse.java").exists()
        assert (asst_pkg / "handler" / "EntityOperationHandler.java").exists()
        assert (asst_pkg / "handler" / "OperationRegistry.java").exists()
        assert (asst_pkg / "handler" / "ClienteOperationHandler.java").exists()
        assert (asst_pkg / "handler" / "ReservaOperationHandler.java").exists()
        assert (asst_pkg / "interpreter" / "BusinessCommandInterpreter.java").exists()
        assert (asst_pkg / "controller" / "AssistantController.java").exists()

        test_file = output_dir / "src" / "test" / "java" / "com" / "case2code" / "app" / "AssistantIntegrationTest.java"
        assert test_file.exists()
        test_code = test_file.read_text(encoding="utf-8")
        assert "/api/assistant/execute" in test_code
        assert "Cliente" in test_code
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def test_business_assistant_compilation_and_tests(business_document):
    generator = SpringBootGenerator()
    verifier = CompilationVerifier()
    output_dir = Path(tempfile.mkdtemp(prefix="test_asst_verify_"))

    try:
        generator.generate(business_document, output_dir)

        # Execute full mvnw.cmd test and mvnw.cmd package
        success, output = verifier.verify(output_dir)

        assert success is True, f"Maven compilation/tests failed:\n{output}"
        assert "BUILD SUCCESS" in output

        target_dir = output_dir / "target"
        jar_files = list(target_dir.glob("*.jar"))
        assert len(jar_files) >= 1
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

