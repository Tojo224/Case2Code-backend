import logging

from app.core.log_redaction import TokenRedactionFilter, install_token_redaction, redact_tokens

JWT = "eyJhbGciOiJIUzI1NiJ9.payload.signature"


def test_redact_tokens_in_query_strings():
    assert JWT not in redact_tokens(f"/api/diagrams/d1/ws?token={JWT}")
    assert redact_tokens(f"/ws?a=1&token={JWT}&b=2") == "/ws?a=1&token=[redacted]&b=2"
    assert redact_tokens("/api/diagrams") == "/api/diagrams"


def test_filter_rewrites_formatted_records_with_args():
    record = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" [accepted]',
        ("1.2.3.4:5", f"/api/d/ws?token={JWT}"), None,
    )
    assert TokenRedactionFilter().filter(record) is True
    assert JWT not in record.getMessage()
    assert "[accepted]" in record.getMessage()


def test_installed_filter_is_applied_by_the_uvicorn_loggers(caplog):
    install_token_redaction()
    install_token_redaction()  # idempotent
    logger = logging.getLogger("uvicorn.error")
    assert sum(isinstance(f, TokenRedactionFilter) for f in logger.filters) == 1
