"""Pure text policies of the AI room: who is the AI being spoken to, did the user
confirm a proposal, which business domain is being asked for.

Everything here is deterministic and accent-insensitive; no I/O.
"""

import re
import unicodedata
from typing import Optional, Tuple
from urllib.parse import urlparse

# Explicit mention tokens only: @ai, @senior, @bot or /ai at a word boundary.
_MENTION_RE = re.compile(r"(?<![\w@./])(?:@(?:ai|senior|bot)|/ai)(?!\w)", re.IGNORECASE)


def fold(text: str) -> str:
    """Lowercase and strip accents (NFKD) so matching is accent-insensitive."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def is_addressed_to_ai(
    content: str,
    force_ai: bool = False,
    active_user_count: Optional[int] = None,
) -> bool:
    """Decides whether a chat message in the room wakes the Senior AI.

    - When `active_user_count` is provided:
      - 1 user in the room (`active_user_count <= 1`): the AI responds automatically
        without needing `@ai` or `@senior`.
      - 2 or more users (`active_user_count >= 2`): human-to-human chat is the default
        and the AI ONLY responds when explicitly mentioned (`@ai`, `@senior`, `@bot`, `/ai`).
    - When `active_user_count` is None: falls back to `force_ai` or explicit mention.
    """
    has_mention = bool(_MENTION_RE.search(content or ""))
    if active_user_count is not None:
        if active_user_count <= 1:
            return True
        return has_mention
    if force_ai:
        return True
    return has_mention


def strip_mention(text: str) -> str:
    """Removes leading mention token(s) and their separators."""
    return re.sub(
        r"^\s*(?:(?:@(?:ai|senior|bot)|/ai)(?!\w)[\s,:;-]*)+",
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


AI_PROVIDER_MODES = ("local", "hybrid", "gemini")


def llm_url_host(url: str) -> Optional[str]:
    """Lowercase hostname of an http(s) URL, or None when it is not one."""
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return None
        return (parsed.hostname or "").lower() or None
    except ValueError:
        return None


def check_llm_base_url(url: str, allowed_hosts_csv: str, current_url: str = "") -> Optional[str]:
    """None when `url` may be used as the local LLM endpoint, else a Spanish reason.

    Only http/https, and the host must be allowlisted (AI_ALLOWED_LLM_HOSTS) or be
    the host of the URL that is already configured."""
    host = llm_url_host(url)
    if host is None:
        return "La URL del LLM local debe ser http:// o https:// con un host válido."
    allowed = {h.strip().lower() for h in (allowed_hosts_csv or "").split(",") if h.strip()}
    current_host = llm_url_host(current_url)
    if current_host:
        allowed.add(current_host)
    if host not in allowed:
        return (
            f"El host '{host}' no está permitido para el LLM local. "
            "Pedile al administrador que lo agregue a la variable de entorno AI_ALLOWED_LLM_HOSTS."
        )
    return None


def can_user_edit_via_ai(owner_id: Optional[str], user_id: str, role: Optional[str]) -> Tuple[bool, bool]:
    """Returns (allowed_in_room, can_edit) for a sender, from FRESH data.

    - open diagram (no owner): guests and anyone may edit;
    - private diagram: the sender must be the owner or a collaborator, otherwise
      they are out of the room (allowed_in_room False);
    - only OWNER and EDITOR may edit; VIEWER may only get advice.
    `role` is the CollaboratorRole value (or None)."""
    role_value = getattr(role, "value", role)
    if not owner_id:
        return True, role_value != "VIEWER"
    if user_id != owner_id and role_value is None:
        return False, False
    return True, user_id == owner_id or role_value in ("OWNER", "EDITOR")
