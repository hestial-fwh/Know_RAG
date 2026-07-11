import re

from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.chunker.protected_patterns import is_structure_like_text
from app.services.chunker.recursive_splitter import split_recursive
from app.services.chunker.heading_utils import normalize_context_header
from app.utils.text import sanitize_text_for_postgres


HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
PROJECT_STRUCTURE_HEADING_RE = re.compile(
    r"(项目结构|目录结构|后端目录|前端目录|核心服务|服务层目录|backend/|frontend/|app/services|src/)",
    re.I,
)


def has_markdown_headings(text: str) -> bool:
    return bool(HEADING_RE.search(text or ""))


def _heading_path(stack: list[tuple[int, str]]) -> list[str]:
    return [normalize_context_header(title) for _, title in stack]


def split_by_headings(text: str, cfg: ChunkerConfig) -> list[ParsedChunk]:
    text = sanitize_text_for_postgres(text)
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return split_recursive(
            text,
            cfg,
            metadata={"source": "parser", "strategy": "recursive", "heading_path": []},
        )

    chunks: list[ParsedChunk] = []
    stack: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = sanitize_text_for_postgres(match.group(2))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        section_start = match.start()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[section_start:section_end]
        heading_path = _heading_path(stack)
        context_header = normalize_context_header("\n".join(heading_path))
        content_kind = "project_structure" if PROJECT_STRUCTURE_HEADING_RE.search(title) and is_structure_like_text(section) else None
        local_chunks = split_recursive(
            section,
            cfg,
            context_header=context_header,
            base_seq=len(chunks),
            metadata={
                "source": "parser",
                "strategy": "heading",
                "heading_path": heading_path,
                **({"content_kind": content_kind} if content_kind else {}),
            },
            source_section=heading_path[-1] if heading_path else None,
        )
        for chunk in local_chunks:
            chunk.start += section_start
            chunk.end += section_start
            chunk.seq = len(chunks)
            chunk.metadata["seq"] = chunk.seq
            chunks.append(chunk)
    return chunks
