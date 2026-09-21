import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
)
from app.domain.models.commands import CommandTypeEnum
from app.infrastructure.ai.ai_provider import AiProviderPort, GeminiAiProvider, RuleBasedAiProvider
from app.infrastructure.ai.ai_uml_interpreter import AiUmlInterpreter
from app.main import app


@pytest.fixture
def sample_document() -> CanonicalUmlDocument:
    return CanonicalUmlDocument(
        id="diag-test-ai",
        name="AI Test Diagram",
        version=1,
        classes=[
            UmlClass(
                id="cls-cliente-1",
                name="Cliente",
                attributes=[
                    UmlAttribute(id="attr-1", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="attr-2", name="nombre", type="String", primary_key=False, nullable=True),
                ],
            ),
            UmlClass(
                id="cls-reserva-2",
                name="Reserva",
                attributes=[
                    UmlAttribute(id="attr-3", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="attr-4", name="fecha", type="LocalDate", primary_key=False, nullable=True),
                ],
            ),
        ],
        relationships=[],
    )


@pytest.mark.asyncio
async def test_rule_based_create_class(sample_document):
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(
        "Crea la clase Producto", sample_document
    )

    assert len(cmds) == 1
    assert cmds[0].command_type == CommandTypeEnum.CREATE_CLASS
    assert cmds[0].name == "Producto"
    assert any(c.name == "Producto" for c in doc.classes)
    assert "Producto" in reply


@pytest.mark.asyncio
async def test_rule_based_create_class_with_attributes(sample_document):
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(
        "Crea la clase Factura con atributos id de tipo Long, total de tipo Double y fecha de tipo LocalDate",
        sample_document,
    )

    assert len(cmds) == 3
    factura_cls = next(c for c in doc.classes if c.name == "Factura")
    assert len(factura_cls.attributes) == 3
    attr_names = {a.name: a.type for a in factura_cls.attributes}
    assert attr_names["id"] == "Long"
    assert attr_names["total"] == "Double"
    assert attr_names["fecha"] == "LocalDate"
    assert any(a.primary_key for a in factura_cls.attributes if a.name == "id")


@pytest.mark.asyncio
async def test_rule_based_add_attribute(sample_document):
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(
        "Agrega el atributo telefono de tipo String a la clase Cliente", sample_document
    )

    assert len(cmds) == 1
    assert cmds[0].command_type == CommandTypeEnum.ADD_ATTRIBUTE
    cliente_cls = next(c for c in doc.classes if c.id == "cls-cliente-1")
    assert any(a.name == "telefono" and a.type == "String" for a in cliente_cls.attributes)


@pytest.mark.asyncio
async def test_rule_based_rename_class(sample_document):
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(
        "Renombra la clase Cliente a Persona", sample_document
    )

    assert len(cmds) == 1
    assert cmds[0].command_type == CommandTypeEnum.RENAME_CLASS
    assert any(c.name == "Persona" for c in doc.classes)
    assert not any(c.name == "Cliente" for c in doc.classes)


@pytest.mark.asyncio
async def test_rule_based_delete_attribute(sample_document):
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(
        "Elimina el atributo nombre de la clase Cliente", sample_document
    )

    assert len(cmds) == 1
    assert cmds[0].command_type == CommandTypeEnum.DELETE_ATTRIBUTE
    cliente_cls = next(c for c in doc.classes if c.id == "cls-cliente-1")
    assert not any(a.name == "nombre" for a in cliente_cls.attributes)


@pytest.mark.asyncio
async def test_rule_based_create_relationship_one_to_many(sample_document):
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(
        "Relaciona Cliente con Reserva de uno a muchos", sample_document
    )

    assert len(cmds) == 1
    assert cmds[0].command_type == CommandTypeEnum.CREATE_RELATIONSHIP
    assert len(doc.relationships) == 1
    rel = doc.relationships[0]
    assert rel.type == RelationshipTypeEnum.ONE_TO_MANY
    assert rel.source_class_id == "cls-cliente-1"
    assert rel.target_class_id == "cls-reserva-2"
    assert rel.source_cardinality == "1"
    assert rel.target_cardinality == "*"


@pytest.mark.asyncio
async def test_rule_based_delete_relationship(sample_document):
    sample_document.relationships.append(
        UmlRelationship(
            id="rel-123",
            type=RelationshipTypeEnum.ONE_TO_MANY,
            source_class_id="cls-cliente-1",
            target_class_id="cls-reserva-2",
        )
    )
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(
        "Elimina la relacion entre Cliente y Reserva", sample_document
    )

    assert len(cmds) == 1
    assert cmds[0].command_type == CommandTypeEnum.DELETE_RELATIONSHIP
    assert len(doc.relationships) == 0


@pytest.mark.asyncio
async def test_mocked_gemini_provider(sample_document):
    mock_provider = GeminiAiProvider(api_key="mock-key")
    mock_response = [
        {
            "command_type": "CREATE_CLASS",
            "class_id": "cls-servicio-99",
            "name": "Servicio",
            "position": {"x": 200, "y": 200},
        },
        {
            "command_type": "ADD_ATTRIBUTE",
            "class_id": "cls-servicio-99",
            "name": "costo",
            "type": "Double",
            "primary_key": False,
            "nullable": True,
        },
    ]

    with patch.object(mock_provider, "generate_commands", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_response
        interpreter = AiUmlInterpreter(provider=mock_provider)

        doc, cmds, reply = await interpreter.interpret_and_execute(
            "Crea el servicio con costo", sample_document
        )

        assert len(cmds) == 2
        assert any(c.name == "Servicio" for c in doc.classes)
        servicio_cls = next(c for c in doc.classes if c.name == "Servicio")
        assert any(a.name == "costo" and a.type == "Double" for a in servicio_cls.attributes)


def test_assistant_api_endpoint():
    client = TestClient(app)

    # 1. Create diagram
    create_resp = client.post("/api/diagrams", json={"name": "Diagram Assistant Test"})
    assert create_resp.status_code == 201
    diag_id = create_resp.json()["id"]

    # 2. Call assistant endpoint to create class
    prompt_resp = client.post(
        f"/api/diagrams/{diag_id}/assistant",
        json={"prompt": "Crea una clase Vehiculo con atributos id de tipo Long y placa de tipo String"},
    )
    assert prompt_resp.status_code == 200
    data = prompt_resp.json()
    assert data["success"] is True
    assert len(data["executed_commands"]) >= 1
    assert any(c["name"] == "Vehiculo" for c in data["document"]["classes"])

    # 3. Call assistant endpoint to add attribute
    add_attr_resp = client.post(
        f"/api/diagrams/{diag_id}/assistant",
        json={"prompt": "Agrega el atributo modelo de tipo String a la clase Vehiculo"},
    )
    assert add_attr_resp.status_code == 200
    data2 = add_attr_resp.json()
    assert data2["success"] is True
    vehiculo = next(c for c in data2["document"]["classes"] if c["name"] == "Vehiculo")
    assert any(a["name"] == "modelo" for a in vehiculo["attributes"])


@pytest.mark.asyncio
async def test_multi_relationship_role_auto_resolution(sample_document):
    """Verifies that when AI replicates a sketch with multiple relationships between the same two tables
    without specifying roles, the interpreter automatically resolves distinct roles without collision.
    """
    class MockSketchProvider(AiProviderPort):
        async def generate_commands(self, prompt, current_uml, image_data=None):
            return [
                {
                    "command_type": "CREATE_RELATIONSHIP",
                    "source_class_id": "cls-cliente-1",
                    "target_class_id": "cls-reserva-2",
                    "type": "ONE_TO_MANY",
                    "source_cardinality": "1",
                    "target_cardinality": "*",
                },
                {
                    "command_type": "CREATE_RELATIONSHIP",
                    "source_class_id": "cls-cliente-1",
                    "target_class_id": "cls-reserva-2",
                    "type": "ONE_TO_MANY",
                    "source_cardinality": "1",
                    "target_cardinality": "*",
                },
                {
                    "command_type": "CREATE_RELATIONSHIP",
                    "source_class_id": "cls-cliente-1",
                    "target_class_id": "cls-reserva-2",
                    "type": "ONE_TO_MANY",
                    "source_cardinality": "1",
                    "target_cardinality": "*",
                },
            ]

    interpreter = AiUmlInterpreter(provider=MockSketchProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(
        "Replicar boceto con 3 relaciones entre Cliente y Reserva", sample_document
    )

    assert len(cmds) == 3
    assert len(doc.relationships) == 3
    # Check that all 3 have distinct roles so there is zero collision in SQL/JPA
    roles = [r.target_role for r in doc.relationships]
    assert len(set(roles)) == 3
    assert "rol_2" in roles
    assert "rol_3" in roles
    assert "Roles asignados" in reply


@pytest.mark.asyncio
async def test_rule_based_sql_ddl_multi_table_with_fk(sample_document):
    sql_script = """
    CREATE TABLE Categoria (
        id BIGINT PRIMARY KEY,
        nombre VARCHAR(100) NOT NULL
    );
    CREATE TABLE Articulo (
        id BIGINT PRIMARY KEY,
        titulo VARCHAR(150),
        precio DECIMAL(10, 2),
        categoria_id BIGINT,
        CONSTRAINT fk_art_cat FOREIGN KEY (categoria_id) REFERENCES Categoria(id)
    );
    """
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(sql_script, sample_document)

    # Must have created Categoria, Articulo, their attributes, and the relationship
    cat_cls = next((c for c in doc.classes if c.name == "Categoria"), None)
    art_cls = next((c for c in doc.classes if c.name == "Articulo"), None)
    assert cat_cls is not None
    assert art_cls is not None
    assert any(a.name == "precio" and a.type == "Double" for a in art_cls.attributes)

    # Check relationship
    assert any(
        r.source_class_id == art_cls.id and r.target_class_id == cat_cls.id
        for r in doc.relationships
    )


@pytest.mark.asyncio
async def test_rule_based_schema_notation_multi_table_with_fk(sample_document):
    prompt = """
    Doctor (id, nombre, especialidad)
    Paciente (id, nombre, telefono)
    Cita (id, fecha, doctor_id, paciente_id)
    """
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(prompt, sample_document)

    doc_names = {c.name for c in doc.classes}
    assert "Doctor" in doc_names
    assert "Paciente" in doc_names
    assert "Cita" in doc_names

    cita_cls = next(c for c in doc.classes if c.name == "Cita")
    doctor_cls = next(c for c in doc.classes if c.name == "Doctor")
    paciente_cls = next(c for c in doc.classes if c.name == "Paciente")

    # Check FK relationships were auto-linked
    cita_targets = {r.target_class_id for r in doc.relationships if r.source_class_id == cita_cls.id}
    assert doctor_cls.id in cita_targets
    assert paciente_cls.id in cita_targets


@pytest.mark.asyncio
async def test_rule_based_entity_list_without_crea(sample_document):
    prompt = "Base de datos de biblioteca: Libro, Autor, Editorial, Prestamo"
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(prompt, sample_document)

    doc_names = {c.name for c in doc.classes}
    assert "Libro" in doc_names
    assert "Autor" in doc_names
    assert "Editorial" in doc_names
    assert "Prestamo" in doc_names


@pytest.mark.asyncio
async def test_clarification_question_when_unknown_or_ambiguous(sample_document):
    prompt = "Hola, que tal, no se que hacer aca con esto"
    interpreter = AiUmlInterpreter(provider=RuleBasedAiProvider())
    doc, cmds, reply = await interpreter.interpret_and_execute(prompt, sample_document)

    assert len(cmds) == 0
    # Must ask a clarification question in Spanish
    assert "¿Qué base de datos o modelo te gustaría crear?" in reply

@pytest.mark.asyncio
async def test_sanitize_identifier_utility():
    # Classes (PascalCase)
    assert AiUmlInterpreter.sanitize_identifier("Producto Especial", pascal=True) == "ProductoEspecial"
    assert AiUmlInterpreter.sanitize_identifier("Plantilla de Fabricación", pascal=True) == "PlantillaDeFabricacion"
    assert AiUmlInterpreter.sanitize_identifier("OrdenDeTrabajo", pascal=True) == "OrdenDeTrabajo"
    assert AiUmlInterpreter.sanitize_identifier("catálogo", pascal=True) == "Catalogo"

    # Attributes (camelCase)
    assert AiUmlInterpreter.sanitize_identifier("fecha de creación", pascal=False) == "fechaDeCreacion"
    assert AiUmlInterpreter.sanitize_identifier("id_cliente", pascal=False) == "idCliente"
    assert AiUmlInterpreter.sanitize_identifier("precioUnitario", pascal=False) == "precioUnitario"


@pytest.mark.asyncio
async def test_resolve_and_validate_with_spaced_names(sample_document):
    """Verifies that entity names with spaces and accents are sanitized and relationships resolve properly."""
    raw_commands = [
        {
            "command_type": "CREATE_CLASS",
            "class_id": "cls-prod-esp",
            "name": "Producto Especial",
            "position": {"x": 100, "y": 100},
        },
        {
            "command_type": "CREATE_CLASS",
            "class_id": "cls-plantilla-fab",
            "name": "Plantilla de Fabricación",
            "position": {"x": 300, "y": 100},
        },
        {
            "command_type": "ADD_ATTRIBUTE",
            "class_id": "cls-prod-esp",
            "name": "fecha de creación",
            "type": "LocalDate",
        },
        {
            "command_type": "CREATE_RELATIONSHIP",
            "relationship_id": "rel-test-1",
            "type": "ONE_TO_ONE",
            "source_class_id": "Producto Especial",
            "target_class_id": "Plantilla de Fabricación",
            "source_cardinality": "1",
            "target_cardinality": "1",
            "target_role": "tiene plantilla",
        },
    ]

    mock_provider = AsyncMock(spec=AiProviderPort)
    mock_provider.generate_commands.return_value = {"commands": raw_commands, "question": None}

    interpreter = AiUmlInterpreter(provider=mock_provider)
    doc, executed_cmds, reply = await interpreter.interpret_and_execute(
        "Crea las clases y relación", sample_document
    )

    assert len(executed_cmds) == 4
    class_names = {c.name for c in doc.classes}
    assert "ProductoEspecial" in class_names
    assert "PlantillaDeFabricacion" in class_names

    prod_esp = next(c for c in doc.classes if c.name == "ProductoEspecial")
    attr_names = {a.name for a in prod_esp.attributes}
    assert "fechaDeCreacion" in attr_names

    # Verify relationship was created and linked to the sanitized IDs
    rel = next((r for r in doc.relationships if r.id == "rel-test-1"), None)
    assert rel is not None
    assert rel.source_class_id == prod_esp.id
    plantilla = next(c for c in doc.classes if c.name == "PlantillaDeFabricacion")
    assert rel.target_class_id == plantilla.id
