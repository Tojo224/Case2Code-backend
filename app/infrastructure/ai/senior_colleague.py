import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.chat import ChatMessage
from app.domain.models.commands import UmlCommand
from app.domain.models.user import User, UserPresence
from app.infrastructure.ai.ai_provider import (
    AiProviderPort,
    GeminiAiProvider,
    LocalLlmAiProvider,
    RuleBasedAiProvider,
)
from app.infrastructure.ai.ai_uml_interpreter import AiUmlInterpreter

logger = logging.getLogger(__name__)


class SeniorColleagueOrchestrator:
    """Orchestrates the AI Senior Developer Colleague in the live multiplayer room.
    Maintains speaker awareness, room context, technical mentoring, and optional
    execution of UML mutations using a Local-First Hybrid AI provider chain.
    """

    def __init__(self, provider: Optional[AiProviderPort] = None):
        self.custom_provider = provider
        self.rule_based_provider = RuleBasedAiProvider()
        self.local_provider = LocalLlmAiProvider(
            base_url=settings.LOCAL_LLM_BASE_URL,
            model=settings.LOCAL_LLM_MODEL,
            api_key=settings.LOCAL_LLM_API_KEY,
            timeout=settings.LOCAL_LLM_TIMEOUT,
        )
        self.gemini_provider = (
            GeminiAiProvider(api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL)
            if settings.GEMINI_API_KEY
            else None
        )
        self.provider_mode: str = settings.AI_PROVIDER_MODE.lower().strip()
        self.last_used_engine: str = self.local_provider.provider_name

    def update_runtime_config(
        self,
        provider_mode: Optional[str] = None,
        local_base_url: Optional[str] = None,
        local_model: Optional[str] = None,
    ) -> None:
        """Updates the AI provider configuration dynamically at runtime."""
        if provider_mode and provider_mode.lower() in ("local", "hybrid", "gemini"):
            self.provider_mode = provider_mode.lower()
            settings.AI_PROVIDER_MODE = self.provider_mode
        if local_base_url and local_base_url.strip():
            clean_url = local_base_url.strip().rstrip("/")
            if clean_url.endswith("/v1"):
                clean_url = clean_url[:-3].rstrip("/")
            self.local_provider.base_url = clean_url
            settings.LOCAL_LLM_BASE_URL = clean_url
        if local_model and local_model.strip():
            self.local_provider.model = local_model.strip()
            settings.LOCAL_LLM_MODEL = local_model.strip()

    async def get_status(self) -> Dict[str, Any]:
        """Returns runtime telemetry and health status of the Local AI and Cloud fallback engines."""
        local_health = await self.local_provider.check_health()
        return {
            "provider_mode": self.provider_mode,
            "local_base_url": self.local_provider.base_url,
            "local_model": self.local_provider.model,
            "local_online": local_health.get("online", False),
            "local_available_models": local_health.get("available_models", []),
            "gemini_configured": bool(self.gemini_provider),
            "gemini_model": settings.GEMINI_MODEL if self.gemini_provider else None,
            "last_used_engine": self.last_used_engine,
        }

    def _get_provider_chain(self) -> List[AiProviderPort]:
        """Returns the ordered list of LLM providers according to the active mode."""
        if self.custom_provider:
            return [self.custom_provider]

        chain: List[AiProviderPort] = []
        if self.provider_mode == "local":
            chain.append(self.local_provider)
        elif self.provider_mode == "gemini":
            if self.gemini_provider:
                chain.append(self.gemini_provider)
        else:
            # Default "hybrid": Local LLM first -> Cloud Gemini fallback
            chain.append(self.local_provider)
            if self.gemini_provider:
                chain.append(self.gemini_provider)
        return chain

    async def process_colleague_interaction(
        self,
        prompt: str,
        speaker: User,
        active_users: List[UserPresence],
        document: CanonicalUmlDocument,
        chat_history: List[ChatMessage],
        ai_mode: str = "PAIR_PROGRAMMER",
    ) -> Tuple[str, Optional[str], List[UmlCommand], CanonicalUmlDocument]:
        """Processes a message from a team member and returns:
        (reply_text, addressed_to, executed_commands, updated_document)
        """
        addressed_to = speaker.name
        norm_prompt = prompt.strip()
        normalized_mode = (ai_mode or "PAIR_PROGRAMMER").strip().upper()

        is_mutation_intent = self._has_mutation_intent(norm_prompt)

        executed_commands: List[UmlCommand] = []
        updated_doc = document

        if normalized_mode == "PAIR_PROGRAMMER" and is_mutation_intent:
            # Try providers in chain before falling back to deterministic rule engine
            for cand_provider in self._get_provider_chain():
                try:
                    raw = await cand_provider.generate_commands(norm_prompt, document)
                    cmds = raw.get("commands", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                    question = raw.get("question") if isinstance(raw, dict) else None
                    if cmds or question:
                        interpreter = AiUmlInterpreter(provider=cand_provider)
                        updated_doc, executed_commands, interpreter_reply = (
                            await interpreter.interpret_and_execute(norm_prompt, document)
                        )
                        self.last_used_engine = cand_provider.provider_name
                        prefix = (
                            f"@{speaker.name}, "
                            if not interpreter_reply.startswith(f"@{speaker.name}")
                            else ""
                        )
                        return f"{prefix}{interpreter_reply}", addressed_to, executed_commands, updated_doc
                except Exception as e:
                    logger.info(f"Provider {cand_provider.provider_name} unavailable for commands: {e}")

            # Fallback to local deterministic rule engine
            fallback_interpreter = AiUmlInterpreter(provider=self.rule_based_provider)
            updated_doc, executed_commands, interpreter_reply = (
                await fallback_interpreter.interpret_and_execute(norm_prompt, document)
            )
            self.last_used_engine = self.rule_based_provider.provider_name
            prefix = f"@{speaker.name}, " if not interpreter_reply.startswith(f"@{speaker.name}") else ""
            return f"{prefix}{interpreter_reply}", addressed_to, executed_commands, updated_doc

        # Generate contextual conversational response as a Senior Architect
        reply_text = await self._generate_senior_advice(
            prompt=norm_prompt,
            speaker=speaker,
            active_users=active_users,
            document=document,
            chat_history=chat_history,
            ai_mode=normalized_mode,
        )

        return reply_text, addressed_to, [], document

    def _has_mutation_intent(self, text: str) -> bool:
        """Determines if the message explicitly asks to create or modify UML entities."""
        text_lower = text.lower().strip()
        # Questions asking for opinion ("¿qué opinás...", "¿conviene crear...?") are advisory
        if re.search(r"^(¿|\?)?\s*(qu[eé]\s+opin|c[oó]mo\s+ves|conviene|deber[ií]amos|por\s+qu[eé]|explic)", text_lower):
            return False

        mutation_keywords = [
            r"\b(crea(?:r|á)?|nueva|agregar?|agreg[aá]|add|create|new)\b",
            r"\b(elimina(?:r|á)?|delete|drop|borra(?:r|á)?|remove)\b",
            r"\b(relaciona(?:r|á)?|conecta(?:r|á)?|link|relate)\b",
            r"\b(renombra(?:r|á)?|rename)\b",
            r"\b(create\s+table)\b",
        ]
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
        """Generates natural architectural advice addressing the speaker via the active provider chain."""
        system_prompt = self._build_senior_system_prompt(
            speaker=speaker,
            active_users=active_users,
            document=document,
            chat_history=chat_history,
            ai_mode=ai_mode,
        )
        user_msg = f"Mensaje de {speaker.name}: {prompt}"

        for cand_provider in self._get_provider_chain():
            try:
                raw_reply = await cand_provider.generate_chat_response(system_prompt, user_msg)
                if raw_reply:
                    self.last_used_engine = cand_provider.provider_name
                    prefix = f"@{speaker.name}, " if f"@{speaker.name}" not in raw_reply else ""
                    return f"{prefix}{raw_reply}"
            except Exception as e:
                logger.info(f"Provider {cand_provider.provider_name} unavailable for chat: {e}")

        self.last_used_engine = self.rule_based_provider.provider_name
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

        recent_msgs = []
        for m in chat_history[-6:]:
            sender = m.sender_name
            recent_msgs.append(f"[{sender}]: {m.content}")
        history_str = "\n".join(recent_msgs) if recent_msgs else "Inicio de la conversación."

        classes_str = ", ".join([c.name for c in document.classes]) or "Ninguna entidad aún"
        rels_str = (
            ", ".join([f"{r.source_class_id} -> {r.target_class_id} ({r.type})" for r in document.relationships])
            or "Sin relaciones"
        )

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

        if any(w in norm for w in ["3fn", "normaliza", "normalización", "tercera forma"]):
            return (
                f"@{speaker.name}, para garantizar la 3FN en nuestras {len(document.classes)} entidad(es), "
                f"verificá que ningún atributo no-clave dependa transitivamente de otro atributo no-clave. "
                f"Si tenés datos repetidos (como categoría o ciudad dentro de una tabla transaccional), extraelos a su propia entidad maestra con relación 1:N."
            )

        if any(w in norm for w in ["cardinalidad", "relación", "relacion", "entidades"]):
            cls_names = ", ".join([c.name for c in document.classes]) if document.classes else "ninguna clase aún"
            return (
                f"@{speaker.name}, revisando el modelo actual ({cls_names}): asegurate de que cada relación 1:N tenga la clave foránea del lado 'muchos' (`*`) "
                f"y que no queden relaciones N:M sin tabla intermedia si necesitás auditar fechas o estados. ¿Querés que ajustemos alguna relación en modo Pair Dev?"
            )

        return (
            f"@{speaker.name}, estuve analizando tu consulta sobre '{prompt}'. "
            f"Actualmente en el tablero tenemos {len(document.classes)} entidad(es). "
            f"¿Querés que modelemos una nueva tabla o preferís que revisemos las relaciones existentes?"
        )


senior_colleague_orchestrator = SeniorColleagueOrchestrator()
