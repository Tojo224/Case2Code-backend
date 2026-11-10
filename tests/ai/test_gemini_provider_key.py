"""The Gemini API key must travel in a header, never in the URL."""

import asyncio

import httpx

from app.infrastructure.ai.ai_provider import GeminiAiProvider

SECRET = "AIza-secret-key"


def test_endpoint_does_not_contain_the_key():
    provider = GeminiAiProvider(api_key=SECRET)
    assert SECRET not in provider.endpoint
    assert "key=" not in provider.endpoint


def test_chat_request_sends_key_in_header(monkeypatch):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["header"] = request.headers.get("x-goog-api-key")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hola"}]}}]})

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    provider = GeminiAiProvider(api_key=SECRET)
    reply = asyncio.run(provider.generate_chat_response("sys", "msg"))

    assert reply == "hola"
    assert captured["header"] == SECRET
    assert SECRET not in captured["url"]
