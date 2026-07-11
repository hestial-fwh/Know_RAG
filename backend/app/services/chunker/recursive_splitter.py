import re

from app.services.chunker.heading_utils import make_continuation_header, normalize_context_header
from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.chunker.protected_patterns import (
    ProtectedSpan,
    find_protected_spans,
    is_protected_range,
    is_structure_like_text,
    looks_like_directory_tree,
)
from app.services.chunker.text_unit import (
    TextUnit,
    is_bad_chunk_start,
    pack_units_into_chunks,
    repair_bad_start,
    split_text_to_sentence_units,
)
from app.utils.text import sanitize_text_for_postgres


_STRONG_SEPARATORS = ["。", "！", "？", "；", ". ", "! ", "? ", "; ", "\n\n", "\n"]
_WEAK_SEPARATORS = ["，", ", ", "、", "：", ": ", " "]
FENCED_CODE_RE = re.compile(r"^\s*```[\s\S]*?```\s*$")


def _separator_positions(text: str, start: int, end: int, separators: list[str]) -> list[int]:
    positions: list[int] = []
    window = text[start:end]
    for sep in separators:
        if not sep:
            continue
        offset = 0
        while True:
            found = window.find(sep, offset)
            if found < 0:
                break
            positions.append(start + found + len(sep))
            offset = found + len(sep)
    return sorted(set(positions))


def _valid_break(pos: int, protected_spans) -> bool:
    return not is_protected_range(max(pos - 1, 0), pos, protected_spans)


def _span_containing(pos: int, protected_spans: list[ProtectedSpan]) -> ProtectedSpan | None:
    for span in protected_spans:
        if span.start < pos < span.end:
            return span
    return None


def _content_kind(text: str) -> str:
    if FENCED_CODE_RE.search(text or ""):
        return "code_block"
    if looks_like_directory_tree(text):
        return "directory_tree"
    if is_structure_like_text(text):
        return "code_structure"
    return "text"


def split_structure_by_lines(
    text: str,
    cfg: ChunkerConfig,
    *,
    context_header: str = "",
    base_seq: int = 0,
    metadata: dict | None = None,
    source_section: str | None = None,
) -> list[ParsedChunk]:
    """Split directory trees/file listings by complete lines, never mid-token."""
    text = sanitize_text_for_postgres(text)
    if not text:
        return []
    context_header = normalize_context_header(context_header)
    metadata = metadata or {}
    size = max(cfg.chunk_size, 1)
    overlap_chars = max(min(cfg.chunk_overlap, size // 2), 0)
    kind = metadata.get("content_kind") or _content_kind(text)
    lines = text.splitlines(keepends=True)
    chunks: list[ParsedChunk] = []
    current: list[tuple[int, str]] = []
    current_len = 0
    offset = 0

    def emit(*, forced_long_line_split: bool = False) -> None:
        nonlocal current, current_len
        if not current:
            return
        start = current[0][0]
        end = current[-1][0] + len(current[-1][1])
        content = sanitize_text_for_postgres("".join(line for _, line in current)).strip()
        if content:
            seq = base_seq + len(chunks)
            chunk_header = context_header if (not chunks or not context_header) else make_continuation_header(context_header)
            chunks.append(
                ParsedChunk(
                    content=content,
                    context_header=chunk_header,
                    seq=seq,
                    start=start,
                    end=end,
                    metadata={
                        **metadata,
                        "strategy": metadata.get("strategy", "recursive"),
                        "seq": seq,
                        "sentence_aware": False,
                        "content_kind": kind,
                        "structure_like": True,
                        "preserve_line_boundaries": True,
                        "forced_long_line_split": forced_long_line_split,
                    },
                    chunk_type="text",
                    source_section=source_section,
                )
            )
        current = []
        current_len = 0

    def overlap_lines() -> list[tuple[int, str]]:
        if overlap_chars <= 0 or not current:
            return []
        selected: list[tuple[int, str]] = []
        total = 0
        for item in reversed(current):
            if selected and total + len(item[1]) > overlap_chars:
                break
            selected.append(item)
            total += len(item[1])
        return list(reversed(selected))

    for line in lines:
        line_start = offset
        offset += len(line)
        if len(line) > size:
            emit()
            forced_cfg = ChunkerConfig(
                chunk_size=size,
                chunk_overlap=0,
                strategy=cfg.strategy,
                separators=[" ", ""],
            )
            for part in split_recursive(
                line,
                forced_cfg,
                context_header=context_header,
                base_seq=base_seq + len(chunks),
                metadata={**metadata, "content_kind": kind, "forced_long_line_split": True},
                source_section=source_section,
            ):
                part.start += line_start
                part.end += line_start
                part.metadata["structure_like"] = True
                part.metadata["preserve_line_boundaries"] = False
                chunks.append(part)
            continue
        if current and current_len + len(line) > size:
            keep = overlap_lines()
            emit()
            current = keep
            current_len = sum(len(item[1]) for item in current)
        current.append((line_start, line))
        current_len += len(line)
    emit()
    return chunks


def split_recursive(
    text: str,
    cfg: ChunkerConfig,
    *,
    context_header: str = "",
    base_seq: int = 0,
    metadata: dict | None = None,
    source_section: str | None = None,
) -> list[ParsedChunk]:
    """Split plain text using sentence-unit packing.

    Replaces the old character-window + character-overlap approach with
    complete-sentence/paragraph accumulation.  Structure-like text
    (directory trees, code listings) is still routed through
    ``split_structure_by_lines``.
    """
    text = sanitize_text_for_postgres(text)
    if not text:
        return []

    context_header = normalize_context_header(context_header)
    metadata = metadata or {}

    # Route structure-like text to line-aware splitter
    if is_structure_like_text(text):
        return split_structure_by_lines(
            text,
            cfg,
            context_header=context_header,
            base_seq=base_seq,
            metadata={**metadata, "content_kind": metadata.get("content_kind") or _content_kind(text)},
            source_section=source_section,
        )

    size = max(cfg.chunk_size, 1)
    overlap = max(min(cfg.chunk_overlap, size // 2), 0)

    # 1. Detect protected spans (code blocks, tables, formulas, etc.)
    protected_spans = find_protected_spans(text)

    # 2. Split into sentence/paragraph/protected units
    units = split_text_to_sentence_units(text, size, protected_spans)

    # 3. Pack units into chunks with sentence-level overlap
    chunks = pack_units_into_chunks(
        units,
        size,
        overlap,
        context_header=context_header,
        base_seq=base_seq,
        metadata=metadata,
        source_section=source_section,
    )

    # 4. Repair any remaining bad starts (merge-first, then mark)
    chunks = repair_bad_start(chunks, cfg)

    # 5. Tag the first unit's paragraph-start status on each chunk
    for chunk in chunks:
        chunk.metadata.setdefault("sentence_aware", True)

    return chunks
