"""Tree-sitter node type mappings and symbol-name extraction."""

# Node types considered "definitions" per language.
DEFINITION_TYPES: dict[str, set[str]] = {
    "python": {"class_definition", "function_definition"},
    "ruby": {"class", "module", "method", "singleton_method"},
    "javascript": {
        "class_declaration",
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "variable_declarator",
    },
    "typescript": {
        "class_declaration",
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "variable_declarator",
    },
    "go": {"function_declaration", "method_declaration", "type_spec"},
    "java": {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "method_declaration",
        "constructor_declaration",
    },
}


def definition_types_for(language: str) -> set[str]:
    return DEFINITION_TYPES.get(language, set())


def node_symbol_name(node) -> str | None:
    """Best-effort symbol name from a tree-sitter definition node."""
    for field in ("name", "declarator"):
        child = node.child_by_field_name(field)
        if child is not None:
            text = _node_text(child)
            if text:
                # variable_declarator: take LHS identifier only
                return text.split("(")[0].strip()[:200]
    return None


def _node_text(node) -> str:
    try:
        return node.text.decode("utf-8")
    except Exception:
        return ""
