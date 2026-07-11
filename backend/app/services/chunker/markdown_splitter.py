import re

from app.services.chunker.heading_splitter import has_markdown_headings, split_by_headings
from app.services.chunker.heading_utils import is_caption_text, is_page_noise_text, looks_like_section_heading
from app.services.chunker.heuristic_splitter import has_heuristic_structure, split_by_heuristics
from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.chunker.recursive_splitter import split_recursive
from app.services.chunker.chunk_postprocess import postprocess_chunk_boundaries
from app.services.content_cleaning import clear_embedding_skip_metadata
from app.utils.text import sanitize_text_for_postgres


_CODE_FENCE_RE = re.compile(r"^\s*```[\s\S]*?```\s*$")
_EQUATION_LABEL_RE = re.compile(r"^\s*(公式|equation|eq\.)\s*[\d一二三四五六七八九十]+", re.I)
_KEEP_SHORT_PREFIX_RE = re.compile(r"^\s*(注意|结论|摘要|说明|提示|重要|Note|Conclusion)\s*[:：]", re.I)
_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)、])\s+\S+")
_MARKDOWN_TABLE_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")


def _is_code_block_chunk(chunk: ParsedChunk) -> bool:
    meta = chunk.metadata or {}
    return meta.get("content_kind") == "code_block" or bool(_CODE_FENCE_RE.search(chunk.content or ""))


def is_orphan_short_text_chunk(content: str, metadata: dict | None = None) -> bool:
    """Detect ordinary short text fragments that should not stand alone."""
    value = sanitize_text_for_postgres(content or "").strip()
    meta = metadata or {}
    if not value:
        return False
    if len(value) >= 120:
        return False
    if meta.get("structure_like") or meta.get("content_kind") in {"directory_tree", "project_structure", "code_structure", "code_block"}:
        return False
    if looks_like_section_heading(value, meta) or is_caption_text(value) or is_page_noise_text(value):
        return False
    if _EQUATION_LABEL_RE.search(value):
        return False
    if _KEEP_SHORT_PREFIX_RE.search(value):
        return False
    if len(_LIST_ITEM_RE.findall(value)) >= 2:
        return False
    if _MARKDOWN_TABLE_RE.search(value):
        return False
    caption_keys = ("caption", "table_caption", "figure_caption", "image_caption", "bound_caption")
    if any(meta.get(key) for key in caption_keys):
        return False
    return len(value) < 80 or (len(value) < 120 and not re.search(r"[。！？!?；;]$", value))


def _merge_short_text_into_chunk(target: ParsedChunk, short: ParsedChunk, *, position: str) -> None:
    text = sanitize_text_for_postgres(short.content or "").strip()
    if not text:
        return
    target.metadata = dict(target.metadata or {})
    if position == "lead":
        existing = sanitize_text_for_postgres(target.metadata.get("lead_in_text") or "").strip()
        target.metadata["lead_in_text"] = "\n".join(part for part in [existing, text] if part)
        target.start = min(target.start, short.start)
    else:
        existing = sanitize_text_for_postgres(target.metadata.get("tail_note_text") or "").strip()
        target.metadata["tail_note_text"] = "\n".join(part for part in [existing, text] if part)
        target.end = max(target.end, short.end)
    target.metadata["absorbed_orphan_short_text"] = True


def post_process_orphan_short_chunks(chunks: list[ParsedChunk]) -> list[ParsedChunk]:
    """Merge or hide orphan short text chunks after strategy splitting."""
    if not chunks:
        return []
    out: list[ParsedChunk] = []
    for index, chunk in enumerate(chunks):
        if chunk.chunk_type != "text" or _is_code_block_chunk(chunk):
            out.append(chunk)
            continue
        if not is_orphan_short_text_chunk(chunk.content, chunk.metadata):
            out.append(chunk)
            continue
        prev_chunk = out[-1] if out else None
        next_chunk = chunks[index + 1] if index + 1 < len(chunks) else None
        if next_chunk is not None and _is_code_block_chunk(next_chunk):
            _merge_short_text_into_chunk(next_chunk, chunk, position="lead")
            continue
        if prev_chunk is not None and _is_code_block_chunk(prev_chunk):
            _merge_short_text_into_chunk(prev_chunk, chunk, position="tail")
            continue
        if prev_chunk is not None and prev_chunk.chunk_type == "text" and prev_chunk.context_header == chunk.context_header and not _is_code_block_chunk(prev_chunk):
            prev_chunk.content = sanitize_text_for_postgres(f"{prev_chunk.content}\n{chunk.content}")
            prev_chunk.end = max(prev_chunk.end, chunk.end)
            prev_meta = clear_embedding_skip_metadata(prev_chunk.metadata or {})
            prev_meta["absorbed_orphan_short_text"] = True
            prev_chunk.metadata = prev_meta
            continue
        if next_chunk is not None and next_chunk.chunk_type == "text" and next_chunk.context_header == chunk.context_header and not _is_code_block_chunk(next_chunk):
            next_chunk.content = sanitize_text_for_postgres(f"{chunk.content}\n{next_chunk.content}")
            next_chunk.start = min(next_chunk.start, chunk.start)
            next_meta = clear_embedding_skip_metadata(next_chunk.metadata or {})
            next_meta["absorbed_orphan_short_text"] = True
            next_chunk.metadata = next_meta
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


def split_markdown(text: str, cfg: ChunkerConfig) -> list[ParsedChunk]:
    """Unified WeKnora-style strategy dispatch for Markdown-like content."""
    def finish(items: list[ParsedChunk]) -> list[ParsedChunk]:
        return postprocess_chunk_boundaries(post_process_orphan_short_chunks(items), cfg)

    strategy = (cfg.strategy or "auto").lower()
    if strategy == "legacy":
        return finish(split_recursive(text, cfg, metadata={"source": "parser", "strategy": "legacy", "heading_path": []}))
    if strategy == "heading":
        return finish(split_by_headings(text, cfg))
    if strategy == "heuristic":
        return finish(split_by_heuristics(text, cfg))
    if strategy == "recursive":
        return finish(split_recursive(text, cfg, metadata={"source": "parser", "strategy": "recursive", "heading_path": []}))
    if has_markdown_headings(text):
        try:
            return finish(split_by_headings(text, cfg))
        except Exception:
            return finish(split_recursive(text, cfg, metadata={"source": "parser", "strategy": "recursive", "heading_path": []}))
    if has_heuristic_structure(text):
        try:
            return finish(split_by_heuristics(text, cfg))
        except Exception:
            return finish(split_recursive(text, cfg, metadata={"source": "parser", "strategy": "recursive", "heading_path": []}))
    return finish(split_recursive(text, cfg, metadata={"source": "parser", "strategy": "recursive", "heading_path": []}))
