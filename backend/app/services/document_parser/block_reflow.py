"""Block-level post-processing before chunking.

The parser should produce layout blocks; this module keeps that structure but
adds a lightweight reflow pass for scanned PDFs where OCR engines often emit one
line per block and concatenate obvious words/identifiers.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.chunker.heading_utils import (
    is_caption_text,
    is_page_noise_text,
    looks_like_section_heading,
)
from app.services.chunker.protected_patterns import is_structure_like_text
from app.services.ocr import repair_text
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres


_OCR_SIGNAL_RE = re.compile(
    r"(OCR\s*text\s*line|OCRtextline|OCR-CANDIDATE|Raster-only|raster\s*page|invoice\s*number|invoicenumber|INV[-_ ]?[0-9O0U]{2,4})",
    re.I,
)
_SENTENCE_END_RE = re.compile(r"[。！？.!?；;:]$|[\)\]）】]$")
_NON_TEXT_REFLOW_TYPES = {"table", "image", "equation", "formula"}
_PAGE_PRIMARY_VISUAL_SOURCES = {"pdf_page_render", "pdf_page_visual_guard"}


def repair_scanned_ocr_text(text: str) -> str:
    """Repair common scanned-PDF OCR spacing without aggressive ID rewriting."""
    value = repair_text(text or "")
    if not value:
        return ""

    replacements = [
        (r"\bNoisyskewedscanned\b", "Noisy skewed scanned"),
        (r"\bnoisyskewedscanned\b", "noisy skewed scanned"),
        (
            r"\bRaster-only\.\s*OCRtextline\s*([0-9O0U]{2,4}[-_][0-9O0U]{1,3})",
            r"Raster-only OCR text line \1",
        ),
        (
            r"\bRaster-only\s*OCRtextline\s*([0-9O0U]{2,4}[-_][0-9O0U]{1,3})",
            r"Raster-only OCR text line \1",
        ),
        (r"\bOCRtextline\s*([0-9O0U]{2,4}[-_][0-9O0U]{1,3})", r"OCR text line \1"),
        (r"\brasterpage\s*([0-9O0U]{2,4})", r"raster page \1"),
        (r"\bcontract-raster\s*page\b", "contract - raster page"),
        (r"\binvoice\s*number\s*(INV[-_ ]?[A-Za-z0-9O0U-]+)", r"invoice number \1"),
        (r"\binvoicenumber\s*(INV[-_ ]?[A-Za-z0-9O0U-]+)", r"invoice number \1"),
        (r"\bnumber\s*(INV[-_ ]?[A-Za-z0-9O0U-]+)", r"number \1"),
        (r"\bvector\.\s*indexcheck\b", "vector index check"),
        (r"\bvectorindexcheck\b", "vector index check"),
        (r"\bline\s*([0-9O0U]{2,4}[-_][0-9O0U]{1,3})", r"line \1"),
    ]
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.I)

    # Missing spaces after common delimiters.  Keep identifiers such as INV-002-00 intact.
    value = re.sub(r",(?=\S)", ", ", value)
    value = re.sub(r":(?=\S)", ": ", value)
    value = re.sub(r"\s+([,;:])", r"\1", value)

    # Boundaries often lost around all-caps parser tokens / identifiers.
    value = re.sub(
        r"\b(number|page|line|text|contract|invoice)(INV|OCR|CN|EN|KB|SCAN)\b",
        r"\1 \2",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(OCR|PDF|INV)(text|line|number|candidate)\b", r"\1 \2", value, flags=re.I
    )

    # Normalize a few common record labels.
    value = re.sub(r"OCR\s*-\s*CANDIDATE", "OCR-CANDIDATE", value, flags=re.I)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return sanitize_text_for_postgres(value.strip())


def _is_ocr_like(block: ParsedBlock, text: str) -> bool:
    meta = block.metadata or {}
    if meta.get("ocr_layered") or meta.get("ocr_status") or meta.get("ocr_raw_text"):
        return True
    source = "\n".join(
        [
            text,
            " ".join(str(v) for v in meta.values() if isinstance(v, (str, int, float))),
        ]
    )
    return bool(_OCR_SIGNAL_RE.search(source))


def _is_line_fragment(text: str) -> bool:
    clean = sanitize_text_for_postgres(text or "").strip()
    if not clean:
        return False
    if is_structure_like_text(clean):
        return False
    if (
        is_caption_text(clean)
        or is_page_noise_text(clean)
        or looks_like_section_heading(clean)
    ):
        return False
    if len(clean) <= 120 and not _SENTENCE_END_RE.search(clean[-1:]):
        return True
    lines = [line for line in clean.splitlines() if line.strip()]
    if (
        len(lines) >= 2
        and sum(1 for line in lines if len(line.strip()) <= 120) / len(lines) > 0.7
    ):
        return True
    return not bool(_SENTENCE_END_RE.search(clean[-1:]))


def _bbox_merge_compatible(prev: ParsedBlock, current: ParsedBlock) -> bool:
    """Require close layout geometry before joining OCR/text line fragments."""
    if not prev.bbox or not current.bbox:
        return True
    try:
        px0, py0, px1, py1 = [float(v) for v in prev.bbox]
        cx0, cy0, cx1, cy1 = [float(v) for v in current.bbox]
    except Exception:
        return True
    prev_height = max(py1 - py0, 1.0)
    cur_height = max(cy1 - cy0, 1.0)
    vertical_gap = cy0 - py1
    x_delta = abs(cx0 - px0)
    width = max(px1 - px0, cx1 - cx0, 1.0)
    return -0.25 * prev_height <= vertical_gap <= 1.8 * max(
        prev_height, cur_height
    ) and x_delta <= max(32.0, width * 0.12)


def _block_role_text(block: ParsedBlock) -> str:
    meta = block.metadata or {}
    return " ".join(
        str(meta.get(key) or "")
        for key in (
            "role",
            "layout_role",
            "raw_block_type",
            "type",
            "subtype",
            "block_type",
        )
    ).lower()


def _is_caption_block(block: ParsedBlock, text: str) -> bool:
    role = _block_role_text(block)
    return "caption" in role or is_caption_text(text)


def _is_page_noise_block(block: ParsedBlock, text: str) -> bool:
    role = _block_role_text(block)
    return role in {"header", "footer", "page_number"} or is_page_noise_text(text)


def _is_structure_block(block: ParsedBlock, text: str) -> bool:
    meta = block.metadata or {}
    if meta.get("structure_like") is True:
        return True
    return is_structure_like_text(text)


def _merge_texts(texts: list[str], *, ocr_like: bool) -> str:
    lines = [repair_scanned_ocr_text(t) if ocr_like else repair_text(t) for t in texts]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    if ocr_like:
        # OCR records should stay readable line-by-line, but live in one parser
        # block so the chunker can form natural parent/child chunks.
        return sanitize_text_for_postgres("\n".join(lines))

    paragraphs: list[str] = []
    current = ""
    for line in lines:
        if not current:
            current = line
            continue
        if _SENTENCE_END_RE.search(current[-1:]) or len(current) > 240:
            paragraphs.append(current)
            current = line
        else:
            current = f"{current} {line}"
    if current:
        paragraphs.append(current)
    return sanitize_text_for_postgres("\n\n".join(paragraphs))


def _flush_buffer(
    buffer: list[ParsedBlock], output: list[ParsedBlock], stats: dict[str, int]
) -> None:
    if not buffer:
        return
    if len(buffer) == 1:
        block = buffer[0]
        original_text = block.text
        repaired = (
            repair_scanned_ocr_text(block.text)
            if _is_ocr_like(block, block.text)
            else repair_text(block.text)
        )
        block.text = repaired or block.text
        block.metadata = sanitize_json_for_postgres(
            {
                **(block.metadata or {}),
                "reflow_repaired": bool(repaired and repaired != original_text),
            }
        )
        output.append(block)
        buffer.clear()
        return

    ocr_like = any(_is_ocr_like(block, block.text) for block in buffer)
    merged_text = _merge_texts([block.text for block in buffer], ocr_like=ocr_like)
    first = buffer[0]
    last = buffer[-1]
    meta = {
        **(first.metadata or {}),
        "block_reflow": True,
        "reflow_engine": "block_merge_v1",
        "reflow_source_orders": [block.order_index for block in buffer],
        "reflow_merged_count": len(buffer),
        "reflow_ocr_like": ocr_like,
    }
    output.append(
        ParsedBlock(
            block_type="text",
            text=merged_text,
            order_index=first.order_index,
            page_no=first.page_no,
            bbox=first.bbox,
            context_header=first.context_header or last.context_header or "",
            metadata=sanitize_json_for_postgres(meta),
        )
    )
    stats["reflow_merged_blocks"] += len(buffer) - 1
    stats["reflow_output_merged_text_blocks"] += 1
    if ocr_like:
        stats["ocr_repaired_blocks"] += len(buffer)
    buffer.clear()


def _heading_level(block: ParsedBlock) -> int | None:
    meta = block.metadata or {}
    raw = meta.get("text_level") or meta.get("level")
    if raw is None and meta.get("is_heading"):
        raw = 1
    try:
        level = int(raw)
        if 1 <= level <= 6:
            return level
    except Exception:
        return None
    return None


def _heading_path_text(stack: list[tuple[int, str]]) -> str:
    return " > ".join(text for _, text in stack if text)


def _is_page_primary_visual(block: ParsedBlock) -> bool:
    if block.block_type != "image":
        return False
    meta = block.metadata or {}
    return (
        meta.get("visual_role") == "page_primary_visual"
        or meta.get("semantic_role") == "page_primary_visual"
        or meta.get("source") in _PAGE_PRIMARY_VISUAL_SOURCES
    )


def _visual_page_heading_candidate(
    page_blocks: list[ParsedBlock],
) -> ParsedBlock | None:
    text_blocks = [block for block in page_blocks if block.block_type == "text"]
    concrete_bottoms = [
        float(block.bbox[3])
        for block in page_blocks
        if block.bbox is not None
        and len(block.bbox) >= 4
        and isinstance(block.bbox[3], (int, float))
    ]
    page_bottom = max(concrete_bottoms, default=0.0)
    for block in text_blocks[:3]:
        text = sanitize_text_for_postgres(block.text or "").strip()
        if (
            not text
            or _is_caption_block(block, text)
            or _is_page_noise_block(block, text)
        ):
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or len(lines) > 3:
            continue
        first_line = lines[0]
        if not (2 <= len(first_line) <= 60) or len(text) > 180:
            continue
        if _SENTENCE_END_RE.search(first_line[-1:]):
            continue
        if block.bbox is not None and page_bottom > 0.0:
            try:
                if float(block.bbox[1]) > page_bottom * 0.22:
                    continue
            except (TypeError, ValueError):
                continue
        return block
    return None


def _prepare_visual_page_sections(
    blocks: list[ParsedBlock],
) -> tuple[list[ParsedBlock], set[int], int, int, int]:
    visual_pages = {
        int(block.page_no)
        for block in blocks
        if block.page_no is not None and _is_page_primary_visual(block)
    }
    if not visual_pages:
        return blocks, set(), 0, 0, 0

    inferred_count = 0
    synthetic_count = 0
    demoted_count = 0
    synthetic_by_page: dict[int, ParsedBlock] = {}
    for page_no in sorted(
        {int(block.page_no) for block in blocks if block.page_no is not None}
    ):
        page_blocks = sorted(
            (block for block in blocks if block.page_no == page_no),
            key=lambda item: item.order_index,
        )
        if page_no in visual_pages:
            parser_headings = [
                block for block in page_blocks if _heading_level(block) is not None
            ]
            candidate = _visual_page_heading_candidate(page_blocks)
            for block in parser_headings:
                if block is candidate:
                    continue
                metadata = dict(block.metadata or {})
                metadata.pop("is_heading", None)
                metadata.pop("text_level", None)
                metadata.pop("level", None)
                block.context_header = ""
                block.metadata = sanitize_json_for_postgres(
                    {
                        **metadata,
                        "heading_demoted": True,
                        "heading_demotion_reason": "visual_page_unreliable_layout",
                    }
                )
                demoted_count += 1
            if candidate is not None:
                candidate_metadata = {
                    **(candidate.metadata or {}),
                    "is_heading": True,
                    "text_level": 1,
                    "heading_scope": "page",
                }
                if candidate not in parser_headings:
                    candidate_metadata["heading_inference"] = "visual_page_top_text"
                    inferred_count += 1
                candidate.metadata = sanitize_json_for_postgres(candidate_metadata)
            else:
                synthetic_by_page[page_no] = ParsedBlock(
                    block_type="text",
                    text=f"第 {page_no} 页内容",
                    page_no=page_no,
                    metadata=sanitize_json_for_postgres(
                        {
                            "parser": "document_profile",
                            "source": "visual_page_boundary",
                            "role": "page_boundary",
                            "semantic_role": "page_boundary",
                            "is_heading": True,
                            "text_level": 1,
                            "heading_scope": "page",
                            "synthetic_page_heading": True,
                        }
                    ),
                )
                synthetic_count += 1

    # Preserve the parser's global order, including document-level blocks that
    # do not carry page_no.  Synthetic page boundaries are inserted immediately
    # before the first original block on their visual page.
    prepared: list[ParsedBlock] = []
    inserted_pages: set[int] = set()
    for block in sorted(blocks, key=lambda item: item.order_index):
        page_no = int(block.page_no) if block.page_no is not None else None
        if (
            page_no is not None
            and page_no in synthetic_by_page
            and page_no not in inserted_pages
        ):
            prepared.append(synthetic_by_page[page_no])
            inserted_pages.add(page_no)
        prepared.append(block)
    for order_index, block in enumerate(prepared):
        block.order_index = order_index
    return (
        prepared,
        visual_pages,
        inferred_count,
        synthetic_count,
        demoted_count,
    )


def reflow_blocks(
    blocks: list[ParsedBlock],
) -> tuple[list[ParsedBlock], dict[str, Any]]:
    input_count = len(blocks)
    blocks, visual_pages, inferred_headings, synthetic_headings, demoted_headings = (
        _prepare_visual_page_sections(list(blocks))
    )
    output: list[ParsedBlock] = []
    buffer: list[ParsedBlock] = []
    heading_stack: list[tuple[int, str]] = []
    stats: dict[str, int] = {
        "reflow_input_blocks": input_count,
        "reflow_merged_blocks": 0,
        "reflow_output_merged_text_blocks": 0,
        "ocr_repaired_blocks": 0,
        "visual_page_section_count": len(visual_pages),
        "visual_page_inferred_heading_count": inferred_headings,
        "visual_page_synthetic_heading_count": synthetic_headings,
        "visual_page_demoted_heading_count": demoted_headings,
    }
    previous_page: int | None = None

    def can_merge(prev: ParsedBlock, current: ParsedBlock) -> bool:
        if prev.block_type != "text" or current.block_type != "text":
            return False
        if (
            _block_role_text(prev) in _NON_TEXT_REFLOW_TYPES
            or _block_role_text(current) in _NON_TEXT_REFLOW_TYPES
        ):
            return False
        if _heading_level(prev) is not None or _heading_level(current) is not None:
            return False
        prev_page = prev.page_no
        cur_page = current.page_no
        if prev_page is not None and cur_page is not None and prev_page != cur_page:
            return False
        prev_text = prev.text or ""
        cur_text = current.text or ""
        if _is_structure_block(prev, prev_text) or _is_structure_block(
            current, cur_text
        ):
            return False
        if _is_page_noise_block(prev, prev_text) or _is_page_noise_block(
            current, cur_text
        ):
            return False
        if _is_caption_block(prev, prev_text) != _is_caption_block(current, cur_text):
            return False
        if looks_like_section_heading(prev_text) or looks_like_section_heading(
            cur_text
        ):
            return False
        if _SENTENCE_END_RE.search(sanitize_text_for_postgres(prev_text).strip()[-1:]):
            return False
        if not _bbox_merge_compatible(prev, current):
            return False
        if _is_ocr_like(prev, prev_text) or _is_ocr_like(current, cur_text):
            return True
        return _is_line_fragment(prev_text) and _is_line_fragment(cur_text)

    for block in sorted(blocks, key=lambda item: item.order_index):
        current_page = int(block.page_no) if block.page_no is not None else None
        if current_page != previous_page:
            if previous_page is not None and (
                previous_page in visual_pages or current_page in visual_pages
            ):
                _flush_buffer(buffer, output, stats)
                heading_stack.clear()
            previous_page = current_page
        if block.block_type != "text":
            _flush_buffer(buffer, output, stats)
            if not block.context_header and heading_stack:
                block.context_header = _heading_path_text(heading_stack)
                block.metadata = sanitize_json_for_postgres(
                    {**(block.metadata or {}), "context_header": block.context_header}
                )
            output.append(block)
            continue

        block.text = (
            repair_scanned_ocr_text(block.text)
            if _is_ocr_like(block, block.text)
            else repair_text(block.text)
        )
        if _is_structure_block(block, block.text):
            _flush_buffer(buffer, output, stats)
            block.metadata = sanitize_json_for_postgres(
                {
                    **(block.metadata or {}),
                    "structure_like": True,
                    "content_kind": "directory_tree",
                    "preserve_line_boundaries": True,
                    "reflow_suppressed": True,
                }
            )
            if not block.context_header and heading_stack:
                block.context_header = _heading_path_text(heading_stack)
            output.append(block)
            continue
        if _is_page_noise_block(block, block.text):
            _flush_buffer(buffer, output, stats)
            block.metadata = sanitize_json_for_postgres(
                {
                    **(block.metadata or {}),
                    "page_noise": True,
                    "reflow_suppressed": True,
                }
            )
            output.append(block)
            continue
        if _is_caption_block(block, block.text):
            block.metadata = sanitize_json_for_postgres(
                {**(block.metadata or {}), "caption": block.text, "is_caption": True}
            )
        level = _heading_level(block)
        # Caption blocks inherit their context from the current heading stack
        # but must NOT push their own text as a new heading — table continuation
        # captions (续表2, etc.) are often mis-tagged as headings by parsers.
        if level is not None and block.text and not _is_caption_block(block, block.text):
            heading_stack = [(lvl, txt) for lvl, txt in heading_stack if lvl < level]
            heading_stack.append((level, block.text.strip()))
            block.context_header = _heading_path_text(heading_stack)
            block.metadata = sanitize_json_for_postgres(
                {
                    **(block.metadata or {}),
                    "heading_path": [txt for _, txt in heading_stack],
                    "context_header": block.context_header,
                }
            )
        elif not block.context_header and heading_stack:
            block.context_header = _heading_path_text(heading_stack)
            block.metadata = sanitize_json_for_postgres(
                {
                    **(block.metadata or {}),
                    "heading_path": [txt for _, txt in heading_stack],
                    "context_header": block.context_header,
                }
            )

        if not buffer:
            buffer.append(block)
            continue
        if can_merge(buffer[-1], block):
            buffer.append(block)
        else:
            _flush_buffer(buffer, output, stats)
            buffer.append(block)
    _flush_buffer(buffer, output, stats)

    for idx, block in enumerate(output):
        block.order_index = idx
    stats["reflow_output_blocks"] = len(output)
    stats["block_reflow_engine"] = "block_merge_v1"
    stats["block_reflow_enabled"] = True
    stats["ocr_repair_enabled"] = True
    return output, sanitize_json_for_postgres(stats)


def reflow_parsed_document(document: ParsedDocument) -> ParsedDocument:
    blocks, meta = reflow_blocks(document.blocks or [])
    content = "\n\n".join(
        block.index_text() or block.text or block.table_html
        for block in blocks
        if block.index_text() or block.text or block.table_html
    )
    return ParsedDocument(
        content=sanitize_text_for_postgres(content),
        blocks=blocks,
        metadata=sanitize_json_for_postgres({**(document.metadata or {}), **meta}),
        source_content=document.source_content,
    )
