import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Set

from jinja2 import Environment, FileSystemLoader

from app.application.ports.code_generator import CodeGeneratorPort
from app.domain.models.canonical_uml import CanonicalUmlDocument, UmlClass
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
        for p in [src_main_java / "domain", src_main_java / "repository", src_main_java / "service", src_main_java / "controller", src_main_resources, src_test_java, src_test_resources]:
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

        # Render application.properties
        app_props_template = self.jinja_env.get_template("application.properties.jinja2")
        (src_main_resources / "application.properties").write_text(app_props_template.render(root_context), encoding="utf-8")

        # Render test application.properties
        test_props_template = self.jinja_env.get_template("test_application.properties.jinja2")
        (src_test_resources / "application.properties").write_text(test_props_template.render(root_context), encoding="utf-8")

        # Render ApplicationTests.java
        app_tests_template = self.jinja_env.get_template("ApplicationTests.java.jinja2")
        (src_test_java / "ApplicationTests.java").write_text(app_tests_template.render(root_context), encoding="utf-8")

        # Render each Entity & its layers
        entity_template = self.jinja_env.get_template("Entity.java.jinja2")
        repo_template = self.jinja_env.get_template("Repository.java.jinja2")
        service_template = self.jinja_env.get_template("Service.java.jinja2")
        controller_template = self.jinja_env.get_template("Controller.java.jinja2")
        int_test_template = self.jinja_env.get_template("EntityIntegrationTest.java.jinja2")

        for uml_class in document.classes:
            entity_data = self._build_entity_context(uml_class)
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
            # Write EntityIntegrationTest.java
            (src_test_java / f"{uml_class.name}IntegrationTest.java").write_text(
                int_test_template.render(ctx), encoding="utf-8"
            )

        # Copy Maven Wrapper files
        self._copy_maven_wrapper(output_dir)

        return output_dir

    def _build_entity_context(self, uml_class: UmlClass) -> Dict[str, Any]:
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

        # Ensure executable permission on Unix-like systems if run there
        try:
            mvnw_dest.chmod(0o755)
        except Exception:
            pass

