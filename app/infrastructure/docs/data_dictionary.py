"""Genera el PDF "Diccionario de Datos y Reglas de Negocio" desde un modelo UML."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from app.domain.models.canonical_uml import CanonicalUmlDocument, RelationshipTypeEnum
from app.infrastructure.codegen.sql_generator import SqlGenerator, SqlTableDef, _sanitize_identifier

MAX_CONTENT_PAGES = 5
SCALES = [1.0, 0.9, 0.8, 0.7, 0.6]
HEAD_COLOR = colors.HexColor("#17475E")
TABLE_COLOR = colors.HexColor("#4BACC6")
LABEL_BG = colors.HexColor("#D9E8F5")
MAX_RULES = 12


@dataclass
class DictionaryResult:
    pdf: bytes
    content_pages: int
    total_pages: int
    scale: float


@dataclass
class DictionaryEnrichment:
    """Texto redactado por IA; lo que falte queda con la heuristica. Claves en minusculas."""
    table_descriptions: Dict[str, str] = field(default_factory=dict)
    columns: Dict[str, Dict[str, Tuple[str, str]]] = field(default_factory=dict)
    rules: List[Tuple[str, List[str]]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.table_descriptions or self.columns or self.rules)


# ---------------------------------------------------------------- helpers
def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "modelo").strip().lower()).strip("_")
    return s or "modelo"


def map_data_type(uml_type: str) -> str:
    t = (uml_type or "").strip().lower()
    if t in ("long", "bigint"):
        return "bigint"
    if t in ("int", "integer", "short", "smallint"):
        return "integer"
    if t in ("double", "float", "real", "bigdecimal", "decimal", "numeric"):
        return "numeric"
    if t in ("boolean", "bool"):
        return "boolean"
    if t in ("localdate", "date"):
        return "date"
    if t in ("localdatetime", "datetime", "timestamp"):
        return "timestamp"
    if t == "text":
        return "text"
    if t == "uuid":
        return "uuid"
    return "varchar"


def pk_fk_marker(is_pk: bool, is_fk: bool) -> str:
    if is_pk and is_fk:
        return "pk, fk"
    return "pk" if is_pk else ("fk" if is_fk else "")


def describe_column(col_name: str, table: str, *, is_pk: bool, fk_target: Optional[str], nullable: bool) -> str:
    n = col_name.lower()
    table = table.lower()
    if n == "id":
        text = f"identificador único de {table}"
    elif fk_target:
        text = f"referencia a {fk_target.lower()} asociado a este registro"
    elif n in ("nombre", "titulo", "name", "title"):
        text = f"nombre de {table}"
    elif n in ("descripcion", "description"):
        text = f"descripción de {table}"
    elif n.startswith("fecha") or n in ("created", "updated") or n.startswith(("created_", "updated_")):
        text = f"fecha de {n.replace('fecha_', '').replace('_', ' ')} de {table}" if n.startswith("fecha_") else f"fecha de registro de {table}"
    elif n == "estado":
        text = "estado del registro"
    elif n in ("precio", "total", "monto", "subtotal") or n.startswith(("precio_", "monto_", "total_")):
        text = f"monto {n.replace('_', ' ')} de {table}"
    elif n in ("cantidad", "stock") or n.startswith("cantidad_"):
        text = f"cantidad {n.replace('_', ' ')} de {table}"
    elif "email" in n or "correo" in n:
        text = f"correo electrónico de {table}"
    elif n in ("telefono", "celular"):
        text = "teléfono de contacto"
    elif n.startswith("direccion"):
        text = f"dirección de {table}"
    else:
        text = f"{n.replace('_', ' ')} de {table}"
        if not nullable:
            text += ", obligatorio"
    return text.lower()


def example_value(col_name: str, data_type: str, *, is_pk: bool, is_fk: bool) -> str:
    n = col_name.lower()
    if n == "id" or is_pk or is_fk:
        return "1"
    if "email" in n or "correo" in n:
        return "usuario@correo.com"
    if n in ("telefono", "celular"):
        return "70000000"
    if n == "estado":
        return "activo, inactivo"
    if n in ("nombre", "titulo"):
        return "ejemplo"
    return {
        "boolean": "true, false",
        "date": "2026-01-01",
        "timestamp": "2026-01-01 10:30:00",
        "numeric": "199.00",
        "integer": "10",
        "bigint": "1",
    }.get(data_type, "ejemplo")


def _pl(name: str) -> str:
    """Simple Spanish plural of a snake_case table name (cliente -> clientes, pedido -> pedidos)."""
    if name.endswith("z"):
        return name[:-1] + "ces"
    if name[-1:] in "aeiou":
        return name + "s"
    return name if name.endswith("s") else name + "es"


def _plural_rules(document: CanonicalUmlDocument, tables: List[SqlTableDef]) -> List[Tuple[str, List[str]]]:
    # Same naming as the SQL tables (snake_case), so the rules match the dictionary tables.
    names = {c.id: _sanitize_identifier(c.name, "entity") for c in document.classes}
    rules: List[Tuple[str, List[str]]] = []
    for rel in document.relationships:
        a = names.get(rel.source_class_id)
        b = names.get(rel.target_class_id)
        if not a or not b:
            continue
        t = rel.type
        if t == RelationshipTypeEnum.ONE_TO_MANY:
            rules.append((f"un {a} puede tener muchos {_pl(b)}; cada {b} pertenece a un {a}.",
                          [f"un {a} con varios {_pl(b)} registrados.", f"cada {b} guarda la referencia a su {a}."]))
        elif t == RelationshipTypeEnum.MANY_TO_ONE:
            rules.append((f"muchos {_pl(a)} pueden estar asociados a un {b}; cada {a} pertenece a un {b}.",
                          [f"varios {_pl(a)} asociados al mismo {b}."]))
        elif t == RelationshipTypeEnum.ONE_TO_ONE:
            rules.append((f"un {a} se relaciona con un único {b} y viceversa.", []))
        elif t == RelationshipTypeEnum.MANY_TO_MANY:
            rules.append((f"un {a} puede relacionarse con muchos {_pl(b)} y un {b} con muchos {_pl(a)}, mediante una tabla intermedia.",
                          [f"la tabla intermedia almacena los pares ({a}, {b})."]))
        elif t == RelationshipTypeEnum.INHERITANCE:
            rules.append((f"{a} hereda los atributos de {b}.", []))
        elif t == RelationshipTypeEnum.COMPOSITION:
            rules.append((f"{a} está compuesto por {b}; si se elimina {a}, también se eliminan sus {b}.", []))
        elif t == RelationshipTypeEnum.AGGREGATION:
            rules.append((f"{a} agrupa a {b}, pero {b} puede existir de forma independiente.", []))
        elif t == RelationshipTypeEnum.REALIZATION:
            rules.append((f"{a} implementa el contrato definido por {b}.", []))
        else:
            rules.append((f"{a} depende de {b}.", []))
    return rules


def build_rules(document: CanonicalUmlDocument, tables: List[SqlTableDef]) -> List[Tuple[str, List[str]]]:
    rules = _plural_rules(document, tables)
    tnames = [t.name.lower() for t in tables if not t.is_join_table]
    pk_ex = [f"{n}.id debe ser único y no nulo." for n in tnames[:3]]
    rules.append(("toda tabla debe tener una clave primaria única que identifique cada registro.", pk_ex))
    for t in tables:
        req = [c.name.lower() for c in t.columns if not c.nullable and not c.primary_key]
        if req:
            rules.append((f"en {t.name.lower()} los campos obligatorios son: {', '.join(req)}.", []))
    fk_ex = []
    for t in tables:
        for fk in t.foreign_keys:
            fk_ex.append(f"{t.name.lower()}.{fk.column_name.lower()} debe existir en {fk.target_table.lower()}.")
    if fk_ex:
        rules.append(("las claves foráneas garantizan la integridad referencial: no se puede referenciar un registro inexistente.", fk_ex[:3]))
    # prioriza: si hay demasiadas, conserva relaciones y las 3 reglas generales al final
    if len(rules) > MAX_RULES:
        general = rules[-(len(rules) - len(_plural_rules(document, tables))):]
        rel_rules = rules[: len(rules) - len(general)]
        rules = rel_rules[: max(0, MAX_RULES - len(general))] + general
        rules = rules[:MAX_RULES]
    return [(t.lower(), [e.lower() for e in ex]) for t, ex in rules[:MAX_RULES]]


# ---------------------------------------------------------------- canvas
def _numbered_canvas_factory(project: str, version: int):
    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                if self._pageNumber > 1:
                    self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            w, h = landscape(A4)
            self.setFont("Times-Bold", 10)
            self.drawRightString(w - 36, h - 28, project)
            self.setStrokeColor(colors.grey)
            self.setLineWidth(0.4)
            self.line(36, 34, w - 36, 34)
            self.setFont("Times-Roman", 9)
            self.drawString(36, 22, f"Proyecto: {project}")
            self.drawCentredString(w / 2, 22, f"Versión {version}")
            self.drawRightString(w - 36, 22, f"Página {self._pageNumber} de {total}")

    return NumberedCanvas


class _Doc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        key = getattr(flowable, "_toc", None)
        if key:
            level, text = key
            self.notify("TOCEntry", (level, text, self.page))
            if level == 0 and text.startswith("1. diccionario"):
                self.block_page = self.page


# ---------------------------------------------------------------- build
def _build_pdf(document, tables, rules, author, today, scale,
               enrichment: Optional[DictionaryEnrichment] = None) -> Tuple[bytes, int, int]:
    ai_tables = enrichment.table_descriptions if enrichment else {}
    ai_cols = enrichment.columns if enrichment else {}
    project = document.name
    slug = _slug(project)
    pw, ph = landscape(A4)
    margin = 36 * scale
    top = 50 * scale + 6
    bottom = 46 * scale + 6
    buf = io.BytesIO()
    doc = _Doc(buf, pagesize=(pw, ph), leftMargin=margin, rightMargin=margin,
               topMargin=top, bottomMargin=bottom, title=f"{project} - diccionario de datos",
               author=author)
    doc.block_page = None
    frame = Frame(margin, bottom, pw - 2 * margin, ph - top - bottom, id="f",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])

    fs = lambda base: max(5.0, base * scale)  # noqa: E731
    st_title = ParagraphStyle("t", fontName="Times-Bold", fontSize=28, leading=34, alignment=TA_CENTER)
    st_sub = ParagraphStyle("s", fontName="Times-Roman", fontSize=16, leading=20, alignment=TA_CENTER)
    st_cell = ParagraphStyle("c", fontName="Times-Roman", fontSize=fs(10), leading=fs(12))
    st_head = ParagraphStyle("h", fontName="Times-Bold", fontSize=fs(10), leading=fs(12),
                             alignment=TA_CENTER, textColor=colors.white)
    st_h1 = ParagraphStyle("h1", fontName="Times-Bold", fontSize=fs(18), leading=fs(22),
                           textColor=HEAD_COLOR, spaceAfter=fs(6))
    st_h2 = ParagraphStyle("h2", fontName="Times-Bold", fontSize=fs(14), leading=fs(17),
                           textColor=HEAD_COLOR, spaceBefore=fs(8), spaceAfter=fs(3))
    st_h3 = ParagraphStyle("h3", fontName="Times-Bold", fontSize=fs(11), leading=fs(14),
                           textColor=HEAD_COLOR, spaceBefore=fs(4), spaceAfter=fs(2))
    st_body = ParagraphStyle("b", fontName="Times-Roman", fontSize=fs(10.5), leading=fs(13))
    st_ex = ParagraphStyle("e", parent=st_body, leftIndent=18)
    st_toc = [ParagraphStyle(f"toc{i}", fontName="Times-Roman", fontSize=11, leading=15,
                             leftIndent=20 * i, rightIndent=30) for i in range(2)]

    def heading(text, style, level):
        p = Paragraph(_esc(text), style)
        p._toc = (level, text)
        return p

    story = []
    # portada
    story.append(Spacer(1, 70))
    story.append(Paragraph(_esc(project), st_title))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Diccionario de Datos y Reglas de Negocio", st_sub))
    story.append(Spacer(1, 40))
    lab = ParagraphStyle("lab", fontName="Times-Bold", fontSize=12, leading=15)
    val = ParagraphStyle("val", fontName="Times-Roman", fontSize=12, leading=15)
    today = today or date.today()
    rows = [
        ("Nombre del proyecto", project),
        ("Nombre del documento", "Diccionario de Datos y Reglas de Negocio"),
        ("Versión", str(document.version)),
        ("Fecha", today.strftime("%d/%m/%Y")),
        ("Autor", author),
        ("Estado", "Generado automáticamente"),
    ]
    cover = Table([[Paragraph(_esc(a), lab), Paragraph(_esc(b), val)] for a, b in rows],
                  colWidths=[180, 380], hAlign="CENTER")
    cover.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LABEL_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(cover)
    story.append(PageBreak())
    # indice
    story.append(Paragraph("Contenido", st_h1))
    toc = TableOfContents()
    toc.levelStyles = st_toc
    toc.dotsMinLevel = 0
    story.append(toc)
    story.append(PageBreak())
    # cuerpo
    block = f"1. diccionario de datos — bloque 1: {slug}"
    story.append(heading(block, st_h1, 0))
    pad = 3 * scale + 1
    widths = [0.08, 0.20, 0.10, 0.07, 0.33, 0.22]
    avail = pw - 2 * margin
    col_w = [w * avail for w in widths]
    for i, t in enumerate(tables, 1):
        tname = t.name.lower()
        story.append(heading(f"{i}. tabla: {tname}", st_h2, 1))
        if t.is_join_table and len(t.foreign_keys) >= 2:
            desc = (f"tabla intermedia que resuelve la relación muchos a muchos entre "
                    f"{t.foreign_keys[0].target_table.lower()} y {t.foreign_keys[1].target_table.lower()}.")
        else:
            desc = f"almacena los registros de {tname}."
        desc = ai_tables.get(tname) or desc
        t_ai_cols = ai_cols.get(tname, {})
        story.append(Paragraph(_esc(desc), st_body))
        story.append(Spacer(1, 3 * scale))
        fks = {fk.column_name: fk.target_table for fk in t.foreign_keys}
        data = [[Paragraph(h, st_head) for h in ("número", "atributo", "tipo dato", "pk/fk", "descripción", "ejemplos")]]
        for n, c in enumerate(t.columns, 1):
            dt = map_data_type(c.uml_type)
            is_fk = c.name in fks
            cells = [
                str(n), c.name.lower(), dt, pk_fk_marker(c.primary_key, is_fk),
                describe_column(c.name, t.name, is_pk=c.primary_key, fk_target=fks.get(c.name), nullable=c.nullable),
                example_value(c.name, dt, is_pk=c.primary_key, is_fk=is_fk),
            ]
            ai = t_ai_cols.get(c.name.lower())
            if ai and not c.primary_key and not is_fk:
                cells[4] = ai[0] or cells[4]
                cells[5] = ai[1] or cells[5]
            data.append([Paragraph(_esc(x), st_cell) for x in cells])
        tbl = Table(data, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TABLE_COLOR),
            ("GRID", (0, 0), (-1, -1), 0.5, TABLE_COLOR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
    # reglas
    story.append(heading(f"observaciones importantes del modelo {slug}", st_h1, 0))
    for i, (text, examples) in enumerate(rules, 1):
        story.append(heading(f"regla {i}", st_h3, 1))
        story.append(Paragraph(_esc(text), st_body))
        if examples:
            story.append(Paragraph("ejemplo:", st_body))
            for j, ex in enumerate(examples, 1):
                story.append(Paragraph(f"{j}. {_esc(ex)}", st_ex))

    doc.multiBuild(story, canvasmaker=_numbered_canvas_factory(project, document.version))
    return buf.getvalue(), doc.block_page or 3, doc.page


def build_data_dictionary(document: CanonicalUmlDocument, *, author: str,
                          today: Optional[date] = None,
                          enrichment: Optional[DictionaryEnrichment] = None) -> DictionaryResult:
    tables = SqlGenerator()._build_relational_schema(document)
    rules = build_rules(document, tables)
    if enrichment and enrichment.rules:
        ai_rules = [(t.lower(), [e.lower() for e in ex]) for t, ex in enrichment.rules[:4]]
        rules = (ai_rules + rules)[:MAX_RULES]
    last: Optional[DictionaryResult] = None
    for scale in SCALES:
        pdf, block_page, _last_page = _build_pdf(document, tables, rules, author, today, scale, enrichment)
        total = _count_pages(pdf)
        index_pages = max(1, block_page - 2)
        content = total - 1 - index_pages
        last = DictionaryResult(pdf=pdf, content_pages=content, total_pages=total, scale=scale)
        if content <= MAX_CONTENT_PAGES:
            return last
    return last  # type: ignore[return-value]


def _count_pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", pdf))



