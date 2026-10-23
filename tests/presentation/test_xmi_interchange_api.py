import io
import pytest
from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
)
from app.infrastructure.interchange.xmi_exporter import XmiExporter


def test_export_diagram_to_xmi_endpoint(client, diagram_repo):
    # 1. Seed a diagram in repository
    doc = CanonicalUmlDocument(
        id="diag-xmi-export-test",
        name="Veterinaria EA Export",
        version=1,
        classes=[
            UmlClass(
                id="cls-paciente",
                name="Paciente",
                attributes=[
                    UmlAttribute(id="a-1", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a-2", name="nombre", type="String", primary_key=False, nullable=False),
                ],
            ),
            UmlClass(
                id="cls-dueno",
                name="Dueno",
                attributes=[
                    UmlAttribute(id="a-3", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a-4", name="telefono", type="String", primary_key=False, nullable=True),
                ],
            ),
        ],
        relationships=[
            UmlRelationship(
                id="rel-1",
                type=RelationshipTypeEnum.MANY_TO_ONE,
                source_class_id="cls-paciente",
                target_class_id="cls-dueno",
                source_cardinality="*",
                target_cardinality="1",
                target_role="dueno",
            )
        ],
    )
    diagram_repo.save(doc)

    # 2. Call export endpoint
    resp = client.get(f"/api/diagrams/{doc.id}/export/xmi")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    assert 'attachment; filename="Veterinaria_EA_Export.xmi"' in resp.headers["content-disposition"]

    xml_text = resp.text
    assert "<xmi:XMI" in xml_text
    assert 'xmi:type="uml:Class"' in xml_text
    assert 'name="Paciente"' in xml_text
    assert 'name="Dueno"' in xml_text
    assert 'name="telefono"' in xml_text
    assert "Enterprise Architect" in xml_text


def test_import_xmi_into_existing_diagram_endpoint(client, diagram_repo):
    # 1. Create empty target diagram
    doc = CanonicalUmlDocument(
        id="diag-xmi-import-test",
        name="Target Diagram",
        version=1,
        classes=[],
        relationships=[],
    )
    diagram_repo.save(doc)

    # 2. Prepare XMI content with 2 classes and 1 relationship
    source_doc = CanonicalUmlDocument(
        id="diag-source",
        name="Source Model",
        version=1,
        classes=[
            UmlClass(
                id="c-medico",
                name="Medico",
                attributes=[
                    UmlAttribute(id="m-1", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="m-2", name="especialidad", type="String"),
                ],
            ),
            UmlClass(
                id="c-paciente",
                name="Paciente",
                attributes=[
                    UmlAttribute(id="p-1", name="id", type="Long", primary_key=True),
                ],
            ),
        ],
        relationships=[
            UmlRelationship(
                id="r-med-pac",
                type=RelationshipTypeEnum.ONE_TO_MANY,
                source_class_id="c-medico",
                target_class_id="c-paciente",
                source_cardinality="1",
                target_cardinality="*",
            )
        ],
    )
    xmi_content = XmiExporter.export(source_doc)

    # 3. Post to import endpoint as multipart file
    file_bytes = io.BytesIO(xmi_content.encode("utf-8"))
    files = {"file": ("hospital.xmi", file_bytes, "application/xml")}

    resp = client.post(f"/api/diagrams/{doc.id}/import/xmi", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == doc.id
    assert len(data["classes"]) == 2
    class_names = {c["name"] for c in data["classes"]}
    assert "Medico" in class_names
    assert "Paciente" in class_names
    assert len(data["relationships"]) == 1


def test_import_new_diagram_from_xmi_endpoint(client):
    source_doc = CanonicalUmlDocument(
        id="diag-hotel",
        name="Hotel Schema",
        version=1,
        classes=[
            UmlClass(
                id="c-hab",
                name="Habitacion",
                attributes=[
                    UmlAttribute(id="h-1", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="h-2", name="numero", type="Integer"),
                    UmlAttribute(id="h-3", name="precio", type="Double"),
                ],
            )
        ],
        relationships=[],
    )
    xmi_content = XmiExporter.export(source_doc)

    file_bytes = io.BytesIO(xmi_content.encode("utf-8"))
    files = {"file": ("hotel_model.xmi", file_bytes, "application/xml")}

    resp = client.post("/api/diagrams/import/xmi?name=Hotel+Enterprise", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Hotel Enterprise"
    assert len(data["classes"]) == 1
    assert data["classes"][0]["name"] == "Habitacion"
