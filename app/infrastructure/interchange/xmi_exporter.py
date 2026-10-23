import html
import uuid
import xml.etree.ElementTree as ET
from typing import Dict, Optional
from xml.dom import minidom

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
    VisibilityEnum,
)


class XmiExporter:
    """Exports a CanonicalUmlDocument into OMG XMI 2.1 format compatible with Sparx Systems Enterprise Architect."""

    UML_PRIMITIVE_MAP = {
        "string": "String",
        "long": "Integer",
        "integer": "Integer",
        "int": "Integer",
        "double": "Real",
        "float": "Real",
        "boolean": "Boolean",
        "bool": "Boolean",
        "date": "Date",
        "localdate": "Date",
        "localdatetime": "DateTime",
        "datetime": "DateTime",
    }

    @classmethod
    def export(cls, document: CanonicalUmlDocument) -> str:
        """Serializes CanonicalUmlDocument into an XMI 2.1 XML string."""
        xmi_root = ET.Element("xmi:XMI")
        xmi_root.set("xmi:version", "2.1")
        xmi_root.set("xmlns:uml", "http://schema.omg.org/spec/UML/2.1")
        xmi_root.set("xmlns:xmi", "http://schema.omg.org/spec/XMI/2.1")

        # Documentation header
        doc_elem = ET.SubElement(xmi_root, "xmi:Documentation")
        doc_elem.set("exporter", "Enterprise Architect")
        doc_elem.set("exporterVersion", "6.5")

        # UML Model element
        model_name = document.name.strip() or "Model"
        uml_model = ET.SubElement(xmi_root, "uml:Model")
        uml_model.set("xmi:type", "uml:Model")
        uml_model.set("name", model_name)

        # Mapping for generalizations (Inheritance)
        inheritance_by_source: Dict[str, str] = {}
        for rel in document.relationships:
            if rel.type == RelationshipTypeEnum.INHERITANCE:
                inheritance_by_source[rel.source_class_id] = rel.target_class_id

        # 1. Export Classes
        for cls_item in document.classes:
            class_elem = ET.SubElement(uml_model, "packagedElement")
            class_elem.set("xmi:type", "uml:Class")
            class_elem.set("xmi:id", cls_item.id)
            class_elem.set("name", cls_item.name)
            class_elem.set("visibility", "public")
            if cls_item.is_abstract:
                class_elem.set("isAbstract", "true")

            # Attributes
            for attr in cls_item.attributes:
                cls._export_attribute(class_elem, attr)

            # Inheritance Generalization inside child class
            if cls_item.id in inheritance_by_source:
                parent_id = inheritance_by_source[cls_item.id]
                gen_elem = ET.SubElement(class_elem, "generalization")
                gen_elem.set("xmi:type", "uml:Generalization")
                gen_elem.set("xmi:id", f"gen-{cls_item.id}-{parent_id}")
                gen_elem.set("general", parent_id)

        # 2. Export Relationships (Associations, Compositions, Aggregations, etc.)
        for rel in document.relationships:
            if rel.type == RelationshipTypeEnum.INHERITANCE:
                # Generalizations are nested inside classes
                continue
            cls._export_relationship(uml_model, rel, document)

        # 3. Enterprise Architect Extension (Diagrams and Layout)
        cls._export_ea_extension(xmi_root, document)

        # Pretty-print XML string
        rough_string = ET.tostring(xmi_root, encoding="utf-8")
        parsed = minidom.parseString(rough_string)
        return parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

    @classmethod
    def _export_attribute(cls, class_elem: ET.Element, attr: UmlAttribute) -> None:
        attr_elem = ET.SubElement(class_elem, "ownedAttribute")
        attr_elem.set("xmi:type", "uml:Property")
        attr_elem.set("xmi:id", attr.id)
        attr_elem.set("name", attr.name)

        vis_str = "public"
        if attr.visibility == VisibilityEnum.PRIVATE:
            vis_str = "private"
        elif attr.visibility == VisibilityEnum.PROTECTED:
            vis_str = "protected"
        elif attr.visibility == VisibilityEnum.PACKAGE:
            vis_str = "package"
        attr_elem.set("visibility", vis_str)

        # Map Primitive Type
        type_norm = attr.type.strip().lower()
        uml_type = cls.UML_PRIMITIVE_MAP.get(type_norm, "String")
        type_elem = ET.SubElement(attr_elem, "type")
        type_elem.set("xmi:type", "uml:PrimitiveType")
        type_elem.set("href", f"http://schema.omg.org/spec/UML/2.1/uml.xml#{uml_type}")

        # Multiplicity (0..1 if nullable, 1..1 if required/PK)
        lower_val = "0" if (attr.nullable and not attr.primary_key) else "1"
        lower_elem = ET.SubElement(attr_elem, "lowerValue")
        lower_elem.set("xmi:type", "uml:LiteralInteger")
        lower_elem.set("xmi:id", f"{attr.id}_lower")
        lower_elem.set("value", lower_val)

        upper_elem = ET.SubElement(attr_elem, "upperValue")
        upper_elem.set("xmi:type", "uml:LiteralUnlimitedNatural")
        upper_elem.set("xmi:id", f"{attr.id}_upper")
        upper_elem.set("value", "1")

        if attr.default_value:
            def_elem = ET.SubElement(attr_elem, "defaultValue")
            def_elem.set("xmi:type", "uml:LiteralString")
            def_elem.set("xmi:id", f"{attr.id}_default")
            def_elem.set("value", str(attr.default_value))

    @classmethod
    def _export_relationship(
        cls, uml_model: ET.Element, rel: UmlRelationship, doc: CanonicalUmlDocument
    ) -> None:
        rel_id = rel.id
        src_id = rel.source_class_id
        tgt_id = rel.target_class_id

        # Source / Target End IDs
        src_end_id = f"{rel_id}_src"
        tgt_end_id = f"{rel_id}_tgt"

        assoc_elem = ET.SubElement(uml_model, "packagedElement")
        assoc_elem.set("xmi:type", "uml:Association")
        assoc_elem.set("xmi:id", rel_id)
        if rel.name:
            assoc_elem.set("name", rel.name)
        assoc_elem.set("memberEnd", f"{src_end_id} {tgt_end_id}")

        # Source End
        src_end = ET.SubElement(assoc_elem, "ownedEnd")
        src_end.set("xmi:type", "uml:Property")
        src_end.set("xmi:id", src_end_id)
        src_end.set("type", src_id)
        src_end.set("association", rel_id)
        if rel.source_role:
            src_end.set("name", rel.source_role)

        cls._set_cardinality(src_end, src_end_id, rel.source_cardinality)

        # Target End
        tgt_end = ET.SubElement(assoc_elem, "ownedEnd")
        tgt_end.set("xmi:type", "uml:Property")
        tgt_end.set("xmi:id", tgt_end_id)
        tgt_end.set("type", tgt_id)
        tgt_end.set("association", rel_id)
        if rel.target_role:
            tgt_end.set("name", rel.target_role)

        # Aggregation kind (composite for COMPOSITION, shared for AGGREGATION)
        if rel.type == RelationshipTypeEnum.COMPOSITION:
            tgt_end.set("aggregation", "composite")
        elif rel.type == RelationshipTypeEnum.AGGREGATION:
            tgt_end.set("aggregation", "shared")

        cls._set_cardinality(tgt_end, tgt_end_id, rel.target_cardinality)

    @classmethod
    def _set_cardinality(cls, end_elem: ET.Element, end_id: str, card_str: str) -> None:
        card = (card_str or "1").strip().lower()
        if card in ("*", "0..*", "n", "m"):
            lower, upper = "0", "*"
        elif card in ("1..*", "1..n"):
            lower, upper = "1", "*"
        elif card in ("0..1", "?"):
            lower, upper = "0", "1"
        else:
            lower, upper = "1", "1"

        lower_elem = ET.SubElement(end_elem, "lowerValue")
        lower_elem.set("xmi:type", "uml:LiteralInteger")
        lower_elem.set("xmi:id", f"{end_id}_lower")
        lower_elem.set("value", lower)

        upper_elem = ET.SubElement(end_elem, "upperValue")
        upper_elem.set("xmi:type", "uml:LiteralUnlimitedNatural")
        upper_elem.set("xmi:id", f"{end_id}_upper")
        upper_elem.set("value", upper)

    @classmethod
    def _export_ea_extension(cls, xmi_root: ET.Element, doc: CanonicalUmlDocument) -> None:
        """Adds Enterprise Architect proprietary tags so EA automatically renders diagram elements and geometry."""
        ext_elem = ET.SubElement(xmi_root, "xmi:Extension")
        ext_elem.set("extender", "Enterprise Architect")
        ext_elem.set("extenderID", "6.5")

        elements_elem = ET.SubElement(ext_elem, "elements")
        for cls_item in doc.classes:
            elem = ET.SubElement(elements_elem, "element")
            elem.set("xmi:idref", cls_item.id)
            elem.set("xmi:type", "uml:Class")
            elem.set("name", cls_item.name)
            elem.set("scope", "public")

            props = ET.SubElement(elem, "properties")
            props.set("isSpecification", "false")
            props.set("sType", "Class")
            props.set("nType", "0")
            props.set("scope", "public")
            props.set("isAbstract", "true" if cls_item.is_abstract else "false")
            if cls_item.stereotype:
                props.set("stereotype", cls_item.stereotype)

            proj = ET.SubElement(elem, "project")
            proj.set("author", "Case2Code")
            proj.set("version", "1.0")
            proj.set("status", "Proposed")

        # Connectors
        connectors_elem = ET.SubElement(ext_elem, "connectors")
        for rel in doc.relationships:
            conn = ET.SubElement(connectors_elem, "connector")
            conn.set("xmi:idref", rel.id)

            source = ET.SubElement(conn, "source")
            source.set("xmi:idref", rel.source_class_id)
            target = ET.SubElement(conn, "target")
            target.set("xmi:idref", rel.target_class_id)

            c_props = ET.SubElement(conn, "properties")
            ea_rel_type = "Association"
            if rel.type == RelationshipTypeEnum.INHERITANCE:
                ea_rel_type = "Generalization"
            elif rel.type == RelationshipTypeEnum.AGGREGATION:
                ea_rel_type = "Aggregation"
            elif rel.type == RelationshipTypeEnum.COMPOSITION:
                ea_rel_type = "Aggregation"
                c_props.set("subtype", "Strong")
            elif rel.type == RelationshipTypeEnum.REALIZATION:
                ea_rel_type = "Realisation"
            elif rel.type == RelationshipTypeEnum.DEPENDENCY:
                ea_rel_type = "Dependency"

            c_props.set("ea_type", ea_rel_type)
            c_props.set("direction", "Source -> Destination")

            labels = ET.SubElement(conn, "labels")
            if rel.name:
                labels.set("mt", rel.name)
            if rel.source_cardinality:
                labels.set("mb", rel.source_cardinality)
            if rel.target_cardinality:
                labels.set("rb", rel.target_cardinality)

        # Diagrams
        diagrams_elem = ET.SubElement(ext_elem, "diagrams")
        diag = ET.SubElement(diagrams_elem, "diagram")
        diag.set("xmi:id", f"ea_diag_{doc.id}")

        model_elem = ET.SubElement(diag, "model")
        model_elem.set("package", doc.name)
        model_elem.set("localID", "1")

        d_props = ET.SubElement(diag, "properties")
        d_props.set("name", doc.name)
        d_props.set("type", "Logical")

        d_elements = ET.SubElement(diag, "elements")
        for idx, cls_item in enumerate(doc.classes):
            x = int(cls_item.position.x)
            y = int(cls_item.position.y)
            w = 220
            h = max(100, 40 + len(cls_item.attributes) * 22)

            d_elem = ET.SubElement(d_elements, "element")
            d_elem.set("subject", cls_item.id)
            d_elem.set("seqno", str(idx + 1))
            d_elem.set("geometry", f"Left={x};Top={y};Right={x + w};Bottom={y + h};")
