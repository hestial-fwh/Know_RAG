"""Unified Canonical Markdown -> WeKnora parent/child chunk planning.

This module is intentionally storage-agnostic.  It fixes three architectural
problems in the old Know_RAG chain:

1. Every file format now enters the same parent/child splitter through
   Canonical Markdown.
2. Structural fragments (horizontal rule + one-line heading) are never emitted
   as standalone retrieval children.
3. Image references are materialized as image children whose searchable content
   combines section context, caption, VLM description, OCR and nearby prose,
   while raw media metadata stays in the asset sidecar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from app.services.chunker.heading_utils import is_page_noise_text
from app.services.chunker.models import ChunkerConfig
from app.services.chunker.parent_child_splitter import split_parent_child_result
from app.services.document_parser.canonical_document import (
    CanonicalAsset,
    CanonicalDocument,
    find_image_asset_refs,
)


_FENCE_START_RE = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,})([^\n]*)\n")
_HEADING_LINE_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_HORIZONTAL_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_HTML_TABLE_RE = re.compile(r"(?is)^\s*<table\b.*?</table>\s*$")
_HTML_TABLE_FIND_RE = re.compile(r"(?is)<table\b.*?</table>")
_MD_TABLE_DELIMITER_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_FENCE_LINE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


@dataclass
class CanonicalParentDraft:
    parent_index: int
    content: str
    context_header: str
    start: int
    end: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalChildDraft:
    content: str
    chunk_type: str
    parent_index: int | None
    context_header: str
    start: int
    end: int
    source_child_seq: int
    metadata: dict[str, Any] = field(default_factory=dict)
    image_info: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalChunkPlan:
    markdown_content: str
    parents: list[CanonicalParentDraft]
    children: list[CanonicalChildDraft]
    assets: dict[str, CanonicalAsset] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Canonical Markdown normalization
# ---------------------------------------------------------------------------


def _preserve(value: str) -> str:
    return (value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def _split_long_line(line: str, max_chars: int) -> list[str]:
    """Keep one physical code line atomic.

    Old structure quality is prioritized over cosmetic chunk-size uniformity.
    Character-slicing a long code/JSON/URL line can corrupt syntax and make the
    stored source impossible to reconstruct.  Oversized atomic lines are kept
    intact and may produce an oversized fenced child; callers can surface that
    fact through normal chunk-length diagnostics instead of mutating content.
    """
    return [line]


def _split_fenced_block(block: str, child_size: int) -> list[str]:
    """Split an oversized fenced code block into valid fenced blocks.

    The old runtime protected the entire code block, which created a tiny
    heading-only child followed by a multi-thousand-character code child.  We
    normalize the *canonical source* before splitting, so every generated child
    remains an exact slice of that normalized source and parent-window lookup
    continues to work.
    """
    value = _preserve(block)
    lines = value.splitlines(keepends=True)
    if len(lines) < 2:
        return [value]

    first = lines[0]
    marker_match = re.match(r"\s*(`{3,}|~{3,})", first)
    if not marker_match:
        return [value]
    marker = marker_match.group(1)
    closing_index = None
    for index in range(len(lines) - 1, 0, -1):
        stripped = lines[index].lstrip()
        if stripped.startswith(marker[0] * len(marker)):
            closing_index = index
            break
    if closing_index is None:
        return [value]

    opening = first if first.endswith("\n") else first + "\n"
    closing = lines[closing_index].strip("\r\n") or marker
    closing = closing + "\n"
    body_lines = lines[1:closing_index]

    # Reserve space for fences and for a short heading/context prelude that may
    # be merged into the first code chunk by the semantic boundary repair.
    reserve = min(96, max(32, child_size // 4))
    body_limit = max(80, child_size - len(opening) - len(closing) - reserve)

    normalized_lines: list[str] = []
    for line in body_lines:
        if len(line) <= body_limit:
            normalized_lines.append(line)
            continue
        newline = "\n" if line.endswith("\n") else ""
        core = line[:-1] if newline else line
        pieces = _split_long_line(core, body_limit)
        normalized_lines.extend(piece + ("\n" if idx < len(pieces) - 1 or newline else "") for idx, piece in enumerate(pieces))

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in normalized_lines:
        if current and current_len + len(line) > body_limit:
            body = "".join(current)
            if body and not body.endswith("\n"):
                body += "\n"
            chunks.append(opening + body + closing)
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current or not chunks:
        body = "".join(current)
        if body and not body.endswith("\n"):
            body += "\n"
        chunks.append(opening + body + closing)
    return chunks


def split_oversize_fenced_blocks(markdown: str, child_size: int) -> str:
    """Normalize oversized code fences before WeKnora two-pass splitting."""
    text = _preserve(markdown)
    if not text:
        return ""

    out: list[str] = []
    cursor = 0
    while cursor < len(text):
        match = _FENCE_START_RE.search(text, cursor)
        if not match:
            out.append(text[cursor:])
            break
        out.append(text[cursor : match.start()])
        marker = match.group(1)
        close_re = re.compile(rf"(?m)^[ \t]*{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$")
        close_match = close_re.search(text, match.end())
        if not close_match:
            out.append(text[match.start() :])
            break
        end = close_match.end()
        if end < len(text) and text[end] == "\n":
            end += 1
        block = text[match.start() : end]
        if len(block) > child_size:
            pieces = _split_fenced_block(block, child_size)
            out.append("\n".join(piece.rstrip("\n") for piece in pieces) + "\n")
        else:
            out.append(block)
        cursor = end
    return "".join(out)


def _is_table_delimiter_line(line: str) -> bool:
    return bool(_MD_TABLE_DELIMITER_RE.match(line.strip()))


def _split_markdown_table_block(block: str, child_size: int) -> list[str]:
    lines = [line for line in _preserve(block).splitlines() if line.strip()]
    if len(lines) < 2 or not _is_table_delimiter_line(lines[1]):
        return [block]
    header, delimiter = lines[0], lines[1]
    rows = lines[2:]
    prefix = f"{header}\n{delimiter}\n"
    if len(block) <= child_size or not rows:
        return [block]

    row_limit = max(80, child_size - len(prefix) - min(64, child_size // 5))
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for row in rows:
        row_text = row + "\n"
        if current and current_len + len(row_text) > row_limit:
            chunks.append(prefix + "".join(current).rstrip("\n"))
            current = []
            current_len = 0
        current.append(row_text)
        current_len += len(row_text)
    if current:
        chunks.append(prefix + "".join(current).rstrip("\n"))
    return chunks or [block]


def split_oversize_markdown_tables(markdown: str, child_size: int) -> str:
    """Split long pipe tables into valid repeated-header Markdown tables."""
    lines = _preserve(markdown).splitlines(keepends=True)
    if not lines:
        return ""
    out: list[str] = []
    index = 0
    fence = ""
    while index < len(lines):
        line = lines[index]
        fence_match = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if fence:
            out.append(line)
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = ""
            index += 1
            continue
        if fence_match:
            fence = fence_match.group(1)
            out.append(line)
            index += 1
            continue

        if index + 1 < len(lines) and "|" in line and _is_table_delimiter_line(lines[index + 1]):
            end = index + 2
            while end < len(lines):
                candidate = lines[end]
                if not candidate.strip() or "|" not in candidate:
                    break
                end += 1
            block = "".join(lines[index:end])
            pieces = _split_markdown_table_block(block, child_size)
            out.append("\n\n".join(piece.strip("\n") for piece in pieces))
            if end < len(lines) and lines[end].strip():
                out.append("\n")
            index = end
            continue

        out.append(line)
        index += 1
    return "".join(out)


def normalize_canonical_markdown(markdown: str, child_size: int) -> str:
    text = split_oversize_fenced_blocks(markdown, child_size)
    text = split_oversize_markdown_tables(text, child_size)
    return text


# ---------------------------------------------------------------------------
# Child classification and asset materialization
# ---------------------------------------------------------------------------


def _meaningful_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in _preserve(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if _HORIZONTAL_RULE_RE.match(line):
            continue
        if _HEADING_LINE_RE.match(line):
            continue
        if line in {"```", "~~~"}:
            continue
        lines.append(line)
    return lines


def is_structural_only(text: str) -> bool:
    """True for separators/headings with no retrievable semantic body."""
    return not _meaningful_lines(text)


def _is_native_markdown_metadata(metadata: dict[str, Any]) -> bool:
    return str(metadata.get("canonical_source") or "").strip().lower() == "native_markdown"


def _strip_parser_page_noise_from_text(text: str, *, native_markdown: bool) -> tuple[str, int]:
    """Conservatively remove parser page-number noise from materialized text.

    Native Markdown is authoritative and is never rewritten by this guard. For
    parser-derived canonical text we only remove page-noise lines outside fenced
    code when either the entire semantic body is page noise, or at least two such
    lines occur in the same child. This catches OCR/PDF footer fragments such as
    ``526 / 524 / 117`` without deleting ordinary prose or numeric data in code.
    """
    value = _preserve(text)
    if native_markdown or not value.strip():
        return value, 0

    lines = value.splitlines(keepends=True)
    fence = ""
    semantic_indexes: list[int] = []
    noise_indexes: list[int] = []
    heading_indexes: list[int] = []
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        fence_match = _FENCE_LINE_RE.match(raw)
        if fence:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = ""
            continue
        if fence_match:
            fence = fence_match.group(1)
            continue
        if not stripped or _HORIZONTAL_RULE_RE.match(stripped):
            continue
        if _HEADING_LINE_RE.match(stripped):
            heading_indexes.append(index)
            continue
        semantic_indexes.append(index)
        if is_page_noise_text(stripped):
            noise_indexes.append(index)

    if not semantic_indexes:
        return value, 0
    if len(noise_indexes) == len(semantic_indexes) and len(noise_indexes) >= 2:
        # Heading + only page numbers is still a non-retrieval fragment.
        return "", len(noise_indexes)
    if len(noise_indexes) < 2:
        return value, 0

    dropped = set(noise_indexes)
    cleaned = "".join(raw for index, raw in enumerate(lines) if index not in dropped)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, len(noise_indexes)


def _quality_gate_plain_text(
    text: str,
    *,
    metadata: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Final text-only quality gate before a child draft is materialized."""
    value = _preserve(text).strip()
    if not value:
        return "", metadata
    cleaned, dropped_noise_lines = _strip_parser_page_noise_from_text(
        value,
        native_markdown=_is_native_markdown_metadata(metadata),
    )
    if not cleaned or is_structural_only(cleaned):
        return "", metadata
    if dropped_noise_lines:
        metadata = {
            **metadata,
            "child_noise_guard_applied": True,
            "child_page_noise_lines_dropped": dropped_noise_lines,
        }
    return cleaned, metadata


def _is_pure_markdown_table(text: str) -> bool:
    lines = [line for line in _preserve(text).splitlines() if line.strip()]
    return len(lines) >= 2 and "|" in lines[0] and bool(_MD_TABLE_DELIMITER_RE.match(lines[1]))


def _is_pure_html_table(text: str) -> bool:
    return bool(_HTML_TABLE_RE.match(_preserve(text)))


def _is_pure_fenced_code(text: str) -> bool:
    value = _preserve(text).strip()
    return bool(re.match(r"^(?:```|~~~)[\s\S]*?(?:```|~~~)$", value))


def _last_heading(text: str) -> str:
    result = ""
    for line in _preserve(text).splitlines():
        match = _HEADING_LINE_RE.match(line)
        if match:
            result = f"{match.group(1)} {match.group(2).strip()}"
    return result


def _apply_heading_context_to_source_children(source_children) -> None:
    """Carry Markdown heading hierarchy across child boundaries.

    The WeKnora parent-child runtime intentionally uses the legacy splitter for
    both passes, so repeated code/table continuation chunks may not get a
    context header. Canonical Markdown gives us a stable heading stream; carry
    that hierarchy forward without emitting heading-only children.
    """
    stack: dict[int, str] = {}
    for child in sorted(source_children, key=lambda item: item.seq):
        context_stack = dict(stack)
        semantic_body_seen = False

        for raw in _preserve(child.content).splitlines():
            stripped = raw.strip()
            if not stripped or _HORIZONTAL_RULE_RE.match(stripped):
                continue

            match = _HEADING_LINE_RE.match(stripped)
            if match:
                level = len(match.group(1))
                stack[level] = f"{'#' * level} {match.group(2).strip()}"
                for deeper in list(stack):
                    if deeper > level:
                        stack.pop(deeper, None)
                # A heading at the beginning of a child defines that child's
                # context.  Later headings only update the stream for following
                # children; otherwise overlap can incorrectly label an earlier
                # chunk with the *last* heading it happens to contain.
                if not semantic_body_seen:
                    context_stack = dict(stack)
                continue

            semantic_body_seen = True

        inherited = "\n".join(context_stack[level] for level in sorted(context_stack))
        if inherited:
            child.context_header = inherited


def _join_context(*parts: str) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = _preserve(part).strip()
        if not value or value in seen:
            continue
        values.append(value)
        seen.add(value)
    return "\n".join(values)


def _truncate(value: str, limit: int) -> str:
    text = _preserve(value).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _asset_image_info(asset: CanonicalAsset) -> dict[str, Any]:
    info: dict[str, Any] = {
        "asset_id": asset.asset_id,
        "asset_type": asset.asset_type,
    }
    if asset.image_path:
        info["image_path"] = asset.image_path
    if asset.image_url:
        info["image_url"] = asset.image_url
    if asset.image_base64:
        info["image_base64"] = asset.image_base64
    if asset.page_no is not None:
        info["page_no"] = asset.page_no
    if asset.bbox:
        info["bbox"] = list(asset.bbox)
    if asset.caption:
        info["caption"] = asset.caption
    return info


def compose_image_child_content(
    asset: CanonicalAsset,
    *,
    context_header: str = "",
    local_context: str = "",
    child_size: int = 384,
) -> str:
    """Build the searchable content of one image child.

    VLM and OCR are intentionally injected here, not into Canonical Markdown.
    This keeps the source structure clean while ensuring the image itself is a
    first-class retrieval unit.
    """
    max_total = max(1200, child_size * 4)
    parts: list[str] = []

    section = _join_context(context_header, asset.context_header)
    if section:
        parts.append(f"章节：{_truncate(section, 320)}")

    if asset.caption or asset.alt_text:
        parts.append(f"图片标题：{_truncate(asset.caption or asset.alt_text, 360)}")

    nearby = _join_context(local_context, asset.nearby_before, asset.nearby_after)
    if nearby:
        parts.append(f"上下文：{_truncate(nearby, 600)}")

    if asset.vlm_description:
        parts.append(f"图片描述：{_truncate(asset.vlm_description, 900)}")

    if asset.ocr_text:
        # OCR may be very long.  Keep enough exact text for BM25/id lookup while
        # avoiding multi-thousand-character image chunks.
        remaining = max(360, max_total - sum(len(part) for part in parts) - 64)
        parts.append(f"图片文字：{_truncate(asset.ocr_text, remaining)}")

    content = "\n\n".join(part for part in parts if part.strip())
    if not content:
        content = f"图片：{asset.alt_text or asset.asset_id}"
    return _truncate(content, max_total)


def _split_pipe_table_segments(text: str) -> list[tuple[str, str]]:
    lines = _preserve(text).splitlines(keepends=True)
    if not lines:
        return []
    out: list[tuple[str, str]] = []
    text_start = 0
    index = 0
    fence = ""
    while index < len(lines):
        line = lines[index]
        fence_match = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if fence:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = ""
            index += 1
            continue
        if fence_match:
            fence = fence_match.group(1)
            index += 1
            continue
        if index + 1 < len(lines) and "|" in line and _is_table_delimiter_line(lines[index + 1]):
            if index > text_start:
                out.append(("text", "".join(lines[text_start:index])))
            end = index + 2
            while end < len(lines) and lines[end].strip() and "|" in lines[end]:
                end += 1
            out.append(("table", "".join(lines[index:end])))
            index = end
            text_start = end
            continue
        index += 1
    if text_start < len(lines):
        out.append(("text", "".join(lines[text_start:])))
    return [(kind, value) for kind, value in out if value.strip()]


def _split_table_segments(text: str) -> list[tuple[str, str]]:
    value = _preserve(text)
    out: list[tuple[str, str]] = []
    cursor = 0
    for match in _HTML_TABLE_FIND_RE.finditer(value):
        before = value[cursor:match.start()]
        if before.strip():
            out.extend(_split_pipe_table_segments(before) or [("text", before)])
        out.append(("table", match.group(0)))
        cursor = match.end()
    tail = value[cursor:]
    if tail.strip():
        out.extend(_split_pipe_table_segments(tail) or [("text", tail)])
    return out or ([('text', value)] if value.strip() else [])


def _emit_table_child(
    children: list[CanonicalChildDraft],
    *,
    text: str,
    parent_index: int | None,
    context_header: str,
    start: int,
    end: int,
    source_child_seq: int,
    metadata: dict[str, Any],
) -> None:
    value = _preserve(text).strip()
    if not value:
        return
    children.append(
        CanonicalChildDraft(
            content=value,
            chunk_type="table",
            parent_index=parent_index,
            context_header=context_header,
            start=start,
            end=end,
            source_child_seq=source_child_seq,
            metadata={**metadata, "content_kind": "table"},
            image_info={"table_markdown": value} if not _is_pure_html_table(value) else {"table_html": value},
            flags={"content_kind": "table", "canonical_markdown": True},
        )
    )


def _emit_plain_child(
    children: list[CanonicalChildDraft],
    *,
    text: str,
    parent_index: int | None,
    context_header: str,
    start: int,
    end: int,
    source_child_seq: int,
    metadata: dict[str, Any],
) -> None:
    value = _preserve(text).strip()
    if not value or is_structural_only(value):
        return
    chunk_type = "table" if (_is_pure_markdown_table(value) or _is_pure_html_table(value)) else "text"
    content_kind = "code_block" if _is_pure_fenced_code(value) else ("table" if chunk_type == "table" else "text")
    if content_kind == "text":
        value, metadata = _quality_gate_plain_text(value, metadata=metadata)
        if not value:
            return
    children.append(
        CanonicalChildDraft(
            content=value,
            chunk_type=chunk_type,
            parent_index=parent_index,
            context_header=context_header,
            start=start,
            end=end,
            source_child_seq=source_child_seq,
            metadata={**metadata, "content_kind": content_kind},
            image_info={"table_markdown": value} if chunk_type == "table" else {},
            flags={"content_kind": content_kind, "canonical_markdown": True},
        )
    )


def _materialize_text_segment_with_assets(
    source_child,
    canonical: CanonicalDocument,
    text: str,
    *,
    child_size: int,
) -> list[CanonicalChildDraft]:
    text = _preserve(text)
    refs = find_image_asset_refs(text)
    base_meta = {
        **(source_child.metadata or {}),
        "canonical_markdown": True,
        "canonical_source_child_seq": source_child.seq,
        "canonical_source": canonical.metadata.get("canonical_source"),
        "canonical_source_format": canonical.source_format,
    }
    if not refs:
        out: list[CanonicalChildDraft] = []
        _emit_plain_child(
            out,
            text=text,
            parent_index=source_child.parent_index,
            context_header=source_child.context_header,
            start=source_child.start,
            end=source_child.end,
            source_child_seq=source_child.seq,
            metadata=base_meta,
        )
        return out

    children: list[CanonicalChildDraft] = []
    cursor = 0
    pending_structure = ""

    for asset_id, alt, start, end in refs:
        before = text[cursor:start]
        if before.strip():
            if is_structural_only(before):
                pending_structure = _join_context(pending_structure, before)
            else:
                _emit_plain_child(
                    children,
                    text=_join_context(pending_structure, before),
                    parent_index=source_child.parent_index,
                    context_header=source_child.context_header,
                    start=source_child.start,
                    end=source_child.end,
                    source_child_seq=source_child.seq,
                    metadata=base_meta,
                )
                pending_structure = ""

        asset = canonical.assets.get(asset_id)
        if asset is not None:
            local_heading = _last_heading(pending_structure)
            image_context = _join_context(source_child.context_header, local_heading)
            local_prose = ""
            # The remaining source-child text is often the best immediate
            # context for the image, but do not feed another asset marker.
            tail = text[end:]
            tail = re.sub(r"!\[[^\]]*\]\(asset://[^)]+\)", "", tail)
            if tail.strip() and not is_structural_only(tail):
                local_prose = _truncate(tail, 360)

            content = compose_image_child_content(
                asset,
                context_header=image_context,
                local_context=local_prose,
                child_size=child_size,
            )
            metadata = {
                **base_meta,
                **(asset.metadata or {}),
                "content_kind": "image",
                "asset_id": asset.asset_id,
                "asset_type": asset.asset_type,
                "asset_alt_text": alt or asset.alt_text,
                "caption": asset.caption,
                "ocr_text": asset.ocr_text,
                "vlm_description": asset.vlm_description,
                "page_no": asset.page_no,
                "bbox": list(asset.bbox) if asset.bbox else None,
                "source_block_order": asset.source_block_order,
                "image_semantics_in_child": True,
            }
            children.append(
                CanonicalChildDraft(
                    content=content,
                    chunk_type="image",
                    parent_index=source_child.parent_index,
                    context_header=image_context,
                    start=source_child.start,
                    end=source_child.end,
                    source_child_seq=source_child.seq,
                    metadata=metadata,
                    image_info=_asset_image_info(asset),
                    flags={
                        "content_kind": "image",
                        "canonical_markdown": True,
                        "has_image": True,
                        "has_ocr_text": bool(asset.ocr_text),
                        "has_vlm_description": bool(asset.vlm_description),
                        "index_content_enriched": True,
                    },
                )
            )
        cursor = end

    after = text[cursor:]
    if after.strip():
        if is_structural_only(after):
            pending_structure = _join_context(pending_structure, after)
        else:
            _emit_plain_child(
                children,
                text=_join_context(pending_structure, after),
                parent_index=source_child.parent_index,
                context_header=source_child.context_header,
                start=source_child.start,
                end=source_child.end,
                source_child_seq=source_child.seq,
                metadata=base_meta,
            )
            pending_structure = ""

    return children


def _materialize_source_child(
    source_child,
    canonical: CanonicalDocument,
    *,
    child_size: int,
) -> list[CanonicalChildDraft]:
    children: list[CanonicalChildDraft] = []
    base_meta = {
        **(source_child.metadata or {}),
        "canonical_markdown": True,
        "canonical_source_child_seq": source_child.seq,
        "canonical_source": canonical.metadata.get("canonical_source"),
        "canonical_source_format": canonical.source_format,
    }
    for kind, segment in _split_table_segments(source_child.content):
        if kind == "table":
            _emit_table_child(
                children,
                text=segment,
                parent_index=source_child.parent_index,
                context_header=source_child.context_header,
                start=source_child.start,
                end=source_child.end,
                source_child_seq=source_child.seq,
                metadata=base_meta,
            )
        else:
            children.extend(
                _materialize_text_segment_with_assets(
                    source_child,
                    canonical,
                    segment,
                    child_size=child_size,
                )
            )
    return children



# ---------------------------------------------------------------------------
# Overlap shadow deduplication
# ---------------------------------------------------------------------------


def _source_spans_overlap(left: CanonicalChildDraft, right: CanonicalChildDraft) -> tuple[int, float, float]:
    """Return overlap chars and per-side source-span coverage ratios.

    WeKnora's child overlap reuses complete split units. A short unit can
    therefore become 100% of the next child prefix. The source ``start/end``
    coordinates are the strongest evidence that two adjacent retrieval drafts
    describe the same canonical slice rather than merely repeating a common
    phrase.
    """
    left_len = max(0, int(left.end) - int(left.start))
    right_len = max(0, int(right.end) - int(right.start))
    if left_len <= 0 or right_len <= 0:
        return 0, 0.0, 0.0
    overlap = max(0, min(int(left.end), int(right.end)) - max(int(left.start), int(right.start)))
    if overlap <= 0:
        return 0, 0.0, 0.0
    return overlap, overlap / left_len, overlap / right_len


def _shadow_comparable_text(value: str) -> str:
    """Normalize a plain-text child for exact overlap-shadow comparison.

    Only formatting noise is normalized. Headings and real body text remain in
    the comparison, so this is deliberately stricter than semantic similarity
    and cannot delete a merely related neighbouring chunk.
    """
    lines: list[str] = []
    for raw in _preserve(value).splitlines():
        line = raw.strip()
        if not line or _HORIZONTAL_RULE_RE.match(line):
            continue
        lines.append(re.sub(r"\s+", " ", line))
    return "\n".join(lines).strip()


def _boundary_containment_coverage(
    shadow_text: str,
    carrier_text: str,
    *,
    expected_boundary: str,
) -> float:
    """Return strict text coverage when one child is a boundary-aligned shadow.

    For the normal forward-overlap case the previous short chunk must be a
    prefix of the following larger chunk. The reverse direction is supported as
    a defensive fallback for unusual splitter orderings.
    """
    shadow = _shadow_comparable_text(shadow_text)
    carrier = _shadow_comparable_text(carrier_text)
    if not shadow or not carrier or len(shadow) > len(carrier):
        return 0.0
    if shadow == carrier:
        return 1.0
    if expected_boundary == "prefix" and carrier.startswith(shadow):
        return 1.0
    if expected_boundary == "suffix" and carrier.endswith(shadow):
        return 1.0
    return 0.0


def _mark_overlap_shadow_dedup(
    carrier: CanonicalChildDraft,
    shadow: CanonicalChildDraft,
    *,
    direction: str,
    source_coverage: float,
    text_coverage: float,
) -> None:
    previous = carrier.metadata.get("deduplicated_overlap_shadow_source_child_seqs")
    source_seqs: list[int] = []
    if isinstance(previous, (list, tuple)):
        for item in previous:
            try:
                source_seqs.append(int(item))
            except (TypeError, ValueError):
                continue
    source_seqs.extend(_merged_source_sequences(shadow))
    source_seqs = list(dict.fromkeys(source_seqs))

    carrier.metadata.update(
        {
            "overlap_shadow_dedup_applied": True,
            "overlap_shadow_dedup_count": int(carrier.metadata.get("overlap_shadow_dedup_count") or 0) + 1,
            "deduplicated_overlap_shadow_source_child_seqs": source_seqs,
            "overlap_shadow_last_direction": direction,
            "overlap_shadow_last_source_coverage": round(float(source_coverage), 4),
            "overlap_shadow_last_text_coverage": round(float(text_coverage), 4),
        }
    )
    carrier.flags["overlap_shadow_dedup_applied"] = True


def deduplicate_overlap_shadow_children(
    children: list[CanonicalChildDraft],
    *,
    min_source_coverage: float = 0.80,
    min_text_coverage: float = 0.95,
) -> list[CanonicalChildDraft]:
    """Drop adjacent text children fully reproduced by WeKnora overlap.

    This fixes the concrete pattern seen in the chunk tree::

        Child A:  heading + short prelude
        Child B:  heading + short prelude + real following body

    ``A`` is not a useful independent retrieval unit when all of its source span
    and text are already carried by ``B``. The function is intentionally
    conservative:

    - only adjacent plain-text children are considered;
    - children materialized from the same source child are ignored;
    - original source spans must overlap heavily;
    - the smaller text must be an exact boundary-aligned prefix/suffix of the
      larger text after whitespace-only normalization;
    - the carrier must contain additional semantic content, unless the two
      chunks are exact source duplicates;
    - media/table/code boundaries are never crossed.

    The pass runs before short-sibling merge and parent validation. Removing a
    shadow child can therefore collapse a parent to one child, which the normal
    final validation safely downgrades to standalone.
    """
    if len(children) < 2:
        return list(children)

    work = list(children)
    changed = True
    while changed:
        changed = False
        index = 0
        while index + 1 < len(work):
            left = work[index]
            right = work[index + 1]

            if not (_is_mergeable_plain_text_child(left) and _is_mergeable_plain_text_child(right)):
                index += 1
                continue
            if int(left.source_child_seq) == int(right.source_child_seq):
                # Text/image/table materialization can create multiple drafts
                # with the same broad source span; they are not overlap shadows.
                index += 1
                continue

            _, left_source_coverage, right_source_coverage = _source_spans_overlap(left, right)
            left_text = _shadow_comparable_text(left.content)
            right_text = _shadow_comparable_text(right.content)
            if not left_text or not right_text:
                index += 1
                continue

            # Normal forward overlap: the earlier short child is fully copied
            # into the prefix of the following child.
            left_text_coverage = _boundary_containment_coverage(
                left.content,
                right.content,
                expected_boundary="prefix",
            )
            right_has_extra = len(right_text) > len(left_text) or int(right.end) > int(left.end)
            exact_duplicate = left_text == right_text and left.start == right.start and left.end == right.end
            if (
                left_source_coverage >= min_source_coverage
                and left_text_coverage >= min_text_coverage
                and (right_has_extra or exact_duplicate)
            ):
                _mark_overlap_shadow_dedup(
                    right,
                    left,
                    direction="left_shadowed_by_right",
                    source_coverage=left_source_coverage,
                    text_coverage=left_text_coverage,
                )
                del work[index]
                changed = True
                index = max(0, index - 1)
                continue

            # Defensive reverse case: the following child is wholly contained
            # in the suffix of the previous child.
            right_text_coverage = _boundary_containment_coverage(
                right.content,
                left.content,
                expected_boundary="suffix",
            )
            left_has_extra = len(left_text) > len(right_text) or int(left.start) < int(right.start)
            exact_duplicate = left_text == right_text and left.start == right.start and left.end == right.end
            if (
                right_source_coverage >= min_source_coverage
                and right_text_coverage >= min_text_coverage
                and (left_has_extra or exact_duplicate)
            ):
                _mark_overlap_shadow_dedup(
                    left,
                    right,
                    direction="right_shadowed_by_left",
                    source_coverage=right_source_coverage,
                    text_coverage=right_text_coverage,
                )
                del work[index + 1]
                changed = True
                index = max(0, index - 1)
                continue

            index += 1

    return work

# ---------------------------------------------------------------------------
# Short sibling text merge
# ---------------------------------------------------------------------------


def _normalized_context_header(value: str) -> str:
    """Normalize a context header for safe sibling-merge comparison."""
    return "\n".join(
        line.strip()
        for line in _preserve(value).splitlines()
        if line.strip()
    )


def _semantic_text_length(text: str) -> int:
    """Count retrievable body characters, excluding headings/separators."""
    return len("\n".join(_meaningful_lines(text)).strip())


def _is_mergeable_plain_text_child(child: CanonicalChildDraft) -> bool:
    if child.chunk_type != "text":
        return False
    content_kind = str(
        child.metadata.get("content_kind")
        or child.flags.get("content_kind")
        or "text"
    ).strip().lower()
    if content_kind != "text":
        return False
    value = _preserve(child.content).strip()
    if not value or is_structural_only(value):
        return False
    if _is_pure_fenced_code(value) or _is_pure_markdown_table(value) or _is_pure_html_table(value):
        return False
    return True


def _children_are_merge_compatible(
    left: CanonicalChildDraft,
    right: CanonicalChildDraft,
) -> bool:
    """Only merge adjacent text children in the same scope and heading context.

    ``None + None`` is intentionally allowed: WeKnora emits standalone children
    for parent candidates that were not genuinely subdivided, and parent
    validation can also flatten a one-child parent after materialization.  Those
    adjacent standalone fragments still need the same short-sibling repair.
    Mixed parented/standalone pairs remain forbidden.
    """
    if left.parent_index != right.parent_index:
        return False
    if not (_is_mergeable_plain_text_child(left) and _is_mergeable_plain_text_child(right)):
        return False
    return _normalized_context_header(left.context_header) == _normalized_context_header(right.context_header)


def _exact_suffix_prefix_overlap(left: str, right: str, max_chars: int = 192) -> int:
    """Return the longest exact overlap introduced by chunk overlap settings."""
    limit = min(len(left), len(right), max_chars)
    for size in range(limit, 15, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def _combine_sibling_text(left: str, right: str) -> str:
    """Join adjacent text in document order without duplicating exact overlap."""
    left_value = _preserve(left).strip()
    right_value = _preserve(right).strip()
    if not left_value:
        return right_value
    if not right_value:
        return left_value

    overlap = _exact_suffix_prefix_overlap(left_value, right_value)
    if overlap:
        return (left_value + right_value[overlap:]).strip()
    return f"{left_value}\n\n{right_value}".strip()


def _merged_source_sequences(child: CanonicalChildDraft) -> list[int]:
    raw = child.metadata.get("merged_source_child_seqs")
    if isinstance(raw, (list, tuple)):
        values: list[int] = []
        for item in raw:
            try:
                values.append(int(item))
            except (TypeError, ValueError):
                continue
        if values:
            return values
    return [int(child.source_child_seq)]


def _merge_two_text_children(
    left: CanonicalChildDraft,
    right: CanonicalChildDraft,
    *,
    threshold: int,
) -> CanonicalChildDraft:
    content = _combine_sibling_text(left.content, right.content)
    source_seqs = list(dict.fromkeys(_merged_source_sequences(left) + _merged_source_sequences(right)))
    left_count = int(left.metadata.get("short_sibling_merge_count") or 1)
    right_count = int(right.metadata.get("short_sibling_merge_count") or 1)

    metadata = {**right.metadata, **left.metadata}
    metadata.update(
        {
            "content_kind": "text",
            "short_sibling_merged": True,
            "short_sibling_merge_count": left_count + right_count,
            "short_sibling_merge_threshold": threshold,
            "merged_source_child_seqs": source_seqs,
        }
    )
    flags = {**right.flags, **left.flags}
    flags.update(
        {
            "content_kind": "text",
            "short_sibling_merged": True,
        }
    )

    return CanonicalChildDraft(
        content=content,
        chunk_type="text",
        parent_index=left.parent_index,
        context_header=left.context_header or right.context_header,
        start=min(left.start, right.start),
        end=max(left.end, right.end),
        source_child_seq=min(left.source_child_seq, right.source_child_seq),
        metadata=metadata,
        image_info={},
        flags=flags,
    )


def merge_short_text_siblings(
    children: list[CanonicalChildDraft],
    *,
    child_size: int,
    min_child_size: int | None = None,
) -> list[CanonicalChildDraft]:
    """Merge short adjacent text siblings before parent materialization.

    Rules:
    - only plain text children;
    - only adjacent children in the same scope (same parent, or both standalone);
    - context headers must match exactly after whitespace normalization;
    - never cross image/table/code boundaries;
    - merged content must stay within ``child_size``;
    - heading-led short chunks prefer the following sibling, while ordinary
      short tails prefer the previous sibling.

    The threshold is a *merge trigger*, never a deletion threshold.  If no safe
    sibling fits, the short child is kept unchanged.
    """
    if not children:
        return []

    max_size = max(int(child_size or 1), 1)
    threshold = int(min_child_size) if min_child_size is not None else min(120, max(80, max_size // 3))
    threshold = max(1, min(threshold, max_size))
    work = list(children)

    # A merge can create another short chunk, so iterate until stable.  Each
    # successful merge reduces the list length, guaranteeing termination.
    changed = True
    while changed:
        changed = False
        index = 0
        while index < len(work):
            current = work[index]
            if not _is_mergeable_plain_text_child(current):
                index += 1
                continue
            if _semantic_text_length(current.content) >= threshold:
                index += 1
                continue

            candidates: list[tuple[str, int, CanonicalChildDraft]] = []
            if index > 0:
                previous = work[index - 1]
                if _children_are_merge_compatible(previous, current):
                    combined = _combine_sibling_text(previous.content, current.content)
                    if len(combined) <= max_size:
                        candidates.append(("backward", index - 1, _merge_two_text_children(previous, current, threshold=threshold)))
            if index + 1 < len(work):
                following = work[index + 1]
                if _children_are_merge_compatible(current, following):
                    combined = _combine_sibling_text(current.content, following.content)
                    if len(combined) <= max_size:
                        candidates.append(("forward", index, _merge_two_text_children(current, following, threshold=threshold)))

            if not candidates:
                index += 1
                continue

            starts_with_heading = any(
                _HEADING_LINE_RE.match(line.strip())
                for line in _preserve(current.content).splitlines()
                if line.strip()
            )

            # New-section short heads should absorb following prose; ordinary
            # short tails should attach to preceding prose.  If the preferred
            # side is unavailable, choose the merge that fills the target size
            # most efficiently without exceeding it.
            preferred_direction = "forward" if starts_with_heading else "backward"
            preferred = [item for item in candidates if item[0] == preferred_direction]
            pool = preferred or candidates
            direction, replace_at, merged = min(
                pool,
                key=lambda item: (max_size - len(item[2].content), 0 if item[0] == preferred_direction else 1),
            )
            work[replace_at : replace_at + 2] = [merged]
            changed = True
            index = max(0, replace_at - 1)

    return work


def _flatten_underfilled_parent_links(
    children: list[CanonicalChildDraft],
    *,
    pass_name: str,
) -> tuple[list[CanonicalChildDraft], dict[int, int], set[int]]:
    """Flatten parent links that have fewer than two surviving children."""
    counts: dict[int, int] = {}
    for child in children:
        if child.parent_index is not None:
            counts[child.parent_index] = counts.get(child.parent_index, 0) + 1

    kept = {parent_index for parent_index, count in counts.items() if count >= 2}
    dropped = {parent_index for parent_index, count in counts.items() if count < 2}
    if dropped:
        for child in children:
            if child.parent_index not in dropped:
                continue
            child.parent_index = None
            child.metadata["parent_materialized"] = False
            child.metadata["parent_dropped_after_materialization"] = True
            child.metadata["parent_flattened_reason"] = "fewer_than_two_surviving_children"
            child.metadata["parent_flattened_pass"] = pass_name
            child.flags["parent_materialized"] = False
    return children, counts, kept


# ---------------------------------------------------------------------------
# Public plan builder
# ---------------------------------------------------------------------------


def build_canonical_parent_child_plan(
    canonical: CanonicalDocument,
    cfg: ChunkerConfig,
) -> CanonicalChunkPlan:
    child_size = max(getattr(cfg, "child_chunk_size", 384) or 384, 1)
    normalized_markdown = normalize_canonical_markdown(canonical.markdown_content, child_size)
    result = split_parent_child_result(normalized_markdown, cfg)
    _apply_heading_context_to_source_children(result.children)

    children: list[CanonicalChildDraft] = []
    for source_child in sorted(result.children, key=lambda item: item.seq):
        children.extend(_materialize_source_child(source_child, canonical, child_size=child_size))

    # Structure-first post-processing policy.
    #
    # Keep only the deterministic exact-overlap cleanup.  Do NOT run automatic
    # short-sibling merging in the runtime plan: a short child can be a complete
    # section, conclusion, captioned note, or other meaningful structural unit.
    # Pairwise merging optimizes length at the cost of the Old pipeline's main
    # advantage—stable document/heading boundaries.  The helper remains public
    # for compatibility and experiments, but it is intentionally not part of
    # the production parent-child path.
    children = deduplicate_overlap_shadow_children(children)
    for child in children:
        child.metadata["runtime_postprocess_policy"] = "structure_first"
        child.flags["runtime_postprocess_policy"] = "structure_first"

    # Final integrity rule only: parent materialization follows surviving child
    # links without changing child content or boundaries.
    children, counts, kept_parent_indexes = _flatten_underfilled_parent_links(
        children,
        pass_name="structure_first_final_validation",
    )

    parents: list[CanonicalParentDraft] = []
    for parent_index, parent in enumerate(result.parents):
        if parent_index not in kept_parent_indexes:
            continue
        parents.append(
            CanonicalParentDraft(
                parent_index=parent_index,
                content=_preserve(parent.content),
                context_header=parent.context_header,
                start=parent.start,
                end=parent.end,
                metadata={
                    **(parent.metadata or {}),
                    "canonical_markdown": True,
                    "chunk_role": "parent",
                    "children_count_planned": counts.get(parent_index, 0),
                    "runtime_postprocess_policy": "structure_first",
                },
            )
        )

    # Re-sequence expanded children in actual document order.  Multiple drafts
    # from one source child keep their local order (text -> image -> text).
    children = [child for child in children if child.content.strip()]
    return CanonicalChunkPlan(
        markdown_content=normalized_markdown,
        parents=parents,
        children=children,
        assets=canonical.assets,
    )


__all__ = [
    "CanonicalParentDraft",
    "CanonicalChildDraft",
    "CanonicalChunkPlan",
    "build_canonical_parent_child_plan",
    "compose_image_child_content",
    "is_structural_only",
    "split_oversize_fenced_blocks",
    "split_oversize_markdown_tables",
    "normalize_canonical_markdown",
    "deduplicate_overlap_shadow_children",
    "merge_short_text_siblings",
]
