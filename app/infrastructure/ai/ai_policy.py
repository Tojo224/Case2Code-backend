"""Pure text policies of the AI room: who is the AI being spoken to, did the user
confirm a proposal, which business domain is being asked for.

Everything here is deterministic and accent-insensitive; no I/O.
"""

import re
import unicodedata
from typing import Optional

# Explicit mention tokens only: @ai, @senior, @bot or /ai at a word boundary.
_MENTION_RE = re.compile(r"(?<![\w@./])(?:@(?:ai|senior|bot)|/ai)(?!\w)", re.IGNORECASE)


def fold(text: str) -> str:
    """Lowercase and strip accents (NFKD) so matching is accent-insensitive."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def is_addressed_to_ai(content: str, force_ai: bool = False) -> bool:
    """True only for an explicit mention token or when the client forces the AI
    (the assistant panel). Plain room chat between humans never wakes the AI."""
    if force_ai:
        return True
    return bool(_MENTION_RE.search(content or ""))


def strip_mention(text: str) -> str:
    """Removes a leading mention token and its separators."""
    return re.sub(
        r"^\s*(?:@(?:ai|senior|bot)|/ai)(?!\w)[\s,:;-]*",
        "",
        text or "",
        flags=re.IGNORECASE,
    ).strip()


_CONFIRMATION_RE = re.compile(
    r"^(?:(?:si|dale)[\s,]+)?(?:por\s+favor[\s,]+)?"
    r"(?:(?:puedes|podes)\s+hacerlo|hacelo|hazlo|hagalo|aplicalo|aplica\s+los\s+cambios"
    r"|aplica\s+el\s+holograma|crealo|armalo|dale|procede|adelante|hace\s+eso|haz\s+eso)"
    r"(?:\s+(?:por\s+favor|ya|nomas))?$"
)


def is_confirmation(text: str) -> bool:
    """A short message whose whole content is an acceptance ("hacelo", "dale"...).

    Anchored on purpose: "dale un nombre al atributo" is NOT a confirmation.
    The caller must still require a live pending proposal.
    """
    clean = fold(strip_mention(text))
    clean = re.sub(r"[^\w\s]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return bool(clean) and bool(_CONFIRMATION_RE.match(clean))


# Whole-word, accent-insensitive keywords. Ambiguous words ("menu", "animal",
# "libro", "stock", "ventas", "salud", "comida"...) are deliberately absent.
_DOMAIN_KEYWORDS = [
    ("ecommerce", ["ecommerce", "e-commerce", "ecomerce", "e comerce", "tienda", "carrito"]),
    ("veterinaria", ["veterinaria", "mascota"]),
    ("hotel", ["hotel", "hospedaje", "alojamiento"]),
    ("universidad", ["universidad", "colegio", "escuela", "instituto"]),
    ("biblioteca", ["biblioteca"]),
    ("hospital", ["hospital", "clinica", "paciente", "consultorio"]),
    ("restaurante", ["restaurante", "restaurant"]),
    ("inventario", ["inventario", "almacen", "bodega", "logistica"]),
]

_DOMAIN_PATTERNS = [
    (topic, re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(k) for k in kws) + r")(?:es|s)?(?![a-z0-9])"))
    for topic, kws in _DOMAIN_KEYWORDS
]


def detect_topic(prompt: str) -> Optional[str]:
    """Domain topic of the CURRENT prompt only. Never scans chat history."""
    norm = fold(strip_mention(prompt))
    for topic, pattern in _DOMAIN_PATTERNS:
        if pattern.search(norm):
            return topic
    return None


_GENERIC_DB_RE = re.compile(
    r"^(?:crea\w*|hace\w*|haz\w*|arma\w*|genera\w*|disena\w*|modela\w*)\s+"
    r"(?:me\s+)?(?:una?\s+|la\s+|el\s+)?(?:base\s+de\s+datos|esquema|modelo)"
    r"(?:\s+(?:basic\w+|simple|complet\w+))?(?:\s+(?:de\s+)?(?:ejemplo|prueba))?$"
)


def is_generic_database_request(prompt: str) -> bool:
    """"hacé la base de datos" with no domain at all (nothing after the noun)."""
    clean = fold(strip_mention(prompt))
    clean = re.sub(r"[^\w\s]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return bool(_GENERIC_DB_RE.match(clean))
