import asyncio
import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

from app.domain.models.canonical_uml import CanonicalUmlDocument, RelationshipTypeEnum
from app.domain.models.commands import CommandTypeEnum

logger = logging.getLogger(__name__)


class DatabaseSchemaParser:
    """Parses SQL DDL, relational schema notations, and multi-entity table lists into UML commands."""

    @classmethod
    def parse(cls, text: str, current_uml: CanonicalUmlDocument) -> List[Dict[str, Any]]:
        # 1. Check for SQL DDL (CREATE TABLE)
        if re.search(r"CREATE\s+TABLE", text, re.IGNORECASE):
            sql_cmds = cls._parse_sql_ddl(text, current_uml)
            if sql_cmds:
                return sql_cmds

        # 2. Check for relational schema notation: TableName (col1, col2, ...)
        schema_cmds = cls._parse_schema_notation(text, current_uml)
        if schema_cmds:
            return schema_cmds

        # 3. Check for list of tables: "Tablas: A, B, C" or "Base de datos de ...: A, B, C"
        table_list_cmds = cls._parse_table_list(text, current_uml)
        if table_list_cmds:
            return table_list_cmds

        return []

    @classmethod
    def _find_create_tables(cls, text: str) -> List[Tuple[str, str]]:
        """Extracts (table_name, body) from CREATE TABLE statements using parenthetical balance."""
        results = []
        pattern = re.compile(
            r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+[`\"']?([A-Za-z0-9_]+)[`\"']?\s*\(",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            table_name = match.group(1).strip()
            start_pos = match.end()
            depth = 1
            idx = start_pos
            while idx < len(text) and depth > 0:
                char = text[idx]
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                idx += 1
            if depth == 0:
                body = text[start_pos : idx - 1]
                results.append((table_name, body))
        return results

    @classmethod
    def _split_sql_columns(cls, body: str) -> List[str]:
        items = []
        current = []
        depth = 0
        in_quote = False
        quote_char = None
        for char in body:
            if char in ("'", '"', "`") and not in_quote:
                in_quote = True
                quote_char = char
                current.append(char)
            elif char == quote_char and in_quote:
                in_quote = False
                quote_char = None
                current.append(char)
            elif not in_quote:
                if char == "(":
                    depth += 1
                    current.append(char)
                elif char == ")":
                    depth = max(0, depth - 1)
                    current.append(char)
                elif char == "," and depth == 0:
                    items.append("".join(current).strip())
                    current = []
                else:
                    current.append(char)
            else:
                current.append(char)
        if current:
            items.append("".join(current).strip())
        return [it for it in items if it]

    @classmethod
    def _sql_type_to_uml(cls, sql_type: str, col_name: str = "") -> str:
        t = sql_type.strip().lower()
        if re.match(r"^(bigint|serial|bigserial)", t) or (not t and col_name.lower() == "id"):
            return "Long"
        if re.match(r"^(int|integer|smallint|tinyint)", t):
            return "Integer"
        if re.match(r"^(varchar|char|text|clob|string)", t):
            return "String"
        if re.match(r"^(decimal|numeric|double|float|real)", t) or "precio" in col_name.lower() or "total" in col_name.lower():
            return "Double"
        if re.match(r"^(bool|boolean|bit)", t):
            return "Boolean"
        if re.match(r"^(date)", t) and "time" not in t:
            return "LocalDate"
        if re.match(r"^(timestamp|datetime|timestamptz)", t):
            return "LocalDateTime"
        if not t:
            if col_name.lower() in ("fecha", "date"):
                return "LocalDate"
            if col_name.lower() in ("activo", "habilitado", "enabled", "active"):
                return "Boolean"
            return "String"
        return "String"

    @classmethod
    def _parse_sql_ddl(cls, sql_text: str, current_uml: CanonicalUmlDocument) -> List[Dict[str, Any]]:
        tables = cls._find_create_tables(sql_text)
        if not tables:
            return []

        commands: List[Dict[str, Any]] = []
        created_tables: Dict[str, str] = {}
        pending_fks: List[Dict[str, str]] = []

        existing_count = len(current_uml.classes)
        for i, (raw_table_name, body) in enumerate(tables):
            class_name = raw_table_name[0].upper() + raw_table_name[1:]
            class_id = f"cls-{uuid.uuid4().hex[:8]}"
            created_tables[raw_table_name.lower()] = class_id
            created_tables[class_name.lower()] = class_id

            col_idx = (existing_count + i) % 3
            row_idx = (existing_count + i) // 3
            pos_x = 80 + col_idx * 280
            pos_y = 80 + row_idx * 260

            commands.append({
                "command_type": CommandTypeEnum.CREATE_CLASS.value,
                "class_id": class_id,
                "name": class_name,
                "position": {"x": float(pos_x), "y": float(pos_y)},
            })

            lines = cls._split_sql_columns(body)
            table_pks = set()

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                pk_table_match = re.search(r"PRIMARY\s+KEY\s*\((.*?)\)", line, re.IGNORECASE)
                if pk_table_match:
                    for pk_col in pk_table_match.group(1).split(","):
                        table_pks.add(pk_col.strip().strip("`\"'").lower())
                    continue

                fk_match = re.search(
                    r"FOREIGN\s+KEY\s*\((.*?)\)\s+REFERENCES\s+[`\"']?([A-Za-z0-9_]+)[`\"']?\s*(?:\((.*?)\))?",
                    line,
                    re.IGNORECASE,
                )
                if fk_match:
                    fk_col = fk_match.group(1).strip().strip("`\"'")
                    ref_table = fk_match.group(2).strip().strip("`\"'")
                    pending_fks.append({
                        "source_class_id": class_id,
                        "target_table": ref_table.lower(),
                        "col": fk_col,
                    })
                    continue

                col_match = re.match(r"^[`\"']?([A-Za-z0-9_]+)[`\"']?\s+([A-Za-z0-9_()]+)(.*)", line, re.IGNORECASE)
                if col_match:
                    col_name = col_match.group(1).strip()
                    sql_type = col_match.group(2).strip()
                    rest = col_match.group(3) or ""

                    if col_name.upper() in ("CONSTRAINT", "KEY", "INDEX", "UNIQUE", "CHECK"):
                        continue

                    is_pk = bool(re.search(r"\bPRIMARY\s+KEY\b", rest, re.IGNORECASE)) or (col_name.lower() in table_pks)
                    nullable = not bool(re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE))
                    if is_pk:
                        nullable = False

                    inline_ref = re.search(r"REFERENCES\s+[`\"']?([A-Za-z0-9_]+)[`\"']?", rest, re.IGNORECASE)
                    if inline_ref:
                        pending_fks.append({
                            "source_class_id": class_id,
                            "target_table": inline_ref.group(1).strip().lower(),
                            "col": col_name,
                        })

                    if col_name.lower() == "id":
                        continue

                    attr_type = cls._sql_type_to_uml(sql_type, col_name)

                    commands.append({
                        "command_type": CommandTypeEnum.ADD_ATTRIBUTE.value,
                        "class_id": class_id,
                        "name": col_name,
                        "type": attr_type,
                        "primary_key": is_pk,
                        "nullable": nullable,
                    })

        for c in current_uml.classes:
            if c.name.lower() not in created_tables:
                created_tables[c.name.lower()] = c.id

        for fk in pending_fks:
            target_id = created_tables.get(fk["target_table"])
            if target_id and target_id != fk["source_class_id"]:
                commands.append({
                    "command_type": CommandTypeEnum.CREATE_RELATIONSHIP.value,
                    "relationship_id": f"rel-{uuid.uuid4().hex[:8]}",
                    "type": RelationshipTypeEnum.MANY_TO_ONE.value,
                    "source_class_id": fk["source_class_id"],
                    "target_class_id": target_id,
                    "source_cardinality": "*",
                    "target_cardinality": "1",
                })

        return commands

    @classmethod
    def _parse_schema_notation(cls, text: str, current_uml: CanonicalUmlDocument) -> List[Dict[str, Any]]:
        pattern = re.compile(
            r"(?:^|[\n\r]+)\s*(?:[-*•]\s*)?(?:tabla|table|clase|class|entidad)?\s*([A-Za-z0-9_]+)\s*(?:\(([^)]+)\)|:\s*([^\n\r]+))",
            re.IGNORECASE,
        )
        matches = list(pattern.finditer(text))
        if not matches:
            return []

        if len(matches) == 1 and re.search(r"\b(crea|agrega|elimina|relaciona|renombra)\b", text, re.IGNORECASE):
            return []

        commands: List[Dict[str, Any]] = []
        created_tables: Dict[str, str] = {}
        pending_fk_checks: List[Tuple[str, str]] = []

        existing_count = len(current_uml.classes)
        for i, m in enumerate(matches):
            raw_name = m.group(1).strip()
            cols_str = (m.group(2) or m.group(3) or "").strip()
            if not cols_str:
                continue

            class_name = raw_name[0].upper() + raw_name[1:]
            class_id = f"cls-{uuid.uuid4().hex[:8]}"
            created_tables[class_name.lower()] = class_id

            col_idx = (existing_count + i) % 3
            row_idx = (existing_count + i) // 3
            pos_x = 80 + col_idx * 280
            pos_y = 80 + row_idx * 260

            commands.append({
                "command_type": CommandTypeEnum.CREATE_CLASS.value,
                "class_id": class_id,
                "name": class_name,
                "position": {"x": float(pos_x), "y": float(pos_y)},
            })

            col_tokens = re.split(r"[,;]", cols_str)
            for token in col_tokens:
                token = token.strip()
                if not token:
                    continue

                is_pk = bool(re.search(r"\b(pk|primary|id)\b", token, re.IGNORECASE))
                clean_token = re.sub(r"\(.*?\)", "", token).strip()

                parts = clean_token.split()
                attr_name = parts[0]
                raw_type = parts[1] if len(parts) > 1 and parts[1].upper() not in ("PK", "FK") else None

                if attr_name.lower() == "id":
                    continue

                attr_type = cls._sql_type_to_uml(raw_type or "", attr_name)

                commands.append({
                    "command_type": CommandTypeEnum.ADD_ATTRIBUTE.value,
                    "class_id": class_id,
                    "name": attr_name,
                    "type": attr_type,
                    "primary_key": is_pk,
                    "nullable": not is_pk,
                })

                if re.search(r"(_id|id_)", attr_name, re.IGNORECASE) or "fk" in token.lower():
                    pending_fk_checks.append((class_id, attr_name))

        for c in current_uml.classes:
            if c.name.lower() not in created_tables:
                created_tables[c.name.lower()] = c.id

        for src_class_id, attr_name in pending_fk_checks:
            cand = re.sub(r"(_id|id_)", "", attr_name, flags=re.IGNORECASE).lower()
            if cand in created_tables and created_tables[cand] != src_class_id:
                tgt_class_id = created_tables[cand]
                commands.append({
                    "command_type": CommandTypeEnum.CREATE_RELATIONSHIP.value,
                    "relationship_id": f"rel-{uuid.uuid4().hex[:8]}",
                    "type": RelationshipTypeEnum.MANY_TO_ONE.value,
                    "source_class_id": src_class_id,
                    "target_class_id": tgt_class_id,
                    "source_cardinality": "*",
                    "target_cardinality": "1",
                })

        return commands

    @classmethod
    def _parse_table_list(cls, text: str, current_uml: CanonicalUmlDocument) -> List[Dict[str, Any]]:
        match = re.search(
            r"(?:tablas|tables|entidades|modelo\s+de\s+datos|base\s+de\s+datos(?:\s+de\s+[A-Za-z0-9_]+)?)\s*(?:son|:|\bcon\b)?\s*([A-Za-z0-9_,\s]+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return []

        raw_list = match.group(1).strip()
        names = [n.strip() for n in re.split(r"[,yY]\s+|\s+and\s+", raw_list) if n.strip()]
        if len(names) < 2:
            return []

        commands: List[Dict[str, Any]] = []
        existing_count = len(current_uml.classes)
        for i, name in enumerate(names):
            class_name = name[0].upper() + name[1:]
            class_id = f"cls-{uuid.uuid4().hex[:8]}"
            col_idx = (existing_count + i) % 3
            row_idx = (existing_count + i) // 3
            pos_x = 80 + col_idx * 280
            pos_y = 80 + row_idx * 260

            commands.append({
                "command_type": CommandTypeEnum.CREATE_CLASS.value,
                "class_id": class_id,
                "name": class_name,
                "position": {"x": float(pos_x), "y": float(pos_y)},
            })
        return commands


class AiProviderPort(ABC):
    """Port for AI / LLM providers converting natural language and diagrams to UML commands."""

    @abstractmethod
    async def generate_commands(
        self,
        prompt: str,
        current_uml: CanonicalUmlDocument,
        image_data: Optional[Dict[str, str]] = None,
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """Takes a user prompt, current diagram state and optional image data, returns raw command dicts or envelope."""
        pass


class GeminiAiProvider(AiProviderPort):
    """AI provider backed by Google Gemini API via REST HTTP."""

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite"):
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

        return f"""You are an expert Software Architect assisting with CASE UML and Relational Database diagrams.
Your mission is to understand the user's intention and translate natural language instructions, database schemas, SQL DDL, or conceptual database design images into strongly-typed UML commands.

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
   {{"command_type": "CREATE_RELATIONSHIP", "relationship_id": "rel-uuid", "type": "ONE_TO_MANY|MANY_TO_ONE|ONE_TO_ONE|MANY_TO_MANY|INHERITANCE|AGGREGATION|COMPOSITION|REALIZATION|DEPENDENCY", "source_class_id": "cls-source-id-or-name", "target_class_id": "cls-target-id-or-name", "source_cardinality": "1", "target_cardinality": "*", "source_role": "rol_origen", "target_role": "rol_destino"}}
8. DELETE_RELATIONSHIP:
   {{"command_type": "DELETE_RELATIONSHIP", "relationship_id": "rel-id"}}

INTENTION RECOGNITION & REPLICATION (CRITICAL):
- When an image is provided (hand-drawn sketch, whiteboard, ER diagram, database screenshot, relational schema):
  1. The user's intention is ALWAYS to extract and REPLICATE the entire database/diagram into UML classes, attributes, and relationships. Do not wait for the word 'crea'.
  2. Extract every entity box as a CREATE_CLASS with logical (x, y) coordinates preserving the visual layout (e.g. spread across x: 80..800, y: 80..600).
  3. Class names MUST be valid alphanumeric PascalCase identifiers without spaces or accents (e.g., 'ProductoEspecial', 'OrdenDeTrabajo', 'PlantillaFabricacion', 'Catalogo').
  4. For every attribute listed inside the box, generate an ADD_ATTRIBUTE with appropriate Java/SQL types (Long for IDs, String for names/text, LocalDate for dates, Double for amounts, Boolean for flags). Attribute names MUST be camelCase or snake_case without spaces (e.g., 'fechaCreacion', 'precioUnitario'). Mark primary_key: true if it has PK, #, underline, or is 'id'.
  5. Replicate all relationship lines between entities with their cardinalities.
  6. Multi-relationships between same pair of tables: assign distinct semantic target_roles (e.g. 'origen', 'destino' or 'rol_1', 'rol_2').

CLARIFICATION RULE ("SI NO SABES, PREGUNTA"):
- If the image or text is completely ambiguous, blurry, cut off, or key information is missing to make a correct decision:
  Include a "question" field in your JSON output in Spanish explaining what you observed and asking specifically what needs to be clarified.
  Example: "No logro distinguir con claridad el nombre de dos de las tablas en la parte inferior del boceto. ¿Podrías indicarme qué entidades representan y cómo se relacionan con Cliente?"
- If some tables are clear but one part is ambiguous, still generate the commands for the clear tables and add the "question" for the ambiguous part.

OUTPUT FORMAT:
Return a JSON object with:
{{
  "commands": [ ...array of UML command objects... ],
  "question": "Clarification question in Spanish if something was unclear or ambiguous, otherwise null"
}}
Or alternatively, a direct JSON array of UML commands: [ ... ].
Do not output markdown codeblocks, explanations outside JSON, or any extra text.
"""

    async def generate_commands(
        self,
        prompt: str,
        current_uml: CanonicalUmlDocument,
        image_data: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        system_instruction = self._build_system_prompt(current_uml)
        parts: List[Dict[str, Any]] = [
            {"text": f"{system_instruction}\n\nUser request: {prompt}"}
        ]
        if image_data and image_data.get("data"):
            parts.append({
                "inline_data": {
                    "mime_type": image_data.get("mime_type", "image/png"),
                    "data": image_data["data"],
                }
            })

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        data = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    response = await client.post(self.endpoint, json=payload)
                    if response.status_code in (503, 429) and attempt < 2:
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    data = response.json()
                    break
            except Exception as e:
                if attempt == 2:
                    raise e
                await asyncio.sleep(1.0)

        if not data:
            return {"commands": [], "question": None}

        text_content = ""
        candidates = data.get("candidates", [])
        if candidates and "content" in candidates[0]:
            parts_resp = candidates[0]["content"].get("parts", [])
            if parts_resp:
                text_content = parts_resp[0].get("text", "").strip()

        # Robust extraction of JSON from markdown blocks or surrounding prose
        text_content = text_content.strip()
        code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text_content)
        if code_block_match:
            candidate_json = code_block_match.group(1).strip()
        else:
            json_structure_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text_content)
            candidate_json = json_structure_match.group(1).strip() if json_structure_match else text_content

        try:
            parsed = json.loads(candidate_json)
        except Exception as e:
            logger.warning(f"Failed to parse model JSON: {e}. Raw content: {text_content[:300]}")
            return {"commands": [], "question": "No pude procesar la respuesta del modelo como JSON estructurado."}

        if isinstance(parsed, dict):
            cmds = parsed.get("commands", [])
            q = parsed.get("question") or parsed.get("clarification_question")
            if not cmds and "command_type" in parsed:
                cmds = [parsed]
            return {"commands": cmds, "question": q}
        elif isinstance(parsed, list):
            return {"commands": parsed, "question": None}
        return {"commands": [], "question": None}


class RuleBasedAiProvider(AiProviderPort):
    """Deterministic pattern-matching AI provider supporting Spanish and English commands.
    Ensures offline, robust, reproducible execution during exams and unit testing.
    """

    async def generate_commands(
        self,
        prompt: str,
        current_uml: CanonicalUmlDocument,
        image_data: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        commands: List[Dict[str, Any]] = []
        normalized_prompt = prompt.strip()

        # 0. Check for Database Schema (SQL DDL, relational schema notation, or tables list)
        schema_cmds = DatabaseSchemaParser.parse(normalized_prompt, current_uml)
        if schema_cmds:
            return {"commands": schema_cmds, "question": None}

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

        # 7. INHERITANCE:
        # e.g. "Cliente hereda de Persona" / "Gerente extends Empleado" / "Crea herencia de Perro a Animal"
        inh_match = re.search(
            r"(?:(?:crea(?:r)?\s+(?:una\s+)?herencia\s+(?:de\s+)?([A-Za-z0-9_]+)\s+(?:a|hacia|con)\s+([A-Za-z0-9_]+))|([A-Za-z0-9_]+)\s+(?:hereda\s+de|extiende\s+a|extiende\s+de|extends)\s+([A-Za-z0-9_]+))",
            normalized_prompt,
            re.IGNORECASE,
        )
        if inh_match:
            child_name = inh_match.group(1) or inh_match.group(3)
            parent_name = inh_match.group(2) or inh_match.group(4)
            child_name = child_name.strip()
            parent_name = parent_name.strip()

            c_cls = self._find_class_by_name(current_uml, child_name)
            p_cls = self._find_class_by_name(current_uml, parent_name)
            c_ref = c_cls.id if c_cls else child_name
            p_ref = p_cls.id if p_cls else parent_name

            commands.append({
                "command_type": CommandTypeEnum.CREATE_RELATIONSHIP.value,
                "relationship_id": f"rel-{uuid.uuid4().hex[:8]}",
                "type": RelationshipTypeEnum.INHERITANCE.value,
                "source_class_id": c_ref,
                "target_class_id": p_ref,
                "source_cardinality": "",
                "target_cardinality": "",
            })
            return commands

        # 8. COMPOSITION:
        # e.g. "Crea composicion entre Pedido y DetallePedido" / "Factura compone DetalleFactura"
        comp_match = re.search(
            r"(?:(?:crea(?:r)?\s+(?:una\s+)?composici[oó]n\s+(?:entre\s+)?([A-Za-z0-9_]+)\s+(?:con|y)\s+([A-Za-z0-9_]+))|([A-Za-z0-9_]+)\s+compone\s+(?:a\s+)?([A-Za-z0-9_]+))",
            normalized_prompt,
            re.IGNORECASE,
        )
        if comp_match:
            src_name = (comp_match.group(1) or comp_match.group(3)).strip()
            tgt_name = (comp_match.group(2) or comp_match.group(4)).strip()
            src_cls = self._find_class_by_name(current_uml, src_name)
            tgt_cls = self._find_class_by_name(current_uml, tgt_name)
            src_ref = src_cls.id if src_cls else src_name
            tgt_ref = tgt_cls.id if tgt_cls else tgt_name

            commands.append({
                "command_type": CommandTypeEnum.CREATE_RELATIONSHIP.value,
                "relationship_id": f"rel-{uuid.uuid4().hex[:8]}",
                "type": RelationshipTypeEnum.COMPOSITION.value,
                "source_class_id": src_ref,
                "target_class_id": tgt_ref,
                "source_cardinality": "1",
                "target_cardinality": "*",
            })
            return commands

        # 9. AGGREGATION:
        # e.g. "Crea agregacion entre Departamento y Empleado" / "Empresa agrega Empleado"
        agg_match = re.search(
            r"(?:(?:crea(?:r)?\s+(?:una\s+)?agregaci[oó]n\s+(?:entre\s+)?([A-Za-z0-9_]+)\s+(?:con|y)\s+([A-Za-z0-9_]+))|([A-Za-z0-9_]+)\s+agrega\s+(?:a\s+)?([A-Za-z0-9_]+))",
            normalized_prompt,
            re.IGNORECASE,
        )
        if agg_match:
            src_name = (agg_match.group(1) or agg_match.group(3)).strip()
            tgt_name = (agg_match.group(2) or agg_match.group(4)).strip()
            src_cls = self._find_class_by_name(current_uml, src_name)
            tgt_cls = self._find_class_by_name(current_uml, tgt_name)
            src_ref = src_cls.id if src_cls else src_name
            tgt_ref = tgt_cls.id if tgt_cls else tgt_name

            commands.append({
                "command_type": CommandTypeEnum.CREATE_RELATIONSHIP.value,
                "relationship_id": f"rel-{uuid.uuid4().hex[:8]}",
                "type": RelationshipTypeEnum.AGGREGATION.value,
                "source_class_id": src_ref,
                "target_class_id": tgt_ref,
                "source_cardinality": "1",
                "target_cardinality": "*",
            })
            return commands

        # 10. REALIZATION / IMPLEMENTS:
        # e.g. "NotificadorEmail implementa Notificador"
        real_match = re.search(
            r"(?:(?:crea(?:r)?\s+(?:una\s+)?realizaci[oó]n\s+(?:entre\s+)?([A-Za-z0-9_]+)\s+(?:con|y)\s+([A-Za-z0-9_]+))|([A-Za-z0-9_]+)\s+(?:implementa(?:\s+a)?|implements)\s+([A-Za-z0-9_]+))",
            normalized_prompt,
            re.IGNORECASE,
        )
        if real_match:
            src_name = (real_match.group(1) or real_match.group(3)).strip()
            tgt_name = (real_match.group(2) or real_match.group(4)).strip()
            src_cls = self._find_class_by_name(current_uml, src_name)
            tgt_cls = self._find_class_by_name(current_uml, tgt_name)
            src_ref = src_cls.id if src_cls else src_name
            tgt_ref = tgt_cls.id if tgt_cls else tgt_name

            commands.append({
                "command_type": CommandTypeEnum.CREATE_RELATIONSHIP.value,
                "relationship_id": f"rel-{uuid.uuid4().hex[:8]}",
                "type": RelationshipTypeEnum.REALIZATION.value,
                "source_class_id": src_ref,
                "target_class_id": tgt_ref,
                "source_cardinality": "",
                "target_cardinality": "",
            })
            return commands

        # 11. GENERAL ASSOCIATIONS:
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

        if not commands:
            if image_data and image_data.get("data"):
                question = (
                    "Detecté una imagen adjunta pero no se pudo procesar con el servicio de visión de IA. "
                    "¿Podrías confirmarme qué tablas y columnas contiene tu diseño o pegarme la estructura en SQL/texto para construirla inmediatamente?"
                )
            else:
                question = (
                    "No pude identificar con certeza las entidades o tablas de tu diseño. "
                    "¿Qué base de datos o modelo te gustaría crear? Podés pegarme un script SQL (CREATE TABLE), "
                    "una lista de tablas (ej. 'Cliente (id, nombre), Pedido (id, total, cliente_id)') o describir las reglas de tu negocio."
                )
            return {"commands": [], "question": question}

        return {"commands": commands, "question": None}

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

