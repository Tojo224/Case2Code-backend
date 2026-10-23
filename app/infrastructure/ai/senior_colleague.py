import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.application.command_bus import command_bus
from app.core.config import settings
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage, SenderType
from app.domain.models.commands import UmlCommand
from app.domain.models.user import User, UserPresence
from app.domain.services.uml_validator import UmlValidationError
from app.infrastructure.ai.ai_provider import (
    AiProviderPort,
    DatabaseSchemaParser,
    GeminiAiProvider,
    RuleBasedAiProvider,
)
from app.infrastructure.ai.ai_uml_interpreter import AiUmlInterpreter

logger = logging.getLogger(__name__)


class SeniorColleagueOrchestrator:
    """Orchestrates the AI Senior Developer Colleague in the live multiplayer room.
    Maintains speaker awareness, room context, technical mentoring, and optional
    execution of UML mutations.
    """

    def __init__(self, provider: Optional[AiProviderPort] = None):
        self.rule_based_provider = RuleBasedAiProvider()
        self.uml_interpreter = AiUmlInterpreter(provider=provider)
        if provider:
            self.primary_provider = provider
        elif settings.GEMINI_API_KEY:
            self.primary_provider = GeminiAiProvider(
                api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL
            )
        else:
            self.primary_provider = self.rule_based_provider

    async def process_colleague_interaction(
        self,
        prompt: str,
        speaker: User,
        active_users: List[UserPresence],
        document: CanonicalUmlDocument,
        chat_history: List[ChatMessage],
        ai_mode: str = "PAIR_PROGRAMMER",  # "ADVISORY" | "PAIR_PROGRAMMER"
    ) -> Tuple[str, Optional[str], List[UmlCommand], CanonicalUmlDocument]:
        """Processes a message from a team member and returns:
        (reply_text, addressed_to, executed_commands, updated_document)
        """
        addressed_to = speaker.name
        norm_prompt = prompt.strip()

        # Check if the prompt is an explicit diagram creation/mutation or question
        is_mutation_intent = self._has_mutation_intent(norm_prompt)

        # In PAIR_PROGRAMMER mode with mutation intent, delegate command generation and execution
        executed_commands: List[UmlCommand] = []
        updated_doc = document

        if ai_mode == "PAIR_PROGRAMMER" and is_mutation_intent:
            updated_doc, executed_commands, interpreter_reply = (
                await self.uml_interpreter.interpret_and_execute(norm_prompt, document)
            )
            # Ensure the reply addresses the speaker
            prefix = f"@{speaker.name}, " if not interpreter_reply.startswith(f"@{speaker.name}") else ""
            final_reply = f"{prefix}{interpreter_reply}"
            return final_reply, addressed_to, executed_commands, updated_doc

        # Otherwise, generate a contextual conversational response as a Senior Architect
        reply_text = await self._generate_senior_advice(
            prompt=norm_prompt,
            speaker=speaker,
            active_users=active_users,
            document=document,
            chat_history=chat_history,
            ai_mode=ai_mode,
        )

        return reply_text, addressed_to, [], document

    def _has_mutation_intent(self, text: str) -> bool:
        """Determines if the message asks to create or modify UML entities."""
        mutation_keywords = [
            r"\b(crea(?:r)?|nueva|agregar?|add|create|new)\b",
            r"\b(elimina(?:r)?|delete|drop|borra(?:r)?|remove)\b",
            r"\b(relaciona(?:r)?|conecta(?:r)?|link|relate)\b",
            r"\b(renombra(?:r)?|rename)\b",
            r"\b(create\s+table)\b",
        ]
        text_lower = text.lower()
        return any(re.search(pat, text_lower) for pat in mutation_keywords)

    async def _generate_senior_advice(
        self,
        prompt: str,
        speaker: User,
        active_users: List[UserPresence],
        document: CanonicalUmlDocument,
        chat_history: List[ChatMessage],
        ai_mode: str,
    ) -> str:
        """Generates natural architectural advice addressing the speaker."""
        # If Gemini is configured, use it for rich architectural reasoning
        if isinstance(self.primary_provider, GeminiAiProvider):
            try:
                system_prompt = self._build_senior_system_prompt(
                    speaker=speaker,
                    active_users=active_users,
                    document=document,
                    chat_history=chat_history,
                    ai_mode=ai_mode,
                )
                payload = {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": f"{system_prompt}\n\nMensaje de {speaker.name}: {prompt}"}],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.4,
                        "maxOutputTokens": 2048,
                    },
                }

                timeout = httpx.Timeout(10.0, connect=3.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(self.primary_provider.endpoint, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        parts = candidates[0]["content"].get("parts", [])
                        if parts:
                            raw_reply = parts[0].get("text", "").strip()
                            if raw_reply:
                                return raw_reply
            except Exception as e:
                logger.warning(f"Error calling Gemini in SeniorColleagueOrchestrator: {e}")

        # Fallback heuristic Senior dev response
        return self._build_fallback_senior_advice(prompt, speaker, document)

    def _build_senior_system_prompt(
        self,
        speaker: User,
        active_users: List[UserPresence],
        document: CanonicalUmlDocument,
        chat_history: List[ChatMessage],
        ai_mode: str,
    ) -> str:
        team_list = ", ".join([f"{u.name} ({u.email})" for u in active_users]) or speaker.name
        
        # Format recent messages
        recent_msgs = []
        for m in chat_history[-6:]:
            sender = m.sender_name
            recent_msgs.append(f"[{sender}]: {m.content}")
        history_str = "\n".join(recent_msgs) if recent_msgs else "Inicio de la conversación."

        # Format current UML classes
        classes_str = ", ".join([c.name for c in document.classes]) or "Ninguna entidad aún"
        rels_str = ", ".join([f"{r.source_class_id} -> {r.target_class_id} ({r.type})" for r in document.relationships]) or "Sin relaciones"

        return f"""Sos un Arquitecto de Software Senior y Desarrollador Líder en el equipo de Case2Code.
Estás conectado en la misma sala colaborativa en tiempo real con el equipo de desarrollo.
Tu rol es acompañar, aconsejar con criterio técnico sólido (buenas prácticas, normalización, JPA, Spring Boot) y resolver dudas.

ESTADO DE LA SALA:
- Miembros conectados: {team_list}
- Estado del Diagrama UML actual:
  * Entidades: {classes_str}
  * Relaciones: {rels_str}
- Historial reciente de la sala:
{history_str}

REGLAS DE INTERACCIÓN OBLIGATORIAS:
1. Dirigite siempre a la persona que te preguntó (@{speaker.name}) por su nombre.
2. Sé apasionado, claro, técnico y conciso. Usá español rioplatense natural (voseo amigable y profesional).
3. Modo actual de IA: {ai_mode}.
   - Si es ADVISORY: Da tu opinión de diseño, plantea los tradeoffs (pros/contras) y preguntá si quieren que apliques los cambios.
   - Si te piden aplicar un cambio o crear tablas, confirmá lo que recomendás.
4. Si la pregunta de @{speaker.name} se relaciona con algo que otro compañero dijo antes, podés mencionarlo con naturalidad.
"""

    def _build_fallback_senior_advice(
        self, prompt: str, speaker: User, document: CanonicalUmlDocument
    ) -> str:
        norm = prompt.lower()
        if any(w in norm for w in ["hola", "buenas", "que tal", "saludos"]):
            return f"¡Hola @{speaker.name}! ¿En qué te puedo ayudar hoy con el diseño del diagrama o las entidades?"

        if any(w in norm for w in ["muchos a muchos", "n:m", "m:n", "many to many"]):
            return (
                f"@{speaker.name}, para relaciones de muchos a muchos (N:M) en JPA/Spring Boot, "
                f"te recomiendo evaluar si la relación tiene atributos propios (como fecha, estado o cantidad). "
                f"Si los tiene, es mejor modelar una entidad intermedia explícita con dos relaciones 1:N. "
                f"Si es una relación pura sin datos extra, usamos ManyToMany directo. ¿Querés que armemos esa entidad intermedia?"
            )

        if any(w in norm for w in ["herencia", "inheritance", "polimorfismo"]):
            return (
                f"@{speaker.name}, ojo con abusar de la herencia en bases de datos relacionales. "
                f"En Spring Boot/JPA podés usar `InheritanceType.JOINED` o `SINGLE_TABLE`. "
                f"Recordá la regla de oro: 'prefer composition over inheritance' salvo que sea una jerarquía 'ES-UN' estricta."
            )

        return (
            f"@{speaker.name}, estuve analizando tu consulta sobre '{prompt}'. "
            f"Actualmente en el tablero tenemos {len(document.classes)} entidad(es). "
            f"¿Querés que modelemos una nueva tabla o preferís que revisemos las relaciones existentes?"
        )


senior_colleague_orchestrator = SeniorColleagueOrchestrator()
