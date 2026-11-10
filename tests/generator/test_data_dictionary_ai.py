import io
import json

import pytest
from pypdf import PdfReader

from app.core.rate_limit import ai_limiter
from app.domain.models.canonical_uml import CanonicalUmlDocument, UmlAttribute, UmlClass
from app.infrastructure.ai.senior_colleague import senior_colleague_orchestrator
from app.infrastructure.docs import ai_enrichment as ae
from app.infrastructure.docs.data_dictionary import build_data_dictionary
from tests.ai.fakes import FakeProvider
from tests.generator.test_data_dictionary import _cls, _doc, _model


@pytest.fixture(autouse=True)
def _clean():
    ae.clear_cache()
    ai_limiter.reset()
    yield
    ae.clear_cache()
    ai_limiter.reset()


def _pdf_text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages).lower()


AI_REPLY = json.dumps({
    "tablas": {"cliente": {"d": "Persona que compra en la tienda", "c": {"nombre": ["Nombre completo del comprador", "Maria Lopez"]}}},
    "reglas": [{"t": "Un cliente debe tener nombre", "e": ["Maria Lopez"]}],
})


def test_parse_valid_and_filters():
    included = {"cliente": {"nombre"}}
    text = "```json\n" + json.dumps({
        "tablas": {"cliente": {"d": "X" * 300, "c": {"nombre": ["Desc", "E" * 100], "id": ["a", "b"]}},
                   "otra": {"d": "no"}},
        "reglas": [{"t": f"R{i}", "e": ["a", "b", "c", "d"]} for i in range(6)],
    }) + "\n```"
    e = ae.parse_enrichment(text, included)
    assert list(e.table_descriptions) == ["cliente"] and len(e.table_descriptions["cliente"]) == 140
    assert e.columns == {"cliente": {"nombre": ("desc", "E" * 60)}}
    assert len(e.rules) == 4 and e.rules[0][0] == "r0" and len(e.rules[0][1]) == 3


@pytest.mark.parametrize("text", [None, "", "no json", "{bad", "[1,2]", '{"tablas": 5, "reglas": "x"}'])
def test_parse_invalid_is_empty(text):
    assert ae.parse_enrichment(text, {"cliente": {"nombre"}}).is_empty()


def test_request_excludes_pk_fk():
    _, user, included = ae.build_enrichment_request(_model())
    data = json.loads(user)
    cols = {t["t"]: [c[0] for c in t["c"]] for t in data["tablas"]}
    assert cols["cliente"] == ["nombre"]
    assert all(c not in ("id", "cliente_id") for v in cols.values() for c in v)
    assert "categoria" not in cols  # solo tiene id
    assert included["cliente"] == {"nombre"}


def test_request_truncates_columns_and_tables():
    classes = [_cls(f"c{i}", f"T{i}", [("id", "Long", True, False)] + [(f"campo{j}", "String", False, True) for j in range(10)])
               for i in range(30)]
    _, user, included = ae.build_enrichment_request(_doc(classes))
    data = json.loads(user)
    assert len(data["tablas"]) <= 25
    assert sum(len(t["c"]) for t in data["tablas"]) == 70
    assert sum(len(v) for v in included.values()) == 70


def test_enrichment_replaces_heuristic_in_pdf():
    doc = _model()
    base = _pdf_text(build_data_dictionary(doc, author="a").pdf)
    enr = ae.parse_enrichment(AI_REPLY, ae.build_enrichment_request(doc)[2])
    text = _pdf_text(build_data_dictionary(doc, author="a", enrichment=enr).pdf)
    assert "persona que compra en la tienda" in text and "persona que compra" not in base
    assert "nombre completo del comprador" in text and "maria lopez" in text
    assert "un cliente debe tener nombre" in text


def _use(monkeypatch, provider):
    monkeypatch.setattr(senior_colleague_orchestrator, "custom_provider", provider)


def _save(diagram_repo, doc_id="diag-ai"):
    doc = _model()
    doc.id = doc_id
    diagram_repo.save(doc)
    return doc


def test_endpoint_ai_then_cache_then_fallback(client, diagram_repo, monkeypatch):
    fake = FakeProvider(chat_reply=AI_REPLY)
    _use(monkeypatch, fake)
    doc = _save(diagram_repo)
    r = client.get(f"/api/diagrams/{doc.id}/export/docs/ai")
    assert r.status_code == 200 and r.headers["x-docs-ai"] == "ai"
    assert r.content.startswith(b"%PDF")
    assert 'filename="Tienda_Demo_diccionario_datos_ia.pdf"' in r.headers["content-disposition"]
    assert len(fake.chat_calls) == 1
    r = client.get(f"/api/diagrams/{doc.id}/export/docs/ai")
    assert r.headers["x-docs-ai"] == "cache" and len(fake.chat_calls) == 1


def test_endpoint_fallback(client, diagram_repo, monkeypatch):
    _use(monkeypatch, FakeProvider(chat_reply=None))
    doc = _save(diagram_repo, "diag-ai-fb")
    r = client.get(f"/api/diagrams/{doc.id}/export/docs/ai")
    assert r.status_code == 200 and r.headers["x-docs-ai"] == "fallback"
    assert r.content.startswith(b"%PDF")


def test_endpoint_no_classes(client, diagram_repo, monkeypatch):
    _use(monkeypatch, FakeProvider(chat_reply=AI_REPLY))
    diagram_repo.save(_doc(doc_id="diag-ai-empty"))
    assert client.get("/api/diagrams/diag-ai-empty/export/docs/ai").status_code == 400


def test_endpoint_rate_limited(client, diagram_repo, monkeypatch):
    _use(monkeypatch, FakeProvider(chat_reply=AI_REPLY))
    doc = _save(diagram_repo, "diag-ai-rl")
    monkeypatch.setattr(ai_limiter, "hit", lambda key: 7.2)
    r = client.get(f"/api/diagrams/{doc.id}/export/docs/ai")
    assert r.status_code == 429 and r.headers["retry-after"] == "8"


# --- regression: the model answers per column with {"d": ..., "e": ...} -----------------------

import asyncio as _asyncio
import json as _json

from app.domain.models.canonical_uml import CanonicalUmlDocument as _Doc, UmlAttribute as _Attr, UmlClass as _Cls
from app.infrastructure.docs import ai_enrichment as _ai


def test_column_answers_as_objects_are_parsed_and_arrays_still_work():
    included = {"cliente": {"nombre", "email"}}
    text = _json.dumps({
        "tablas": {"cliente": {"d": "compradores", "c": {
            "nombre": {"d": "nombre completo del comprador", "e": "Juan Pérez"},
            "email": ["correo del comprador", "juan@correo.com"],
        }}}
    })
    result = _ai.parse_enrichment(text, included)
    assert result.columns["cliente"]["nombre"] == ("nombre completo del comprador", "Juan Pérez")
    assert result.columns["cliente"]["email"] == ("correo del comprador", "juan@correo.com")


def test_providers_that_accept_it_get_a_longer_timeout_and_token_ceiling():
    seen = {}

    class LongAnswerProvider:
        provider_name = "stub-long"

        async def generate_chat_response(self, system, user, *, timeout=None, max_output_tokens=None):
            seen.update(timeout=timeout, max_output_tokens=max_output_tokens)
            return _json.dumps({"tablas": {"cliente": {"d": "compradores", "c": {}}}})

    class PlainProvider:
        provider_name = "stub-plain"

        async def generate_chat_response(self, system, user):
            seen["plain_called"] = True
            return None

    async def passthrough(provider, started_at, call):
        return await call()

    doc = _Doc(id="d-long", name="Tienda", classes=[
        _Cls(id="c1", name="Cliente", attributes=[
            _Attr(id="a0", name="id", type="Long", primary_key=True, nullable=False),
            _Attr(id="a1", name="nombre", type="String"),
        ]),
    ])
    enrichment, status = _asyncio.run(_ai.enrich_document(doc, [PlainProvider(), LongAnswerProvider()], passthrough))
    assert status == "ai" and enrichment is not None
    assert seen["plain_called"] is True  # the 2-argument provider is still called the old way
    assert seen["timeout"] == _ai.AI_CALL_TIMEOUT_S
    assert seen["max_output_tokens"] == _ai.AI_MAX_OUTPUT_TOKENS
