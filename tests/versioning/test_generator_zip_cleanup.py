import tempfile
import uuid
from pathlib import Path

from app.domain.models.canonical_uml import CanonicalUmlDocument, UmlAttribute, UmlClass


def _doc(name: str) -> CanonicalUmlDocument:
    return CanonicalUmlDocument(
        id=f"diag-zip-{uuid.uuid4().hex[:8]}",
        name=name,
        classes=[
            UmlClass(
                id="c1",
                name="Cliente",
                attributes=[UmlAttribute(id="a1", name="id", type="Long", primary_key=True)],
            )
        ],
    )


def test_generate_backend_removes_zip_after_response(client, diagram_repo):
    name = f"zipclean{uuid.uuid4().hex[:6]}"
    doc = _doc(name)
    diagram_repo.save(doc)

    resp = client.post(f"/api/diagrams/{doc.id}/generate?verify=false")

    assert resp.status_code == 200 and resp.content[:2] == b"PK"
    assert not (Path(tempfile.gettempdir()) / f"{name}_backend.zip").exists()


def test_generate_mobile_removes_zip_after_response(client, diagram_repo):
    name = f"zipmob{uuid.uuid4().hex[:6]}"
    doc = _doc(name)
    diagram_repo.save(doc)

    resp = client.post(f"/api/diagrams/{doc.id}/generate/mobile")

    assert resp.status_code == 200 and resp.content[:2] == b"PK"
    assert not (Path(tempfile.gettempdir()) / f"{name}_flutter_mobile.zip").exists()
