import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from jinja2 import Environment, FileSystemLoader

from app.application.ports.code_generator import MobileGeneratorPort
from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    UmlAttribute,
    UmlClass,
)
from app.infrastructure.codegen.spring_boot_generator import (
    SpringBootGenerator,
    to_plural,
    to_snake_case,
)


def _humanize_identifier(name: str) -> str:
    snake = to_snake_case(name or "")
    words = [w.capitalize() for w in snake.split("_") if w]
    return " ".join(words) or name


def _map_uml_type_to_dart(uml_type: str, field_name: str) -> Tuple[str, str, str]:
    """Returns (dart_type, field_kind, from_json_expr) for a UML attribute."""
    norm = (uml_type or "String").strip().lower()
    if norm in ("long", "bigint", "int", "integer", "short", "smallint"):
        return (
            "int",
            "integer",
            f"json['{field_name}'] is int ? json['{field_name}'] as int : int.tryParse(json['{field_name}']?.toString() ?? '')",
        )
    if norm in ("double", "float", "real", "bigdecimal", "decimal", "numeric"):
        return (
            "double",
            "decimal",
            f"json['{field_name}'] is num ? (json['{field_name}'] as num).toDouble() : double.tryParse(json['{field_name}']?.toString() ?? '')",
        )
    if norm in ("boolean", "bool"):
        return (
            "bool",
            "boolean",
            f"json['{field_name}'] == true || json['{field_name}']?.toString().toLowerCase() == 'true'",
        )
    if norm in ("localdate", "date"):
        return ("String", "date", f"json['{field_name}']?.toString()")
    if norm in ("localdatetime", "datetime", "timestamp"):
        return ("String", "dateTime", f"json['{field_name}']?.toString()")
    if norm == "text":
        return ("String", "text", f"json['{field_name}']?.toString()")

    return ("String", "string", f"json['{field_name}']?.toString()")


class FlutterGenerator(MobileGeneratorPort):
    """Generates a complete, runnable Flutter 3 (Material 3) mobile project with
    typed domain models and a generic metadata-driven CRUD UI engine.
    """

    def __init__(self):
        templates_dir = Path(__file__).parent / "templates" / "flutter"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._spring_helper = SpringBootGenerator()

    def generate_mobile(self, document: CanonicalUmlDocument, output_dir: Path) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        lib_dir = output_dir / "lib"
        config_dir = lib_dir / "config"
        models_dir = lib_dir / "models"
        services_dir = lib_dir / "services"
        screens_dir = lib_dir / "screens"
        assets_dir = output_dir / "assets"

        for d in (config_dir, models_dir, services_dir, screens_dir, assets_dir):
            d.mkdir(parents=True, exist_ok=True)

        raw_pkg = re.sub(r"[^a-z0-9_]", "_", to_snake_case(document.name or "case2code_mobile"))
        raw_pkg = re.sub(r"_+", "_", raw_pkg).strip("_") or "case2code_mobile"
        if raw_pkg[0].isdigit():
            raw_pkg = f"app_{raw_pkg}"
        dart_package_name = f"{raw_pkg}_mobile"

        # Reuse SpringBootGenerator's relationship & inheritance resolution so mobile matches backend 100%
        relationships_by_class, inheritance_map, parent_classes, interfaces_map = (
            self._spring_helper._resolve_relationships(document)
        )

        class_by_name: Dict[str, UmlClass] = {c.name: c for c in document.classes}
        entities_ctx: List[Dict[str, Any]] = []
        spring_entities_for_schema: List[Dict[str, Any]] = []

        for uml_class in document.classes:
            class_rels = relationships_by_class.get(uml_class.id, [])
            super_class = inheritance_map.get(uml_class.id)
            is_parent = uml_class.id in parent_classes
            interfaces = interfaces_map.get(uml_class.id, [])

            # Collect inherited attributes from parent hierarchy (same order as SpringBootGenerator)
            inherited_attrs: List[UmlAttribute] = []
            curr_parent_name = super_class
            visited_parents: Set[str] = set()
            while curr_parent_name and curr_parent_name not in visited_parents:
                visited_parents.add(curr_parent_name)
                parent_cls = class_by_name.get(curr_parent_name)
                if parent_cls:
                    for a in parent_cls.attributes:
                        if not a.primary_key:
                            inherited_attrs.append(a)
                    curr_parent_name = inheritance_map.get(parent_cls.id)
                else:
                    break

            spring_entity_data = self._spring_helper._build_entity_context(
                uml_class,
                class_rels,
                super_class=super_class,
                is_parent_class=is_parent,
                interfaces=interfaces,
                inherited_attributes=inherited_attrs,
            )
            spring_entities_for_schema.append(spring_entity_data)

            entity_ctx = self._build_flutter_entity_context(
                uml_class=uml_class,
                inherited_attributes=inherited_attrs,
                class_rels=class_rels,
                super_class=super_class,
            )
            entities_ctx.append(entity_ctx)

        root_ctx = {
            "dart_package_name": dart_package_name,
            "project_name": document.name or "Case2Code App",
            "description": document.description
            or f"Generated Flutter mobile application for {document.name}",
            "entities": entities_ctx,
        }

        # 1. Render root files (pubspec.yaml, analysis_options.yaml, README.md, assets/app-schema.json)
        self._render_to_file("pubspec.yaml.jinja2", root_ctx, output_dir / "pubspec.yaml")
        self._render_to_file(
            "analysis_options.yaml.jinja2",
            root_ctx,
            output_dir / "analysis_options.yaml",
        )
        self._render_to_file("README.md.jinja2", root_ctx, output_dir / "README.md")

        app_schema_dict = self._spring_helper._build_app_schema(
            document, spring_entities_for_schema
        )
        (assets_dir / "app-schema.json").write_text(
            json.dumps(app_schema_dict, indent=2), encoding="utf-8"
        )

        # 2. Render lib/main.dart, lib/config/app_schema.dart, lib/services/api_service.dart
        self._render_to_file("main.dart.jinja2", root_ctx, lib_dir / "main.dart")
        self._render_to_file(
            "app_schema.dart.jinja2", root_ctx, config_dir / "app_schema.dart"
        )
        self._render_to_file(
            "api_service.dart.jinja2", root_ctx, services_dir / "api_service.dart"
        )

        # 3. Render typed Dart model per entity
        for entity_ctx in entities_ctx:
            self._render_to_file(
                "entity_model.dart.jinja2",
                {"entity": entity_ctx},
                models_dir / f"{entity_ctx['snake_name']}.dart",
            )

        # 4. Render generic adaptive screens
        self._render_to_file(
            "home_screen.dart.jinja2", root_ctx, screens_dir / "home_screen.dart"
        )
        self._render_to_file(
            "entity_list_screen.dart.jinja2",
            root_ctx,
            screens_dir / "entity_list_screen.dart",
        )
        self._render_to_file(
            "entity_detail_screen.dart.jinja2",
            root_ctx,
            screens_dir / "entity_detail_screen.dart",
        )
        self._render_to_file(
            "entity_form_screen.dart.jinja2",
            root_ctx,
            screens_dir / "entity_form_screen.dart",
        )
        self._render_to_file(
            "assistant_screen.dart.jinja2",
            root_ctx,
            screens_dir / "assistant_screen.dart",
        )

        return output_dir

    def _render_to_file(self, template_name: str, context: Dict[str, Any], dest: Path) -> None:
        template = self.jinja_env.get_template(template_name)
        dest.write_text(template.render(context), encoding="utf-8")

    def _build_flutter_entity_context(
        self,
        uml_class: UmlClass,
        inherited_attributes: List[UmlAttribute],
        class_rels: List[Dict[str, Any]],
        super_class: Optional[str],
    ) -> Dict[str, Any]:
        snake_name = to_snake_case(uml_class.name)
        endpoint_path = to_plural(uml_class.name)

        all_fields: List[Dict[str, Any]] = []
        seen_names: Set[str] = set()
        pk_field = "id"

        # Find PK attribute
        pk_attr = next((a for a in uml_class.attributes if a.primary_key), None)
        if pk_attr:
            pk_field = pk_attr.name
            dart_type, field_kind, from_json_expr = _map_uml_type_to_dart(
                pk_attr.type, pk_attr.name
            )
            all_fields.append(
                {
                    "name": pk_attr.name,
                    "label": _humanize_identifier(pk_attr.name),
                    "uml_type": pk_attr.type or "Long",
                    "dart_type": dart_type,
                    "field_kind": field_kind,
                    "from_json_expr": from_json_expr,
                    "primary_key": True,
                    "required": True,
                }
            )
            seen_names.add(pk_attr.name)
        else:
            dart_type, field_kind, from_json_expr = _map_uml_type_to_dart("Long", "id")
            all_fields.append(
                {
                    "name": "id",
                    "label": "Id",
                    "uml_type": "Long",
                    "dart_type": dart_type,
                    "field_kind": field_kind,
                    "from_json_expr": from_json_expr,
                    "primary_key": True,
                    "required": True,
                }
            )
            seen_names.add("id")

        # Add inherited attributes (so forms for subclasses include parent attributes)
        for attr in inherited_attributes:
            if attr.name in seen_names:
                continue
            seen_names.add(attr.name)
            dart_type, field_kind, from_json_expr = _map_uml_type_to_dart(
                attr.type, attr.name
            )
            all_fields.append(
                {
                    "name": attr.name,
                    "label": _humanize_identifier(attr.name),
                    "uml_type": attr.type or "String",
                    "dart_type": dart_type,
                    "field_kind": field_kind,
                    "from_json_expr": from_json_expr,
                    "primary_key": False,
                    "required": not attr.nullable,
                }
            )

        # Add class's own non-PK attributes
        for attr in uml_class.attributes:
            if attr.primary_key or attr.name in seen_names:
                continue
            seen_names.add(attr.name)
            dart_type, field_kind, from_json_expr = _map_uml_type_to_dart(
                attr.type, attr.name
            )
            all_fields.append(
                {
                    "name": attr.name,
                    "label": _humanize_identifier(attr.name),
                    "uml_type": attr.type or "String",
                    "dart_type": dart_type,
                    "field_kind": field_kind,
                    "from_json_expr": from_json_expr,
                    "primary_key": False,
                    "required": not attr.nullable,
                }
            )

        foreign_keys: List[Dict[str, Any]] = []
        child_collections: List[Dict[str, Any]] = []

        for rel in class_rels:
            jpa_ann = rel.get("jpa_annotation", "")
            target_cls_name = rel["target_class_name"]
            field_name = rel["field_name"]
            target_endpoint = to_plural(target_cls_name)

            if not rel.get("is_collection", False):
                if jpa_ann == "@ManyToOne" or (
                    jpa_ann == "@OneToOne" and rel.get("is_owner", False)
                ):
                    foreign_keys.append(
                        {
                            "field_name": field_name,
                            "label": _humanize_identifier(field_name),
                            "target_entity": target_cls_name,
                            "target_endpoint_path": target_endpoint,
                            "relationship_type": jpa_ann.replace("@", ""),
                        }
                    )
            else:
                if jpa_ann == "@OneToMany" and rel.get("mapped_by"):
                    child_collections.append(
                        {
                            "field_name": field_name,
                            "label": _humanize_identifier(field_name),
                            "target_entity": target_cls_name,
                            "target_endpoint_path": target_endpoint,
                            "mapped_by": rel["mapped_by"],
                        }
                    )

        return {
            "name": uml_class.name,
            "display_name": _humanize_identifier(uml_class.name),
            "snake_name": snake_name,
            "table_name": snake_name,
            "endpoint_path": endpoint_path,
            "pk_field": pk_field,
            "super_class": super_class,
            "all_fields": all_fields,
            "foreign_keys": foreign_keys,
            "child_collections": child_collections,
        }
