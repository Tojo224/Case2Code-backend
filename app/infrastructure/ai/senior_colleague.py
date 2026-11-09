from datetime import datetime, timezone
import json
import logging
from pathlib import Path
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

MEMORY_JSON_PATH = Path(__file__).resolve().parents[3] / "ai_memory.json"


class AiJsonMemoryStore:
    """Persists per-diagram AI conversational context, last domain topic,
    and pending hologram command suggestions in a JSON file.
    """

    def __init__(self, file_path: Path = MEMORY_JSON_PATH):
        self.file_path = file_path

    def _read_all(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            return {}
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Could not read AI memory JSON: {e}")
            return {}

    def _write_all(self, data: Dict[str, Any]) -> None:
        try:
            self.file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Could not write AI memory JSON: {e}")

    def get_diagram_memory(self, diagram_id: str) -> Dict[str, Any]:
        all_data = self._read_all()
        return all_data.get(diagram_id, {})

    def save_diagram_memory(
        self,
        diagram_id: str,
        last_topic: Optional[str] = None,
        last_prompt: Optional[str] = None,
        pending_commands: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        all_data = self._read_all()
        entry = all_data.get(diagram_id, {})
        if last_topic is not None:
            entry["last_topic"] = last_topic
        if last_prompt is not None:
            entry["last_prompt"] = last_prompt
        if pending_commands is not None:
            entry["pending_commands"] = pending_commands
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        all_data[diagram_id] = entry
        self._write_all(all_data)
        return entry


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
        self.last_suggested_commands: List[UmlCommand] = []
        self.memory_store = AiJsonMemoryStore()

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

    async def _run_interpreter(
        self, prompt_to_run: str, target_doc: CanonicalUmlDocument
    ) -> Tuple[CanonicalUmlDocument, List[UmlCommand], str]:
        """Executes or simulates UML commands through the provider chain or rule engine fallback."""
        for cand_provider in self._get_provider_chain():
            try:
                raw = await cand_provider.generate_commands(prompt_to_run, target_doc)
                cmds = raw.get("commands", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                question = raw.get("question") if isinstance(raw, dict) else None
                if cmds or question:
                    interpreter = AiUmlInterpreter(provider=cand_provider)
                    updated_doc, executed_commands, interpreter_reply = (
                        await interpreter.interpret_and_execute(prompt_to_run, target_doc)
                    )
                    self.last_used_engine = cand_provider.provider_name
                    return updated_doc, executed_commands, interpreter_reply
            except Exception as e:
                logger.info(f"Provider {cand_provider.provider_name} unavailable for commands: {e}")

        fallback_interpreter = AiUmlInterpreter(provider=self.rule_based_provider)
        updated_doc, executed_commands, interpreter_reply = (
            await fallback_interpreter.interpret_and_execute(prompt_to_run, target_doc)
        )
        self.last_used_engine = self.rule_based_provider.provider_name
        return updated_doc, executed_commands, interpreter_reply

    def _extract_domain_topic(self, text: str) -> Optional[str]:
        norm = text.lower()
        domain_map = [
            ("ecommerce", ["ecommerce", "e-commerce", "ecomerce", "e comerce", "tienda", "carrito", "compras", "ventas"]),
            ("veterinaria", ["veterinaria", "mascota", "animal"]),
            ("hotel", ["hotel", "hospedaje", "alojamiento"]),
            ("universidad", ["universidad", "colegio", "escuela", "instituto", "educativo", "academico", "académico"]),
            ("biblioteca", ["biblioteca", "libro", "prestamo", "préstamo"]),
            ("hospital", ["hospital", "clinica", "clínica", "medico", "médico", "paciente", "consultorio", "salud"]),
            ("restaurante", ["restaurante", "restaurant", "comida", "gastronomia", "gastronomía", "menu", "menú"]),
            ("inventario", ["inventario", "almacen", "almacén", "bodega", "stock", "logistica", "logística"]),
        ]
        for topic_key, keywords in domain_map:
            if any(k in norm for k in keywords):
                return topic_key
        return None

    def _is_confirmation_intent(self, text: str) -> bool:
        clean = re.sub(
            r"^(?:@(?:ai|senior|bot)|/ai)\b[\s,:;-]*",
            "",
            text.lower().strip(),
        ).strip()
        return bool(
            re.search(
                r"\b(puedes\s+hacerlo|pod[eé]s\s+hacerlo|hacelo|h[aá]galo|hazlo|aplicalo|apl[ií]calo|aplica\s+los\s+cambios|aplica\s+el\s+holograma|crealo|cr[eé]alo|armalo|[aá]rmalo|dale|procede|adelante|s[ií]\s+por\s+favor|hace\s+eso|hac[eé]\s+eso)\b",
                clean,
            )
        )

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
        self.last_suggested_commands = []
        addressed_to = speaker.name
        norm_prompt = prompt.strip()
        normalized_mode = (ai_mode or "PAIR_PROGRAMMER").strip().upper()
        diagram_id = document.id

        # 1. Load and update persistent JSON memory for this diagram
        mem = self.memory_store.get_diagram_memory(diagram_id)
        detected_topic = self._extract_domain_topic(norm_prompt)
        if not detected_topic:
            for msg in reversed(chat_history[-6:]):
                t = self._extract_domain_topic(msg.content)
                if t:
                    detected_topic = t
                    break
        active_topic = detected_topic or mem.get("last_topic")
        if detected_topic:
            mem = self.memory_store.save_diagram_memory(
                diagram_id=diagram_id,
                last_topic=detected_topic,
                last_prompt=norm_prompt,
            )

        is_confirmation = self._is_confirmation_intent(norm_prompt)
        is_mutation_intent = self._has_mutation_intent(norm_prompt) or is_confirmation

        effective_prompt = norm_prompt
        if is_confirmation:
            if active_topic:
                effective_prompt = f"crea una base de datos para {active_topic}"
            elif mem.get("last_prompt"):
                effective_prompt = str(mem["last_prompt"])

        executed_commands: List[UmlCommand] = []
        updated_doc = document

        # 2. PAIR_PROGRAMMER mode with mutation or confirmation intent -> execute for real
        if normalized_mode == "PAIR_PROGRAMMER" and is_mutation_intent:
            updated_doc, executed_commands, interpreter_reply = await self._run_interpreter(
                effective_prompt, document
            )
            if executed_commands:
                self.memory_store.save_diagram_memory(
                    diagram_id=diagram_id,
                    last_topic=active_topic,
                    last_prompt=effective_prompt,
                    pending_commands=[],
                )
            prefix = f"@{speaker.name}, " if not interpreter_reply.startswith(f"@{speaker.name}") else ""
            return f"{prefix}{interpreter_reply}", addressed_to, executed_commands, updated_doc

        # 3. ADVISORY mode (or domain consultation) -> generate advice AND project non-destructive Hologram
        reply_text = await self._generate_senior_advice(
            prompt=norm_prompt,
            speaker=speaker,
            active_users=active_users,
            document=document,
            chat_history=chat_history,
            ai_mode=normalized_mode,
        )

        # Simulate hologram commands on a deep copy without mutating the real document
        hologram_prompt = effective_prompt if is_mutation_intent else (
            f"crea una base de datos para {active_topic}" if detected_topic else None
        )
        if hologram_prompt:
            try:
                sim_doc = document.model_copy(deep=True)
                _, sim_commands, _ = await self._run_interpreter(hologram_prompt, sim_doc)
                if sim_commands:
                    self.last_suggested_commands = sim_commands
                    self.memory_store.save_diagram_memory(
                        diagram_id=diagram_id,
                        last_topic=active_topic,
                        last_prompt=hologram_prompt,
                        pending_commands=[c.model_dump(mode="json") for c in sim_commands],
                    )
                    if "Holograma" not in reply_text:
                        reply_text += (
                            f"\n\n👻 **Holograma proyectado ({len(sim_commands)} cambios sugeridos):** "
                            f"Dibujé la propuesta en el lienzo como vista previa sin modificar tu diagrama real. "
                            f"Podés revisarla y hacer clic en **Aplicar Holograma** o decirme `@ai hacelo` en modo **Pair Dev**."
                        )
            except Exception as e:
                logger.warning(f"Could not simulate hologram preview: {e}")

        return reply_text, addressed_to, [], document

    def _has_mutation_intent(self, text: str) -> bool:
        """Determines if the message explicitly asks to create or modify UML entities."""
        text_lower = re.sub(
            r"^(?:@(?:ai|senior|bot)|/ai)\b[\s,:;-]*",
            "",
            text.lower().strip(),
        ).strip()

        # Questions asking for opinion or evaluation ("¿qué opinás...", "dices que estamos bien...?") are advisory
        if re.search(
            r"^(¿|\?)?\s*(qu[eé]\s+opin|c[oó]mo\s+ves|conviene|deber[ií]amos|por\s+qu[eé]|explic|dices\s+que|crees\s+que|estamos\s+bien|revisa\s+si|valida\s+si)",
            text_lower,
        ):
            return False

        mutation_keywords = [
            r"\b(crea(?:r|á|me)?|nueva|nuevo|agregar?|agreg[aá](?:me)?|a[ñn]ade|a[ñn]adir|add|create|new)\b",
            r"\b(hace(?:r|me)?|hac[eé](?:me)?|hagas|haz(?:me)?|arma(?:r|me)?|arm[aá](?:me)?|dise[ñn]a(?:r|me)?|genera(?:r|me)?|modela(?:r|me)?|construye|construir)\b",
            r"\b(base\s+de\s+datos|esquema|modelo|sistema|diagrama)\s+(?:b[aá]sica\s+|b[aá]sico\s+|completa\s+|completo\s+|simple\s+)?(?:de|para|sobre)\b",
            r"\b(elimina(?:r|á)?|delete|drop|borra(?:r|á)?|quita(?:r)?|remove)\b",
            r"\b(relaciona(?:r|á)?|conecta(?:r|á)?|vincula(?:r)?|asocia(?:r)?|hereda|extiende|compone|implementa|link|relate|connect|extends|implements)\b",
            r"\b(renombra(?:r|á)?|cambia(?:r)?\s+el\s+nombre|rename)\b",
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
        norm = re.sub(
            r"^(?:@(?:ai|senior|bot)|/ai)\b[\s,:;-]*",
            "",
            prompt.lower().strip(),
        ).strip()
        cls_names = [c.name for c in document.classes]
        cls_names_str = ", ".join(cls_names) if cls_names else "ninguna entidad aún"

        if any(w in norm for w in ["hola", "buenas", "que tal", "saludos", "alo", "aló", "estas", "estás", "ahi", "ahí"]):
            return (
                f"¡Acá estoy @{speaker.name}! Actualmente veo en el tablero: {cls_names_str}. "
                f"En modo **Asesor** te doy feedback de arquitectura, y en **Pair Dev** modifico el diagrama en vivo "
                f"(ej. `@ai hacé la base de datos para un ecommerce` o `@ai crea la clase Producto con nombre y precio`). ¿Qué armamos?"
            )

        if any(w in norm for w in ["ecommerce", "e-commerce", "ecomerce", "e comerce", "tienda", "carrito", "ventas"]):
            expected = ["Cliente", "Categoria", "Producto", "Pedido", "DetallePedido"]
            existing_lower = {c.lower() for c in cls_names}
            missing = [e for e in expected if e.lower() not in existing_lower]
            if not missing:
                return (
                    f"@{speaker.name}, ¡sí, van muy bien encaminados! Ya tienen las entidades clave del e-commerce ({cls_names_str}). "
                    f"Como siguiente paso, podrían sumar `Pago` o `DireccionEnvio` asociados a `Pedido`."
                )
            return (
                f"@{speaker.name}, revisando el tablero actual ({cls_names_str}), para un e-commerce sólido todavía les faltan "
                f"las entidades clave: **{', '.join(missing)}** (especialmente `DetallePedido` para resolver la relación N:M entre Pedido y Producto guardando cantidad y precio histórico). "
                f"Si estás en modo **Pair Dev**, escribí `@ai hacé la base de datos para un ecommerce` y te las genero ahora mismo."
            )

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
                f"@{speaker.name}, para garantizar la 3FN en nuestras {len(document.classes)} entidad(es) ({cls_names_str}), "
                f"verificá que ningún atributo no-clave dependa transitivamente de otro atributo no-clave. "
                f"Si tenés datos repetidos (como categoría o ciudad dentro de una tabla transaccional), extraelos a su propia entidad maestra con relación 1:N."
            )

        if any(w in norm for w in ["cardinalidad", "relación", "relacion", "entidades", "encaminados", "ecaminados", "opinas", "opinás", "revisa"]):
            return (
                f"@{speaker.name}, revisando el modelo actual ({cls_names_str} y {len(document.relationships)} relación(es)): "
                f"asegurate de que cada relación 1:N tenga la clave foránea del lado 'muchos' (`*`) "
                f"y que no queden relaciones N:M sin tabla intermedia si necesitás auditar fechas o estados. ¿Querés que ajustemos o agreguemos tablas en modo Pair Dev?"
            )

        return (
            f"@{speaker.name}, analizando tu consulta sobre '{prompt}': "
            f"actualmente en el tablero tenemos {len(document.classes)} entidad(es) ({cls_names_str}). "
            f"En modo **Pair Dev** podés pedirme `@ai hacé la base de datos para un ecommerce` (o veterinaria, hotel, hospital, restaurante, inventario) o `@ai crea la clase X con...`."
        )


senior_colleague_orchestrator = SeniorColleagueOrchestrator()
