import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
)
from app.infrastructure.codegen.flutter_generator import FlutterGenerator
from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
from app.presentation.routes.generator import generate_mobile_app


def _build_sample_vet_document() -> CanonicalUmlDocument:
    return CanonicalUmlDocument(
        id="diag-vet-mobile",
        name="Veterinaria Mobile",
        description="Veterinary mobile management app",
        version=2,
        classes=[
            UmlClass(
                id="cls-persona",
                name="Persona",
                attributes=[
                    UmlAttribute(id="a1", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a2", name="nombreCompleto", type="String", nullable=False),
                ],
            ),
            UmlClass(
                id="cls-cliente",
                name="Cliente",
                attributes=[
                    UmlAttribute(id="a3", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a4", name="activo", type="Boolean", nullable=False),
                ],
            ),
            UmlClass(
                id="cls-mascota",
                name="Mascota",
                attributes=[
                    UmlAttribute(id="a5", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a6", name="nombre", type="String", nullable=False),
                    UmlAttribute(id="a7", name="peso", type="Double", nullable=True),
                    UmlAttribute(id="a8", name="fechaNacimiento", type="LocalDate", nullable=True),
                ],
            ),
        ],
        relationships=[
            UmlRelationship(
                id="rel-inh",
                type=RelationshipTypeEnum.INHERITANCE,
                source_class_id="cls-cliente",
                target_class_id="cls-persona",
            ),
            UmlRelationship(
                id="rel-1ton",
                type=RelationshipTypeEnum.ONE_TO_MANY,
                source_class_id="cls-cliente",
                target_class_id="cls-mascota",
                source_cardinality="1",
                target_cardinality="*",
            ),
        ],
    )


def test_flutter_generator_creates_complete_project_tree():
    doc = _build_sample_vet_document()
    gen = FlutterGenerator()
    temp_root = Path(tempfile.mkdtemp(prefix="test_flutter_tree_"))
    try:
        out_dir = gen.generate_mobile(doc, temp_root / "flutter_out")

        # Root files
        assert (out_dir / "pubspec.yaml").exists()
        assert (out_dir / "analysis_options.yaml").exists()
        assert (out_dir / "README.md").exists()
        assert (out_dir / "assets" / "app-schema.json").exists()

        pubspec = (out_dir / "pubspec.yaml").read_text(encoding="utf-8")
        assert "name: veterinaria_mobile_mobile" in pubspec
        assert "http: ^1.2.0" in pubspec

        # Schema metadata JSON
        schema_json = json.loads((out_dir / "assets" / "app-schema.json").read_text(encoding="utf-8"))
        assert schema_json["projectName"] == "Veterinaria Mobile"
        assert len(schema_json["entities"]) == 3

        # Core Dart files
        assert (out_dir / "lib" / "main.dart").exists()
        assert (out_dir / "lib" / "config" / "app_schema.dart").exists()
        assert (out_dir / "lib" / "services" / "api_service.dart").exists()

        # Typed Domain Models
        assert (out_dir / "lib" / "models" / "persona.dart").exists()
        assert (out_dir / "lib" / "models" / "cliente.dart").exists()
        assert (out_dir / "lib" / "models" / "mascota.dart").exists()

        # Check inherited field in Cliente model and app_schema.dart
        cliente_dart = (out_dir / "lib" / "models" / "cliente.dart").read_text(encoding="utf-8")
        assert "final String nombreCompleto;" in cliente_dart
        assert "final bool activo;" in cliente_dart

        # Check foreign key in Mascota model
        mascota_dart = (out_dir / "lib" / "models" / "mascota.dart").read_text(encoding="utf-8")
        assert "final dynamic clienteId;" in mascota_dart
        assert "data['cliente'] = {'id': clienteId};" in mascota_dart

        # Check generic screens
        for screen_name in (
            "home_screen.dart",
            "entity_list_screen.dart",
            "entity_detail_screen.dart",
            "entity_form_screen.dart",
            "assistant_screen.dart",
        ):
            assert (out_dir / "lib" / "screens" / screen_name).exists()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_generate_mobile_endpoint_returns_valid_zip(db_session, diagram_repo):
    user_repo = SqlAlchemyUserRepository(db_session)
    doc = _build_sample_vet_document()
    diagram_repo.save(doc)

    res = generate_mobile_app(
        diagram_id=doc.id,
        repo=diagram_repo,
        user_repo=user_repo,
        current_user=None,
    )
    assert res.media_type == "application/zip"
    zip_path = Path(res.path)
    assert zip_path.exists()

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            assert "pubspec.yaml" in names
            assert "lib/main.dart" in names
            assert "lib/config/app_schema.dart" in names
            assert "lib/models/mascota.dart" in names
            assert "lib/screens/entity_form_screen.dart" in names
    finally:
        zip_path.unlink(missing_ok=True)
