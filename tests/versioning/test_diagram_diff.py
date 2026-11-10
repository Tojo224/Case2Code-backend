from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
)
from app.domain.services.diagram_diff import build_summary, diff_documents


def _doc(classes=None, relationships=None) -> CanonicalUmlDocument:
    return CanonicalUmlDocument(
        id="d1", name="Demo", classes=classes or [], relationships=relationships or []
    )


def _cls(cid="c1", name="Producto", attrs=None) -> UmlClass:
    return UmlClass(id=cid, name=name, attributes=attrs or [])


def _attr(aid="a1", name="id", type_="Long", pk=False, nullable=True) -> UmlAttribute:
    return UmlAttribute(id=aid, name=name, type=type_, primary_key=pk, nullable=nullable)


def _rel(rid="r1", type_=RelationshipTypeEnum.ONE_TO_MANY, **kw) -> UmlRelationship:
    return UmlRelationship(id=rid, type=type_, source_class_id="c1", target_class_id="c2", **kw)


def test_identical_documents_have_no_changes():
    doc = _doc([_cls(attrs=[_attr()])])
    changes = diff_documents(doc, doc.model_copy(deep=True))
    assert changes == []
    assert build_summary(changes) == "Sin cambios"


def test_class_added_removed_renamed():
    old = _doc([_cls("c1", "Producto"), _cls("c2", "Viejo")])
    new = _doc([_cls("c1", "Articulo"), _cls("c3", "Nuevo")])
    changes = diff_documents(old, new)
    assert [(c.kind, c.target_id) for c in changes] == [
        ("class_added", "c3"),
        ("class_removed", "c2"),
        ("class_renamed", "c1"),
    ]
    assert changes[0].description == "Se agregó la clase Nuevo"
    assert changes[1].description == "Se eliminó la clase Viejo"
    assert changes[2].description == "Se renombró la clase Producto a Articulo"


def test_attribute_changes():
    old = _doc([_cls(attrs=[_attr("a1", "id"), _attr("a2", "precio", "Double"), _attr("a3", "x")])])
    new = _doc(
        [
            _cls(
                attrs=[
                    _attr("a1", "id", "Long", pk=True, nullable=False),
                    _attr("a2", "precio", "BigDecimal"),
                    _attr("a4", "stock", "Integer"),
                ]
            )
        ]
    )
    changes = diff_documents(old, new)
    kinds = [c.kind for c in changes]
    assert kinds == ["attribute_added", "attribute_removed", "attribute_changed", "attribute_changed"]
    assert changes[0].description == "Se agregó el atributo stock a la clase Producto"
    assert changes[1].description == "Se eliminó el atributo x de la clase Producto"
    assert "clave primaria" in changes[2].description and "nulos" in changes[2].description
    assert "Double → BigDecimal" in changes[3].description


def test_attributes_of_new_or_removed_classes_are_not_listed_separately():
    old = _doc([])
    new = _doc([_cls(attrs=[_attr()])])
    assert [c.kind for c in diff_documents(old, new)] == ["class_added"]


def test_relationship_changes():
    classes = [_cls("c1", "Cliente"), _cls("c2", "Pedido")]
    old = _doc(classes, [_rel("r1"), _rel("r2"), _rel("r4")])
    new = _doc(
        classes,
        [
            _rel("r1", RelationshipTypeEnum.MANY_TO_ONE, target_cardinality="0..*", name="hace"),
            _rel("r2"),
            _rel("r3"),
        ],
    )
    changes = diff_documents(old, new)
    assert [(c.kind, c.target_id) for c in changes] == [
        ("relationship_added", "r3"),
        ("relationship_removed", "r4"),
        ("relationship_changed", "r1"),
    ]
    assert changes[0].description == "Se agregó la relación ONE_TO_MANY de Cliente a Pedido"
    assert "ONE_TO_MANY → MANY_TO_ONE" in changes[2].description
    assert "cardinalidad destino: 1 → 0..*" in changes[2].description
    assert "nombre" in changes[2].description


def test_diff_is_deterministic():
    old = _doc([_cls("c1", "A")])
    new = _doc([_cls("c1", "B"), _cls("c2", "C"), _cls("c3", "D")])
    assert diff_documents(old, new) == diff_documents(old, new)


def test_summary_pluralization():
    old = _doc([])
    new = _doc([_cls("c1", "A"), _cls("c2", "B")], [_rel()])
    summary = build_summary(diff_documents(old, new))
    assert summary == "2 clases agregadas, 1 relación agregada"
