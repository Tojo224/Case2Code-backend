import logging
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
        self, prompt: str, document: CanonicalUmlDocument
    ) -> Tuple[CanonicalUmlDocument, List[UmlCommand], str]:
        """Translates natural language prompt to commands and executes them on the document.
        Returns:
            (updated_document, executed_commands, assistant_reply)
        """
        raw_commands = await self._generate_raw_commands(prompt, document)
        if not raw_commands:
            return (
                document,
                [],
                "No pude identificar cambios específicos para el diagrama. "
                "Puedes pedirme cosas como:\n"
                "- 'Crea la clase Cliente con atributos id de tipo Long y nombre de tipo String'\n"
                "- 'Agrega el atributo telefono a Cliente'\n"
                "- 'Relaciona Cliente con Reserva de uno a muchos'\n"
                "- 'Elimina la clase Factura'",
            )

        resolved_commands, validation_error = self._resolve_and_validate_commands(
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

        reply = self._build_natural_reply(executed_commands)
        return current_doc, executed_commands, reply

    async def _generate_raw_commands(
        self, prompt: str, document: CanonicalUmlDocument
    ) -> List[Dict[str, Any]]:
        # First try primary provider (Gemini if key is present)
        if self.primary_provider != self.rule_based_provider:
            try:
                raw_cmds = await self.primary_provider.generate_commands(prompt, document)
                if raw_cmds:
                    return raw_cmds
            except Exception as e:
                logger.warning(f"Primary AI provider failed, falling back to rule-based: {e}")

        # Fallback to deterministic rule-based
        return await self.rule_based_provider.generate_commands(prompt, document)

    def _resolve_and_validate_commands(
        self, raw_commands: List[Dict[str, Any]], initial_doc: CanonicalUmlDocument
    ) -> Tuple[List[UmlCommand], Optional[str]]:
        """Maps class/attribute names to IDs and validates types against Pydantic models."""
        # Build lookup maps
        name_to_class_id: Dict[str, str] = {c.name.lower(): c.id for c in initial_doc.classes}
        id_to_class: Dict[str, Any] = {c.id: c for c in initial_doc.classes}

        validated_commands: List[UmlCommand] = []

        for raw in raw_commands:
            cmd_type = raw.get("command_type")
            if not cmd_type:
                continue

            # If CREATE_CLASS, ensure class_id is generated and record in name lookup
            if cmd_type == CommandTypeEnum.CREATE_CLASS.value:
                if not raw.get("class_id"):
                    raw["class_id"] = f"cls-{uuid.uuid4().hex[:8]}"
                class_name = raw.get("name", "")
                if class_name:
                    name_to_class_id[class_name.lower()] = raw["class_id"]

            # If references class_id by class name, resolve to real ID
            if "class_id" in raw and raw["class_id"]:
                class_ref = str(raw["class_id"])
                if class_ref.lower() in name_to_class_id:
                    raw["class_id"] = name_to_class_id[class_ref.lower()]

            # If references source_class_id or target_class_id by name, resolve
            if "source_class_id" in raw and raw["source_class_id"]:
                src_ref = str(raw["source_class_id"])
                if src_ref.lower() in name_to_class_id:
                    raw["source_class_id"] = name_to_class_id[src_ref.lower()]

            if "target_class_id" in raw and raw["target_class_id"]:
                tgt_ref = str(raw["target_class_id"])
                if tgt_ref.lower() in name_to_class_id:
                    raw["target_class_id"] = name_to_class_id[tgt_ref.lower()]

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

            try:
                cmd_obj = command_adapter.validate_python(raw)
                validated_commands.append(cmd_obj)
            except Exception as e:
                logger.error(f"Failed to validate command {raw}: {e}")
                return [], f"Comando inválido ({cmd_type}): {str(e)}"

        return validated_commands, None

    def _build_natural_reply(self, executed_commands: List[UmlCommand]) -> str:
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
                actions.append(f"Se creó una relación {cmd.type.value} ({cmd.source_cardinality} : {cmd.target_cardinality})")
            elif cmd.command_type == CommandTypeEnum.DELETE_RELATIONSHIP:
                actions.append("Se eliminó una relación")

        summary = "; ".join(actions) + "."
        return f"¡Listo! {summary}"


# Global singleton instance
ai_uml_interpreter = AiUmlInterpreter()

