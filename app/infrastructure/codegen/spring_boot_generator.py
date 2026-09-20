import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Set

from jinja2 import Environment, FileSystemLoader

from app.application.ports.code_generator import CodeGeneratorPort
from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlClass,
    UmlRelationship,
)
from app.infrastructure.codegen.type_mapper import map_uml_type_to_java


def to_snake_case(name: str) -> str:
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def to_plural(name: str) -> str:
    s = to_snake_case(name)
    if s.endswith("s"):
        return s
    if s.endswith("y") and not s.endswith("ay") and not s.endswith("ey"):
        return s[:-1] + "ies"
    return s + "s"


class SpringBootGenerator(CodeGeneratorPort):
    def __init__(self):
        templates_dir = Path(__file__).parent / "templates" / "spring_boot"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.assets_wrapper_dir = Path(__file__).parent / "assets" / "wrapper"

    def generate(self, document: CanonicalUmlDocument, output_dir: Path) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        package_name = "com.case2code.app"
        package_path = package_name.replace(".", "/")
        src_main_java = output_dir / "src" / "main" / "java" / package_path
        src_main_resources = output_dir / "src" / "main" / "resources"
        src_test_java = output_dir / "src" / "test" / "java" / package_path
        src_test_resources = output_dir / "src" / "test" / "resources"

        # Create directories
        for p in [
            src_main_java / "domain",
            src_main_java / "repository",
            src_main_java / "service",
            src_main_java / "controller",
            src_main_java / "config",
            src_main_java / "exception",
            src_main_java / "assistant" / "model",
            src_main_java / "assistant" / "handler",
            src_main_java / "assistant" / "interpreter",
            src_main_java / "assistant" / "controller",
            src_main_resources,
            src_test_java,
            src_test_resources,
        ]:
            p.mkdir(parents=True, exist_ok=True)

        # Context for root files
        artifact_id = to_snake_case(document.name).replace("_", "-") or "case2code-app"
        root_context = {
            "group_id": "com.case2code",
            "artifact_id": artifact_id,
            "project_name": document.name,
            "description": document.description or f"Generated Spring Boot service for {document.name}",
            "package_name": package_name,
        }

        # Render pom.xml
        pom_template = self.jinja_env.get_template("pom.xml.jinja2")
        (output_dir / "pom.xml").write_text(pom_template.render(root_context), encoding="utf-8")

        # Render Application.java
        app_template = self.jinja_env.get_template("Application.java.jinja2")
        (src_main_java / "Application.java").write_text(app_template.render(root_context), encoding="utf-8")

        # Render config and exception classes
        cors_template = self.jinja_env.get_template("CorsConfig.java.jinja2")
        (src_main_java / "config" / "CorsConfig.java").write_text(cors_template.render(root_context), encoding="utf-8")

        openapi_template = self.jinja_env.get_template("OpenApiConfig.java.jinja2")
        (src_main_java / "config" / "OpenApiConfig.java").write_text(openapi_template.render(root_context), encoding="utf-8")

        res_not_found_template = self.jinja_env.get_template("ResourceNotFoundException.java.jinja2")
        (src_main_java / "exception" / "ResourceNotFoundException.java").write_text(res_not_found_template.render(root_context), encoding="utf-8")

        error_resp_template = self.jinja_env.get_template("ErrorResponse.java.jinja2")
        (src_main_java / "exception" / "ErrorResponse.java").write_text(error_resp_template.render(root_context), encoding="utf-8")

        global_handler_template = self.jinja_env.get_template("GlobalExceptionHandler.java.jinja2")
        (src_main_java / "exception" / "GlobalExceptionHandler.java").write_text(global_handler_template.render(root_context), encoding="utf-8")

        # Render assistant core classes
        action_template = self.jinja_env.get_template("BusinessAction.java.jinja2")
        (src_main_java / "assistant" / "model" / "BusinessAction.java").write_text(action_template.render(root_context), encoding="utf-8")

        cmd_template = self.jinja_env.get_template("BusinessCommand.java.jinja2")
        (src_main_java / "assistant" / "model" / "BusinessCommand.java").write_text(cmd_template.render(root_context), encoding="utf-8")

        req_template = self.jinja_env.get_template("AssistantRequest.java.jinja2")
        (src_main_java / "assistant" / "model" / "AssistantRequest.java").write_text(req_template.render(root_context), encoding="utf-8")

        resp_template = self.jinja_env.get_template("AssistantResponse.java.jinja2")
        (src_main_java / "assistant" / "model" / "AssistantResponse.java").write_text(resp_template.render(root_context), encoding="utf-8")

        handler_interface_template = self.jinja_env.get_template("EntityOperationHandler.java.jinja2")
        (src_main_java / "assistant" / "handler" / "EntityOperationHandler.java").write_text(handler_interface_template.render(root_context), encoding="utf-8")

        registry_template = self.jinja_env.get_template("OperationRegistry.java.jinja2")
        (src_main_java / "assistant" / "handler" / "OperationRegistry.java").write_text(registry_template.render(root_context), encoding="utf-8")

        interpreter_template = self.jinja_env.get_template("BusinessCommandInterpreter.java.jinja2")
        (src_main_java / "assistant" / "interpreter" / "BusinessCommandInterpreter.java").write_text(interpreter_template.render(root_context), encoding="utf-8")

        asst_ctrl_template = self.jinja_env.get_template("AssistantController.java.jinja2")
        (src_main_java / "assistant" / "controller" / "AssistantController.java").write_text(asst_ctrl_template.render(root_context), encoding="utf-8")

        # Render application.properties
        app_props_template = self.jinja_env.get_template("application.properties.jinja2")
        (src_main_resources / "application.properties").write_text(app_props_template.render(root_context), encoding="utf-8")

        # Render test application.properties
        test_props_template = self.jinja_env.get_template("test_application.properties.jinja2")
        (src_test_resources / "application.properties").write_text(test_props_template.render(root_context), encoding="utf-8")

        # Render ApplicationTests.java
        app_tests_template = self.jinja_env.get_template("ApplicationTests.java.jinja2")
        (src_test_java / "ApplicationTests.java").write_text(app_tests_template.render(root_context), encoding="utf-8")

        # Resolve relationships across classes
        relationships_by_class = self._resolve_relationships(document)

        # Render each Entity & its layers
        entity_template = self.jinja_env.get_template("Entity.java.jinja2")
        repo_template = self.jinja_env.get_template("Repository.java.jinja2")
        service_template = self.jinja_env.get_template("Service.java.jinja2")
        controller_template = self.jinja_env.get_template("Controller.java.jinja2")
        int_test_template = self.jinja_env.get_template("EntityIntegrationTest.java.jinja2")
        entity_handler_template = self.jinja_env.get_template("EntityOperationHandlerImpl.java.jinja2")

        all_entities = []
        for uml_class in document.classes:
            class_rels = relationships_by_class.get(uml_class.id, [])
            entity_data = self._build_entity_context(uml_class, class_rels)
            all_entities.append(entity_data)
            ctx = {
                "package_name": package_name,
                "entity": entity_data,
            }

            # Write Entity.java
            (src_main_java / "domain" / f"{uml_class.name}.java").write_text(
                entity_template.render(ctx), encoding="utf-8"
            )
            # Write Repository.java
            (src_main_java / "repository" / f"{uml_class.name}Repository.java").write_text(
                repo_template.render(ctx), encoding="utf-8"
            )
            # Write Service.java
            (src_main_java / "service" / f"{uml_class.name}Service.java").write_text(
                service_template.render(ctx), encoding="utf-8"
            )
            # Write Controller.java
            (src_main_java / "controller" / f"{uml_class.name}Controller.java").write_text(
                controller_template.render(ctx), encoding="utf-8"
            )
            # Write EntityOperationHandler.java
            (src_main_java / "assistant" / "handler" / f"{uml_class.name}OperationHandler.java").write_text(
                entity_handler_template.render(ctx), encoding="utf-8"
            )
            # Write EntityIntegrationTest.java
            (src_test_java / f"{uml_class.name}IntegrationTest.java").write_text(
                int_test_template.render(ctx), encoding="utf-8"
            )

        # Render Assistant Integration Test
        asst_test_template = self.jinja_env.get_template("AssistantIntegrationTest.java.jinja2")
        asst_test_ctx = {
            "package_name": package_name,
            "first_entity": all_entities[0] if all_entities else None,
        }
        (src_test_java / "AssistantIntegrationTest.java").write_text(
            asst_test_template.render(asst_test_ctx), encoding="utf-8"
        )

        # Render Relationship Integration Tests
        rel_test_template = self.jinja_env.get_template("RelationshipIntegrationTest.java.jinja2")
        class_by_id = {c.id: c for c in document.classes}
        for rel in document.relationships:
            src = class_by_id.get(rel.source_class_id)
            tgt = class_by_id.get(rel.target_class_id)
            if src and tgt:
                rel_ctx = self._build_relationship_test_context(src, tgt, rel)
                test_filename = f"{src.name}{tgt.name}RelationshipIntegrationTest.java"
                (src_test_java / test_filename).write_text(
                    rel_test_template.render({"package_name": package_name, "rel": rel_ctx}),
                    encoding="utf-8",
                )

        # Render app-schema.json in root and in resources
        schema_dict = self._build_app_schema(document, all_entities)
        schema_json = json.dumps(schema_dict, indent=2)
        (output_dir / "app-schema.json").write_text(schema_json, encoding="utf-8")
        (src_main_resources / "app-schema.json").write_text(schema_json, encoding="utf-8")

        # Render README.md
        readme_template = self.jinja_env.get_template("README.md.jinja2")
        readme_ctx = {
            **root_context,
            "entities": all_entities,
        }
        (output_dir / "README.md").write_text(readme_template.render(readme_ctx), encoding="utf-8")

        # Copy Maven Wrapper files
        self._copy_maven_wrapper(output_dir)

        return output_dir

    def _resolve_relationships(self, document: CanonicalUmlDocument) -> Dict[str, List[Dict[str, Any]]]:
        class_by_id = {c.id: c for c in document.classes}
        rel_map: Dict[str, List[Dict[str, Any]]] = {c.id: [] for c in document.classes}

        for rel in document.relationships:
            src = class_by_id.get(rel.source_class_id)
            tgt = class_by_id.get(rel.target_class_id)
            if not src or not tgt:
                continue

            src_name = src.name
            tgt_name = tgt.name
            src_id_type = next((map_uml_type_to_java(a.type).java_type for a in src.attributes if a.primary_key), "Long")
            tgt_id_type = next((map_uml_type_to_java(a.type).java_type for a in tgt.attributes if a.primary_key), "Long")

            if rel.type == RelationshipTypeEnum.ONE_TO_MANY:
                # Source (Parent) has collection of Target
                field_src = rel.source_role or to_plural(tgt_name)
                field_tgt = rel.target_role or to_snake_case(src_name)

                rel_map[src.id].append({
                    "field_name": field_src,
                    "target_class_name": tgt_name,
                    "jpa_annotation": "@OneToMany",
                    "mapped_by": field_tgt,
                    "is_collection": True,
                    "opposite_field_name": field_tgt,
                })
                # Target (Child) has reference to Source
                rel_map[tgt.id].append({
                    "field_name": field_tgt,
                    "target_class_name": src_name,
                    "jpa_annotation": "@ManyToOne",
                    "join_column": f"{to_snake_case(field_tgt)}_id",
                    "is_collection": False,
                    "opposite_field_name": field_src,
                    "id_type": src_id_type,
                })

            elif rel.type == RelationshipTypeEnum.MANY_TO_ONE:
                field_src = rel.source_role or to_snake_case(tgt_name)
                field_tgt = rel.target_role or to_plural(src_name)

                rel_map[src.id].append({
                    "field_name": field_src,
                    "target_class_name": tgt_name,
                    "jpa_annotation": "@ManyToOne",
                    "join_column": f"{to_snake_case(field_src)}_id",
                    "is_collection": False,
                    "opposite_field_name": field_tgt,
                    "id_type": tgt_id_type,
                })
                rel_map[tgt.id].append({
                    "field_name": field_tgt,
                    "target_class_name": src_name,
                    "jpa_annotation": "@OneToMany",
                    "mapped_by": field_src,
                    "is_collection": True,
                    "opposite_field_name": field_src,
                })

            elif rel.type == RelationshipTypeEnum.ONE_TO_ONE:
                field_src = rel.source_role or to_snake_case(tgt_name)
                field_tgt = rel.target_role or to_snake_case(src_name)

                rel_map[src.id].append({
                    "field_name": field_src,
                    "target_class_name": tgt_name,
                    "jpa_annotation": "@OneToOne",
                    "join_column": f"{to_snake_case(field_src)}_id",
                    "is_owner": True,
                    "is_collection": False,
                    "opposite_field_name": field_tgt,
                })
                rel_map[tgt.id].append({
                    "field_name": field_tgt,
                    "target_class_name": src_name,
                    "jpa_annotation": "@OneToOne",
                    "mapped_by": field_src,
                    "is_owner": False,
                    "is_collection": False,
                    "opposite_field_name": field_src,
                })

            elif rel.type == RelationshipTypeEnum.MANY_TO_MANY:
                field_src = rel.source_role or to_plural(tgt_name)
                field_tgt = rel.target_role or to_plural(src_name)
                table_name = f"{to_snake_case(src_name)}_{to_snake_case(tgt_name)}"

                rel_map[src.id].append({
                    "field_name": field_src,
                    "target_class_name": tgt_name,
                    "jpa_annotation": "@ManyToMany",
                    "join_table": table_name,
                    "join_column": f"{to_snake_case(src_name)}_id",
                    "inverse_join_column": f"{to_snake_case(tgt_name)}_id",
                    "is_owner": True,
                    "is_collection": True,
                    "opposite_field_name": field_tgt,
                })
                rel_map[tgt.id].append({
                    "field_name": field_tgt,
                    "target_class_name": src_name,
                    "jpa_annotation": "@ManyToMany",
                    "mapped_by": field_src,
                    "is_owner": False,
                    "is_collection": True,
                    "opposite_field_name": field_src,
                })

        return rel_map

    def _build_relationship_test_context(
        self,
        src: UmlClass,
        tgt: UmlClass,
        rel: UmlRelationship,
    ) -> Dict[str, Any]:
        source_attrs = [
            {"name": a.name, "sample_value": map_uml_type_to_java(a.type).sample_value}
            for a in src.attributes
            if not a.primary_key
        ]
        target_attrs = [
            {"name": a.name, "sample_value": map_uml_type_to_java(a.type).sample_value}
            for a in tgt.attributes
            if not a.primary_key
        ]

        field_tgt = rel.target_role or to_snake_case(src.name)
        setter_name = f"set{field_tgt[0].upper()}{field_tgt[1:]}"
        getter_name = f"get{field_tgt[0].upper()}{field_tgt[1:]}"

        return {
            "test_class_name": f"{src.name}{tgt.name}RelationshipIntegrationTest",
            "source_class_name": src.name,
            "target_class_name": tgt.name,
            "source_sample_attrs": source_attrs,
            "target_sample_attrs": target_attrs,
            "target_to_source_setter": setter_name,
            "target_to_source_getter": getter_name,
        }

    def _build_entity_context(self, uml_class: UmlClass, relationships: List[Dict[str, Any]]) -> Dict[str, Any]:
        imports: Set[str] = set()
        attributes_list: List[Dict[str, Any]] = []
        updatable_attributes: List[Dict[str, Any]] = []
        id_type = "Long"

        for attr in uml_class.attributes:
            type_info = map_uml_type_to_java(attr.type)
            if type_info.import_stmt:
                imports.add(type_info.import_stmt)

            if attr.primary_key:
                id_type = type_info.java_type

            attr_dict = {
                "name": attr.name,
                "java_type": type_info.java_type,
                "column_name": to_snake_case(attr.name),
                "primary_key": attr.primary_key,
                "nullable": attr.nullable,
                "sample_value": type_info.sample_value,
            }
            attributes_list.append(attr_dict)
            if not attr.primary_key:
                updatable_attributes.append(attr_dict)

        return {
            "name": uml_class.name,
            "table_name": to_snake_case(uml_class.name),
            "endpoint_path": to_plural(uml_class.name),
            "id_type": id_type,
            "imports": sorted(list(imports)),
            "attributes": attributes_list,
            "updatable_attributes": updatable_attributes,
            "relationships": relationships,
        }

    def _copy_maven_wrapper(self, output_dir: Path) -> None:
        if not self.assets_wrapper_dir.exists():
            return

        mvnw_dest = output_dir / "mvnw"
        mvnw_cmd_dest = output_dir / "mvnw.cmd"
        wrapper_dir_dest = output_dir / ".mvn" / "wrapper"
        wrapper_dir_dest.mkdir(parents=True, exist_ok=True)

        shutil.copy2(self.assets_wrapper_dir / "mvnw", mvnw_dest)
        shutil.copy2(self.assets_wrapper_dir / "mvnw.cmd", mvnw_cmd_dest)
        shutil.copy2(
            self.assets_wrapper_dir / "maven-wrapper.properties",
            wrapper_dir_dest / "maven-wrapper.properties",
        )

        try:
            mvnw_dest.chmod(0o755)
        except Exception:
            pass

    def _build_app_schema(self, document: CanonicalUmlDocument, all_entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        entities_schema = []
        for entity in all_entities:
            fields = []
            pk_field = None
            for attr in entity["attributes"]:
                field_info = {
                    "name": attr["name"],
                    "type": attr["java_type"],
                    "required": not attr["nullable"],
                    "isPrimaryKey": attr["primary_key"],
                }
                fields.append(field_info)
                if attr["primary_key"]:
                    pk_field = field_info

            relationships = []
            for rel in entity["relationships"]:
                rel_info = {
                    "type": rel.get("jpa_annotation", "").replace("@", ""),
                    "targetEntity": rel["target_class_name"],
                    "fieldName": rel["field_name"],
                    "isCollection": rel.get("is_collection", False),
                }
                if "join_column" in rel:
                    rel_info["joinColumn"] = rel["join_column"]
                relationships.append(rel_info)

            base_path = f"/api/{entity['endpoint_path']}"
            entities_schema.append({
                "name": entity["name"],
                "tableName": entity["table_name"],
                "primaryKey": pk_field or {
                    "name": "id",
                    "type": "Long",
                    "required": True,
                    "isPrimaryKey": True,
                },
                "fields": fields,
                "relationships": relationships,
                "endpoints": {
                    "base": base_path,
                    "list": f"GET {base_path}",
                    "getById": f"GET {base_path}/{{id}}",
                    "create": f"POST {base_path}",
                    "update": f"PUT {base_path}/{{id}}",
                    "delete": f"DELETE {base_path}/{{id}}",
                },
            })

        return {
            "projectName": document.name,
            "version": "1.0.0",
            "description": document.description or f"Metadata schema for {document.name}",
            "generatedAt": document.updated_at.isoformat(),
            "entities": entities_schema,
        }

