# syntax=docker/dockerfile:1
# --- builder: resolve and install dependencies into an isolated venv -------------------
FROM python:3.14-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

# --- runner: slim image, non-root user ---------------------------------------------------
FROM python:3.14-slim AS runner

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app app ./app

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# ONE worker on purpose: realtime rooms, room locks, rate limiters and AI circuit breakers
# live in the process memory. Several workers would split a room across processes.
# --proxy-headers: the container is only reachable through nginx, which sets X-Forwarded-For
# (needed so per-client rate limits see the real client and not the proxy).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--ws-max-size", "65536", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
