import tempfile
from pathlib import Path

from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
)
from app.infrastructure.codegen.spring_boot_generator import SpringBootGenerator


def test_one_to_many_relationship_code_generation():
    doc = CanonicalUmlDocument(
        id="diag-rel-test",
        name="PeluqueriaRel",
        classes=[
            UmlClass(
                id="c-cliente",
                name="Cliente",
                attributes=[
                    UmlAttribute(id="a-1", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="a-2", name="nombre", type="String"),
                ],
            ),
            UmlClass(
                id="c-reserva",
                name="Reserva",
                attributes=[
                    UmlAttribute(id="a-3", name="id", type="Long", primary_key=True),
                    UmlAttribute(id="a-4", name="fecha", type="String"),
                ],
            ),
        ],
        relationships=[
            UmlRelationship(
                id="rel-1",
                name="cliente_reservas",
                type=RelationshipTypeEnum.ONE_TO_MANY,
                source_class_id="c-cliente",
                target_class_id="c-reserva",
                source_cardinality="1",
                target_cardinality="*",
            )
        ],
    )

    with tempfile.TemporaryDirectory(prefix="test_rel_gen_") as tmp:
        out_dir = Path(tmp)
        generator = SpringBootGenerator()
        generator.generate(doc, out_dir)

        pkg_dir = out_dir / "src" / "main" / "java" / "com" / "case2code" / "app"
        test_dir = out_dir / "src" / "test" / "java" / "com" / "case2code" / "app"

        # 1. Verify Cliente.java contains @OneToMany
        cliente_code = (pkg_dir / "domain" / "Cliente.java").read_text(encoding="utf-8")
        assert "@OneToMany(mappedBy = \"cliente\", cascade = CascadeType.ALL)" in cliente_code
        assert "@JsonIgnoreProperties(\"cliente\")" in cliente_code
        assert "private List<Reserva> reservas" in cliente_code
        assert "@ToString.Exclude" in cliente_code
        assert "@EqualsAndHashCode.Exclude" in cliente_code

        # 2. Verify Reserva.java contains @ManyToOne
        reserva_code = (pkg_dir / "domain" / "Reserva.java").read_text(encoding="utf-8")
        assert "@ManyToOne(fetch = FetchType.LAZY)" in reserva_code
        assert "@JoinColumn(name = \"cliente_id\")" in reserva_code
        assert "@JsonIgnoreProperties(\"reservas\")" in reserva_code
        assert "private Cliente cliente;" in reserva_code

        # 3. Verify ReservaRepository has findByClienteId
        repo_code = (pkg_dir / "repository" / "ReservaRepository.java").read_text(encoding="utf-8")
        assert "List<Reserva> findByClienteId(Long clienteId);" in repo_code

        # 4. Verify ReservaController has /by-cliente/{clienteId} endpoint
        controller_code = (pkg_dir / "controller" / "ReservaController.java").read_text(encoding="utf-8")
        assert "@GetMapping(\"/by-cliente/{clienteId}\")" in controller_code

        # 5. Verify relationship test generated
        assert (test_dir / "ClienteReservaRelationshipIntegrationTest.java").exists()
        rel_test_code = (test_dir / "ClienteReservaRelationshipIntegrationTest.java").read_text(encoding="utf-8")
        assert "class ClienteReservaRelationshipIntegrationTest" in rel_test_code
        assert "target.setCliente(savedSource);" in rel_test_code
