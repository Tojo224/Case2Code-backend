import re
import uuid
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    Position,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
    VisibilityEnum,
)


class XmiImporter:
    """Parses OMG XMI 2.1 and Sparx Systems Enterprise Architect XML models into CanonicalUmlDocument."""

    TYPE_NORMALIZATION = {
        "string": "String",
        "varchar": "String",
        "text": "String",
        "char": "String",
        "integer": "Integer",
        "int": "Integer",
        "long": "Long",
        "bigint": "Long",
        "real": "Double",
        "float": "Double",
        "double": "Double",
        "decimal": "Double",
        "boolean": "Boolean",
        "bool": "Boolean",
        "date": "LocalDate",
        "localdate": "LocalDate",
        "time": "LocalDateTime",
        "datetime": "LocalDateTime",
        "localdatetime": "LocalDateTime",
        "timestamp": "LocalDateTime",
    }

    @classmethod
    def _clean_attribs(cls, elem: ET.Element) -> Dict[str, str]:
        """Returns element attributes with namespace prefixes stripped (e.g. {xmi}type -> type)."""
        return {k.split("}")[-1]: v for k, v in elem.attrib.items()}

    @classmethod
    def import_xmi(cls, xml_content: str, target_document: CanonicalUmlDocument) -> CanonicalUmlDocument:
        """Parses XMI content and updates the target CanonicalUmlDocument with imported classes and relationships."""
        # Parse XML preserving valid namespace prefixes
        root = ET.fromstring(xml_content.encode("utf-8") if isinstance(xml_content, str) else xml_content)

        # 1. Parse Geometry / Positions from Enterprise Architect Extension if present
        positions = cls._parse_ea_geometry(root)

        # 2. Parse Classes
        classes: List[UmlClass] = []
        class_id_map: Dict[str, UmlClass] = {}
        generalizations: List[Tuple[str, str]] = []  # (child_id, parent_id)

        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            attrs = cls._clean_attribs(elem)
            elem_type = attrs.get("type", "")

            if (tag == "packagedElement" and "Class" in elem_type) or tag == "Class":
                class_id = attrs.get("id") or f"cls-{uuid.uuid4().hex[:8]}"
                class_name = attrs.get("name", "UnnamedClass").strip()
                if not class_name:
                    continue

                is_abstract = attrs.get("isAbstract", "false").lower() == "true"
                stereotype = attrs.get("stereotype")

                # Parse Attributes
                attributes: List[UmlAttribute] = []
                for child in elem:
                    child_tag = child.tag.split("}")[-1]
                    child_attrs = cls._clean_attribs(child)
                    child_type = child_attrs.get("type", "")

                    if (child_tag == "ownedAttribute" and ("Property" in child_type or not child_type)) or child_tag == "Property":
                        # Skip association ends defined as ownedAttribute if they link to an association
                        if child_attrs.get("association"):
                            continue

                        attr = cls._parse_attribute(child, child_attrs, class_name)
                        if attr:
                            attributes.append(attr)

                    # Inheritance (Generalization inside class)
                    elif child_tag == "generalization":
                        parent_ref = child_attrs.get("general")
                        if parent_ref:
                            generalizations.append((class_id, parent_ref))

                # Ensure default PK if no attributes or no primary key
                has_pk = any(a.primary_key for a in attributes)
                if not has_pk:
                    id_attr = next((a for a in attributes if a.name.lower() == "id"), None)
                    if id_attr:
                        id_attr.primary_key = True
                    else:
                        attributes.insert(
                            0,
                            UmlAttribute(
                                id=f"attr-{uuid.uuid4().hex[:8]}",
                                name="id",
                                type="Long",
                                primary_key=True,
                                nullable=False,
                            ),
                        )

                # Determine position
                pos = positions.get(class_id)
                if not pos:
                    idx = len(classes)
                    pos = Position(x=float(80 + (idx % 3) * 320), y=float(80 + (idx // 3) * 280))

                uml_class = UmlClass(
                    id=class_id,
                    name=class_name,
                    position=pos,
                    attributes=attributes,
                    is_abstract=is_abstract,
                    stereotype=stereotype,
                )
                classes.append(uml_class)
                class_id_map[class_id] = uml_class

        # 3. Parse Relationships
        relationships: List[UmlRelationship] = []

        # 3.1 Associations
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            attrs = cls._clean_attribs(elem)
            elem_type = attrs.get("type", "")

            if (tag == "packagedElement" and "Association" in elem_type) or tag == "Association":
                rel = cls._parse_association(elem, attrs, class_id_map)
                if rel:
                    relationships.append(rel)

        # 3.2 Add Generalizations as INHERITANCE
        for child_id, parent_id in generalizations:
            if child_id in class_id_map and parent_id in class_id_map:
                relationships.append(
                    UmlRelationship(
                        id=f"rel-gen-{uuid.uuid4().hex[:8]}",
                        type=RelationshipTypeEnum.INHERITANCE,
                        source_class_id=child_id,
                        target_class_id=parent_id,
                        source_cardinality="",
                        target_cardinality="",
                    )
                )

        # 4. Merge into target document
        target_document.classes = classes
        target_document.relationships = relationships
        target_document.version += 1

        return target_document

    @classmethod
    def _parse_attribute(cls, elem: ET.Element, attrs: Dict[str, str], class_name: str) -> Optional[UmlAttribute]:
        attr_id = attrs.get("id") or f"attr-{uuid.uuid4().hex[:8]}"
        attr_name = attrs.get("name", "").strip()
        if not attr_name:
            return None

        # Determine type
        raw_type = "String"
        for child in elem:
            child_tag = child.tag.split("}")[-1]
            child_attrs = cls._clean_attribs(child)
            if child_tag == "type":
                href = child_attrs.get("href", "")
                if "#" in href:
                    raw_type = href.split("#")[-1]
                elif child_attrs.get("name"):
                    raw_type = child_attrs["name"]
                break

        if not raw_type or raw_type == "String":
            type_attr = attrs.get("type")
            if type_attr and not type_attr.startswith("uml:"):
                raw_type = type_attr

        norm_type = cls.TYPE_NORMALIZATION.get(raw_type.lower(), "String")

        # Visibility
        vis_raw = attrs.get("visibility", "public").upper()
        visibility = VisibilityEnum.PUBLIC
        if vis_raw == "PRIVATE":
            visibility = VisibilityEnum.PRIVATE
        elif vis_raw == "PROTECTED":
            visibility = VisibilityEnum.PROTECTED
        elif vis_raw == "PACKAGE":
            visibility = VisibilityEnum.PACKAGE

        # Nullability & Primary Key
        is_pk = attr_name.lower() in ("id", f"{class_name.lower()}_id", f"{class_name.lower()}id")
        nullable = not is_pk

        for child in elem:
            child_tag = child.tag.split("}")[-1]
            child_attrs = cls._clean_attribs(child)
            if child_tag == "lowerValue":
                val = child_attrs.get("value", "1")
                if val == "0":
                    nullable = True
                elif val == "1":
                    nullable = False

        return UmlAttribute(
            id=attr_id,
            name=attr_name,
            type=norm_type,
            primary_key=is_pk,
            nullable=nullable,
            visibility=visibility,
        )

    @classmethod
    def _parse_association(
        cls, elem: ET.Element, attrs: Dict[str, str], class_id_map: Dict[str, UmlClass]
    ) -> Optional[UmlRelationship]:
        rel_id = attrs.get("id") or f"rel-{uuid.uuid4().hex[:8]}"
        rel_name = attrs.get("name")

        owned_ends = [
            child for child in elem if child.tag.split("}")[-1] == "ownedEnd"
        ]
        if len(owned_ends) < 2:
            return None

        src_end = owned_ends[0]
        tgt_end = owned_ends[1]
        src_attrs = cls._clean_attribs(src_end)
        tgt_attrs = cls._clean_attribs(tgt_end)

        src_id = src_attrs.get("type")
        tgt_id = tgt_attrs.get("type")

        if not src_id or not tgt_id or src_id not in class_id_map or tgt_id not in class_id_map:
            return None

        src_card = cls._extract_multiplicity(src_end)
        tgt_card = cls._extract_multiplicity(tgt_end)

        # Check Aggregation
        aggregation = tgt_attrs.get("aggregation") or src_attrs.get("aggregation")
        rel_type = RelationshipTypeEnum.ONE_TO_MANY

        if aggregation == "composite":
            rel_type = RelationshipTypeEnum.COMPOSITION
        elif aggregation == "shared":
            rel_type = RelationshipTypeEnum.AGGREGATION
        else:
            if src_card == "*" and tgt_card == "*":
                rel_type = RelationshipTypeEnum.MANY_TO_MANY
            elif src_card == "1" and tgt_card == "1":
                rel_type = RelationshipTypeEnum.ONE_TO_ONE
            elif src_card == "*" and tgt_card == "1":
                rel_type = RelationshipTypeEnum.MANY_TO_ONE
            else:
                rel_type = RelationshipTypeEnum.ONE_TO_MANY

        return UmlRelationship(
            id=rel_id,
            name=rel_name,
            type=rel_type,
            source_class_id=src_id,
            target_class_id=tgt_id,
            source_cardinality=src_card,
            target_cardinality=tgt_card,
            source_role=src_attrs.get("name"),
            target_role=tgt_attrs.get("name"),
        )

    @classmethod
    def _extract_multiplicity(cls, end_elem: ET.Element) -> str:
        lower = "1"
        upper = "1"
        for child in end_elem:
            tag = child.tag.split("}")[-1]
            attrs = cls._clean_attribs(child)
            if tag == "lowerValue":
                lower = attrs.get("value", "1")
            elif tag == "upperValue":
                upper = attrs.get("value", "1")

        if upper == "*":
            return "*"
        if lower == "1" and upper == "1":
            return "1"
        if lower == "0" and upper == "1":
            return "0..1"
        return f"{lower}..{upper}"

    @classmethod
    def _parse_ea_geometry(cls, root: ET.Element) -> Dict[str, Position]:
        """Extracts X and Y coordinates from Enterprise Architect diagram elements if present."""
        positions: Dict[str, Position] = {}
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            attrs = cls._clean_attribs(elem)
            if tag == "element" and attrs.get("geometry"):
                subject = attrs.get("subject")
                geom = attrs.get("geometry", "")
                if subject and "Left=" in geom and "Top=" in geom:
                    left_m = re.search(r"Left=(-?\d+)", geom)
                    top_m = re.search(r"Top=(-?\d+)", geom)
                    if left_m and top_m:
                        raw_x = abs(float(left_m.group(1)))
                        raw_y = abs(float(top_m.group(1)))
                        positions[subject] = Position(x=raw_x, y=raw_y)
        return positions
