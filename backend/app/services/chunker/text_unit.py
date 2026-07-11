"""Sentence-unit packing for plain-text chunking.

Replaces character-window sliding with complete-sentence/paragraph packing
so chunks always start and end on natural prose boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.chunker.heading_utils import make_continuation_header, normalize_context_header
from app.services.chunker.protected_patterns import ProtectedSpan, is_protected_range
from app.utils.text import sanitize_text_for_postgres


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Chinese sentence-ending punctuation
_SENTENCE_END_CN = re.compile(r"[。！？；]")
# English sentence-ending: . ! ? followed by whitespace + (uppercase or CJK)
_SENTENCE_END_EN = re.compile(r"[.!?](?=\s+(?:[A-Z一-鿿]|\d+\s))")
# Reference-number tail: [19], [22-24], （19）, (19)
_REF_TAIL_RE = re.compile(r"(?:\[\d+(?:[,-]\d+)*\]|[（(]\d+[）)])+$")
# Weak punctuation for long-sentence fallback
_WEAK_PUNCT_RE = re.compile(r"([；;])")
_WEAKER_PUNCT_RE = re.compile(r"([，,、：:])")
# Leading punctuation / bad starts
_LEADING_PUNCT_CHARS: set[str] = set("，。；、：）]》　")
# Connectives that indicate a mid-sentence start (bad unless paragraph-start)
_BAD_CONNECTIVE_PATTERNS = (
    # Chinese connectives (mid-sentence only — not flaggable at paragraph start)
    r"^(且|并|与|及|或|更|从而|因此|此外|同时|但是|而且|其中|"
    r"但|而|则|故|以|以利于|有利于|"
    r"另外|由此|进而|于是|于是乎|"
    r"所以|因而|故此)"
)
# After a Chinese connective we expect CJK chars, punctuation, or whitespace.
_BAD_CONNECTIVE_RE = re.compile(
    _BAD_CONNECTIVE_PATTERNS + r"(?=[，。；、：\s\]》）一-鿿])"
)
# English connectives (separate pattern — must be followed by space/punct/CJK, NOT another letter)
_BAD_EN_CONNECTIVE_RE = re.compile(
    r"^(and|or|but|therefore|thus|hence|however|moreover|furthermore|besides|also)"
    r"(?=[，。；、：\s\]》）一-鿿])"
)
# Half-word / half-path detection at chunk start
_HALF_WORD_RE = re.compile(r"^[a-z0-9_./-]+\.[a-z]{1,6}\b", re.I)
_HALF_PATH_RE = re.compile(r"^[/\\][\w./-]+")
_URL_START_RE = re.compile(r"^https?://", re.I)
# Reference-fragment start: 等[19], ][, ）
_REF_FRAGMENT_START_RE = re.compile(r"^(等?\[\d+(?:[,-]\d+)*\]|][(\[]|[）\)])")

# Paragraph separator
_PARAGRAPH_SEP_RE = re.compile(r"\n\s*\n")


# ---------------------------------------------------------------------------
# TextUnit
# ---------------------------------------------------------------------------


@dataclass
class TextUnit:
    """A logical unit of text: paragraph, sentence, weak clause, or protected span."""

    text: str
    start: int  # character offset in original text
    end: int
    unit_type: str  # "paragraph" | "sentence" | "weak_clause" | "protected" | "hard_split"
    is_paragraph_start: bool = False


# ---------------------------------------------------------------------------
# split_text_to_sentence_units
# ---------------------------------------------------------------------------


def _find_sentence_boundaries(text: str, start: int, end: int) -> list[int]:
    """Find sentence-boundary positions within text[start:end].

    Returns a sorted list of *absolute* positions where a sentence ends
    (the character index right after the terminating punctuation).
    """
    boundaries: list[int] = []
    window = text[start:end]

    # Chinese strong punctuation
    for m in _SENTENCE_END_CN.finditer(window):
        boundaries.append(start + m.end())

    # English sentence-ending punctuation
    for m in _SENTENCE_END_EN.finditer(window):
        boundaries.append(start + m.end())

    return sorted(set(boundaries))


def _split_paragraph_into_sentences(
    text: str, para_start: int, para_end: int
) -> list[TextUnit]:
    """Split a paragraph into sentence units at strong punctuation boundaries.

    Reference numbers like [19] stay attached to their preceding sentence.
    """
    boundaries = _find_sentence_boundaries(text, para_start, para_end)
    if not boundaries:
        # No sentence boundaries found — treat the whole paragraph as one unit
        content = text[para_start:para_end]
        if content.strip():
            return [
                TextUnit(
                    text=content,
                    start=para_start,
                    end=para_end,
                    unit_type="paragraph",
                    is_paragraph_start=True,
                )
            ]
        return []

    units: list[TextUnit] = []
    cursor = para_start
    for boundary in boundaries:
        if boundary <= cursor:
            continue
        segment = text[cursor:boundary]
        if not segment.strip():
            cursor = boundary
            continue
        # Check if this segment ends with a reference tail that should
        # be merged with the next segment.  This handles cases like
        # "...麦麸皮[19]。" where the period after [19] is the real boundary.
        units.append(
            TextUnit(
                text=segment,
                start=cursor,
                end=boundary,
                unit_type="sentence",
                is_paragraph_start=(cursor == para_start),
            )
        )
        cursor = boundary

    # Remainder after the last boundary
    if cursor < para_end:
        remainder = text[cursor:para_end]
        if remainder.strip():
            units.append(
                TextUnit(
                    text=remainder,
                    start=cursor,
                    end=para_end,
                    unit_type="sentence",
                    is_paragraph_start=(cursor == para_start),
                )
            )

    # Post-process: merge reference-only segments with the preceding sentence.
    # A segment like "[19]" or "（19）" by itself should attach to the prior unit.
    merged: list[TextUnit] = []
    for unit in units:
        stripped = unit.text.strip()
        if merged and re.match(r"^[\[（(]\d+[\]）)]\s*$", stripped):
            # This is a bare reference — merge into previous
            prev = merged[-1]
            prev.text = text[prev.start : unit.end]
            prev.end = unit.end
        else:
            merged.append(unit)

    return merged


def split_text_to_sentence_units(
    text: str, chunk_size: int, protected_spans: list[ProtectedSpan] | None = None
) -> list[TextUnit]:
    """Split plain text into TextUnit list.

    Order of operations:
    1. Build non-protected text regions by subtracting protected spans.
    2. Split each region into paragraphs (\\n\\n), then sentences.
    3. Emit protected spans as ``unit_type="protected"``.
    4. Return units sorted by start position.
    """
    text_len = len(text)
    spans = protected_spans or []

    if not spans:
        return _split_regions_into_units(text, [(0, text_len)], chunk_size)

    # Build non-protected regions by subtracting protected spans
    sorted_spans = sorted(spans, key=lambda s: s.start)
    regions: list[tuple[int, int]] = []
    cursor = 0
    for span in sorted_spans:
        if span.start > cursor:
            regions.append((cursor, span.start))
        cursor = max(cursor, span.end)
    if cursor < text_len:
        regions.append((cursor, text_len))

    # Split unprotected regions
    units = _split_regions_into_units(text, regions, chunk_size)

    # Insert protected units
    for span in sorted_spans:
        content = text[span.start : span.end]
        if not content.strip():
            continue
        units.append(
            TextUnit(
                text=content,
                start=span.start,
                end=span.end,
                unit_type="protected",
                is_paragraph_start=False,
            )
        )

    # Sort by start position
    units.sort(key=lambda u: u.start)
    return units


def _split_regions_into_units(
    text: str,
    regions: list[tuple[int, int]],
    chunk_size: int,
) -> list[TextUnit]:
    """Convert unprotected text regions into sentence/paragraph units."""
    units: list[TextUnit] = []
    for reg_start, reg_end in regions:
        region_text = text[reg_start:reg_end]
        if not region_text.strip():
            continue

        # Split region into paragraphs
        para_splits = [m.start() for m in _PARAGRAPH_SEP_RE.finditer(region_text)]
        para_boundaries = [reg_start] + [reg_start + p + 2 for p in para_splits]  # +2 for "\n\n"
        # Recalculate boundaries based on actual separator positions
        cursor = reg_start
        para_boundaries = [cursor]
        for m in _PARAGRAPH_SEP_RE.finditer(region_text):
            para_boundaries.append(reg_start + m.start() + len(m.group()))
        # Filter: only keep boundaries within the region
        para_boundaries = [b for b in para_boundaries if b <= reg_end]

        for i, para_start in enumerate(para_boundaries):
            para_end = para_boundaries[i + 1] if i + 1 < len(para_boundaries) else reg_end
            para_content = text[para_start:para_end]
            if not para_content.strip():
                continue

            para_len = len(para_content)
            if para_len <= chunk_size:
                units.append(
                    TextUnit(
                        text=para_content,
                        start=para_start,
                        end=para_end,
                        unit_type="paragraph",
                        is_paragraph_start=True,
                    )
                )
            else:
                # Split paragraph into sentences
                sentence_units = _split_paragraph_into_sentences(text, para_start, para_end)
                units.extend(sentence_units)

    return units


# ---------------------------------------------------------------------------
# split_long_sentence
# ---------------------------------------------------------------------------


def split_long_sentence(
    text: str, start_offset: int, chunk_size: int
) -> list[TextUnit]:
    """Split a single sentence that exceeds chunk_size.

    Tier 1: split on ；(Chinese semicolon)
    Tier 2: split on ，、：(comma, enumeration comma, colon)
    Tier 3: hard character split (last resort)

    Subsequent parts never keep leading weak punctuation.
    Hard splits avoid cutting through English words, URLs, file paths,
    numbers, and reference numbers.
    """
    text_len = len(text)
    if text_len <= chunk_size:
        return [
            TextUnit(
                text=text,
                start=start_offset,
                end=start_offset + text_len,
                unit_type="sentence",
            )
        ]

    # Tier 1: semicolons
    units = _split_at_pattern(text, start_offset, chunk_size, _WEAK_PUNCT_RE, "weak_clause")
    if len(units) > 1:
        return units

    # Tier 2: commas, enumeration commas, colons
    units = _split_at_pattern(text, start_offset, chunk_size, _WEAKER_PUNCT_RE, "weak_clause")
    if len(units) > 1:
        return units

    # Tier 3: hard split
    return _hard_split(text, start_offset, chunk_size)


def _split_at_pattern(
    text: str,
    start_offset: int,
    chunk_size: int,
    pattern: re.Pattern,
    unit_type: str,
) -> list[TextUnit]:
    """Split text at pattern boundaries, packing segments into chunk_size pieces.

    Returns a single unit if no suitable splits are found.
    """
    # Find all split positions
    split_positions: list[int] = []
    for m in pattern.finditer(text):
        split_positions.append(m.end())

    if not split_positions:
        return [
            TextUnit(
                text=text,
                start=start_offset,
                end=start_offset + len(text),
                unit_type="sentence",
            )
        ]

    # Build candidate segments
    segments: list[tuple[str, int, int]] = []  # (text, rel_start, rel_end)
    cursor = 0
    for pos in split_positions:
        if pos <= cursor:
            continue
        seg = text[cursor:pos]
        if seg.strip():
            segments.append((seg, cursor, pos))
        cursor = pos

    # Remainder
    if cursor < len(text):
        seg = text[cursor:]
        if seg.strip():
            segments.append((seg, cursor, len(text)))

    if not segments:
        return [
            TextUnit(
                text=text,
                start=start_offset,
                end=start_offset + len(text),
                unit_type="sentence",
            )
        ]

    # Pack segments into chunk_size groups
    units: list[TextUnit] = []
    buffer_segs: list[tuple[str, int, int]] = []
    buffer_len = 0

    for seg_text, seg_start, seg_end in segments:
        seg_len = len(seg_text)
        if buffer_segs and buffer_len + seg_len > chunk_size:
            # Emit buffer as one unit
            combined = "".join(s[0] for s in buffer_segs)
            units.append(
                TextUnit(
                    text=combined,
                    start=start_offset + buffer_segs[0][1],
                    end=start_offset + buffer_segs[-1][2],
                    unit_type=unit_type,
                )
            )
            # Start new buffer — strip leading weak punctuation from first segment
            cleaned = _strip_leading_weak_punct(seg_text)
            if cleaned:
                buffer_segs = [(cleaned, seg_start, seg_end)]
                buffer_len = len(cleaned)
            else:
                buffer_segs = []
                buffer_len = 0
        else:
            if not buffer_segs:
                # First segment in buffer — strip leading weak punct
                cleaned = _strip_leading_weak_punct(seg_text)
                if cleaned:
                    buffer_segs = [(cleaned, seg_start, seg_end)]
                    buffer_len = len(cleaned)
            else:
                buffer_segs.append((seg_text, seg_start, seg_end))
                buffer_len += seg_len

    # Emit remaining buffer
    if buffer_segs:
        combined = "".join(s[0] for s in buffer_segs)
        units.append(
            TextUnit(
                text=combined,
                start=start_offset + buffer_segs[0][1],
                end=start_offset + buffer_segs[-1][2],
                unit_type=unit_type,
            )
        )

    return units if len(units) > 1 else [
        TextUnit(
            text=text,
            start=start_offset,
            end=start_offset + len(text),
            unit_type="sentence",
        )
    ]


def _strip_leading_weak_punct(text: str) -> str:
    """Remove leading weak punctuation and whitespace from a segment."""
    value = text
    while value and (value[0] in _LEADING_PUNCT_CHARS or value[0] in " \t\n"):
        value = value[1:]
    return value


def _hard_split(text: str, start_offset: int, chunk_size: int) -> list[TextUnit]:
    """Last-resort hard split that avoids cutting words/URLs/paths/numbers."""
    units: list[TextUnit] = []
    cursor = 0
    text_len = len(text)

    while cursor < text_len:
        hard_end = min(cursor + chunk_size, text_len)
        if hard_end >= text_len:
            seg = text[cursor:]
            if seg.strip():
                units.append(
                    TextUnit(
                        text=seg,
                        start=start_offset + cursor,
                        end=start_offset + text_len,
                        unit_type="hard_split",
                    )
                )
            break

        # Walk back from hard_end to find a safe split point
        split_at = hard_end
        for pos in range(hard_end - 1, cursor + int(chunk_size * 0.5), -1):
            ch = text[pos]
            # Safe to split after CJK characters or whitespace
            if "一" <= ch <= "鿿" or ch in " \t\n":
                split_at = pos + 1
                break
            # Also safe after sentence-ending punctuation
            if ch in "。！？；.!?;":
                split_at = pos + 1
                break

        seg = text[cursor:split_at]
        if seg.strip():
            units.append(
                TextUnit(
                    text=seg,
                    start=start_offset + cursor,
                    end=start_offset + split_at,
                    unit_type="hard_split",
                )
            )

        cursor = split_at

    return units


# ---------------------------------------------------------------------------
# pack_units_into_chunks
# ---------------------------------------------------------------------------


def pack_units_into_chunks(
    units: list[TextUnit],
    chunk_size: int,
    chunk_overlap: int,
    context_header: str = "",
    base_seq: int = 0,
    metadata: dict | None = None,
    source_section: str | None = None,
) -> list[ParsedChunk]:
    """Pack TextUnits into ParsedChunks using complete-unit accumulation.

    Rules:
    - Accumulate units until adding the next would exceed chunk_size.
    - Overlap = last 1 complete sentence/paragraph from the previous chunk.
    - Skip overlap if previous chunk is very short or overlap would cause bad start.
    - Never split a unit mid-way.
    """
    if not units:
        return []

    context_header = normalize_context_header(context_header)
    metadata = metadata or {}
    chunks: list[ParsedChunk] = []
    buffer: list[TextUnit] = []
    buffer_len = 0

    def _chunk_text(buf: list[TextUnit]) -> str:
        return sanitize_text_for_postgres("".join(u.text for u in buf))

    def _content_kind(buf: list[TextUnit]) -> str:
        joined = _chunk_text(buf)
        for u in buf:
            if u.unit_type == "protected":
                return "code_block"
        return "text"

    def _emit(buf: list[TextUnit]) -> ParsedChunk | None:
        content = _chunk_text(buf)
        if not content:
            return None
        start = buf[0].start
        end = buf[-1].end
        kind = _content_kind(buf)
        seq = base_seq + len(chunks)
        chunk_header = (
            context_header
            if (not chunks or not context_header)
            else make_continuation_header(context_header)
        )
        return ParsedChunk(
            content=content,
            context_header=chunk_header,
            seq=seq,
            start=start,
            end=end,
            metadata={
                **metadata,
                "strategy": metadata.get("strategy", "recursive"),
                "seq": seq,
                "sentence_aware": True,
                "content_kind": kind,
                "structure_like": kind != "text",
                "preserve_line_boundaries": kind != "text",
            },
            chunk_type="text",
            source_section=source_section,
        )

    for i, unit in enumerate(units):
        unit_len = len(unit.text)

        # Handle units that exceed chunk_size (e.g., single long sentence)
        if unit_len > chunk_size and unit.unit_type not in ("protected",):
            # Emit current buffer first
            chunk = _emit(buffer)
            if chunk is not None:
                chunks.append(chunk)
            buffer = []
            buffer_len = 0

            # Split this long sentence
            sub_units = split_long_sentence(unit.text, unit.start, chunk_size)
            for su in sub_units:
                su.is_paragraph_start = unit.is_paragraph_start and su.start == unit.start
                if su.unit_type == "hard_split":
                    # Still too large — emit directly
                    chunk = _emit([su])
                    if chunk is not None:
                        chunk.metadata["forced_boundary_split"] = True
                        chunks.append(chunk)
                else:
                    # Try to pack into buffer
                    if buffer and buffer_len + len(su.text) > chunk_size:
                        chunk = _emit(buffer)
                        if chunk is not None:
                            chunks.append(chunk)
                        buffer = []
                        buffer_len = 0
                    buffer.append(su)
                    buffer_len += len(su.text)
            continue

        # Protected units: emit buffer, then emit protected as standalone
        if unit.unit_type == "protected":
            chunk = _emit(buffer)
            if chunk is not None:
                chunks.append(chunk)
            buffer = []
            buffer_len = 0
            # Emit protected unit directly
            chunk = _emit([unit])
            if chunk is not None:
                chunks.append(chunk)
            continue

        # Normal unit: check if it fits in current buffer
        if buffer and buffer_len + unit_len > chunk_size:
            # Emit current buffer
            prev_chunk = _emit(buffer)
            if prev_chunk is not None:
                chunks.append(prev_chunk)

                # Calculate overlap: last 1 complete sentence/paragraph from prev chunk
                if chunk_overlap > 0:
                    overlap_unit = _select_overlap_unit(buffer)
                    if overlap_unit is not None:
                        # Check that overlap won't cause bad start in next chunk
                        if not is_bad_chunk_start(
                            overlap_unit.text,
                            is_paragraph_start=overlap_unit.is_paragraph_start,
                        ):
                            # Check if overlap + current unit fit in one chunk
                            if len(overlap_unit.text) + unit_len <= chunk_size:
                                buffer = [overlap_unit, unit]
                                buffer_len = len(overlap_unit.text) + unit_len
                                continue
                            # Overlap + current unit too big — skip overlap,
                            # start fresh with current unit only

            # Start fresh buffer with current unit
            buffer = [unit]
            buffer_len = unit_len
        else:
            buffer.append(unit)
            buffer_len += unit_len

    # Emit remaining buffer
    chunk = _emit(buffer)
    if chunk is not None:
        chunks.append(chunk)

    return chunks


def _select_overlap_unit(buffer: list[TextUnit]) -> TextUnit | None:
    """Select the last suitable unit from buffer for sentence-level overlap.

    Only use sentence or paragraph units; skip protected and hard_split units.
    Prefer the last sentence unit.
    """
    for unit in reversed(buffer):
        if unit.unit_type in ("sentence", "paragraph"):
            # Don't overlap if the unit is very short (noise)
            if len(unit.text.strip()) >= 8:
                return unit
    return None


# ---------------------------------------------------------------------------
# is_bad_chunk_start
# ---------------------------------------------------------------------------


def is_bad_chunk_start(text: str, is_paragraph_start: bool = False) -> bool:
    """Check whether a chunk starts at a bad (mid-sentence) position.

    Categories:
    1. Leading punctuation: ，。；、：）]》
    2. Reference fragments: "等[19]", "][", "）"
    3. Connectives (only bad if NOT paragraph start): 且, 并, 与, etc.
    4. Half words, half paths, half URLs
    """
    value = (text or "").strip()
    if not value:
        return False

    # Category 1: leading punctuation
    if value[0] in _LEADING_PUNCT_CHARS:
        return True

    # Category 2: reference fragments
    if _REF_FRAGMENT_START_RE.match(value):
        return True

    # Category 3: connectives (only when not a genuine paragraph start)
    if not is_paragraph_start:
        m = _BAD_CONNECTIVE_RE.match(value)
        if m:
            return True
        m = _BAD_EN_CONNECTIVE_RE.match(value)
        if m:
            return True

    # Category 4: half English word / path / URL at start
    # Check if it starts with lowercase alpha or path-like chars
    first_ch = value[0]
    if first_ch.islower() and not value[0:2].isalpha():
        # Single lowercase letter followed by non-alpha could be half a word
        pass  # too aggressive — only detect clear cases
    if first_ch == "/" or first_ch == "\\":
        if _HALF_PATH_RE.match(value):
            return True

    return False


# ---------------------------------------------------------------------------
# repair_bad_start
# ---------------------------------------------------------------------------


def repair_bad_start(
    chunks: list[ParsedChunk], cfg: ChunkerConfig | None = None
) -> list[ParsedChunk]:
    """Repair chunks that have bad starts by merging or re-packing.

    Strategy (in priority order):
    1. Merge with previous chunk if combined size is acceptable.
    2. Mark as bad_boundary if unrepairable.
    """
    if not chunks:
        return []

    cfg = cfg or ChunkerConfig()
    max_merge_size = int(cfg.chunk_size * 1.4)

    out: list[ParsedChunk] = []
    for i, chunk in enumerate(chunks):
        if chunk.chunk_type != "text" or chunk.metadata.get("content_kind") != "text":
            out.append(chunk)
            continue

        is_para_start = chunk.metadata.get("is_paragraph_start", False)
        if not is_bad_chunk_start(chunk.content, is_paragraph_start=is_para_start):
            out.append(chunk)
            continue

        # Bad start detected — try merging with previous
        prev = out[-1] if out else None
        if prev is not None and prev.chunk_type == "text":
            merged_content = sanitize_text_for_postgres(f"{prev.content}\n{chunk.content}")
            if len(merged_content) <= max_merge_size:
                prev.content = merged_content
                prev.end = max(prev.end, chunk.end)
                prev.metadata = {
                    **(prev.metadata or {}),
                    "boundary_postprocessed": True,
                    "bad_start_repaired": True,
                }
                continue

        # Cannot merge — mark as bad boundary
        chunk.metadata = {
            **(chunk.metadata or {}),
            "bad_boundary": True,
            "boundary_postprocessed": True,
        }
        out.append(chunk)

    # Re-sequence
    for seq, chunk in enumerate(out):
        chunk.seq = seq
        chunk.metadata["seq"] = seq

    return out
