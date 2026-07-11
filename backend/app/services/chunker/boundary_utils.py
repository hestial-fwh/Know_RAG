"""Generic chunk boundary utilities for natural prose splitting.

These helpers intentionally avoid document-specific phrases.  They provide
sentence/line-aware boundaries while respecting protected spans such as code,
tables, formulas, links and directory trees.

Note: ``strip_invalid_leading_punctuation`` and ``find_best_backward_boundary`` /
``find_best_forward_start`` are **deprecated**.  Sentence-unit packing
(``text_unit.py``) should be used instead of character-window boundary search.
The deprecated functions are kept only for backward compatibility with
``chunk_service.py``'s legacy post-processing path and are expected to trigger
rarely (if ever) after the sentence-unit refactor.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.chunker.protected_patterns import ProtectedSpan, is_protected_range
from app.utils.text import sanitize_text_for_postgres


LEADING_PUNCTUATION = "。，、；：:,.!?！？)]）】》\"'"
STRONG_SENTENCE_END_RE = re.compile(r"[。！？!?](?:[\"'\"')\)\]】》]+)?$")
WEAK_SENTENCE_END_RE = re.compile(r"[；；](?:[\"'\"')\)\]】》]+)?$")
EN_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?][\"')\]]?\s+")
URL_OR_PATH_RE = re.compile(r"(https?://\S+|[\w.-]+(?:/[\w.@+-]+)+|[\w.@+-]+\.[A-Za-z0-9]{1,8})")
IDENTIFIER_EDGE_RE = re.compile(r"[A-Za-z0-9_./-]$")
IDENTIFIER_START_RE = re.compile(r"^[A-Za-z0-9_./-]")


def _protected(start: int, end: int, protected_spans: list[ProtectedSpan] | None) -> bool:
    return bool(protected_spans and is_protected_range(start, end, protected_spans))


def is_leading_punctuation(text: str) -> bool:
    """Return True when prose starts with an isolated sentence-internal mark."""
    value = sanitize_text_for_postgres(text or "").lstrip()
    if not value:
        return False
    if value.startswith(("```", "|", ">", "-", "*", "+")):
        return False
    if re.match(r"^\d+[.)、]\s+", value):
        return False
    return value[0] in LEADING_PUNCTUATION


def strip_invalid_leading_punctuation(text: str) -> str:
    """[DEPRECATED] Strip leading punctuation.

    This was previously used as the primary boundary fix, but it only masked
    the symptom (chunks starting mid-sentence).  The new sentence-unit packing
    in ``text_unit.py`` ensures chunks start on complete sentences, so this
    function should rarely be needed.

    Kept for backward compatibility with ``chunk_service.py`` legacy path.
    """
    value = sanitize_text_for_postgres(text or "")
    leading_len = len(value) - len(value.lstrip())
    prefix = value[:leading_len]
    rest = value[leading_len:]
    while rest and rest[0] in LEADING_PUNCTUATION:
        rest = rest[1:].lstrip()
    return f"{prefix}{rest}".strip()


def is_sentence_end(text: str) -> bool:
    """Detect strong or acceptable weak sentence endings."""
    value = sanitize_text_for_postgres(text or "").rstrip()
    if not value:
        return False
    return bool(STRONG_SENTENCE_END_RE.search(value) or WEAK_SENTENCE_END_RE.search(value) or re.search(r"[.!?][\"')\]]?$", value))


def _inside_url_or_identifier(text: str, pos: int) -> bool:
    left = text[max(0, pos - 80):pos]
    right = text[pos:min(len(text), pos + 80)]
    window = left + right
    rel = len(left)
    for match in URL_OR_PATH_RE.finditer(window):
        if match.start() < rel < match.end():
            return True
    if left and right and IDENTIFIER_EDGE_RE.search(left[-1]) and IDENTIFIER_START_RE.search(right[:1]):
        return True
    # Avoid splitting decimals or version numbers.
    if pos > 0 and pos < len(text) and text[pos - 1].isdigit() and text[pos : pos + 1] == ".":
        return True
    return False


def _valid_boundary(text: str, pos: int, protected_spans: list[ProtectedSpan] | None) -> bool:
    if pos <= 0 or pos >= len(text):
        return True
    if _protected(max(pos - 1, 0), pos, protected_spans):
        return False
    return not _inside_url_or_identifier(text, pos)


def _candidate_positions(text: str, start: int, end: int, pattern: str) -> list[int]:
    return [start + m.end() for m in re.finditer(pattern, text[start:end])]


# ---------------------------------------------------------------------------
# Deprecated: character-window boundary search functions.
# These are replaced by sentence-unit packing in text_unit.py.
# Kept only to avoid breaking imports from chunk_service.py.
# ---------------------------------------------------------------------------


def find_best_backward_boundary(
    text: str,
    start: int,
    max_end: int,
    min_end: int,
    protected_spans: list[ProtectedSpan] | None = None,
) -> int:
    """[DEPRECATED] Find the best natural boundary inside [min_end, max_end].

    Replaced by ``text_unit.split_text_to_sentence_units``.
    """
    text_len = len(text)
    max_end = min(max(max_end, start + 1), text_len)
    min_end = min(max(min_end, start + 1), max_end)
    tiers: list[list[int]] = [
        _candidate_positions(text, min_end, max_end, r"\n\s*\n"),
        _candidate_positions(text, min_end, max_end, r"\n"),
        _candidate_positions(text, min_end, max_end, r"[。！？]"),
        _candidate_positions(text, min_end, max_end, r"[.!?][\"')\]]?(?=\s|$)"),
        _candidate_positions(text, min_end, max_end, r"[；；]"),
        _candidate_positions(text, min_end, max_end, r"[，,、]"),
        _candidate_positions(text, min_end, max_end, r"\s+"),
    ]
    for positions in tiers:
        for pos in reversed(positions):
            if pos > start and _valid_boundary(text, pos, protected_spans):
                return pos
    return max_end


def find_best_forward_start(text: str, rough_start: int, max_lookahead: int = 80) -> int:
    """[DEPRECATED] Move a rough overlap start forward to a safer prose boundary.

    Replaced by sentence-level overlap in ``text_unit.pack_units_into_chunks``.
    """
    text_len = len(text)
    rough_start = min(max(rough_start, 0), text_len)
    if rough_start >= text_len:
        return text_len
    window_end = min(text_len, rough_start + max_lookahead)
    window = text[rough_start:window_end]

    for pattern in (r"\n\s*\n\s*", r"\n\s*", r"[。！？.!?；；]\s*", r"\s+"):
        match = re.search(pattern, window)
        if match:
            candidate = rough_start + match.end()
            while candidate < text_len and text[candidate].isspace():
                candidate += 1
            if candidate < text_len and not is_leading_punctuation(text[candidate:]):
                return candidate

    if is_leading_punctuation(text[rough_start:]):
        stripped = strip_invalid_leading_punctuation(text[rough_start:])
        if stripped:
            return text.find(stripped, rough_start, window_end) if stripped in text[rough_start:window_end] else rough_start
    return rough_start


def is_plain_text_boundary_target(metadata: dict[str, Any] | None = None, chunk_type: str = "text") -> bool:
    meta = metadata or {}
    if (chunk_type or "text") != "text":
        return False
    if meta.get("structure_like") or meta.get("content_kind") in {"code_block", "directory_tree", "code_structure", "project_structure"}:
        return False
    return True
