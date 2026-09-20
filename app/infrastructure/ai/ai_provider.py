import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

from app.domain.models.canonical_uml import CanonicalUmlDocument, RelationshipTypeEnum
from app.domain.models.commands import CommandTypeEnum

logger = logging.getLogger(__name__)


class AiProviderPort(ABC):
    """Port for AI / LLM providers converting natural language to UML commands."""

    @abstractmethod
    async def generate_commands(
        self, prompt: str, current_uml: CanonicalUmlDocument
    ) -> List[Dict[str, Any]]:
        """Takes a user prompt and current diagram state, returns a list of raw command dicts."""
        pass


class GeminiAiProvider(AiProviderPort):
    """AI provider backed by Google Gemini API via REST HTTP."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model
        self.endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        )

    def _build_system_prompt(self, current_uml: CanonicalUmlDocument) -> str:
        classes_summary = [
            {
                "id": c.id,
                "name": c.name,
                "attributes": [{"id": a.id, "name": a.name, "type": a.type, "pk": a.primary_key} for a in c.attributes],
            }
            for c in current_uml.classes
        ]
        relationships_summary = [
            {
                "id": r.id,
                "type": r.type.value,
                "source_class_id": r.source_class_id,
                "target_class_id": r.target_class_id,
                "source_cardinality": r.source_cardinality,
                "target_cardinality": r.target_cardinality,
            }
            for r in current_uml.relationships
        ]

        return f"""You are an expert Software Architect assisting with CASE UML class diagrams.
Translate the user's natural language instruction into a JSON array of strongly-typed UML commands.

Current diagram state:
- Existing Classes: {json.dumps(classes_summary)}
- Existing Relationships: {json.dumps(relationships_summary)}

Allowed Command Types and schemas:
1. CREATE_CLASS:
   {{"command_type": "CREATE_CLASS", "class_id": "cls-uuid", "name": "ClassName", "position": {{"x": 100, "y": 100}}}}
2. RENAME_CLASS:
   {{"command_type": "RENAME_CLASS", "class_id": "cls-id-or-name", "new_name": "NewName"}}
3. DELETE_CLASS:
   {{"command_type": "DELETE_CLASS", "class_id": "cls-id-or-name"}}
4. ADD_ATTRIBUTE:
   {{"command_type": "ADD_ATTRIBUTE", "class_id": "cls-id-or-name", "name": "attributeName", "type": "String|Long|Integer|Double|Boolean|LocalDate|LocalDateTime", "primary_key": false, "nullable": true}}
5. UPDATE_ATTRIBUTE:
   {{"command_type": "UPDATE_ATTRIBUTE", "class_id": "cls-id-or-name", "attribute_id": "attr-id-or-name", "name": "newName", "type": "String"}}
6. DELETE_ATTRIBUTE:
   {{"command_type": "DELETE_ATTRIBUTE", "class_id": "cls-id-or-name", "attribute_id": "attr-id-or-name"}}
7. CREATE_RELATIONSHIP:
   {{"command_type": "CREATE_RELATIONSHIP", "relationship_id": "rel-uuid", "type": "ONE_TO_MANY|MANY_TO_ONE|ONE_TO_ONE|MANY_TO_MANY", "source_class_id": "cls-source-id-or-name", "target_class_id": "cls-target-id-or-name", "source_cardinality": "1", "target_cardinality": "*"}}
8. DELETE_RELATIONSHIP:
   {{"command_type": "DELETE_RELATIONSHIP", "relationship_id": "rel-id"}}

STRICT RULES:
- Output MUST be a valid JSON array of objects only.
- Do not output markdown codeblocks, explanations, or any extra text.
- If referencing an existing class or attribute, prefer its exact id if known, or its exact name.
"""

    async def generate_commands(
        self, prompt: str, current_uml: CanonicalUmlDocument
    ) -> List[Dict[str, Any]]:
        system_instruction = self._build_system_prompt(current_uml)
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_instruction}\n\nUser request: {prompt}"}],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

        text_content = ""
        candidates = data.get("candidates", [])
        if candidates and "content" in candidates[0]:
            parts = candidates[0]["content"].get("parts", [])
            if parts:
                text_content = parts[0].get("text", "").strip()

        # Clean any wrapping markdown if present
        if text_content.startswith("```"):
            text_content = re.sub(r"^```(?:json)?\n?", "", text_content)
            text_content = re.sub(r"\n?```$", "", text_content).strip()

        parsed = json.loads(text_content)
        if isinstance(parsed, dict):
            # In case the model wrapped it in an object like {"commands": [...]}
            if "commands" in parsed and isinstance(parsed["commands"], list):
                return parsed["commands"]
            return [parsed]
        elif isinstance(parsed, list):
            return parsed
        return []


class RuleBasedAiProvider(AiProviderPort):
    """Deterministic pattern-matching AI provider supporting Spanish and English commands.
    Ensures offline, robust, reproducible execution during exams and unit testing.
    """

    async def generate_commands(
        self, prompt: str, current_uml: CanonicalUmlDocument
    ) -> List[Dict[str, Any]]:
        commands: List[Dict[str, Any]] = []
        normalized_prompt = prompt.strip()

        # 1. CREATE CLASS (with optional attributes: "Crea la clase Cliente con atributos id de tipo Long, nombre de tipo String")
        # Match class creation
        create_class_match = re.search(
            r"(?:crea(?:r)?|nueva|agregar?|add|create|new)\s+(?:la\s+|una\s+)?clase\s+(?:llamada\s+)?([A-Za-z0-9_]+)(?:\s+(?:con\s+atributos?|con\s+campos?|with\s+attributes?)\s+(.*))?",
            normalized_prompt,
            re.IGNORECASE,
        )
        if create_class_match:
            class_name = create_class_match.group(1).strip()
            class_name = class_name[0].upper() + class_name[1:] if class_name else "Entity"
            class_id = f"cls-{uuid.uuid4().hex[:8]}"

            # Calculate staggered position
            existing_count = len(current_uml.classes)
            col = existing_count % 3
            row = existing_count // 3
            pos_x = 60 + col * 280
            pos_y = 60 + row * 240

            commands.append({
                "command_type": CommandTypeEnum.CREATE_CLASS.value,
                "class_id": class_id,
                "name": class_name,
                "position": {"x": float(pos_x), "y": float(pos_y)},
            })

            # Check if attributes are listed in the sentence
            attrs_part = create_class_match.group(2)
            if attrs_part:
                # Split by commas, "y", "and"
                attr_items = re.split(r",|\s+y\s+|\s+and\s+", attrs_part)
                for item in attr_items:
                    item = item.strip()
                    if not item:
                        continue
                    # Match name and optional type (e.g. "id de tipo Long", "nombre String", "precio: Double")
                    attr_match = re.search(
                        r"([A-Za-z0-9_]+)(?:\s+de\s+tipo|\s*:\s*|\s+tipo\s+|\s+type\s+|\s+)?\s*([A-Za-z0-9_]+)?",
                        item,
                        re.IGNORECASE,
                    )
                    if attr_match:
                        attr_name = attr_match.group(1).strip()
                        raw_type = attr_match.group(2)
                        attr_type = self._normalize_type(raw_type) if raw_type else "String"
                        is_pk = bool(re.search(r"\b(id|pk|primary|clave|llave)\b", attr_name, re.IGNORECASE))
                        if is_pk and attr_type == "String" and attr_name.lower() == "id":
                            attr_type = "Long"

                        # Since handle_create_class creates default id (PK), avoid duplicate
                        if attr_name.lower() == "id":
                            continue

                        commands.append({
                            "command_type": CommandTypeEnum.ADD_ATTRIBUTE.value,
                            "class_id": class_id,
                            "name": attr_name,
                            "type": attr_type,
                            "primary_key": is_pk,
                            "nullable": not is_pk,
                        })

            return commands

        # 2. ADD ATTRIBUTE TO EXISTING CLASS:
        # e.g. "Agrega el atributo precio de tipo Double a la clase Producto"
        add_attr_match = re.search(
            r"(?:agrega(?:r)?|a[ñn]adir|crea(?:r)?|add)\s+(?:el\s+|un\s+)?atributo\s+([A-Za-z0-9_]+)(?:\s+de\s+tipo|\s*:\s*|\s+tipo\s+|\s+type\s+)?\s*([A-Za-z0-9_]+)?\s+(?:a\s+(?:la\s+clase\s+)?|en\s+(?:la\s+clase\s+)?|to\s+(?:class\s+)?)([A-Za-z0-9_]+)",
            normalized_prompt,
            re.IGNORECASE,
        )
        if add_attr_match:
            attr_name = add_attr_match.group(1).strip()
            raw_type = add_attr_match.group(2)
            class_name = add_attr_match.group(3).strip()
            attr_type = self._normalize_type(raw_type) if raw_type else "String"
            is_pk = bool(re.search(r"\b(id|pk|primary|clave|llave)\b", attr_name, re.IGNORECASE))

            target_class = self._find_class_by_name(current_uml, class_name)
            class_ref = target_class.id if target_class else class_name

            commands.append({
                "command_type": CommandTypeEnum.ADD_ATTRIBUTE.value,
                "class_id": class_ref,
                "name": attr_name,
                "type": attr_type,
                "primary_key": is_pk,
                "nullable": not is_pk,
            })
            return commands

        # 3. RENAME CLASS:
        # e.g. "Renombra la clase Producto a Articulo"
        rename_class_match = re.search(
            r"(?:renombra(?:r)?|cambia(?:r)?\s+el\s+nombre\s+de|rename)\s+(?:la\s+clase\s+)?([A-Za-z0-9_]+)\s+(?:a|por|to)\s+([A-Za-z0-9_]+)",
            normalized_prompt,
            re.IGNORECASE,
        )
        if rename_class_match:
            old_name = rename_class_match.group(1).strip()
            new_name = rename_class_match.group(2).strip()
            new_name = new_name[0].upper() + new_name[1:] if new_name else new_name

            target_class = self._find_class_by_name(current_uml, old_name)
            class_ref = target_class.id if target_class else old_name

            commands.append({
                "command_type": CommandTypeEnum.RENAME_CLASS.value,
                "class_id": class_ref,
                "new_name": new_name,
            })
            return commands

        # 4. DELETE RELATIONSHIP:
        # e.g. "Elimina la relación entre Cliente y Reserva"
        del_rel_match = re.search(
            r"(?:elimina(?:r)?|borra(?:r)?|quita(?:r)?|delete|remove)\s+(?:la\s+)?relaci[oó]n\s+(?:entre\s+)?([A-Za-z0-9_]+)\s+(?:con|y|and)\s+([A-Za-z0-9_]+)",
            normalized_prompt,
            re.IGNORECASE,
        )
        if del_rel_match:
            src_name = del_rel_match.group(1).strip()
            tgt_name = del_rel_match.group(2).strip()

            src_cls = self._find_class_by_name(current_uml, src_name)
            tgt_cls = self._find_class_by_name(current_uml, tgt_name)

            if src_cls and tgt_cls:
                for rel in current_uml.relationships:
                    if (
                        (rel.source_class_id == src_cls.id and rel.target_class_id == tgt_cls.id)
                        or (rel.source_class_id == tgt_cls.id and rel.target_class_id == src_cls.id)
                    ):
                        commands.append({
                            "command_type": CommandTypeEnum.DELETE_RELATIONSHIP.value,
                            "relationship_id": rel.id,
                        })
                        return commands

        # 5. DELETE ATTRIBUTE:
        # e.g. "Elimina el atributo telefono de la clase Cliente"
        del_attr_match = re.search(
            r"(?:elimina(?:r)?|borra(?:r)?|quita(?:r)?|delete|remove)\s+(?:el\s+atributo\s+)?([A-Za-z0-9_]+)\s+(?:de\s+(?:la\s+clase\s+)?|from\s+(?:class\s+)?)([A-Za-z0-9_]+)",
            normalized_prompt,
            re.IGNORECASE,
        )
        if del_attr_match:
            attr_name = del_attr_match.group(1).strip()
            class_name = del_attr_match.group(2).strip()

            target_class = self._find_class_by_name(current_uml, class_name)
            class_ref = target_class.id if target_class else class_name

            attr_ref = attr_name
            if target_class:
                for a in target_class.attributes:
                    if a.name.lower() == attr_name.lower():
                        attr_ref = a.id
                        break

            commands.append({
                "command_type": CommandTypeEnum.DELETE_ATTRIBUTE.value,
                "class_id": class_ref,
                "attribute_id": attr_ref,
            })
            return commands

        # 6. DELETE CLASS:
        # e.g. "Elimina la clase Factura"
        del_class_match = re.search(
            r"(?:elimina(?:r)?|borra(?:r)?|quita(?:r)?|delete|remove)\s+(?:la\s+)?clase\s+([A-Za-z0-9_]+)",
            normalized_prompt,
            re.IGNORECASE,
        )
        if del_class_match:
            class_name = del_class_match.group(1).strip()
            target_class = self._find_class_by_name(current_uml, class_name)
            class_ref = target_class.id if target_class else class_name

            commands.append({
                "command_type": CommandTypeEnum.DELETE_CLASS.value,
                "class_id": class_ref,
            })
            return commands

        # 7. CREATE RELATIONSHIP:
        # e.g. "Relaciona Cliente con Reserva de uno a muchos" / "Relaciona Pedido con Cliente 1 a N"
        rel_match = re.search(
            r"(?:relaciona(?:r)?|crea(?:r)?\s+(?:una\s+)?relaci[oó]n\s+(?:entre\s+)?|conecta(?:r)?|relate|connect)\s+([A-Za-z0-9_]+)\s+(?:con|y|to|and)\s+([A-Za-z0-9_]+)(?:\s+(?:de\s+)?(uno\s+a\s+muchos|muchos\s+a\s+uno|uno\s+a\s+uno|muchos\s+a\s+muchos|1\s*:\s*[Nn*]|1\s+a\s+[Nn*]|[Nn*]\s*:\s*1|[Nn*]\s+a\s+1|1\s*:\s*1|1\s+a\s+1|[Nn*]\s*:\s*[Nn*]|[Nn*]\s+a\s+[Nn*]|one\s+to\s+many|many\s+to\s+one|one\s+to\s+one|many\s+to\s+many))?",
            normalized_prompt,
            re.IGNORECASE,
        )
        if rel_match:
            src_name = rel_match.group(1).strip()
            tgt_name = rel_match.group(2).strip()
            rel_phrase = (rel_match.group(3) or "").strip().lower()

            rel_type = RelationshipTypeEnum.ONE_TO_MANY
            src_card = "1"
            tgt_card = "*"

            if re.search(r"muchos\s+a\s+uno|many\s+to\s+one|[n*]\s*:\s*1|[n*]\s+a\s+1", rel_phrase):
                rel_type = RelationshipTypeEnum.MANY_TO_ONE
                src_card = "*"
                tgt_card = "1"
            elif re.search(r"uno\s+a\s+uno|one\s+to\s+one|1\s*:\s*1|1\s+a\s+1", rel_phrase):
                rel_type = RelationshipTypeEnum.ONE_TO_ONE
                src_card = "1"
                tgt_card = "1"
            elif re.search(r"muchos\s+a\s+muchos|many\s+to\s+many|[n*]\s*:\s*[n*]|[n*]\s+a\s+[n*]", rel_phrase):
                rel_type = RelationshipTypeEnum.MANY_TO_MANY
                src_card = "*"
                tgt_card = "*"

            src_cls = self._find_class_by_name(current_uml, src_name)
            tgt_cls = self._find_class_by_name(current_uml, tgt_name)

            src_ref = src_cls.id if src_cls else src_name
            tgt_ref = tgt_cls.id if tgt_cls else tgt_name

            commands.append({
                "command_type": CommandTypeEnum.CREATE_RELATIONSHIP.value,
                "relationship_id": f"rel-{uuid.uuid4().hex[:8]}",
                "type": rel_type.value,
                "source_class_id": src_ref,
                "target_class_id": tgt_ref,
                "source_cardinality": src_card,
                "target_cardinality": tgt_card,
            })
            return commands

        return commands

    def _find_class_by_name(self, current_uml: CanonicalUmlDocument, name: str):
        for c in current_uml.classes:
            if c.name.lower() == name.lower():
                return c
        return None

    def _normalize_type(self, raw_type: Optional[str]) -> str:
        if not raw_type:
            return "String"
        t = raw_type.strip().lower()
        if t in ["string", "str", "texto", "varchar"]:
            return "String"
        if t in ["long", "bigint"]:
            return "Long"
        if t in ["int", "integer", "entero", "numero", "número"]:
            return "Integer"
        if t in ["double", "float", "decimal", "precio", "monto", "real"]:
            return "Double"
        if t in ["boolean", "bool", "booleano"]:
            return "Boolean"
        if t in ["date", "fecha", "localdate"]:
            return "LocalDate"
        if t in ["datetime", "fechahora", "localdatetime", "timestamp"]:
            return "LocalDateTime"
        return raw_type.capitalize()

