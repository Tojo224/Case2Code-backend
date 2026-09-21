import logging
import re
import unicodedata
import uuid
from typing import Any, Dict, List, Optional, Tuple
from pydantic import TypeAdapter

from app.application.command_bus import command_bus
from app.core.config import settings
from app.domain.models.canonical_uml import CanonicalUmlDocument
from app.domain.models.commands import CommandTypeEnum, UmlCommand
from app.domain.services.uml_validator import UmlValidationError
from app.infrastructure.ai.ai_provider import (
    AiProviderPort,
    GeminiAiProvider,
    RuleBasedAiProvider,
)

logger = logging.getLogger(__name__)

command_adapter = TypeAdapter(UmlCommand)


class AiUmlInterpreter:
    """Interprets natural language into strongly-typed UML mutation commands
    and dispatches them onto the Canonical UML Document.
    """

    def __init__(self, provider: Optional[AiProviderPort] = None):
        self.rule_based_provider = RuleBasedAiProvider()
        if provider:
            self.primary_provider = provider
        elif settings.GEMINI_API_KEY:
            self.primary_provider = GeminiAiProvider(
                api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL
            )
        else:
            self.primary_provider = self.rule_based_provider

    async def interpret_and_execute(
        self,
        prompt: str,
        document: CanonicalUmlDocument,
        image_data: Optional[Dict[str, str]] = None,
        api_key_override: Optional[str] = None,
    ) -> Tuple[CanonicalUmlDocument, List[UmlCommand], str]:
        """Translates natural language prompt and/or image to commands and executes them on the document.
        Returns:
            (updated_document, executed_commands, assistant_reply)
        """
        raw_commands, clarification_question = await self._generate_raw_commands(
            prompt, document, image_data=image_data, api_key_override=api_key_override
        )
        if not raw_commands:
            if clarification_question:
                return (document, [], clarification_question)
            return (
                document,
                [],
                "No pude identificar entidades ni relaciones en el mensaje o imagen. "
                "¿Qué base de datos o modelo te gustaría crear? Podés pegarme un script SQL (CREATE TABLE), "
                "una lista de tablas (ej. 'Cliente (id, nombre), Pedido (id, total, cliente_id)') o describir tu negocio.",
            )

        resolved_commands, auto_roles, validation_error = self._resolve_and_validate_commands(
            raw_commands, document
        )
        if validation_error:
            return document, [], f"Error en la validación de comandos: {validation_error}"

        if not resolved_commands:
            return document, [], "Los comandos generados no pudieron ser resueltos en el contexto actual."

        current_doc = document
        executed_commands: List[UmlCommand] = []

        for cmd in resolved_commands:
            try:
                current_doc = command_bus.dispatch(current_doc, cmd)
                executed_commands.append(cmd)
            except UmlValidationError as e:
                logger.warning(f"Validation error executing command {cmd}: {e}")
                return (
                    current_doc,
                    executed_commands,
                    f"Se interrumpió la ejecución por una regla de validación UML: {str(e)}",
                )
            except Exception as e:
                logger.error(f"Error executing command {cmd}: {e}")
                return (
                    current_doc,
                    executed_commands,
                    f"Error interno al aplicar el comando: {str(e)}",
                )

        reply = self._build_natural_reply(executed_commands, auto_roles)
        if clarification_question:
            reply += f"\n\n❓ **Consulta del Asistente:** {clarification_question}"
        return current_doc, executed_commands, reply

    def _extract_commands_and_question(self, res: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if isinstance(res, tuple) and len(res) == 2:
            return res[0], res[1]
        if isinstance(res, dict):
            return res.get("commands", []), res.get("question")
        if isinstance(res, list):
            return res, None
        return [], None

    async def _generate_raw_commands(
        self,
        prompt: str,
        document: CanonicalUmlDocument,
        image_data: Optional[Dict[str, str]] = None,
        api_key_override: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        active_provider = self.primary_provider

        # If an override key was passed, instantiate provider with that key
        if api_key_override:
            active_provider = GeminiAiProvider(api_key=api_key_override, model=settings.GEMINI_MODEL)
        elif self.primary_provider == self.rule_based_provider and settings.GEMINI_API_KEY:
            active_provider = GeminiAiProvider(api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL)

        # First try active primary provider (Gemini if key is present)
        if active_provider != self.rule_based_provider:
            try:
                res = await active_provider.generate_commands(
                    prompt, document, image_data=image_data
                )
                cmds, question = self._extract_commands_and_question(res)
                if cmds or question:
                    return cmds, question
            except Exception as e:
                logger.warning(f"Primary AI provider failed, falling back to rule-based: {e}")

        # Fallback to deterministic rule-based
        res = await self.rule_based_provider.generate_commands(
            prompt, document, image_data=image_data
        )
        return self._extract_commands_and_question(res)

    @staticmethod
    def sanitize_identifier(name: str, pascal: bool = False) -> str:
        if not name:
            return "Entity" if pascal else "attribute"
        nfkd = unicodedata.normalize("NFKD", str(name).strip())
        clean = "".join([c for c in nfkd if not unicodedata.combining(c)])
        
        tokens = [t for t in re.split(r"[^A-Za-z0-9]+", clean) if t]
        words = []
        for token in tokens:
            subwords = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|[0-9]+", token)
            if subwords:
                words.extend(subwords)
            else:
                words.append(token)

        if not words:
            return "Entity" if pascal else "attribute"

        def cap(w: str) -> str:
            return w[0].upper() + w[1:] if len(w) > 1 else w.upper()

        if pascal:
            return "".join(cap(w) for w in words)
        else:
            return words[0].lower() + "".join(cap(w) for w in words[1:])

    def _resolve_and_validate_commands(
        self, raw_commands: List[Dict[str, Any]], initial_doc: CanonicalUmlDocument
    ) -> Tuple[List[UmlCommand], List[Tuple[str, str, str]], Optional[str]]:
        """Maps class/attribute names to IDs, resolves multi-relation collisions and validates schemas."""
        # Build lookup maps
        name_to_class_id: Dict[str, str] = {}
        for c in initial_doc.classes:
            name_to_class_id[c.name.lower()] = c.id
            name_to_class_id[c.id.lower()] = c.id
            sanitized = self.sanitize_identifier(c.name, pascal=True).lower()
            name_to_class_id[sanitized] = c.id
            name_to_class_id[c.name.lower().replace(" ", "_")] = c.id
            name_to_class_id[c.name.lower().replace(" ", "-")] = c.id

        id_to_class: Dict[str, Any] = {c.id: c for c in initial_doc.classes}

        # Multi-relation tracking for Tier-2 anti-collision: (src_id, tgt_id, type) -> count
        rel_key_count: Dict[Tuple[str, str, str], int] = {}
        for r in initial_doc.relationships:
            k = (r.source_class_id, r.target_class_id, r.type.value)
            rel_key_count[k] = rel_key_count.get(k, 0) + 1

        auto_resolved_roles: List[Tuple[str, str, str]] = []
        validated_commands: List[UmlCommand] = []

        for raw in raw_commands:
            cmd_type = raw.get("command_type")
            if not cmd_type:
                continue

            # If CREATE_CLASS, sanitize name and ensure class_id is generated and recorded in name lookup
            if cmd_type == CommandTypeEnum.CREATE_CLASS.value:
                raw_name = str(raw.get("name", "")).strip()
                sanitized_name = self.sanitize_identifier(raw_name, pascal=True)
                raw["name"] = sanitized_name
                if not raw.get("class_id"):
                    raw["class_id"] = f"cls-{uuid.uuid4().hex[:8]}"
                class_id = raw["class_id"]

                name_to_class_id[raw_name.lower()] = class_id
                name_to_class_id[sanitized_name.lower()] = class_id
                name_to_class_id[raw_name.lower().replace(" ", "_")] = class_id
                name_to_class_id[raw_name.lower().replace(" ", "-")] = class_id
                name_to_class_id[class_id.lower()] = class_id

            # If ADD_ATTRIBUTE or UPDATE_ATTRIBUTE, sanitize attribute name
            if cmd_type in (CommandTypeEnum.ADD_ATTRIBUTE.value, CommandTypeEnum.UPDATE_ATTRIBUTE.value):
                attr_name = raw.get("name")
                if attr_name:
                    raw["name"] = self.sanitize_identifier(attr_name, pascal=False)

            # If references class_id by class name, resolve to real ID
            if "class_id" in raw and raw["class_id"] and cmd_type != CommandTypeEnum.CREATE_CLASS.value:
                class_ref = str(raw["class_id"]).strip()
                if class_ref.lower() in name_to_class_id:
                    raw["class_id"] = name_to_class_id[class_ref.lower()]
                else:
                    sanitized_cls = self.sanitize_identifier(class_ref, pascal=True).lower()
                    if sanitized_cls in name_to_class_id:
                        raw["class_id"] = name_to_class_id[sanitized_cls]

            # If references source_class_id or target_class_id by name, resolve
            if "source_class_id" in raw and raw["source_class_id"]:
                src_ref = str(raw["source_class_id"]).strip()
                if src_ref.lower() in name_to_class_id:
                    raw["source_class_id"] = name_to_class_id[src_ref.lower()]
                else:
                    sanitized_src = self.sanitize_identifier(src_ref, pascal=True).lower()
                    if sanitized_src in name_to_class_id:
                        raw["source_class_id"] = name_to_class_id[sanitized_src]

            if "target_class_id" in raw and raw["target_class_id"]:
                tgt_ref = str(raw["target_class_id"]).strip()
                if tgt_ref.lower() in name_to_class_id:
                    raw["target_class_id"] = name_to_class_id[tgt_ref.lower()]
                else:
                    sanitized_tgt = self.sanitize_identifier(tgt_ref, pascal=True).lower()
                    if sanitized_tgt in name_to_class_id:
                        raw["target_class_id"] = name_to_class_id[sanitized_tgt]

            # If references attribute_id by attribute name, resolve
            if cmd_type in [CommandTypeEnum.UPDATE_ATTRIBUTE.value, CommandTypeEnum.DELETE_ATTRIBUTE.value]:
                class_id = raw.get("class_id")
                attr_ref = raw.get("attribute_id")
                if class_id and attr_ref and class_id in id_to_class:
                    cls_obj = id_to_class[class_id]
                    for a in cls_obj.attributes:
                        if a.name.lower() == str(attr_ref).lower() or a.id == str(attr_ref):
                            raw["attribute_id"] = a.id
                            break

            # If CREATE_RELATIONSHIP, check multi-relation collision
            if cmd_type == CommandTypeEnum.CREATE_RELATIONSHIP.value:
                src = raw.get("source_class_id")
                tgt = raw.get("target_class_id")
                rtype = raw.get("type", "ONE_TO_MANY")
                if src and tgt:
                    k = (str(src), str(tgt), str(rtype))
                    count = rel_key_count.get(k, 0)
                    target_role = raw.get("target_role")
                    if count > 0:
                        # Auto-assign disambiguated role if missing or empty
                        if not target_role or not str(target_role).strip():
                            new_role = f"rol_{count + 1}"
                            raw["target_role"] = new_role
                            src_name = next((c.name for c in initial_doc.classes if c.id == src), str(src))
                            tgt_name = next((c.name for c in initial_doc.classes if c.id == tgt), str(tgt))
                            auto_resolved_roles.append((src_name, tgt_name, new_role))
                    rel_key_count[k] = count + 1

            try:
                cmd_obj = command_adapter.validate_python(raw)
                validated_commands.append(cmd_obj)
            except Exception as e:
                logger.error(f"Failed to validate command {raw}: {e}")
                return [], [], f"Comando inválido ({cmd_type}): {str(e)}"

        return validated_commands, auto_resolved_roles, None

    def _build_natural_reply(
        self,
        executed_commands: List[UmlCommand],
        auto_roles: Optional[List[Tuple[str, str, str]]] = None,
    ) -> str:
        if not executed_commands:
            return "No se ejecutó ningún comando."

        actions: List[str] = []
        for cmd in executed_commands:
            if cmd.command_type == CommandTypeEnum.CREATE_CLASS:
                actions.append(f"Se creó la clase '{cmd.name}'")
            elif cmd.command_type == CommandTypeEnum.RENAME_CLASS:
                actions.append(f"Se renombró la clase a '{cmd.new_name}'")
            elif cmd.command_type == CommandTypeEnum.DELETE_CLASS:
                actions.append("Se eliminó una clase")
            elif cmd.command_type == CommandTypeEnum.ADD_ATTRIBUTE:
                pk_txt = " (PK)" if cmd.primary_key else ""
                actions.append(f"Se agregó el atributo '{cmd.name}' ({cmd.type}{pk_txt})")
            elif cmd.command_type == CommandTypeEnum.DELETE_ATTRIBUTE:
                actions.append("Se eliminó un atributo")
            elif cmd.command_type == CommandTypeEnum.CREATE_RELATIONSHIP:
                role_info = f" [rol: {cmd.target_role}]" if cmd.target_role else ""
                actions.append(f"Se creó una relación {cmd.type.value}{role_info} ({cmd.source_cardinality} : {cmd.target_cardinality})")
            elif cmd.command_type == CommandTypeEnum.DELETE_RELATIONSHIP:
                actions.append("Se eliminó una relación")

        summary = "; ".join(actions) + "."
        if auto_roles:
            notes = [f"{s} ↔ {t} ('{r}')" for s, t, r in auto_roles]
            summary += f"\n\n💡 **Roles asignados:** Detecté relaciones múltiples entre las mismas tablas sin rol en el boceto. Asigné nombres de rol para la base de datos: {', '.join(notes)}. Podés renombrarlos con doble clic en cada flecha."
        return f"¡Listo! {summary}"


# Global singleton instance
ai_uml_interpreter = AiUmlInterpreter()

