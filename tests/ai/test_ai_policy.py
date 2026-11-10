import pytest

from app.infrastructure.ai.ai_policy import (
    detect_topic,
    is_addressed_to_ai,
    is_confirmation,
    is_generic_database_request,
)


@pytest.mark.parametrize("text", ["@ai hacé algo", "hola @Senior", "/ai ayuda", "che @bot, ¿y esto?", "@AI"])
def test_explicit_mentions_trigger(text):
    assert is_addressed_to_ai(text)


@pytest.mark.parametrize(
    "text",
    [
        "¿cómo lo hacemos?",
        "hola equipo",
        "creá la clase Cliente",
        "el senior de ayer estuvo bien",
        "el asistente no anda",
        "mail a pepe@ai.com",
        "ruta /air/uno",
        "que tal",
    ],
)
def test_plain_human_chat_does_not_trigger(text):
    assert not is_addressed_to_ai(text)


def test_force_ai_flag_triggers_without_mention():
    assert is_addressed_to_ai("agregá un campo precio", force_ai=True)


@pytest.mark.parametrize("text", ["hacelo", "@ai dale", "Sí, hacelo por favor", "aplicá los cambios", "@ai ¡Hacelo!"])
def test_confirmations(text):
    assert is_confirmation(text)


@pytest.mark.parametrize("text", ["dale un nombre al atributo", "no lo hagas", "hacelo con más tablas", "hola"])
def test_non_confirmations(text):
    assert not is_confirmation(text)


@pytest.mark.parametrize(
    "text,topic",
    [
        ("armá una base para una veterinaria", "veterinaria"),
        ("necesito un sistema para HOTELES", "hotel"),
        ("modelo de clínica", "hospital"),
        ("una tienda online", "ecommerce"),
    ],
)
def test_topic_detection_whole_words_accent_insensitive(text, topic):
    assert detect_topic(text) == topic


@pytest.mark.parametrize(
    "text",
    ["agregá el menú principal", "un animal salvaje", "el libro de estilo", "el stock de ideas", "las ventas bajaron", "cotelería"],
)
def test_ambiguous_or_substring_words_do_not_create_topics(text):
    assert detect_topic(text) is None


def test_generic_database_request():
    assert is_generic_database_request("@ai hacé la base de datos")
    assert is_generic_database_request("creá un esquema básico")
    assert not is_generic_database_request("hacé la base de datos para un gimnasio")


@pytest.mark.parametrize(
    "owner,user,role,expected",
    [
        (None, "guest-1", None, (True, True)),
        (None, "u1", "VIEWER", (True, False)),
        ("own", "own", "OWNER", (True, True)),
        ("own", "own", None, (True, True)),
        ("own", "u2", "EDITOR", (True, True)),
        ("own", "u2", "VIEWER", (True, False)),
        ("own", "u2", None, (False, False)),
        ("own", "guest-1", None, (False, False)),
    ],
)
def test_can_user_edit_via_ai(owner, user, role, expected):
    from app.infrastructure.ai.ai_policy import can_user_edit_via_ai

    assert can_user_edit_via_ai(owner, user, role) == expected


@pytest.mark.parametrize(
    "url,rejected",
    [
        ("http://localhost:11434", False),
        ("https://OLLAMA:11434/v1", False),
        ("http://host.docker.internal:11434", False),
        ("http://llm.internal.corp:8000", False),
        ("ftp://localhost", True),
        ("file:///etc/passwd", True),
        ("http://evil.example.com", True),
        ("http://localhost@evil.example.com", True),
        ("not a url", True),
    ],
)
def test_check_llm_base_url(url, rejected):
    from app.infrastructure.ai.ai_policy import check_llm_base_url

    result = check_llm_base_url(
        url, "localhost,127.0.0.1,host.docker.internal,ollama", "http://llm.internal.corp:8000"
    )
    assert (result is not None) is rejected
