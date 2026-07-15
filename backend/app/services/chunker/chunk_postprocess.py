"""Post-split boundary cleanup for ParsedChunk lists.

After the sentence-unit packing refactor, the primary post-processing is:
1. Detect and repair bad chunk starts (merge-first, not strip-first).
2. Merge short orphan text chunks.
3. Strip leading punctuation is now a LAST RESORT, not the primary fix.
"""

from __future__ import annotations

from app.services.chunker.boundary_utils import (
    is_leading_punctuation,
    is_plain_text_boundary_target,
    is_sentence_end,
    strip_invalid_leading_punctuation,
)
from app.services.chunker.heading_utils import is_caption_text, looks_like_section_heading
from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.chunker.text_unit import is_bad_chunk_start
from app.services.content_cleaning import clear_embedding_skip_metadata, normalize_chunk_text
from app.utils.text import sanitize_text_for_postgres


def _is_short_orphan(chunk: ParsedChunk, min_chars: int = 80) -> bool:
    text = sanitize_text_for_postgres(chunk.content or "").strip()
    meta = chunk.metadata or {}
    if not is_plain_text_boundary_target(meta, chunk.chunk_type):
        return False
    if not text or len(text) >= min_chars:
        return False
    if looks_like_section_heading(text, meta) or is_caption_text(text):
        return False
    if text.startswith(("-", "*", "+", ">")):
        return False
    return True


def _merge_metadata(base: dict, extra: dict, *, reason: str) -> dict:
    merged = clear_embedding_skip_metadata(base)
    merged["boundary_postprocessed"] = True
    merged.setdefault("merged_from", [])
    if isinstance(merged["merged_from"], list):
        merged["merged_from"].append({"reason": reason, "metadata": clear_embedding_skip_metadata(extra)})
    return merged


def _merge_into_previous(prev: ParsedChunk, chunk: ParsedChunk, *, reason: str) -> None:
    prev.content = normalize_chunk_text(f"{prev.content}\n{chunk.content}")
    prev.end = max(prev.end, chunk.end)
    prev.metadata = _merge_metadata(prev.metadata or {}, chunk.metadata or {}, reason=reason)


def _merge_into_next(next_chunk: ParsedChunk, chunk: ParsedChunk, *, reason: str) -> None:
    next_chunk.content = normalize_chunk_text(f"{chunk.content}\n{next_chunk.content}")
    next_chunk.start = min(next_chunk.start, chunk.start)
    next_chunk.metadata = _merge_metadata(next_chunk.metadata or {}, chunk.metadata or {}, reason=reason)


def _can_merge(a: ParsedChunk | None, b: ParsedChunk) -> bool:
    if a is None:
        return False
    if a.chunk_type != "text" or b.chunk_type != "text":
        return False
    if (a.context_header or "") != (b.context_header or ""):
        return False
    return is_plain_text_boundary_target(a.metadata, a.chunk_type)


def postprocess_chunk_boundaries(chunks: list[ParsedChunk], cfg: ChunkerConfig | None = None) -> list[ParsedChunk]:
    """Clean natural-language chunk boundaries after strategy splitting.

    Approach (ordered by priority):
    1. **Bad start repair** — if a chunk starts mid-sentence, merge with
       previous chunk.  This is the PRIMARY fix; stripping punctuation is
       only a last resort.
    2. **Leading punctuation** — if a chunk STILL has leading punctuation
       (should be rare after sentence-unit packing), strip ONLY when it
       cannot be merged.
    3. **Short orphan merge** — merge very short text chunks into neighbors.
    """
    if not chunks:
        return []

    cfg = cfg or ChunkerConfig()
    max_merge_size = int(cfg.chunk_size * 1.3)
    min_orphan = 80
    out: list[ParsedChunk] = []

    for index, chunk in enumerate(chunks):
        if not is_plain_text_boundary_target(chunk.metadata, chunk.chunk_type):
            out.append(chunk)
            continue

        chunk.content = normalize_chunk_text(chunk.content)
        if not chunk.content:
            continue

        prev = out[-1] if out else None
        next_chunk = chunks[index + 1] if index + 1 < len(chunks) else None

        # ── 1. Bad start repair (merge-first) ──
        is_para_start = chunk.metadata.get("is_paragraph_start", False)
        if is_bad_chunk_start(chunk.content, is_paragraph_start=is_para_start):
            if prev is not None and _can_merge(prev, chunk):
                merged_content = sanitize_text_for_postgres(f"{prev.content}\n{chunk.content}")
                if len(merged_content) <= max_merge_size:
                    _merge_into_previous(prev, chunk, reason="bad_start_repair")
                    continue
            # Cannot merge — mark as bad boundary (last resort)
            chunk.metadata = {
                **(chunk.metadata or {}),
                "bad_boundary": True,
                "boundary_postprocessed": True,
            }

        # ── 2. Leading punctuation (last resort only) ──
        # After sentence-unit packing, this should be extremely rare.
        if is_leading_punctuation(chunk.content):
            if prev is not None and _can_merge(prev, chunk):
                merged_content = sanitize_text_for_postgres(f"{prev.content} {chunk.content}")
                if len(merged_content) <= max_merge_size:
                    _merge_into_previous(prev, chunk, reason="leading_punct_merge")
                    continue
            # Cannot merge — strip as absolute last resort
            stripped = strip_invalid_leading_punctuation(chunk.content)
            if stripped and stripped != chunk.content:
                chunk.content = stripped
                chunk.metadata = {
                    **(chunk.metadata or {}),
                    "leading_punctuation_removed": True,
                    "boundary_postprocessed": True,
                }
            if not chunk.content:
                continue

        # ── 3. Short orphan merge ──
        if _is_short_orphan(chunk, min_orphan):
            previous_merged_size = (
                len(sanitize_text_for_postgres(f"{prev.content}\n{chunk.content}"))
                if prev is not None
                else 0
            )
            next_merged_size = (
                len(sanitize_text_for_postgres(f"{chunk.content}\n{next_chunk.content}"))
                if next_chunk is not None
                else 0
            )
            if (
                _can_merge(prev, chunk)
                and not is_sentence_end(prev.content)
                and previous_merged_size <= max_merge_size
            ):
                _merge_into_previous(prev, chunk, reason="short_orphan_previous")
                continue
            if (
                next_chunk is not None
                and _can_merge(next_chunk, chunk)
                and next_merged_size <= max_merge_size
            ):
                _merge_into_next(next_chunk, chunk, reason="short_orphan_next")
                continue
            if _can_merge(prev, chunk) and previous_merged_size <= max_merge_size:
                _merge_into_previous(prev, chunk, reason="short_orphan_previous_fallback")
                continue
            meta = clear_embedding_skip_metadata(chunk.metadata or {})
            meta.update({
                "low_information": True,
                "orphan_short_chunk": True,
                "skip_embedding_recommended": True,
                "skip_reason": "orphan_short_chunk",
            })
            chunk.metadata = meta

        out.append(chunk)

    for seq, chunk in enumerate(out):
        chunk.seq = seq
        chunk.metadata["seq"] = seq
    return out
