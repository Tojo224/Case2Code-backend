import json
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
def multi_entity_document() -> CanonicalUmlDocument:
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
            UmlAttribute(id="a-6", name="monto", type="Double", primary_key=False, nullable=True),
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
        id="diag-meta-1",
        name="PeluqueriaApp",
        description="Peluqueria test system with metadata and OpenAPI",
        version=1,
        classes=[cliente, reserva],
        relationships=[rel],
    )


def test_metadata_files_generation(multi_entity_document):
    generator = SpringBootGenerator()
    output_dir = Path(tempfile.mkdtemp(prefix="test_meta_"))

    try:
        generator.generate(multi_entity_document, output_dir)

        # 1. Check root app-schema.json and resources app-schema.json
        root_schema_file = output_dir / "app-schema.json"
        res_schema_file = output_dir / "src" / "main" / "resources" / "app-schema.json"
        assert root_schema_file.exists()
        assert res_schema_file.exists()

        schema_data = json.loads(root_schema_file.read_text(encoding="utf-8"))
        assert schema_data["projectName"] == "PeluqueriaApp"
        assert len(schema_data["entities"]) == 2

        entity_names = {e["name"] for e in schema_data["entities"]}
        assert entity_names == {"Cliente", "Reserva"}

        cliente_entry = next(e for e in schema_data["entities"] if e["name"] == "Cliente")
        assert cliente_entry["tableName"] == "cliente"
        assert cliente_entry["primaryKey"]["name"] == "id"
        assert cliente_entry["primaryKey"]["type"] == "Long"
        assert any(f["name"] == "nombre" and f["required"] for f in cliente_entry["fields"])
        assert cliente_entry["endpoints"]["base"] == "/api/clientes"

        # 2. Check README.md
        readme_file = output_dir / "README.md"
        assert readme_file.exists()
        readme_text = readme_file.read_text(encoding="utf-8")
        assert "PeluqueriaApp" in readme_text
        assert "Swagger UI" in readme_text
        assert "/api/clientes" in readme_text
        assert "/api/reservas" in readme_text

        # 3. Check CorsConfig and OpenApiConfig
        config_dir = output_dir / "src" / "main" / "java" / "com" / "case2code" / "app" / "config"
        cors_file = config_dir / "CorsConfig.java"
        openapi_file = config_dir / "OpenApiConfig.java"
        assert cors_file.exists()
        assert openapi_file.exists()

        cors_code = cors_file.read_text(encoding="utf-8")
        assert "allowedOriginPatterns" in cors_code
        assert "registry.addMapping" in cors_code

        openapi_code = openapi_file.read_text(encoding="utf-8")
        assert "customOpenAPI" in openapi_code
        assert "PeluqueriaApp API" in openapi_code

        # 4. Check GlobalExceptionHandler and ResourceNotFoundException
        exc_dir = output_dir / "src" / "main" / "java" / "com" / "case2code" / "app" / "exception"
        assert (exc_dir / "ResourceNotFoundException.java").exists()
        assert (exc_dir / "ErrorResponse.java").exists()
        assert (exc_dir / "GlobalExceptionHandler.java").exists()

        # 5. Check Swagger annotations in controllers
        ctrl_dir = output_dir / "src" / "main" / "java" / "com" / "case2code" / "app" / "controller"
        cliente_ctrl = (ctrl_dir / "ClienteController.java").read_text(encoding="utf-8")
        assert '@Tag(name = "Cliente"' in cliente_ctrl
        assert '@Operation(summary = "Get all Cliente records")' in cliente_ctrl

    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def test_generated_project_with_openapi_compiles_and_passes_tests(multi_entity_document):
    generator = SpringBootGenerator()
    verifier = CompilationVerifier()
    output_dir = Path(tempfile.mkdtemp(prefix="test_openapi_verify_"))

    try:
        generator.generate(multi_entity_document, output_dir)

        # Run full compilation verification (mvnw.cmd test and mvnw.cmd package)
        success, output = verifier.verify(output_dir)

        assert success is True, f"Compilation failed:\n{output}"
        assert "BUILD SUCCESS" in output

        target_dir = output_dir / "target"
        jar_files = list(target_dir.glob("*.jar"))
        assert len(jar_files) >= 1
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

