"""W3: a hung or dead LLM provider must not hold a room hostage nor be hammered."""

import asyncio

import pytest

from app.core.config import settings
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.user import User
from app.infrastructure.ai.ai_provider import AiProviderPort
from app.infrastructure.ai.circuit_breaker import CircuitBreaker
from app.infrastructure.ai.senior_colleague import SeniorColleagueOrchestrator


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_breaker_opens_after_threshold_and_recovers_after_cooldown():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, cooldown_s=60, clock=clock)
    for _ in range(2):
        breaker.record_failure()
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.allow() is False  # open

    clock.now = 61
    assert breaker.allow() is True  # half-open: one probe
    assert breaker.allow() is False  # a second concurrent call is refused
    breaker.record_success()
    assert breaker.allow() is True and breaker.is_open is False


def test_failed_probe_reopens_the_breaker():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=10, clock=clock)
    breaker.record_failure()
    clock.now = 11
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.allow() is False
    clock.now = 22
    assert breaker.allow() is True


class HangingProvider(AiProviderPort):
    def __init__(self):
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "Hanging"

    async def generate_commands(self, prompt, current_uml, image_data=None):
        self.calls += 1
        await asyncio.sleep(3600)

    async def generate_chat_response(self, system_prompt, user_message):
        self.calls += 1
        await asyncio.sleep(3600)


class BrokenProvider(HangingProvider):
    @property
    def provider_name(self) -> str:
        return "Broken"

    async def generate_chat_response(self, system_prompt, user_message):
        self.calls += 1
        raise ConnectionError("down")


def _speaker():
    return User(id="u1", email="a@b.c", name="Ana", avatar_color="#fff")


async def _advice(orch):
    return await orch._generate_senior_advice(
        prompt="¿qué opinás de mi modelo?",  # a real question: bare greetings skip the provider
        speaker=_speaker(),
        active_users=[],
        document=CanonicalUmlDocument(id="d", name="D"),
        ai_mode="advisory",
        chat_history=[],
    )


def test_hung_provider_is_cut_by_the_deadline_and_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER_DEADLINE_S", 0.05)
    provider = HangingProvider()
    orch = SeniorColleagueOrchestrator(provider=provider)

    async def scenario():
        return await asyncio.wait_for(_advice(orch), timeout=2)

    reply, engine = asyncio.run(scenario())
    assert provider.calls == 1
    assert reply  # rule-based fallback answered
    assert engine == orch.rule_based_provider.provider_name


def test_dead_provider_is_skipped_once_the_breaker_opens(monkeypatch):
    monkeypatch.setattr(settings, "AI_BREAKER_FAILURES", 2)
    monkeypatch.setattr(settings, "AI_BREAKER_COOLDOWN_S", 600)
    provider = BrokenProvider()
    orch = SeniorColleagueOrchestrator(provider=provider)

    async def scenario():
        for _ in range(5):
            await _advice(orch)

    asyncio.run(scenario())
    assert provider.calls == 2  # calls 3..5 never reached the provider
