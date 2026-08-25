"""Semantic code chunking.

Primary strategy: Tree-sitter based extraction of top-level classes, modules
and functions/methods per language. When tree-sitter grammars are unavailable
or a file fails to parse we fall back to a deterministic line-based chunker so
indexing never fails because of one bad file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.ingestion.filters import file_content_hash

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised implicitly when dependency present
    from tree_sitter import Node

    _HAS_TREE_SITTER = True
except ImportError:  # pragma: no cover
    _HAS_TREE_SITTER = False


@dataclass(frozen=True)
class ChunkData:
    relative_path: str
    language: str
    symbol_name: str | None
    symbol_type: str | None
    start_line: int  # 1-indexed inclusive
    end_line: int  # 1-indexed inclusive
    content: str

    @property
    def content_hash(self) -> str:
        return file_content_hash(self.content)


# node-type -> (symbol_type) per supported language.
NODE_TYPES: dict[str, dict[str, str]] = {
    "python": {
        "class_definition": "class",
        "function_definition": "function",
    },
    "ruby": {
        "class": "class",
        "module": "module",
        "singleton_method": "method",
        "method": "method",
    },
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "method_definition": "method",
    },
    "typescript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "method_definition": "method",
        "interface_declaration": "interface",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "constructor_declaration": "constructor",
        "method_declaration": "method",
    },
}

MAX_CHUNK_CHARS = 4_000
FALLBACK_CHUNK_LINES = 60


def _clip(content: str, limit: int = MAX_CHUNK_CHARS) -> str:
    if len(content) <= limit:
        return content
    return content[:limit] + "\n… (truncated)"


def make_chunk(
    relative_path: str,
    language: str,
    symbol_name: str | None,
    symbol_type: str | None,
    start_line: int,
    end_line: int,
    content: str,
) -> ChunkData:
    return ChunkData(
        relative_path=relative_path,
        language=language,
        symbol_name=symbol_name,
        symbol_type=symbol_type,
        start_line=start_line,
        end_line=end_line,
        content=_clip(content),
    )


class TreeSitterChunker:
    """Extract semantic chunks using tree-sitter grammars."""

    def __init__(self, max_chunk_chars: int = MAX_CHUNK_CHARS) -> None:
        self._parsers: dict[str, object] = {}
        self._max_chunk_chars = max_chunk_chars

    @property
    def available(self) -> bool:
        return _HAS_TREE_SITTER

    def _parser(self, language: str):
        if language in self._parsers:
            return self._parsers[language]
        try:
            from tree_sitter_language_pack import get_parser

            parser = get_parser(language)
        except Exception as exc:
            logger.debug("tree_sitter_parser_unavailable", extra={"language": language, "error": str(exc)})
            parser = None
        self._parsers[language] = parser
        return parser

    def chunk(self, source: str, relative_path: str, language: str) -> list[ChunkData]:
        parser = self._parser(language)
        if parser is None:
            return []
        mapping = NODE_TYPES.get(language)
        if not mapping:
            return []
        try:
            tree = parser.parse(source.encode("utf-8"))
            root = tree.root_node
            if root is None or root.has_error:
                # Still attempt extraction; many files parse with recoverable errors.
                pass
        except Exception as exc:
            logger.warning("tree_sitter_parse_failed", extra={"path": relative_path, "error": str(exc)})
            return []

        lines = source.splitlines()
        chunks: list[ChunkData] = []

        def node_identifier(node: Node) -> str | None:
            name_node = node.child_by_field_name("name")
            if name_node is not None and name_node.text:
                return name_node.text.decode("utf-8", "replace")
            return None

        def walk(node: Node, inside_captured: bool) -> None:
            captured_here = False
            suppress_descend = False
            node_type = node.type
            if not inside_captured and node_type in mapping:
                start = node.start_point[0] + 1
                end = node.end_point[0] + 1
                content = "\n".join(lines[start - 1 : end])
                chunks.append(
                    make_chunk(
                        relative_path=relative_path,
                        language=language,
                        symbol_name=node_identifier(node),
                        symbol_type=mapping[node_type],
                        start_line=start,
                        end_line=end,
                        content=content,
                    )
                )
                captured_here = True
                # Classes/modules keep descending so nested methods are chunked too.
                suppress_descend = mapping[node_type] not in ("class", "module")
            for child in node.children:
                walk(child, inside_captured or (captured_here and suppress_descend))

        try:
            walk(root, False)
        except RecursionError:
            return []
        return chunks


class FallbackLineChunker:
    """Deterministic line-based chunker used when semantic parsing fails.

    Blocks of N lines; each block's symbol name is sniffed from common
    definition patterns so retrieval still has something to match on.
    """

    _SYMBOL_PATTERNS = (
        "class ", "def ", "function ", "interface ",
    )

    def __init__(self, chunk_lines: int = FALLBACK_CHUNK_LINES) -> None:
        self._chunk_lines = chunk_lines

    def chunk(
        self,
        source: str,
        relative_path_or_language: str | None = None,
        language: str | None = None,
    ) -> list[ChunkData]:
        # Support both chunk(source, language) and chunk(source, path, language).
        if language is None and relative_path_or_language in NODE_TYPES:
            language, relative_path_or_language = relative_path_or_language, None
        relative_path = relative_path_or_language or "unknown_file"
        lines = source.splitlines()
        if not lines:
            return []
        chunks: list[ChunkData] = []
        step = self._chunk_lines
        for block_number, start in enumerate(range(0, len(lines), step), start=1):
            end = min(start + step, len(lines))
            content = "\n".join(lines[start:end])
            if not content.strip():
                continue
            chunks.append(
                make_chunk(
                    relative_path=relative_path,
                    language=language or "text",
                    symbol_name=self._sniff_symbol(content),
                    symbol_type="block",
                    start_line=start + 1,
                    end_line=end,
                    content=content,
                )
            )
        return chunks

    @classmethod
    def _sniff_symbol(cls, content: str) -> str | None:
        import re

        for line in content.splitlines():
            stripped = line.strip()
            for prefix in ("class", "def", "function", "module", "interface"):
                if stripped.startswith(prefix + " ") or stripped.startswith(f"{prefix}("):
                    remainder = stripped[len(prefix):].strip()
                    match = re.match(r"[A-Za-z_][A-Za-z0-9_.?!]*", remainder)
                    if match:
                        return match.group(0)
        return None


def chunk_source(
    source: str,
    relative_path: str,
    language: str,
    semantic_chunker: TreeSitterChunker | None = None,
) -> list[ChunkData]:
    """Chunk a file semantically with automatic fallback."""
    semantic = semantic_chunker or TreeSitterChunker()
    try:
        chunks = semantic.chunk(source, relative_path, language)
    except Exception as exc:  # never fail indexing on one file
        logger.warning("semantic_chunking_failed", extra={"path": relative_path, "error": str(exc)})
        chunks = []
    if chunks:
        return chunks
    return FallbackLineChunker().chunk(source, relative_path, language)
