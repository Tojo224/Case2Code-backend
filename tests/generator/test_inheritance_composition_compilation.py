import shutil
import tempfile
from pathlib import Path

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
)
from app.infrastructure.codegen.compilation_verifier import CompilationVerifier
from app.infrastructure.codegen.spring_boot_generator import SpringBootGenerator


def test_inheritance_and_composition_spring_boot_compiles():
    doc = CanonicalUmlDocument(
        id="doc-inh-comp",
        name="InheritanceAndCompositionDemo",
        classes=[
            UmlClass(
                id="c1",
                name="Persona",
                attributes=[
                    UmlAttribute(id="a1", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="a2", name="nombre", type="String", nullable=False),
                ],
            ),
            UmlClass(
                id="c2",
                name="Cliente",
                attributes=[
                    # Child inherits id, declares its own code
                    UmlAttribute(id="a3", name="codigoCliente", type="String", nullable=False),
                ],
            ),
            UmlClass(
                id="c3",
                name="Pedido",
                attributes=[
                    UmlAttribute(id="a4", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="a5", name="total", type="Double", nullable=False),
                ],
            ),
            UmlClass(
                id="c4",
                name="DetallePedido",
                attributes=[
                    UmlAttribute(id="a6", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="a7", name="cantidad", type="Integer", nullable=False),
                ],
            ),
        ],
        relationships=[
            # Cliente extends Persona
            UmlRelationship(
                id="rel-1",
                type=RelationshipTypeEnum.INHERITANCE,
                source_class_id="c2",
                target_class_id="c1",
                source_handle="top-source",
                target_handle="bottom-target",
            ),
            # Pedido owns DetallePedido (Composition with cascade delete)
            UmlRelationship(
                id="rel-2",
                type=RelationshipTypeEnum.COMPOSITION,
                source_class_id="c3",
                target_class_id="c4",
                source_cardinality="1",
                target_cardinality="*",
                source_role="detalles",
                target_role="pedido",
                source_handle="right-source",
                target_handle="left-target",
            ),
        ],
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="test_inh_comp_"))
    try:
        generator = SpringBootGenerator()
        out_dir = generator.generate(doc, temp_dir)

        # Verify Java files generated
        persona_java = (out_dir / "src" / "main" / "java" / "com" / "case2code" / "app" / "domain" / "Persona.java").read_text(encoding="utf-8")
        assert "@Inheritance(strategy = InheritanceType.JOINED)" in persona_java

        cliente_java = (out_dir / "src" / "main" / "java" / "com" / "case2code" / "app" / "domain" / "Cliente.java").read_text(encoding="utf-8")
        assert "public class Cliente extends Persona" in cliente_java

        pedido_java = (out_dir / "src" / "main" / "java" / "com" / "case2code" / "app" / "domain" / "Pedido.java").read_text(encoding="utf-8")
        assert "cascade = CascadeType.ALL, orphanRemoval = true" in pedido_java

        # Verify Maven test and package pass
        verifier = CompilationVerifier()
        success, logs = verifier.verify(out_dir)
        assert success, f"Maven compilation failed for Inheritance & Composition:\n{logs}"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

