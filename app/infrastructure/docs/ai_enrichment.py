"""Enriquecimiento opcional del diccionario de datos con un LLM (una llamada por descarga)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.infrastructure.codegen.sql_generator import SqlGenerator
from app.infrastructure.docs.data_dictionary import DictionaryEnrichment, map_data_type

logger = logging.getLogger(__name__)

MAX_TABLES = 25
MAX_COLUMNS = 70
MAX_AI_RULES = 4
CACHE_MAX = 50

SYSTEM_PROMPT = (
    "Eres un analista de datos que redacta un diccionario de datos en español.\n"
    "Recibirás un JSON compacto con el modelo: {\"modelo\":nombre,\"tablas\":[{\"t\":tabla,\"c\":[[columna,tipo],...]}]}.\n"
    "Responde SOLO con JSON válido, sin markdown ni texto adicional, con esta forma exacta:\n"
    "{\"tablas\":{\"cliente\":{\"d\":\"descripción corta de la tabla\",\"c\":{\"nombre\":[\"descripción\",\"ejemplo\"]}}},"
    "\"reglas\":[{\"t\":\"regla de negocio\",\"e\":[\"ejemplo1\",\"ejemplo2\"]}]}\n"
    "Reglas de estilo:\n"
    "- descripciones en español, en minúsculas, máximo 12 palabras.\n"
    "- ejemplos realistas y cortos, adecuados al dominio del modelo.\n"
    "- hasta 4 reglas de negocio útiles para el dominio.\n"
    "- usa exactamente los nombres de tabla y columna recibidos."
)

Included = Dict[str, Set[str]]


# ---------------------------------------------------------------- request
def build_enrichment_request(document) -> Tuple[str, str, Included]:
    tables = SqlGenerator()._build_relational_schema(document)
    included: Included = {}
    out_tables: List[Dict[str, Any]] = []
    total = 0
    for t in tables:
        if len(out_tables) >= MAX_TABLES or total >= MAX_COLUMNS:
            break
        fk_cols = {fk.column_name for fk in t.foreign_keys}
        cols = []
        for c in t.columns:
            if c.primary_key or c.name in fk_cols:
                continue
            if total + len(cols) >= MAX_COLUMNS:
                break
            cols.append([c.name.lower(), map_data_type(c.uml_type)])
        if not cols:
            continue
        total += len(cols)
        tname = t.name.lower()
        included[tname] = {c[0] for c in cols}
        out_tables.append({"t": tname, "c": cols})
    user = json.dumps({"modelo": document.name, "tablas": out_tables}, ensure_ascii=False, separators=(",", ":"))
    return SYSTEM_PROMPT, user, included


# ---------------------------------------------------------------- parse
def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return str(value).strip()[:limit]


def parse_enrichment(text: Optional[str], included: Included) -> DictionaryEnrichment:
    result = DictionaryEnrichment()
    try:
        if not text:
            return result
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return result
        data = json.loads(text[start:end + 1])
        if not isinstance(data, dict):
            return result

        tablas = data.get("tablas")
        if isinstance(tablas, dict):
            for tname, tdata in tablas.items():
                key = str(tname).strip().lower()
                if key not in included or not isinstance(tdata, dict):
                    continue
                desc = _clean(tdata.get("d"), 140).lower()
                if desc:
                    result.table_descriptions[key] = desc
                cols = tdata.get("c")
                if isinstance(cols, dict):
                    for cname, cval in cols.items():
                        ckey = str(cname).strip().lower()
                        if ckey not in included[key] or not isinstance(cval, (list, tuple)) or len(cval) < 2:
                            continue
                        d = _clean(cval[0], 140).lower()
                        e = _clean(cval[1], 60)
                        if d or e:
                            result.columns.setdefault(key, {})[ckey] = (d, e)

        reglas = data.get("reglas")
        if isinstance(reglas, list):
            for r in reglas:
                if len(result.rules) >= MAX_AI_RULES:
                    break
                if not isinstance(r, dict):
                    continue
                rt = _clean(r.get("t"), 300).lower()
                if not rt:
                    continue
                ex = r.get("e")
                examples = []
                if isinstance(ex, list):
                    examples = [s.lower() for s in (_clean(x, 80) for x in ex[:3]) if s]
                result.rules.append((rt, examples))
    except Exception:  # noqa: BLE001 - nunca lanza
        logger.warning("No se pudo interpretar la respuesta de enriquecimiento", exc_info=True)
        return DictionaryEnrichment()
    return result


# ---------------------------------------------------------------- cache
_cache: Dict[Tuple[str, int], DictionaryEnrichment] = {}


def get_cached(document) -> Optional[DictionaryEnrichment]:
    return _cache.get((document.id, document.version))


def put_cached(document, enrichment: DictionaryEnrichment) -> None:
    key = (document.id, document.version)
    _cache.pop(key, None)
    _cache[key] = enrichment
    while len(_cache) > CACHE_MAX:
        _cache.pop(next(iter(_cache)))


def clear_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------- enrich
async def enrich_document(document, chain, guarded_call) -> Tuple[Optional[DictionaryEnrichment], str]:
    system, user, included = build_enrichment_request(document)
    if not included:
        return None, "fallback"
    started_at = time.monotonic()
    for provider in chain:
        try:
            raw = await guarded_call(
                provider, started_at, lambda p=provider: p.generate_chat_response(system, user)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Enriquecimiento IA fallo con %s: %s", getattr(provider, "provider_name", "?"), exc)
            continue
        enrichment = parse_enrichment(raw, included)
        if not enrichment.is_empty():
            return enrichment, "ai"
    return None, "fallback"
