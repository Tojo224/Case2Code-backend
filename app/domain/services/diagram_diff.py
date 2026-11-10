"""Pure structural diff between two canonical UML documents.

Elements are matched by id. Ordering is deterministic: class changes
(added, removed, renamed), then attribute changes per class, then relationship
changes. Descriptions are user facing and written in Spanish.
"""

from typing import Dict, List

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    UmlAttribute,
    UmlRelationship,
)
from app.domain.models.versioning import DiagramChange

_SUMMARY_LABELS = [
    ("class_added", "clase agregada", "clases agregadas"),
    ("class_removed", "clase eliminada", "clases eliminadas"),
    ("class_renamed", "clase renombrada", "clases renombradas"),
    ("attribute_added", "atributo agregado", "atributos agregados"),
    ("attribute_removed", "atributo eliminado", "atributos eliminados"),
    ("attribute_changed", "atributo modificado", "atributos modificados"),
    ("relationship_added", "relación agregada", "relaciones agregadas"),
    ("relationship_removed", "relación eliminada", "relaciones eliminadas"),
    ("relationship_changed", "relación modificada", "relaciones modificadas"),
]


def _enum_value(value) -> str:
    return getattr(value, "value", str(value))


def _attribute_differences(old: UmlAttribute, new: UmlAttribute) -> List[str]:
    parts: List[str] = []
    if old.name != new.name:
        parts.append(f"nombre: {old.name} → {new.name}")
    if old.type != new.type:
        parts.append(f"tipo: {old.type} → {new.type}")
    if old.primary_key != new.primary_key:
        parts.append("ahora es clave primaria" if new.primary_key else "ya no es clave primaria")
    if old.nullable != new.nullable:
        parts.append("ahora admite nulos" if new.nullable else "ya no admite nulos")
    return parts


def _relationship_differences(old: UmlRelationship, new: UmlRelationship) -> List[str]:
    parts: List[str] = []
    if old.type != new.type:
        parts.append(f"tipo: {_enum_value(old.type)} → {_enum_value(new.type)}")
    if old.source_cardinality != new.source_cardinality:
        parts.append(f"cardinalidad origen: {old.source_cardinality} → {new.source_cardinality}")
    if old.target_cardinality != new.target_cardinality:
        parts.append(f"cardinalidad destino: {old.target_cardinality} → {new.target_cardinality}")
    if (old.name or "") != (new.name or ""):
        parts.append(f"nombre: {old.name or '(sin nombre)'} → {new.name or '(sin nombre)'}")
    return parts


def diff_documents(old: CanonicalUmlDocument, new: CanonicalUmlDocument) -> List[DiagramChange]:
    changes: List[DiagramChange] = []
    old_classes = {c.id: c for c in old.classes}
    new_classes = {c.id: c for c in new.classes}

    for cls in new.classes:
        if cls.id not in old_classes:
            changes.append(
                DiagramChange(
                    kind="class_added",
                    target_id=cls.id,
                    description=f"Se agregó la clase {cls.name}",
                )
            )
    for cls in old.classes:
        if cls.id not in new_classes:
            changes.append(
                DiagramChange(
                    kind="class_removed",
                    target_id=cls.id,
                    description=f"Se eliminó la clase {cls.name}",
                )
            )
    for cls in new.classes:
        previous = old_classes.get(cls.id)
        if previous is not None and previous.name != cls.name:
            changes.append(
                DiagramChange(
                    kind="class_renamed",
                    target_id=cls.id,
                    description=f"Se renombró la clase {previous.name} a {cls.name}",
                )
            )

    for cls in new.classes:
        previous = old_classes.get(cls.id)
        if previous is None:
            continue
        old_attrs: Dict[str, UmlAttribute] = {a.id: a for a in previous.attributes}
        new_attrs: Dict[str, UmlAttribute] = {a.id: a for a in cls.attributes}
        for attr in cls.attributes:
            if attr.id not in old_attrs:
                changes.append(
                    DiagramChange(
                        kind="attribute_added",
                        target_id=attr.id,
                        description=f"Se agregó el atributo {attr.name} a la clase {cls.name}",
                    )
                )
        for attr in previous.attributes:
            if attr.id not in new_attrs:
                changes.append(
                    DiagramChange(
                        kind="attribute_removed",
                        target_id=attr.id,
                        description=f"Se eliminó el atributo {attr.name} de la clase {cls.name}",
                    )
                )
        for attr in cls.attributes:
            before = old_attrs.get(attr.id)
            if before is None:
                continue
            differences = _attribute_differences(before, attr)
            if differences:
                changes.append(
                    DiagramChange(
                        kind="attribute_changed",
                        target_id=attr.id,
                        description=(
                            f"Se modificó el atributo {before.name} de la clase {cls.name} "
                            f"({'; '.join(differences)})"
                        ),
                    )
                )

    class_names = {c.id: c.name for c in old.classes}
    class_names.update({c.id: c.name for c in new.classes})

    def describe(rel: UmlRelationship) -> str:
        source = class_names.get(rel.source_class_id, rel.source_class_id)
        target = class_names.get(rel.target_class_id, rel.target_class_id)
        return f"{_enum_value(rel.type)} de {source} a {target}"

    old_rels = {r.id: r for r in old.relationships}
    new_rels = {r.id: r for r in new.relationships}
    for rel in new.relationships:
        if rel.id not in old_rels:
            changes.append(
                DiagramChange(
                    kind="relationship_added",
                    target_id=rel.id,
                    description=f"Se agregó la relación {describe(rel)}",
                )
            )
    for rel in old.relationships:
        if rel.id not in new_rels:
            changes.append(
                DiagramChange(
                    kind="relationship_removed",
                    target_id=rel.id,
                    description=f"Se eliminó la relación {describe(rel)}",
                )
            )
    for rel in new.relationships:
        before = old_rels.get(rel.id)
        if before is None:
            continue
        differences = _relationship_differences(before, rel)
        if differences:
            changes.append(
                DiagramChange(
                    kind="relationship_changed",
                    target_id=rel.id,
                    description=f"Se modificó la relación {describe(rel)} ({'; '.join(differences)})",
                )
            )

    return changes


def build_summary(changes: List[DiagramChange]) -> str:
    """Spanish one-line summary, e.g. '2 clases agregadas, 1 atributo modificado'."""
    if not changes:
        return "Sin cambios"
    counts: Dict[str, int] = {}
    for change in changes:
        counts[change.kind] = counts.get(change.kind, 0) + 1
    parts = []
    for kind, singular, plural in _SUMMARY_LABELS:
        n = counts.get(kind, 0)
        if n:
            parts.append(f"{n} {singular if n == 1 else plural}")
    return ", ".join(parts)
