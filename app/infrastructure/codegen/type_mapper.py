from typing import Dict, Optional, Set, Tuple


class JavaTypeInfo:
    def __init__(self, java_type: str, import_stmt: Optional[str] = None, sample_value: str = "null"):
        self.java_type = java_type
        self.import_stmt = import_stmt
        self.sample_value = sample_value


TYPE_MAPPINGS: Dict[str, JavaTypeInfo] = {
    "string": JavaTypeInfo("String", sample_value='"SampleText"'),
    "str": JavaTypeInfo("String", sample_value='"SampleText"'),
    "text": JavaTypeInfo("String", sample_value='"SampleText"'),
    "long": JavaTypeInfo("Long", sample_value="1L"),
    "int": JavaTypeInfo("Integer", sample_value="1"),
    "integer": JavaTypeInfo("Integer", sample_value="1"),
    "double": JavaTypeInfo("Double", sample_value="10.5"),
    "float": JavaTypeInfo("Double", sample_value="10.5"),
    "boolean": JavaTypeInfo("Boolean", sample_value="true"),
    "bool": JavaTypeInfo("Boolean", sample_value="true"),
    "bigdecimal": JavaTypeInfo("BigDecimal", "java.math.BigDecimal", 'new java.math.BigDecimal("99.99")'),
    "decimal": JavaTypeInfo("BigDecimal", "java.math.BigDecimal", 'new java.math.BigDecimal("99.99")'),
    "localdate": JavaTypeInfo("LocalDate", "java.time.LocalDate", "java.time.LocalDate.now()"),
    "date": JavaTypeInfo("LocalDate", "java.time.LocalDate", "java.time.LocalDate.now()"),
    "localdatetime": JavaTypeInfo("LocalDateTime", "java.time.LocalDateTime", "java.time.LocalDateTime.now()"),
    "datetime": JavaTypeInfo("LocalDateTime", "java.time.LocalDateTime", "java.time.LocalDateTime.now()"),
    "timestamp": JavaTypeInfo("LocalDateTime", "java.time.LocalDateTime", "java.time.LocalDateTime.now()"),
}


def map_uml_type_to_java(uml_type: str) -> JavaTypeInfo:
    cleaned = (uml_type or "String").strip().lower()
    if cleaned in TYPE_MAPPINGS:
        return TYPE_MAPPINGS[cleaned]
    # Default fallback is String
    return JavaTypeInfo("String", sample_value='"SampleText"')

