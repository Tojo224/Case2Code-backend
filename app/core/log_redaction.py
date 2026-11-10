"""Keeps secrets out of the logs.

The realtime socket authenticates with ``?token=<JWT>`` (browsers cannot set headers on a
WebSocket), and uvicorn logs the full request path. A log that contains a live JWT is a
credential leak, so every record is rewritten before it is emitted.
"""

import logging
import re

_TOKEN_QUERY = re.compile(r"([?&]token=)[^&\s\"']+", re.IGNORECASE)
REDACTED = "[redacted]"


def redact_tokens(text: str) -> str:
    return _TOKEN_QUERY.sub(rf"\g<1>{REDACTED}", text)


class TokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if "token=" in message.lower():
            record.msg = redact_tokens(message)
            record.args = ()
        return True


def install_token_redaction() -> None:
    """Idempotent: attach the filter to the loggers uvicorn uses for request lines."""
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, TokenRedactionFilter) for f in logger.filters):
            logger.addFilter(TokenRedactionFilter())
