"""Heuristic section splitter based on WeKnora's structural-marker strategy."""

from __future__ import annotations

import re

from app.services.chunker.heading_utils import (
    CHINESE_SECTION_RE,
    ENGLISH_SECTION_RE,
    NUMBERED_SECTION_RE,
    heading_path_to_header,
    looks_like_section_heading,
)
from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.chunker.recursive_splitter import split_recursive
from app.utils.text import sanitize_text_for_postgres


VISUAL_SEPARATOR_RE = re.compile(r"^\s*(?:---+|===+|\*\*\*+|\f)\s*$")


def heuristic_marker_count(text: str) -> int:
    """Count structural markers used by WeKnora's auto strategy."""
    total = 0
    for line in (text or "").splitlines():
        clean = sanitize_text_for_postgres(line).strip()
        if not clean:
            continue
        if (
            looks_like_section_heading(clean)
            or CHINESE_SECTION_RE.search(clean)
            or NUMBERED_SECTION_RE.search(clean)
            or ENGLISH_SECTION_RE.search(clean)
            or VISUAL_SEPARATOR_RE.search(clean)
        ):
            total += 1
    return total


def has_heuristic_structure(text: str) -> bool:
    return heuristic_marker_count(text) >= 2 or bool(re.search(r"\f", text or ""))


def split_by_heuristics(text: str, cfg: ChunkerConfig) -> list[ParsedChunk]:
    """Split at obvious chapter/section boundaries, then recurse inside each section."""
    text = sanitize_text_for_postgres(text)
    if not text:
        return []

    line_matches: list[tuple[int, int, str]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()
        line_start = offset
        line_end = offset + len(raw_line)
        offset = line_end
        if not line:
            continue
        if looks_like_section_heading(line) or VISUAL_SEPARATOR_RE.search(line):
            line_matches.append((line_start, line_end, line))

    if not line_matches:
        return split_recursive(text, cfg, metadata={"source": "parser", "strategy": "recursive", "heading_path": []})

    chunks: list[ParsedChunk] = []
    for index, (start, _, title) in enumerate(line_matches):
        end = line_matches[index + 1][0] if index + 1 < len(line_matches) else len(text)
        section = text[start:end].strip()
        if not section:
            continue
        heading_path = [title] if not VISUAL_SEPARATOR_RE.search(title) else []
        context_header = heading_path_to_header(heading_path)
        local = split_recursive(
            section,
            cfg,
            context_header=context_header,
            base_seq=len(chunks),
            metadata={"source": "parser", "strategy": "heuristic", "heading_path": heading_path},
            source_section=title if heading_path else None,
        )
        for chunk in local:
            chunk.start += start
            chunk.end += start
            chunk.seq = len(chunks)
            chunk.metadata["seq"] = chunk.seq
            chunks.append(chunk)

    return chunks or split_recursive(text, cfg, metadata={"source": "parser", "strategy": "recursive", "heading_path": []})
