import io
import zipfile
from fastapi.testclient import TestClient

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    UmlAttribute,
    UmlClass,
)


def test_generate_endpoint_zip_download(client, diagram_repo):
    # 1. Setup diagram with one class
    doc = CanonicalUmlDocument(
        id="diag-api-gen",
        name="Peluqueria",
        classes=[
            UmlClass(
                id="c-cliente",
                name="Cliente",
                attributes=[
                    UmlAttribute(id="a-1", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="a-2", name="nombre", type="String"),
                ],
            )
        ],
    )
    diagram_repo.save(doc)

    # 2. Call generate endpoint without verify for fast test
    resp = client.post(f"/api/diagrams/{doc.id}/generate?verify=false")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    # 3. Read zip contents
    zip_bytes = io.BytesIO(resp.content)
    with zipfile.ZipFile(zip_bytes, "r") as zf:
        file_names = zf.namelist()
        assert any("pom.xml" in name for name in file_names)
        assert any("Application.java" in name for name in file_names)
        assert any("Cliente.java" in name for name in file_names)
        assert any("ClienteRepository.java" in name for name in file_names)
        assert any("ClienteService.java" in name for name in file_names)
        assert any("ClienteController.java" in name for name in file_names)
        assert any("mvnw.cmd" in name for name in file_names)


def test_generate_endpoint_empty_diagram_error(client, diagram_repo):
    doc = CanonicalUmlDocument(
        id="diag-empty",
        name="Empty Diagram",
        classes=[],
    )
    diagram_repo.save(doc)

    resp = client.post(f"/api/diagrams/{doc.id}/generate?verify=false")
    assert resp.status_code == 400
    assert "must contain at least one class" in resp.json()["detail"]


def test_generate_endpoint_not_found(client):
    resp = client.post("/api/diagrams/non-existent-id/generate?verify=false")
    assert resp.status_code == 404
