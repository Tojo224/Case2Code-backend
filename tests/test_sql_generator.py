import sqlite3
import pytest

from app.application.ports.code_generator import SqlDialect
from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
    UmlRelationship,
)
from app.infrastructure.codegen.sql_generator import SqlGenerator


def _build_complex_domain_document() -> CanonicalUmlDocument:
    return CanonicalUmlDocument(
        id="diag-vet-01",
        name="Veterinaria",
        description="Complete veterinary domain model",
        version=3,
        classes=[
            UmlClass(
                id="cls-persona",
                name="Persona",
                attributes=[
                    UmlAttribute(id="a1", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a2", name="nombreCompleto", type="String", nullable=False),
                    UmlAttribute(id="a3", name="activo", type="Boolean", nullable=False, default_value="true"),
                ],
            ),
            UmlClass(
                id="cls-cliente",
                name="Cliente",
                attributes=[
                    UmlAttribute(id="a4", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a5", name="puntosFidelidad", type="Integer", nullable=False, default_value="0"),
                ],
            ),
            UmlClass(
                id="cls-mascota",
                name="Mascota",
                attributes=[
                    UmlAttribute(id="a6", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a7", name="nombre", type="String", nullable=False),
                    UmlAttribute(id="a8", name="pesoKg", type="Double", nullable=True),
                    UmlAttribute(id="a9", name="fechaNacimiento", type="LocalDate", nullable=True),
                ],
            ),
            UmlClass(
                id="cls-historia",
                name="HistoriaClinica",
                attributes=[
                    UmlAttribute(id="a10", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a11", name="observaciones", type="Text", nullable=True),
                ],
            ),
            UmlClass(
                id="cls-vacuna",
                name="Vacuna",
                attributes=[
                    UmlAttribute(id="a12", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a13", name="lote", type="String", nullable=False),
                    UmlAttribute(id="a14", name="precio", type="BigDecimal", nullable=False),
                ],
            ),
        ],
        relationships=[
            # Cliente inherits from Persona
            UmlRelationship(
                id="r-inh",
                type=RelationshipTypeEnum.INHERITANCE,
                source_class_id="cls-cliente",
                target_class_id="cls-persona",
            ),
            # Cliente 1 -> * Mascota (Composition -> ON DELETE CASCADE)
            UmlRelationship(
                id="r-comp",
                type=RelationshipTypeEnum.COMPOSITION,
                source_class_id="cls-cliente",
                target_class_id="cls-mascota",
                source_cardinality="1",
                target_cardinality="*",
            ),
            # Mascota 1 -> 1 HistoriaClinica
            UmlRelationship(
                id="r-1to1",
                type=RelationshipTypeEnum.ONE_TO_ONE,
                source_class_id="cls-mascota",
                target_class_id="cls-historia",
                source_cardinality="1",
                target_cardinality="1",
            ),
            # Mascota * <-> * Vacuna
            UmlRelationship(
                id="r-m2m",
                type=RelationshipTypeEnum.MANY_TO_MANY,
                source_class_id="cls-mascota",
                target_class_id="cls-vacuna",
                source_cardinality="*",
                target_cardinality="*",
            ),
        ],
    )


def test_postgresql_ddl_generation():
    doc = _build_complex_domain_document()
    gen = SqlGenerator()
    sql = gen.generate_sql(doc, dialect=SqlDialect.POSTGRESQL)

    assert 'CREATE TABLE "persona"' in sql
    assert '"id" BIGSERIAL NOT NULL' in sql
    assert '"nombre_completo" VARCHAR(255) NOT NULL' in sql
    assert '"activo" BOOLEAN NOT NULL DEFAULT TRUE' in sql

    # Joined inheritance: Cliente PK is BIGINT (not BIGSERIAL) and references Persona(id)
    assert 'CREATE TABLE "cliente"' in sql
    assert '"id" BIGINT NOT NULL' in sql
    assert 'CONSTRAINT "fk_cliente_inherits_persona" FOREIGN KEY ("id") REFERENCES "persona" ("id") ON DELETE CASCADE' in sql

    # Composition 1:* Mascota has cliente_id FK ON DELETE CASCADE and 1:1 historia_clinica_id UNIQUE
    assert '"cliente_id" BIGINT NOT NULL' in sql
    assert '"historia_clinica_id" BIGINT NULL UNIQUE' in sql
    assert 'CREATE TABLE "mascota_vacuna"' in sql
    assert 'CONSTRAINT "pk_mascota_vacuna" PRIMARY KEY ("mascota_id", "vacuna_id")' in sql


def test_mysql_ddl_generation():
    doc = _build_complex_domain_document()
    gen = SqlGenerator()
    sql = gen.generate_sql(doc, dialect=SqlDialect.MYSQL)

    assert "SET FOREIGN_KEY_CHECKS = 0;" in sql
    assert "CREATE TABLE `persona`" in sql
    assert "`id` BIGINT AUTO_INCREMENT NOT NULL" in sql
    assert "`activo` TINYINT(1) NOT NULL DEFAULT 1" in sql
    assert ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;" in sql
    assert "SET FOREIGN_KEY_CHECKS = 1;" in sql


def test_sqlserver_ddl_generation():
    doc = _build_complex_domain_document()
    gen = SqlGenerator()
    sql = gen.generate_sql(doc, dialect=SqlDialect.SQLSERVER)

    assert "CREATE TABLE [persona]" in sql
    assert "[id] BIGINT IDENTITY(1,1) NOT NULL" in sql
    assert "[nombre_completo] NVARCHAR(255) NOT NULL" in sql
    assert "[observaciones] NVARCHAR(MAX) NULL" in sql
    assert "[precio] DECIMAL(19, 2) NOT NULL" in sql


def test_sqlite_ddl_executes_and_enforces_foreign_keys():
    doc = _build_complex_domain_document()
    gen = SqlGenerator()
    sql = gen.generate_sql(doc, dialect=SqlDialect.SQLITE)

    assert "PRAGMA foreign_keys = ON;" in sql
    assert '"id" INTEGER PRIMARY KEY AUTOINCREMENT' in sql

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(sql)
        cur = conn.cursor()

        # Insert Persona & Cliente (Joined Inheritance)
        cur.execute('INSERT INTO "persona" ("nombre_completo", "activo") VALUES (?, ?)', ("Ana Gomez", 1))
        persona_id = cur.lastrowid
        cur.execute('INSERT INTO "cliente" ("id", "puntos_fidelidad") VALUES (?, ?)', (persona_id, 100))

        # Insert Mascota owned by Cliente
        cur.execute(
            'INSERT INTO "mascota" ("nombre", "peso_kg", "cliente_id") VALUES (?, ?, ?)',
            ("Firulais", 12.5, persona_id),
        )
        mascota_id = cur.lastrowid

        # Insert Vacuna and link via Many-to-Many join table mascota_vacuna
        cur.execute('INSERT INTO "vacuna" ("lote", "precio") VALUES (?, ?)', ("LOTE-2026", 45.50))
        vacuna_id = cur.lastrowid
        cur.execute(
            'INSERT INTO "mascota_vacuna" ("mascota_id", "vacuna_id") VALUES (?, ?)',
            (mascota_id, vacuna_id),
        )

        # Verify cascade deletion from Persona -> Cliente -> Mascota -> mascota_vacuna
        cur.execute('DELETE FROM "persona" WHERE "id" = ?', (persona_id,))
        assert cur.execute('SELECT COUNT(*) FROM "cliente"').fetchone()[0] == 0
        assert cur.execute('SELECT COUNT(*) FROM "mascota"').fetchone()[0] == 0
        assert cur.execute('SELECT COUNT(*) FROM "mascota_vacuna"').fetchone()[0] == 0
    finally:
        conn.close()


def test_explicit_junction_table_reuses_existing_fk_columns():
    doc = CanonicalUmlDocument(
        id="diag-junc",
        name="Tienda",
        version=1,
        classes=[
            UmlClass(
                id="c-orden",
                name="Orden",
                attributes=[UmlAttribute(id="a1", name="id", type="Long", primary_key=True, nullable=False)],
            ),
            UmlClass(
                id="c-prod",
                name="Producto",
                attributes=[UmlAttribute(id="a2", name="id", type="Long", primary_key=True, nullable=False)],
            ),
            UmlClass(
                id="c-detalle",
                name="orden_producto",
                attributes=[
                    UmlAttribute(id="a3", name="id", type="Long", primary_key=True, nullable=False),
                    UmlAttribute(id="a4", name="orden_id", type="Long", primary_key=False, nullable=False),
                    UmlAttribute(id="a5", name="producto_id", type="Long", primary_key=False, nullable=False),
                ],
            ),
        ],
        relationships=[
            UmlRelationship(
                id="r1",
                type=RelationshipTypeEnum.ONE_TO_MANY,
                source_class_id="c-orden",
                target_class_id="c-detalle",
            ),
            UmlRelationship(
                id="r2",
                type=RelationshipTypeEnum.ONE_TO_MANY,
                source_class_id="c-prod",
                target_class_id="c-detalle",
            ),
        ],
    )
    gen = SqlGenerator()
    sql = gen.generate_sql(doc, dialect=SqlDialect.SQLITE)

    # Verify it executes cleanly in SQLite (no duplicate column orden_id or producto_id)
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(sql)
    finally:
        conn.close()


def test_sql_preview_and_export_endpoints(db_session, diagram_repo):
    from app.infrastructure.persistence.user_repository import SqlAlchemyUserRepository
    from app.presentation.routes.generator import export_sql_script, preview_sql_script

    user_repo = SqlAlchemyUserRepository(db_session)
    doc = _build_complex_domain_document()
    diagram_repo.save(doc)

    # 1. Preview endpoint
    data = preview_sql_script(
        diagram_id=doc.id,
        dialect=SqlDialect.POSTGRESQL,
        repo=diagram_repo,
        user_repo=user_repo,
        current_user=None,
    )
    assert data["diagram_id"] == doc.id
    assert data["dialect"] == "postgresql"
    assert data["filename"] == "Veterinaria_postgresql.sql"
    assert 'CREATE TABLE "mascota"' in data["sql"]

    # 2. Export endpoint (.sql file download)
    res_export = export_sql_script(
        diagram_id=doc.id,
        dialect=SqlDialect.MYSQL,
        repo=diagram_repo,
        user_repo=user_repo,
        current_user=None,
    )
    assert res_export.status_code == 200
    assert "application/sql" in res_export.media_type
    assert 'filename="Veterinaria_mysql.sql"' in res_export.headers["content-disposition"]
    assert "CREATE TABLE `mascota`" in res_export.body.decode("utf-8")


