"""Input size limits for the realtime/AI surface.

Every value bounds something a client controls that either reaches an LLM prompt,
is stored per message, or is parsed in memory. Override through the environment.
"""

import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


MAX_CHAT_MESSAGE_CHARS = _int_env("MAX_CHAT_MESSAGE_CHARS", 2000)
MAX_ASSISTANT_PROMPT_CHARS = _int_env("MAX_ASSISTANT_PROMPT_CHARS", 4000)
# base64 length: ~7 MB of text is ~5 MB of image.
MAX_IMAGE_BASE64_CHARS = _int_env("MAX_IMAGE_BASE64_CHARS", 7_000_000)
MAX_XMI_UPLOAD_BYTES = _int_env("MAX_XMI_UPLOAD_BYTES", 5_000_000)
MAX_BATCH_COMMANDS = _int_env("MAX_BATCH_COMMANDS", 200)
# One websocket text frame (chat message + JSON envelope).
MAX_WS_FRAME_CHARS = _int_env("MAX_WS_FRAME_CHARS", 16_000)
ALLOWED_IMAGE_MIME_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

# Rate limits (sliding window, per user/client).
CHAT_MESSAGES_PER_WINDOW = _int_env("CHAT_MESSAGES_PER_WINDOW", 20)
CHAT_WINDOW_S = _int_env("CHAT_WINDOW_S", 10)
AI_REQUESTS_PER_WINDOW = _int_env("AI_REQUESTS_PER_WINDOW", 6)
AI_WINDOW_S = _int_env("AI_WINDOW_S", 60)
