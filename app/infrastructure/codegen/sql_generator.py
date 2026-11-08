from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Dict, List, Optional, Set, Tuple

from app.application.ports.code_generator import SqlDialect, SqlGeneratorPort
from app.domain.models.canonical_uml import (
    CanonicalUmlDocument,
    RelationshipTypeEnum,
    UmlAttribute,
    UmlClass,
)
from app.infrastructure.codegen.spring_boot_generator import to_plural, to_snake_case


def _ensure_id_suffix(name: str) -> str:
    cleaned = to_snake_case(name).strip("_")
    if not cleaned:
        return "ref_id"
    if cleaned.endswith("_id"):
        return cleaned
    return f"{cleaned}_id"


def _sanitize_identifier(name: str, fallback: str = "col") -> str:
    s = to_snake_case(name or "")
    s = re.sub(r"[^a-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return fallback
    if s[0].isdigit():
        s = f"t_{s}"
    return s


@dataclass
class SqlColumnDef:
    name: str
    uml_type: str
    primary_key: bool = False
    auto_increment: bool = False
    nullable: bool = True
    unique: bool = False
    default_value: Optional[str] = None
    comment: Optional[str] = None


@dataclass
class SqlForeignKeyDef:
    constraint_name: str
    column_name: str
    target_table: str
    target_column: str
    on_delete: str = "CASCADE"


@dataclass
class SqlTableDef:
    name: str
    columns: List[SqlColumnDef] = field(default_factory=list)
    primary_keys: List[str] = field(default_factory=list)
    foreign_keys: List[SqlForeignKeyDef] = field(default_factory=list)
    is_join_table: bool = False
    comment: Optional[str] = None


class SqlGenerator(SqlGeneratorPort):
    """Generates multi-dialect relational DDL scripts from a CanonicalUmlDocument."""

    DIALECT_LABELS: Dict[SqlDialect, str] = {
        SqlDialect.POSTGRESQL: "PostgreSQL 14+",
        SqlDialect.MYSQL: "MySQL 8.0+ / MariaDB (InnoDB)",
        SqlDialect.SQLITE: "SQLite 3",
        SqlDialect.SQLSERVER: "Microsoft SQL Server (T-SQL)",
    }

    def generate_sql(
        self,
        document: CanonicalUmlDocument,
        dialect: SqlDialect = SqlDialect.POSTGRESQL,
    ) -> str:
        tables = self._build_relational_schema(document)
        ordered_tables, deferred_fks = self._sort_tables_topologically(tables)
        return self._render_ddl(document, ordered_tables, deferred_fks, dialect)

    def _quote(self, identifier: str, dialect: SqlDialect) -> str:
        if dialect == SqlDialect.MYSQL:
            return f"`{identifier}`"
        if dialect == SqlDialect.SQLSERVER:
            return f"[{identifier}]"
        return f'"{identifier}"'

    def _map_column_type(self, col: SqlColumnDef, dialect: SqlDialect) -> str:
        norm = (col.uml_type or "String").strip().lower()

        # Handle Auto-Increment Primary Keys
        if col.primary_key and col.auto_increment:
            is_small_int = norm in ("int", "integer", "short", "smallint")
            if dialect == SqlDialect.POSTGRESQL:
                return "SERIAL" if is_small_int else "BIGSERIAL"
            if dialect == SqlDialect.MYSQL:
                return "INT AUTO_INCREMENT" if is_small_int else "BIGINT AUTO_INCREMENT"
            if dialect == SqlDialect.SQLITE:
                # SQLite requires INTEGER PRIMARY KEY AUTOINCREMENT
                return "INTEGER"
            if dialect == SqlDialect.SQLSERVER:
                return "INT IDENTITY(1,1)" if is_small_int else "BIGINT IDENTITY(1,1)"

        # Standard / Foreign Key / Non-Auto-Increment Types
        if norm in ("long", "bigint"):
            return "INTEGER" if dialect == SqlDialect.SQLITE else "BIGINT"
        if norm in ("int", "integer", "short", "smallint"):
            if dialect == SqlDialect.POSTGRESQL or dialect == SqlDialect.SQLITE:
                return "INTEGER"
            return "INT"
        if norm in ("double", "float", "real"):
            if dialect == SqlDialect.POSTGRESQL:
                return "DOUBLE PRECISION"
            if dialect == SqlDialect.MYSQL:
                return "DOUBLE"
            if dialect == SqlDialect.SQLITE:
                return "REAL"
            return "FLOAT"
        if norm in ("bigdecimal", "decimal", "numeric", "money", "currency"):
            if dialect == SqlDialect.POSTGRESQL:
                return "NUMERIC(19, 2)"
            if dialect == SqlDialect.SQLITE:
                return "NUMERIC"
            return "DECIMAL(19, 2)"
        if norm in ("boolean", "bool"):
            if dialect == SqlDialect.POSTGRESQL:
                return "BOOLEAN"
            if dialect == SqlDialect.MYSQL:
                return "TINYINT(1)"
            if dialect == SqlDialect.SQLITE:
                return "INTEGER"
            return "BIT"
        if norm in ("localdate", "date"):
            return "TEXT" if dialect == SqlDialect.SQLITE else "DATE"
        if norm in ("localdatetime", "datetime", "timestamp"):
            if dialect == SqlDialect.POSTGRESQL:
                return "TIMESTAMP"
            if dialect == SqlDialect.MYSQL:
                return "DATETIME"
            if dialect == SqlDialect.SQLITE:
                return "TEXT"
            return "DATETIME2"
        if norm == "text":
            if dialect == SqlDialect.SQLSERVER:
                return "NVARCHAR(MAX)"
            return "TEXT"
        if norm == "uuid":
            if dialect == SqlDialect.POSTGRESQL:
                return "UUID"
            if dialect == SqlDialect.MYSQL:
                return "CHAR(36)"
            if dialect == SqlDialect.SQLSERVER:
                return "UNIQUEIDENTIFIER"
            return "TEXT"

        # Default String / VARCHAR
        if dialect == SqlDialect.SQLITE:
            return "TEXT"
        if dialect == SqlDialect.SQLSERVER:
            return "NVARCHAR(255)"
        return "VARCHAR(255)"

    def _format_default_value(
        self, default_val: str, uml_type: str, dialect: SqlDialect
    ) -> str:
        raw = default_val.strip()
        norm_type = (uml_type or "String").strip().lower()

        if raw.upper() in ("NULL", "CURRENT_TIMESTAMP", "NOW()"):
            if raw.upper() == "NOW()" and dialect in (SqlDialect.SQLITE, SqlDialect.SQLSERVER):
                return "CURRENT_TIMESTAMP"
            return raw.upper()

        if norm_type in ("boolean", "bool"):
            is_true = raw.lower() in ("true", "1", "t", "yes", "si", "sí")
            if dialect == SqlDialect.POSTGRESQL:
                return "TRUE" if is_true else "FALSE"
            return "1" if is_true else "0"

        if norm_type in (
            "long",
            "bigint",
            "int",
            "integer",
            "double",
            "float",
            "real",
            "bigdecimal",
            "decimal",
            "numeric",
        ):
            if re.match(r"^-?\d+(\.\d+)?$", raw):
                return raw

        escaped = raw.strip("'\"").replace("'", "''")
        return f"'{escaped}'"

    def _get_pk_info(
        self, uml_class: UmlClass, class_by_id: Dict[str, UmlClass], inheritance_parent: Dict[str, str]
    ) -> Tuple[str, str]:
        """Returns (pk_column_name, pk_uml_type) for a class, resolving parent PK if inherited."""
        curr = uml_class
        visited: Set[str] = set()
        while curr.id in inheritance_parent and curr.id not in visited:
            visited.add(curr.id)
            parent_id = inheritance_parent[curr.id]
            parent_cls = class_by_id.get(parent_id)
            if parent_cls:
                curr = parent_cls
            else:
                break

        for attr in curr.attributes:
            if attr.primary_key:
                return _sanitize_identifier(attr.name, "id"), (attr.type or "Long")

        for attr in curr.attributes:
            if attr.name.strip().lower() == "id":
                return "id", (attr.type or "Long")

        return "id", "Long"

    def _build_relational_schema(self, document: CanonicalUmlDocument) -> List[SqlTableDef]:
        class_by_id: Dict[str, UmlClass] = {c.id: c for c in document.classes}
        inheritance_parent: Dict[str, str] = {}

        for rel in document.relationships:
            if (
                rel.type == RelationshipTypeEnum.INHERITANCE
                and rel.source_class_id in class_by_id
                and rel.target_class_id in class_by_id
                and rel.source_class_id != rel.target_class_id
            ):
                inheritance_parent[rel.source_class_id] = rel.target_class_id

        table_by_class_id: Dict[str, SqlTableDef] = {}
        pk_info_by_class_id: Dict[str, Tuple[str, str]] = {}
        used_table_names: Set[str] = set()

        # 1. Build base tables from UML classes
        for uml_class in document.classes:
            base_table_name = _sanitize_identifier(uml_class.name, "entity")
            table_name = base_table_name
            suffix = 2
            while table_name in used_table_names:
                table_name = f"{base_table_name}_{suffix}"
                suffix += 1
            used_table_names.add(table_name)

            pk_col_name, pk_uml_type = self._get_pk_info(
                uml_class, class_by_id, inheritance_parent
            )
            pk_info_by_class_id[uml_class.id] = (pk_col_name, pk_uml_type)

            is_subclass = uml_class.id in inheritance_parent
            is_auto_inc = (not is_subclass) and pk_uml_type.strip().lower() in (
                "long",
                "bigint",
                "int",
                "integer",
                "short",
                "smallint",
            )

            columns: List[SqlColumnDef] = []
            seen_cols: Set[str] = set()

            # Ensure PK column is first
            pk_default: Optional[str] = None
            for attr in uml_class.attributes:
                col_n = _sanitize_identifier(attr.name, "col")
                if attr.primary_key or col_n == pk_col_name:
                    pk_default = attr.default_value
                    break

            columns.append(
                SqlColumnDef(
                    name=pk_col_name,
                    uml_type=pk_uml_type,
                    primary_key=True,
                    auto_increment=is_auto_inc,
                    nullable=False,
                    default_value=None if is_auto_inc else pk_default,
                )
            )
            seen_cols.add(pk_col_name)

            # Add non-PK attributes
            for attr in uml_class.attributes:
                col_name = _sanitize_identifier(attr.name, "column")
                if attr.primary_key or col_name in seen_cols:
                    continue
                seen_cols.add(col_name)
                columns.append(
                    SqlColumnDef(
                        name=col_name,
                        uml_type=attr.type or "String",
                        primary_key=False,
                        auto_increment=False,
                        nullable=attr.nullable,
                        default_value=attr.default_value,
                    )
                )

            table_def = SqlTableDef(
                name=table_name,
                columns=columns,
                primary_keys=[pk_col_name],
                foreign_keys=[],
                comment=f"Entity: {uml_class.name}",
            )

            table_by_class_id[uml_class.id] = table_def

        # 2. Add Joined Inheritance Foreign Keys (Subclass PK -> Parent PK)
        for child_id, parent_id in inheritance_parent.items():
            child_table = table_by_class_id[child_id]
            parent_table = table_by_class_id[parent_id]
            child_pk_col, _ = pk_info_by_class_id[child_id]
            parent_pk_col, _ = pk_info_by_class_id[parent_id]
            child_table.foreign_keys.append(
                SqlForeignKeyDef(
                    constraint_name=f"fk_{child_table.name}_inherits_{parent_table.name}",
                    column_name=child_pk_col,
                    target_table=parent_table.name,
                    target_column=parent_pk_col,
                    on_delete="CASCADE",
                )
            )

        # 3. Resolve Relationships into Foreign Keys and Associative Join Tables
        join_tables: List[SqlTableDef] = []

        for rel in document.relationships:
            src_cls = class_by_id.get(rel.source_class_id)
            tgt_cls = class_by_id.get(rel.target_class_id)
            if not src_cls or not tgt_cls:
                continue

            if rel.type in (
                RelationshipTypeEnum.INHERITANCE,
                RelationshipTypeEnum.REALIZATION,
                RelationshipTypeEnum.DEPENDENCY,
            ):
                continue

            src_table = table_by_class_id[src_cls.id]
            tgt_table = table_by_class_id[tgt_cls.id]
            src_pk_col, src_pk_type = pk_info_by_class_id[src_cls.id]
            tgt_pk_col, tgt_pk_type = pk_info_by_class_id[tgt_cls.id]
            is_self = src_cls.id == tgt_cls.id

            if rel.type == RelationshipTypeEnum.MANY_TO_MANY:
                raw_join_name = (
                    _sanitize_identifier(rel.name, "")
                    if rel.name
                    else f"{src_table.name}_{tgt_table.name}"
                )
                if not raw_join_name:
                    raw_join_name = f"{src_table.name}_{tgt_table.name}"

                # If an explicit junction class with this exact table name already exists, skip duplicate
                if raw_join_name in used_table_names:
                    continue

                used_table_names.add(raw_join_name)

                left_base = rel.source_role or src_table.name
                right_base = rel.target_role or (
                    f"related_{tgt_table.name}" if is_self else tgt_table.name
                )
                left_col = _ensure_id_suffix(left_base)
                right_col = _ensure_id_suffix(right_base)
                if left_col == right_col:
                    right_col = f"target_{right_col}"

                join_table = SqlTableDef(
                    name=raw_join_name,
                    columns=[
                        SqlColumnDef(
                            name=left_col,
                            uml_type=src_pk_type,
                            primary_key=True,
                            auto_increment=False,
                            nullable=False,
                        ),
                        SqlColumnDef(
                            name=right_col,
                            uml_type=tgt_pk_type,
                            primary_key=True,
                            auto_increment=False,
                            nullable=False,
                        ),
                    ],
                    primary_keys=[left_col, right_col],
                    foreign_keys=[
                        SqlForeignKeyDef(
                            constraint_name=f"fk_{raw_join_name}_{left_col}",
                            column_name=left_col,
                            target_table=src_table.name,
                            target_column=src_pk_col,
                            on_delete="CASCADE",
                        ),
                        SqlForeignKeyDef(
                            constraint_name=f"fk_{raw_join_name}_{right_col}",
                            column_name=right_col,
                            target_table=tgt_table.name,
                            target_column=tgt_pk_col,
                            on_delete="CASCADE",
                        ),
                    ],
                    is_join_table=True,
                    comment=f"Many-to-Many join table between {src_cls.name} and {tgt_cls.name}",
                )
                join_tables.append(join_table)

            elif rel.type == RelationshipTypeEnum.COMPOSITION:
                is_to_one = (rel.target_cardinality or "").strip() == "1"
                if is_to_one:
                    role_name = rel.source_role or (
                        f"sub_{to_plural(tgt_cls.name)}" if is_self else tgt_cls.name
                    )
                    self._attach_foreign_key(
                        owner_table=src_table,
                        raw_col_base=role_name,
                        fk_uml_type=tgt_pk_type,
                        target_table=tgt_table.name,
                        target_pk_col=tgt_pk_col,
                        nullable=True if is_self else False,
                        unique=True,
                        on_delete="CASCADE",
                    )
                else:
                    role_name = rel.target_role or (
                        f"parent_{src_table.name}" if is_self else src_table.name
                    )
                    self._attach_foreign_key(
                        owner_table=tgt_table,
                        raw_col_base=role_name,
                        fk_uml_type=src_pk_type,
                        target_table=src_table.name,
                        target_pk_col=src_pk_col,
                        nullable=True if is_self else False,
                        unique=False,
                        on_delete="CASCADE",
                    )

            elif rel.type == RelationshipTypeEnum.AGGREGATION:
                role_name = rel.target_role or (
                    f"parent_{src_table.name}" if is_self else src_table.name
                )
                self._attach_foreign_key(
                    owner_table=tgt_table,
                    raw_col_base=role_name,
                    fk_uml_type=src_pk_type,
                    target_table=src_table.name,
                    target_pk_col=src_pk_col,
                    nullable=True,
                    unique=False,
                    on_delete="SET NULL",
                )

            elif rel.type == RelationshipTypeEnum.ONE_TO_MANY:
                role_name = rel.target_role or (
                    f"parent_{src_table.name}" if is_self else src_table.name
                )
                is_required = (rel.source_cardinality or "1").strip() in ("1", "1..1") and not is_self
                self._attach_foreign_key(
                    owner_table=tgt_table,
                    raw_col_base=role_name,
                    fk_uml_type=src_pk_type,
                    target_table=src_table.name,
                    target_pk_col=src_pk_col,
                    nullable=not is_required,
                    unique=False,
                    on_delete="CASCADE" if is_required else "SET NULL",
                )

            elif rel.type == RelationshipTypeEnum.MANY_TO_ONE:
                role_name = rel.source_role or (
                    f"parent_{tgt_table.name}" if is_self else tgt_table.name
                )
                is_required = (rel.target_cardinality or "1").strip() in ("1", "1..1") and not is_self
                self._attach_foreign_key(
                    owner_table=src_table,
                    raw_col_base=role_name,
                    fk_uml_type=tgt_pk_type,
                    target_table=tgt_table.name,
                    target_pk_col=tgt_pk_col,
                    nullable=not is_required,
                    unique=False,
                    on_delete="CASCADE" if is_required else "SET NULL",
                )

            elif rel.type == RelationshipTypeEnum.ONE_TO_ONE:
                role_name = rel.source_role or (
                    f"related_{tgt_table.name}" if is_self else tgt_table.name
                )
                self._attach_foreign_key(
                    owner_table=src_table,
                    raw_col_base=role_name,
                    fk_uml_type=tgt_pk_type,
                    target_table=tgt_table.name,
                    target_pk_col=tgt_pk_col,
                    nullable=True,
                    unique=True,
                    on_delete="SET NULL",
                )

        return list(table_by_class_id.values()) + join_tables

    def _attach_foreign_key(
        self,
        owner_table: SqlTableDef,
        raw_col_base: str,
        fk_uml_type: str,
        target_table: str,
        target_pk_col: str,
        nullable: bool,
        unique: bool,
        on_delete: str,
    ) -> None:
        base_fk_col = _ensure_id_suffix(raw_col_base)
        existing_fk_cols = {fk.column_name for fk in owner_table.foreign_keys}
        existing_cols_by_name = {c.name: c for c in owner_table.columns}

        fk_col_name = base_fk_col
        # If column exists in owner_table and does not yet have a FK constraint (and is not PK), reuse it
        if (
            fk_col_name in existing_cols_by_name
            and fk_col_name not in existing_fk_cols
            and not existing_cols_by_name[fk_col_name].primary_key
        ):
            existing_col = existing_cols_by_name[fk_col_name]
            existing_col.uml_type = fk_uml_type
            if unique:
                existing_col.unique = True
            if existing_col.nullable and on_delete == "SET NULL":
                pass
            elif not existing_col.nullable and on_delete == "SET NULL":
                on_delete = "CASCADE"
        else:
            # Ensure unique column name if already used
            suffix = 2
            while fk_col_name in existing_cols_by_name:
                stem = base_fk_col[:-3] if base_fk_col.endswith("_id") else base_fk_col
                fk_col_name = f"{stem}_{suffix}_id"
                suffix += 1

            owner_table.columns.append(
                SqlColumnDef(
                    name=fk_col_name,
                    uml_type=fk_uml_type,
                    primary_key=False,
                    auto_increment=False,
                    nullable=nullable,
                    unique=unique,
                )
            )

        constraint_name = f"fk_{owner_table.name}_{fk_col_name}"
        owner_table.foreign_keys.append(
            SqlForeignKeyDef(
                constraint_name=constraint_name,
                column_name=fk_col_name,
                target_table=target_table,
                target_column=target_pk_col,
                on_delete=on_delete,
            )
        )

    def _sort_tables_topologically(
        self, tables: List[SqlTableDef]
    ) -> Tuple[List[SqlTableDef], List[Tuple[str, SqlForeignKeyDef]]]:
        """Orders tables so referenced parent tables come before child tables.
        Returns (ordered_tables, deferred_cyclic_fks).
        """
        table_map = {t.name: t for t in tables}
        deps: Dict[str, Set[str]] = {t.name: set() for t in tables}
        reverse_deps: Dict[str, Set[str]] = defaultdict(set)

        for t in tables:
            for fk in t.foreign_keys:
                if fk.target_table in table_map and fk.target_table != t.name:
                    deps[t.name].add(fk.target_table)
                    reverse_deps[fk.target_table].add(t.name)

        in_degree = {name: len(d) for name, d in deps.items()}
        queue = deque([t.name for t in tables if in_degree[t.name] == 0])
        ordered_names: List[str] = []

        while queue:
            curr = queue.popleft()
            ordered_names.append(curr)
            for dependent in sorted(reverse_deps[curr]):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # If cycles exist, append remaining tables in deterministic order and mark cyclic back-edges
        if len(ordered_names) < len(tables):
            placed = set(ordered_names)
            for t in tables:
                if t.name not in placed:
                    ordered_names.append(t.name)
                    placed.add(t.name)

        order_index = {name: idx for idx, name in enumerate(ordered_names)}
        ordered_tables = [table_map[name] for name in ordered_names]
        deferred_fks: List[Tuple[str, SqlForeignKeyDef]] = []

        for t in ordered_tables:
            for fk in t.foreign_keys:
                if (
                    fk.target_table in order_index
                    and fk.target_table != t.name
                    and order_index[fk.target_table] > order_index[t.name]
                ):
                    deferred_fks.append((t.name, fk))

        return ordered_tables, deferred_fks

    def _render_ddl(
        self,
        document: CanonicalUmlDocument,
        ordered_tables: List[SqlTableDef],
        deferred_fks: List[Tuple[str, SqlForeignKeyDef]],
        dialect: SqlDialect,
    ) -> str:
        lines: List[str] = []
        dialect_label = self.DIALECT_LABELS.get(dialect, dialect.value)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        lines.append("-- =============================================================================")
        lines.append(f"-- Case2Code - Generated SQL DDL Script")
        lines.append(f"-- Project : {document.name} (v{document.version})")
        lines.append(f"-- Dialect : {dialect_label}")
        lines.append(f"-- Date    : {timestamp}")
        lines.append("-- =============================================================================")
        lines.append("")

        if not ordered_tables:
            lines.append("-- Warning: The diagram does not contain any classes yet.")
            return "\n".join(lines) + "\n"

        # 1. Dialect-specific session / pragma headers
        if dialect == SqlDialect.SQLITE:
            lines.append("PRAGMA foreign_keys = ON;")
            lines.append("")
        elif dialect == SqlDialect.MYSQL:
            lines.append("SET FOREIGN_KEY_CHECKS = 0;")
            lines.append("")

        # 2. Drop existing tables in reverse dependency order
        lines.append("-- Drop existing tables in reverse dependency order")
        for t in reversed(ordered_tables):
            q_table = self._quote(t.name, dialect)
            if dialect == SqlDialect.POSTGRESQL:
                lines.append(f"DROP TABLE IF EXISTS {q_table} CASCADE;")
            elif dialect == SqlDialect.SQLSERVER:
                lines.append(f"IF OBJECT_ID(N'dbo.{q_table}', N'U') IS NOT NULL DROP TABLE {q_table};")
            else:
                lines.append(f"DROP TABLE IF EXISTS {q_table};")
        lines.append("")

        deferred_set = {
            (table_name, fk.constraint_name) for table_name, fk in deferred_fks
        }

        # 3. Create tables in topological order
        for t in ordered_tables:
            q_table = self._quote(t.name, dialect)
            if t.comment:
                lines.append(f"-- {t.comment}")
            lines.append(f"CREATE TABLE {q_table} (")

            stmt_items: List[str] = []
            single_pk = len(t.primary_keys) == 1

            for col in t.columns:
                q_col = self._quote(col.name, dialect)
                col_type = self._map_column_type(col, dialect)
                parts = [f"    {q_col} {col_type}"]

                if col.primary_key and single_pk and dialect == SqlDialect.SQLITE and col.auto_increment:
                    parts.append("PRIMARY KEY AUTOINCREMENT")
                else:
                    if not col.nullable or col.primary_key:
                        parts.append("NOT NULL")
                    else:
                        parts.append("NULL")

                    if col.unique and not col.primary_key:
                        parts.append("UNIQUE")

                    if col.default_value is not None and not col.auto_increment:
                        formatted_def = self._format_default_value(
                            col.default_value, col.uml_type, dialect
                        )
                        parts.append(f"DEFAULT {formatted_def}")

                stmt_items.append(" ".join(parts))

            # Primary Key constraint (unless already inline for SQLite AUTOINCREMENT)
            sqlite_inline_pk = (
                single_pk
                and dialect == SqlDialect.SQLITE
                and any(c.primary_key and c.auto_increment for c in t.columns)
            )
            if t.primary_keys and not sqlite_inline_pk:
                pk_cols = ", ".join(self._quote(pk, dialect) for pk in t.primary_keys)
                pk_constraint = self._quote(f"pk_{t.name}", dialect)
                stmt_items.append(f"    CONSTRAINT {pk_constraint} PRIMARY KEY ({pk_cols})")

            # Foreign Key constraints (inline unless deferred due to cycle in PG/MySQL/SQLServer)
            for fk in t.foreign_keys:
                if dialect != SqlDialect.SQLITE and (t.name, fk.constraint_name) in deferred_set:
                    continue
                q_cname = self._quote(fk.constraint_name, dialect)
                q_col = self._quote(fk.column_name, dialect)
                q_target_table = self._quote(fk.target_table, dialect)
                q_target_col = self._quote(fk.target_column, dialect)
                on_delete_clause = fk.on_delete
                if dialect == SqlDialect.SQLSERVER and fk.target_table == t.name:
                    # SQL Server forbids ON DELETE CASCADE/SET NULL on self-referencing FKs
                    on_delete_clause = "NO ACTION"
                stmt_items.append(
                    f"    CONSTRAINT {q_cname} FOREIGN KEY ({q_col}) "
                    f"REFERENCES {q_target_table} ({q_target_col}) ON DELETE {on_delete_clause}"
                )

            lines.append(",\n".join(stmt_items))
            if dialect == SqlDialect.MYSQL:
                lines.append(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;")
            else:
                lines.append(");")
            lines.append("")

        # 4. Emit deferred cyclic Foreign Keys (if any) for non-SQLite dialects
        if deferred_fks and dialect != SqlDialect.SQLITE:
            lines.append("-- Deferred Foreign Keys for circular references")
            for table_name, fk in deferred_fks:
                q_table = self._quote(table_name, dialect)
                q_cname = self._quote(fk.constraint_name, dialect)
                q_col = self._quote(fk.column_name, dialect)
                q_target_table = self._quote(fk.target_table, dialect)
                q_target_col = self._quote(fk.target_column, dialect)
                lines.append(
                    f"ALTER TABLE {q_table} ADD CONSTRAINT {q_cname} "
                    f"FOREIGN KEY ({q_col}) REFERENCES {q_target_table} ({q_target_col}) "
                    f"ON DELETE {fk.on_delete};"
                )
            lines.append("")

        # 5. Create Indexes on Foreign Key Columns
        index_lines: List[str] = []
        for t in ordered_tables:
            pk_set = set(t.primary_keys)
            indexed_cols: Set[str] = set()
            for fk in t.foreign_keys:
                if fk.column_name in pk_set or fk.column_name in indexed_cols:
                    continue
                indexed_cols.add(fk.column_name)
                idx_name = self._quote(f"idx_{t.name}_{fk.column_name}", dialect)
                q_table = self._quote(t.name, dialect)
                q_col = self._quote(fk.column_name, dialect)
                if dialect in (SqlDialect.POSTGRESQL, SqlDialect.SQLITE):
                    index_lines.append(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {q_table} ({q_col});")
                else:
                    index_lines.append(f"CREATE INDEX {idx_name} ON {q_table} ({q_col});")

        if index_lines:
            lines.append("-- Foreign key indexes for join performance")
            lines.extend(index_lines)
            lines.append("")

        if dialect == SqlDialect.MYSQL:
            lines.append("SET FOREIGN_KEY_CHECKS = 1;")
            lines.append("")

        return "\n".join(lines)
