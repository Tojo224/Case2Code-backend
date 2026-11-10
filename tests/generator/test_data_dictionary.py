from app.domain.models.canonical_uml import (
    CanonicalUmlDocument, RelationshipTypeEnum, UmlAttribute, UmlClass, UmlRelationship,
)
from app.infrastructure.docs.data_dictionary import (
    build_data_dictionary, describe_column, map_data_type, pk_fk_marker,
)


def _cls(cid, name, attrs):
    return UmlClass(id=cid, name=name, attributes=[
        UmlAttribute(id=f"{cid}-{i}", name=n, type=t, primary_key=pk, nullable=nl)
        for i, (n, t, pk, nl) in enumerate(attrs)
    ])


def _doc(classes=None, rels=None, doc_id="d1"):
    return CanonicalUmlDocument(id=doc_id, name="Tienda Demo", classes=classes or [], relationships=rels or [])


def _model():
    return _doc(
        [
            _cls("c1", "Cliente", [("id", "Long", True, False), ("nombre", "String", False, False)]),
            _cls("c2", "Pedido", [("id", "Long", True, False), ("total", "Double", False, True)]),
            _cls("c3", "Producto", [("id", "Long", True, False), ("nombre", "String", False, False)]),
            _cls("c4", "Categoria", [("id", "Long", True, False)]),
        ],
        [
            UmlRelationship(id="r1", type=RelationshipTypeEnum.ONE_TO_MANY, source_class_id="c1",
                            target_class_id="c2", source_cardinality="1", target_cardinality="*"),
            UmlRelationship(id="r2", type=RelationshipTypeEnum.MANY_TO_MANY, source_class_id="c3",
                            target_class_id="c4", source_cardinality="*", target_cardinality="*"),
        ],
    )


def test_type_mapping():
    expected = {"Long": "bigint", "int": "integer", "Short": "integer", "Double": "numeric",
                "BigDecimal": "numeric", "bool": "boolean", "LocalDate": "date",
                "LocalDateTime": "timestamp", "Text": "text", "UUID": "uuid", "String": "varchar",
                "Weird": "varchar"}
    for src, dst in expected.items():
        assert map_data_type(src) == dst


def test_pk_fk_markers():
    assert pk_fk_marker(True, False) == "pk"
    assert pk_fk_marker(False, True) == "fk"
    assert pk_fk_marker(True, True) == "pk, fk"
    assert pk_fk_marker(False, False) == ""


def test_pk_fk_in_relational_schema():
    from app.infrastructure.codegen.sql_generator import SqlGenerator
    tables = SqlGenerator()._build_relational_schema(_model())
    pedido = next(t for t in tables if t.name.lower() == "pedido")
    assert pedido.foreign_keys  # one-to-many creates fk in the many side
    join = [t for t in tables if t.is_join_table]
    assert len(join) == 1 and len(join[0].foreign_keys) == 2
    for fk in join[0].foreign_keys:
        assert pk_fk_marker(True, True) == "pk, fk" and fk.column_name


def test_descriptions_lowercase():
    d = describe_column("id", "Pedido", is_pk=True, fk_target=None, nullable=False)
    assert d == "identificador único de pedido"
    d = describe_column("cliente_id", "Pedido", is_pk=False, fk_target="Cliente", nullable=True)
    assert d == "referencia a cliente asociado a este registro"
    d = describe_column("codigo_interno", "Pedido", is_pk=False, fk_target=None, nullable=False)
    assert d == d.lower() and d.endswith("obligatorio")


def test_pdf_generated():
    res = build_data_dictionary(_model(), author="Ana & <Co>")
    assert res.pdf.startswith(b"%PDF")
    assert res.total_pages >= 3 and res.content_pages >= 1


def test_large_model_fits_five_pages():
    classes = [
        _cls(f"c{i}", f"Entidad{i}", [("id", "Long", True, False)] +
             [(f"campo_{j}", "String", False, False) for j in range(7)])
        for i in range(12)
    ]
    rels = [UmlRelationship(id=f"r{i}", type=RelationshipTypeEnum.ONE_TO_MANY, source_class_id=f"c{i}",
                            target_class_id=f"c{i+1}") for i in range(11)]
    res = build_data_dictionary(_doc(classes, rels), author="Ana")
    assert res.pdf.startswith(b"%PDF")
    assert res.content_pages <= 5


def test_endpoint_ok(client, diagram_repo):
    doc = _model()
    doc.id = "diag-docs-ok"
    diagram_repo.save(doc)
    resp = client.get(f"/api/diagrams/{doc.id}/export/docs")
    assert resp.status_code == 200
    assert "application/pdf" in resp.headers["content-type"]
    assert 'filename="Tienda_Demo_diccionario_datos.pdf"' in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")


def test_endpoint_no_classes(client, diagram_repo):
    doc = _doc(doc_id="diag-docs-empty")
    diagram_repo.save(doc)
    resp = client.get(f"/api/diagrams/{doc.id}/export/docs")
    assert resp.status_code == 400


def test_endpoint_private_requires_auth(client, diagram_repo):
    doc = _model()
    doc.id = "diag-docs-private"
    doc.owner_id = "someone"
    diagram_repo.save(doc)
    assert client.get(f"/api/diagrams/{doc.id}/export/docs").status_code == 401
