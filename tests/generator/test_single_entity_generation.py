import tempfile
from pathlib import Path

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    UmlAttribute,
    UmlClass,
    VisibilityEnum,
)
from app.infrastructure.codegen.spring_boot_generator import SpringBootGenerator


def test_single_entity_generation_structure():
    doc = CanonicalUmlDocument(
        id="diag-cliente",
        name="Peluqueria",
        classes=[
            UmlClass(
                id="c-cliente",
                name="Cliente",
                attributes=[
                    UmlAttribute(
                        id="a-1",
                        name="id",
                        type="Long",
                        primary_key=True,
                        nullable=False,
                    ),
                    UmlAttribute(
                        id="a-2",
                        name="nombre",
                        type="String",
                        nullable=False,
                    ),
                    UmlAttribute(
                        id="a-3",
                        name="telefono",
                        type="String",
                        nullable=True,
                    ),
                ],
            )
        ],
    )

    with tempfile.TemporaryDirectory(prefix="test_gen_") as tmp:
        out_dir = Path(tmp)
        generator = SpringBootGenerator()
        generator.generate(doc, out_dir)

        # 1. Root files
        assert (out_dir / "pom.xml").exists()
        assert (out_dir / "mvnw.cmd").exists()
        assert (out_dir / ".mvn" / "wrapper" / "maven-wrapper.properties").exists()

        # 2. Main Java files
        pkg_dir = out_dir / "src" / "main" / "java" / "com" / "case2code" / "app"
        assert (pkg_dir / "Application.java").exists()
        assert (pkg_dir / "domain" / "Cliente.java").exists()
        assert (pkg_dir / "repository" / "ClienteRepository.java").exists()
        assert (pkg_dir / "service" / "ClienteService.java").exists()
        assert (pkg_dir / "controller" / "ClienteController.java").exists()

        # 3. Resources
        assert (out_dir / "src" / "main" / "resources" / "application.properties").exists()
        assert (out_dir / "src" / "test" / "resources" / "application.properties").exists()

        # 4. Check contents of Cliente.java
        entity_content = (pkg_dir / "domain" / "Cliente.java").read_text(encoding="utf-8")
        assert "@Entity" in entity_content
        assert "@Getter" in entity_content
        assert "@Setter" in entity_content
        assert "@Builder" in entity_content
        assert "private Long id;" in entity_content
        assert "private String nombre;" in entity_content
        assert "private String telefono;" in entity_content

        # 5. Check contents of Controller
        controller_content = (pkg_dir / "controller" / "ClienteController.java").read_text(encoding="utf-8")
        assert '@RequestMapping("/api/clientes")' in controller_content
        assert "public ResponseEntity<Cliente> create" in controller_content
        assert "public ResponseEntity<List<Cliente>> getAll" in controller_content
