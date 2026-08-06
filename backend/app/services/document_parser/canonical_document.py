"""Canonical Markdown intermediate representation for every document parser.

All parser backends (MinerU, DeepDoc/RAGFlow, DOCX, spreadsheets, Markdown,
images, etc.) first produce :class:`ParsedDocument`.  This module converts that
parser-specific structure into one stable representation used by the chunker:

    ParsedDocument -> CanonicalDocument(markdown_content + asset sidecar)

The Markdown string is the *only* parent/child splitting input.  Binary/media
metadata never gets flattened into the Markdown body; images are represented by
stable ``asset://`` references and their OCR/VLM/caption/location data lives in
an asset sidecar.  After splitting, the reference is materialized as an image
child whose searchable content contains the image semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import html
import re
from typing import Any, Iterable

from app.services.chunker.heading_utils import is_caption_text, is_page_noise_text
from app.services.content_cleaning import normalize_extracted_rich_text
from app.services.document_parser.image_relation_resolver import (
    SPATIAL_AMBIGUITY_MARGIN,
    SpatialRelationCandidate,
    normalize_bbox,
    relation_text_key,
    resolve_image_relation,
    score_spatial_candidate,
    valid_bbox,
)
from app.services.document_parser.types import ParsedBlock, ParsedDocument


_IMAGE_ASSET_REF_RE = re.compile(r"!\[([^\]]*)\]\(asset://([^)\s]+)\)")
_MD_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_TABLE_DELIMITER_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_PARSER_NOISE_ROLES = {"header", "footer", "page_number", "page-number", "pagination"}
_FENCE_LINE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def _without_binary_payloads(value: Any) -> Any:
    """Remove parser binary blobs before storing the durable asset ledger."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key.endswith("base64") or normalized_key in {
                "image_bytes",
                "binary_payload",
            }:
                continue
            cleaned[str(key)] = _without_binary_payloads(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_without_binary_payloads(item) for item in value]
    if isinstance(value, str) and value.lstrip().lower().startswith("data:image/"):
        return "[binary image omitted]"
    return value


@dataclass
class CanonicalSourceNode:
    node_id: str
    node_type: str
    source_order: int
    canonical_text: str
    text_hash: str
    page_no: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    section_path: str = ""
    semantic_role: str = ""
    source_block_id: str = ""
    asset_id: str = ""
    canonical_start: int | None = None
    canonical_end: int | None = None
    normalized_start: int | None = None
    normalized_end: int | None = None
    resolution: str = "canonical_span"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "source_order": self.source_order,
            "text_hash": self.text_hash,
            "page_no": self.page_no,
            "bbox": list(self.bbox) if self.bbox else None,
            "section_path": self.section_path,
            "semantic_role": self.semantic_role,
            "source_block_id": self.source_block_id,
            "asset_id": self.asset_id,
            "canonical_start": self.canonical_start,
            "canonical_end": self.canonical_end,
            "normalized_start": self.normalized_start,
            "normalized_end": self.normalized_end,
            "resolution": self.resolution,
        }


@dataclass
class CanonicalAsset:
    asset_id: str
    asset_type: str = "image"
    alt_text: str = ""
    caption: str = ""
    caption_method: str = "missing"
    binding_score: float = 0.0
    ambiguous: bool = False
    caption_ambiguous: bool = False
    footnote_ambiguous: bool = False
    figure_ids: list[str] = field(default_factory=list)
    deterministic_binding_allowed: bool = False
    composite_figure: bool = False
    spatial_status: str = "not_evaluated"
    normalized_bbox: tuple[float, float, float, float] | None = None
    bbox_coordinate_space: str = ""
    selected_candidate_orders: list[int] = field(default_factory=list)
    ocr_text: str = ""
    vlm_description: str = ""
    image_path: str = ""
    image_url: str = ""
    image_base64: str = ""
    page_no: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    context_header: str = ""
    nearby_before: str = ""
    nearby_after: str = ""
    footnote: str = ""
    source_block_order: int = 0
    source_node_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def marker(self) -> str:
        alt = _escape_markdown_alt(self.alt_text or self.caption or "图片")
        return f"![{alt}](asset://{self.asset_id})"

    def to_metadata(self) -> dict[str, Any]:
        """Return the durable asset ledger without embedding binary payloads."""
        return {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "alt_text": self.alt_text,
            "caption": self.caption,
            "caption_method": self.caption_method,
            "binding_score": self.binding_score,
            "ambiguous": self.ambiguous,
            "caption_ambiguous": self.caption_ambiguous,
            "footnote_ambiguous": self.footnote_ambiguous,
            "figure_ids": list(self.figure_ids),
            "deterministic_binding_allowed": self.deterministic_binding_allowed,
            "composite_figure": self.composite_figure,
            "spatial_status": self.spatial_status,
            "normalized_bbox": list(self.normalized_bbox) if self.normalized_bbox else None,
            "bbox_coordinate_space": self.bbox_coordinate_space,
            "selected_candidate_orders": list(self.selected_candidate_orders),
            "ocr_text": self.ocr_text,
            "vlm_description": self.vlm_description,
            "image_path": self.image_path,
            "image_url": self.image_url,
            "page_no": self.page_no,
            "bbox": list(self.bbox) if self.bbox else None,
            "context_header": self.context_header,
            "nearby_before": self.nearby_before,
            "nearby_after": self.nearby_after,
            "footnote": self.footnote,
            "source_block_order": self.source_block_order,
            "source_node_id": self.source_node_id,
            "metadata": _without_binary_payloads(self.metadata),
            "binary_payload_persisted": False,
        }


@dataclass
class CanonicalDocument:
    markdown_content: str
    assets: dict[str, CanonicalAsset] = field(default_factory=dict)
    provenance_nodes: list[CanonicalSourceNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_format: str = ""

    def image_assets(self) -> list[CanonicalAsset]:
        return [asset for asset in self.assets.values() if asset.asset_type == "image"]


# ---------------------------------------------------------------------------
# Generic metadata helpers
# ---------------------------------------------------------------------------


def _preserve_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def _first_text_deep(source: Any, keys: Iterable[str]) -> str:
    wanted = {str(key).lower() for key in keys}
    seen: set[int] = set()

    def walk(value: Any, *, allow_scalar: bool = False) -> str:
        if value is None:
            return ""
        if isinstance(value, (str, int, float)):
            return _preserve_text(value).strip() if allow_scalar else ""
        if isinstance(value, dict):
            marker = id(value)
            if marker in seen:
                return ""
            seen.add(marker)
            for key, item in value.items():
                if str(key).lower() in wanted:
                    text = walk(item, allow_scalar=True)
                    if text:
                        return text
            for item in value.values():
                if isinstance(item, (dict, list, tuple)):
                    text = walk(item)
                    if text:
                        return text
            return ""
        if isinstance(value, (list, tuple)):
            marker = id(value)
            if marker in seen:
                return ""
            seen.add(marker)
            for item in value:
                if allow_scalar or isinstance(item, (dict, list, tuple)):
                    text = walk(item, allow_scalar=allow_scalar)
                else:
                    text = ""
                if text:
                    return text
        return ""

    return walk(source)


def _escape_markdown_alt(value: str) -> str:
    return _preserve_text(value).replace("[", "\\[").replace("]", "\\]").replace("\n", " ").strip()


def _clean_context(value: str, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", _preserve_text(value)).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _block_meta(block: ParsedBlock) -> dict[str, Any]:
    return block.metadata if isinstance(block.metadata, dict) else {}


def _block_caption(block: ParsedBlock) -> str:
    meta = _block_meta(block)
    return normalize_extracted_rich_text(
        _first_text_deep(
            meta,
            ["caption", "figure_caption", "image_caption", "title", "alt_text", "alt"],
        )
    )


def _block_ocr(block: ParsedBlock) -> str:
    return normalize_extracted_rich_text(
        _first_text_deep(
            _block_meta(block),
            ["ocr_cleaned_text", "ocr_raw_text", "ocr_text", "normalized_text"],
        )
    )


def _block_vlm(block: ParsedBlock) -> str:
    return normalize_extracted_rich_text(
        _first_text_deep(
            _block_meta(block),
            ["vlm_description", "image_description", "vision_description", "description"],
        )
    )


def _block_footnote(block: ParsedBlock) -> str:
    return normalize_extracted_rich_text(
        _first_text_deep(
            _block_meta(block),
            ["image_footnote", "footnote", "chart_footnote", "figure_footnote"],
        )
    )


def _is_reliable_mineru_block(block: ParsedBlock) -> bool:
    meta = _block_meta(block)
    parser = _preserve_text(meta.get("parser")).strip().lower()
    source = _preserve_text(meta.get("source")).strip().lower()
    return parser == "mineru" or source == "mineru_content_list"


def _page_extent(blocks: list[ParsedBlock], page_no: int | None) -> tuple[float, float, float, float] | None:
    if page_no is None:
        return None
    boxes = [valid_bbox(block.bbox) for block in blocks if block.page_no == page_no]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    return (
        min(0.0, min(box[0] for box in boxes)),
        min(0.0, min(box[1] for box in boxes)),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _spatial_candidate_kind(block: ParsedBlock) -> str:
    if block.block_type != "text":
        return ""
    text = _preserve_text(block.text).strip()
    if not text:
        return ""
    meta = _block_meta(block)
    role = " ".join(
        _preserve_text(meta.get(key)).strip().lower()
        for key in ("role", "layout_role", "raw_block_type", "type", "subtype")
    )
    has_explicit_relation_role = meta.get("is_caption") is True or any(
        marker in role for marker in ("caption", "footnote")
    )
    if is_page_noise_text(text) and not has_explicit_relation_role:
        return ""
    if "footnote" in role or re.match(r"^(?:来源|资料来源|注)\s*[:：]", text):
        return "footnote"
    if meta.get("is_caption") is True or "caption" in role or is_caption_text(text):
        return "caption"
    return ""


def _spatial_relation_candidates(
    blocks: list[ParsedBlock],
    *,
    image_index: int,
    page_no: int | None,
    page_extent: tuple[float, float, float, float] | None,
) -> list[SpatialRelationCandidate]:
    if page_no is None or page_extent is None:
        return []
    candidates: list[SpatialRelationCandidate] = []
    for index, block in enumerate(blocks):
        if index == image_index or block.page_no != page_no:
            continue
        kind = _spatial_candidate_kind(block)
        if not kind:
            continue
        raw_bbox = valid_bbox(block.bbox)
        candidates.append(
            SpatialRelationCandidate(
                text=_preserve_text(block.text).strip(),
                kind=kind,
                page_no=page_no,
                bbox=normalize_bbox(raw_bbox, page_extent=page_extent),
                source_order=block.order_index,
                raw_bbox=raw_bbox,
            )
        )
    owned_candidates: list[SpatialRelationCandidate] = []
    for candidate in candidates:
        scored_images: list[tuple[float, int]] = []
        for candidate_image_index, image_block in enumerate(blocks):
            if image_block.block_type != "image" or image_block.page_no != page_no:
                continue
            if not _is_reliable_mineru_block(image_block):
                continue
            if candidate.kind == "caption":
                attached_relation = resolve_image_relation(
                    caption=_block_caption(image_block),
                    ocr_text=_block_ocr(image_block),
                    footnote=_block_footnote(image_block),
                    enable_spatial=False,
                )
                if attached_relation.caption:
                    continue
            if candidate.kind == "footnote" and _block_footnote(image_block):
                continue
            image_bbox = normalize_bbox(image_block.bbox, page_extent=page_extent)
            if image_bbox is None:
                continue
            metrics = score_spatial_candidate(
                image_bbox,
                image_block.order_index,
                candidate,
            )
            if metrics.get("accepted"):
                scored_images.append((float(metrics["score"]), candidate_image_index))

        scored_images.sort(key=lambda item: (-item[0], item[1]))
        if not scored_images:
            owned_candidates.append(candidate)
            continue
        top_score = scored_images[0][0]
        contenders = [
            candidate_image_index
            for score, candidate_image_index in scored_images
            if top_score - score < SPATIAL_AMBIGUITY_MARGIN
        ]
        if len(contenders) > 1:
            if image_index in contenders:
                owned_candidates.append(replace(candidate, ownership_ambiguous=True))
            continue
        if scored_images[0][1] == image_index:
            owned_candidates.append(candidate)
    return owned_candidates


def _heading_level(block: ParsedBlock, text: str) -> int | None:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    match = _MD_HEADING_RE.match(first_line)
    if match:
        return len(match.group(1))

    meta = _block_meta(block)
    # Caption blocks (marked during reflow) must never be promoted to
    # Markdown headings, even if the parser tagged them with is_heading.
    if meta.get("is_caption"):
        return None
    if not (
        bool(meta.get("is_heading"))
        or str(meta.get("raw_block_type") or meta.get("block_type") or meta.get("type") or "").lower()
        in {"title", "heading", "section_title"}
    ):
        style = str(meta.get("style") or meta.get("style_name") or "")
        style_match = re.search(r"heading\s*([1-6])", style, flags=re.I)
        if style_match:
            return int(style_match.group(1))
        return None

    for key in ("heading_level", "level", "depth", "title_level"):
        try:
            value = int(meta.get(key))
            if 1 <= value <= 6:
                return value
        except Exception:
            pass
    return 2


def _as_markdown_text(block: ParsedBlock, text: str | None = None) -> str:
    text = normalize_extracted_rich_text(
        _preserve_text(text if text is not None else (block.text or block.index_text()))
    ).strip("\n")
    if not text:
        return ""
    level = _heading_level(block, text)
    if level is None or _MD_HEADING_RE.match(text.strip().splitlines()[0]):
        return text
    # Parser-provided headings are normalized to Markdown so every source type
    # reaches the exact same downstream splitter semantics.
    return f"{'#' * level} {text.strip()}"


def _block_role(block: ParsedBlock) -> str:
    meta = _block_meta(block)
    # Explicit semantic/layout roles are more informative than the raw parser
    # block type (usually just "text" or "image").
    for key in (
        "role",
        "layout_role",
        "semantic_type",
        "subtype",
        "raw_block_type",
        "block_type",
        "type",
    ):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _is_explicit_parser_noise_block(block: ParsedBlock, text: str) -> bool:
    """Reject parser-level page furniture before it reaches Canonical Markdown.

    This deliberately stays narrower than the generic low-information filter:
    short headings, code paths and identifiers can be valid knowledge.  We only
    remove explicit header/footer/page-number roles or a whole text block that
    is itself a page-number pattern.
    """
    if _block_role(block) in _PARSER_NOISE_ROLES:
        return True
    value = _preserve_text(text).strip()
    if not value:
        return False
    # A bare number can be a measurement/year/table remnant.  Treat it as page
    # furniture only when block context shows a repeated run (handled below) or
    # the parser explicitly marks it as header/footer/page_number.
    if value.isdigit():
        return False
    return is_page_noise_text(value)


def _repeated_numeric_noise_block_indexes(blocks: list[ParsedBlock]) -> set[int]:
    """Detect consecutive runs of 2+ bare numeric text blocks.

    This catches OCR/PDF page-furniture fragments such as ``526 / 524 / 117``
    while preserving a single standalone numeric value, which may be valid
    scientific or tabular content.
    """
    result: set[int] = set()
    run: list[int] = []

    def flush() -> None:
        if len(run) >= 2:
            result.update(run)
        run.clear()

    for index, block in enumerate(blocks):
        if block.block_type != "text":
            flush()
            continue
        value = _preserve_text(block.text or block.index_text()).strip()
        if value.isdigit() and 1 <= len(value) <= 4 and _block_role(block) not in _PARSER_NOISE_ROLES:
            run.append(index)
            continue
        flush()
    flush()
    return result


def _remove_repeated_page_noise_lines(text: str) -> tuple[str, int]:
    """Remove repeated isolated page-number lines outside fenced code.

    Some parsers collapse a title and several footer/page-number blocks into one
    text block.  Dropping every numeric line would destroy valid scientific data,
    so this fallback only activates when at least two page-noise lines are found
    outside code fences.  Pure page-noise text is removed entirely.
    """
    value = _preserve_text(text)
    if not value.strip():
        return "", 0

    lines = value.splitlines(keepends=True)
    fence = ""
    candidates: list[int] = []
    semantic_indexes: list[int] = []
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
        if not stripped:
            continue
        semantic_indexes.append(index)
        if is_page_noise_text(stripped):
            candidates.append(index)

    if not semantic_indexes:
        return value, 0
    if len(candidates) == len(semantic_indexes) and len(candidates) >= 2:
        return "", len(candidates)
    if len(candidates) < 2:
        return value, 0

    dropped = set(candidates)
    cleaned = "".join(raw for index, raw in enumerate(lines) if index not in dropped)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, len(candidates)


def _clean_parser_text_block(block: ParsedBlock) -> tuple[str, int, bool]:
    """Return ``(clean_text, dropped_line_count, drop_block)`` for one text block."""
    raw = _preserve_text(block.text or block.index_text())
    if _is_explicit_parser_noise_block(block, raw):
        return "", 0, True
    cleaned, dropped_lines = _remove_repeated_page_noise_lines(raw)
    if not cleaned.strip():
        return "", dropped_lines, True
    return cleaned, dropped_lines, False


def _clean_non_native_fallback_content(text: str) -> tuple[str, int]:
    """Conservative noise cleanup when a parser returned content but no blocks."""
    value = _preserve_text(text)
    paragraphs = re.split(r"(\n\s*\n+)", value)
    candidate_indexes = [
        index
        for index in range(0, len(paragraphs), 2)
        if paragraphs[index].strip() and is_page_noise_text(paragraphs[index].strip())
    ]
    if len(candidate_indexes) < 2:
        return value, 0
    dropped = set(candidate_indexes)
    cleaned = "".join(part for index, part in enumerate(paragraphs) if index not in dropped)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, len(candidate_indexes)


def _looks_like_markdown_table(value: str) -> bool:
    lines = [line for line in _preserve_text(value).splitlines() if line.strip()]
    return len(lines) >= 2 and "|" in lines[0] and bool(_MD_TABLE_DELIMITER_RE.match(lines[1]))


def _escape_table_cell(value: str) -> str:
    return re.sub(r"\s+", " ", _preserve_text(value)).strip().replace("|", "\\|")


def _rows_to_markdown(rows: list[list[str]]) -> str:
    normalized = [[_escape_table_cell(cell) for cell in row] for row in rows if any(str(cell).strip() for cell in row)]
    if not normalized:
        return ""
    width = max(len(row) for row in normalized)
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    header = normalized[0]
    body = normalized[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _html_table_to_markdown(value: str) -> str:
    html_value = _preserve_text(value)
    rows: list[list[str]] = []
    for row_html in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", html_value):
        cells: list[str] = []
        for cell_html in re.findall(r"(?is)<t[hd]\b[^>]*>(.*?)</t[hd]>", row_html):
            plain = re.sub(r"(?is)<br\s*/?>", " ", cell_html)
            plain = re.sub(r"(?is)<[^>]+>", " ", plain)
            cells.append(html.unescape(plain).strip())
        if cells:
            rows.append(cells)
    return _rows_to_markdown(rows)


def _plain_delimited_rows_to_markdown(value: str) -> str:
    lines = [line.strip() for line in _preserve_text(value).splitlines() if line.strip()]
    if not lines:
        return ""
    rows: list[list[str]] = []
    if all(" | " in line or "\t" in line for line in lines):
        for line in lines:
            delimiter = "\t" if "\t" in line else " | "
            rows.append([cell.strip() for cell in line.split(delimiter)])
        return _rows_to_markdown(rows)
    return ""


def _table_markdown(block: ParsedBlock) -> str:
    meta = _block_meta(block)
    explicit = _first_text_deep(meta, ["table_markdown", "markdown_table", "table_md"])
    if explicit:
        return _preserve_text(explicit).strip("\n")

    text = _preserve_text(block.text or "").strip("\n")
    if _looks_like_markdown_table(text):
        return text

    table_html = _preserve_text(block.table_html or _first_text_deep(meta, ["table_html", "html"])).strip()
    if table_html:
        converted = _html_table_to_markdown(table_html)
        return converted or table_html

    converted = _plain_delimited_rows_to_markdown(text)
    return converted or text


def _semantic_neighbor_text(block: ParsedBlock) -> str:
    if block.block_type != "text":
        return ""
    text = normalize_extracted_rich_text(
        _preserve_text(block.text or block.index_text())
    ).strip()
    if not text:
        return ""
    if _is_explicit_parser_noise_block(block, text) or is_page_noise_text(text):
        return ""
    # Do not use a bare heading as the only nearby prose; the chunk context
    # header already carries section hierarchy.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(_MD_HEADING_RE.match(line) for line in lines):
        return ""
    return _clean_context(text)

def _nearest_text(
    blocks: list[ParsedBlock],
    index: int,
    direction: int,
    *,
    page_no: int | None = None,
    max_steps: int = 5,
) -> str:
    cursor = index + direction
    while 0 <= cursor < len(blocks):
        block = blocks[cursor]
        if max_steps <= 0:
            return ""
        if page_no is not None and block.page_no is not None and block.page_no != page_no:
            return ""
        raw_text = _preserve_text(block.text or block.index_text()).strip()
        if block.block_type == "text" and raw_text and _heading_level(block, raw_text) is not None:
            return ""
        value = _semantic_neighbor_text(block)
        if value:
            return value
        max_steps -= 1
        cursor += direction
    return ""


def _asset_id(block: ParsedBlock, ordinal: int) -> str:
    raw = "|".join(
        [
            str(block.order_index),
            block.image_url or "",
            block.image_path or "",
            str(block.page_no or ""),
            str(block.bbox or ""),
            str(ordinal),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"image-{ordinal:04d}-{digest}"


def _source_node_id(block: ParsedBlock, canonical_text: str) -> str:
    raw = "|".join(
        [
            str(block.order_index),
            block.block_type,
            str(block.page_no or ""),
            str(block.bbox or ""),
            hashlib.sha256(canonical_text.encode("utf-8", errors="ignore")).hexdigest(),
        ]
    )
    return f"node-{block.order_index:06d}-{hashlib.sha1(raw.encode()).hexdigest()[:12]}"


def _node_section_path(block: ParsedBlock) -> str:
    meta = _block_meta(block)
    heading_path = meta.get("heading_path")
    if isinstance(heading_path, (list, tuple)):
        return " > ".join(str(item).strip() for item in heading_path if str(item).strip())
    section_path = normalize_extracted_rich_text(meta.get("section_path")).strip()
    if section_path:
        return section_path
    return normalize_extracted_rich_text(block.context_header).strip()


def _make_source_node(
    block: ParsedBlock,
    canonical_text: str,
    *,
    asset_id: str = "",
) -> CanonicalSourceNode:
    value = _preserve_text(canonical_text)
    semantic_role = _block_role(block)
    if _block_meta(block).get("is_caption") is True and not semantic_role:
        semantic_role = "caption"
    return CanonicalSourceNode(
        node_id=_source_node_id(block, value),
        node_type=block.block_type,
        source_order=block.order_index,
        canonical_text=value,
        text_hash=hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest(),
        page_no=block.page_no,
        bbox=valid_bbox(block.bbox),
        section_path=_node_section_path(block),
        semantic_role=semantic_role,
        source_block_id=str(_block_meta(block).get("source_block_id") or ""),
        asset_id=asset_id,
    )


def _position_canonical_nodes(
    parts: list[str],
    nodes: list[CanonicalSourceNode],
) -> list[CanonicalSourceNode]:
    positioned: list[CanonicalSourceNode] = []
    offset = 0
    for index, (part, node) in enumerate(zip(parts, nodes, strict=True)):
        start = offset
        end = start + len(part)
        positioned.append(replace(node, canonical_start=start, canonical_end=end))
        offset = end + (2 if index < len(parts) - 1 else 0)
    return positioned


def ground_canonical_provenance(
    normalized_markdown: str,
    nodes: list[CanonicalSourceNode],
) -> list[CanonicalSourceNode]:
    """Ground source nodes into normalized Markdown without guessing duplicates."""
    text = _preserve_text(normalized_markdown)
    grounded: list[CanonicalSourceNode] = []
    cursor = 0
    for node in nodes:
        needle = node.canonical_text
        position = text.find(needle, cursor) if needle else -1
        if position < 0 and needle and text.count(needle) == 1:
            position = text.find(needle)
        if position < 0:
            grounded.append(
                replace(
                    node,
                    normalized_start=None,
                    normalized_end=None,
                    resolution="source_node_only",
                )
            )
            continue
        end = position + len(needle)
        grounded.append(
            replace(
                node,
                normalized_start=position,
                normalized_end=end,
                resolution="normalized_span",
            )
        )
        cursor = end
    return grounded


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def build_canonical_document(
    parsed_document: ParsedDocument,
    *,
    file_type: str = "",
    document_title: str = "",
) -> CanonicalDocument:
    """Convert every parser output to Canonical Markdown + asset sidecar.

    Markdown source is preserved verbatim because it is already canonical.  For
    all other formats, ordered parser blocks are serialized to Markdown.  Image
    blocks become ``asset://`` references; OCR/VLM/caption/bbox/page data stays
    in :class:`CanonicalAsset` and is injected only when image children are
    materialized after splitting.
    """
    source_format = (file_type or "").lower().lstrip(".")
    blocks = sorted(parsed_document.blocks or [], key=lambda item: item.order_index)
    assets: dict[str, CanonicalAsset] = {}
    noise_blocks_dropped = 0
    noise_lines_dropped = 0

    # Native Markdown is the authoritative source.  Rebuilding it from parsed
    # blocks can lose blank lines/fences/tables, exactly the bug this layer is
    # intended to prevent.
    authoritative_source = (
        parsed_document.source_content
        if parsed_document.source_content is not None
        else parsed_document.content
    )
    if source_format in {"md", "markdown"} and _preserve_text(authoritative_source).strip():
        markdown = _preserve_text(authoritative_source)
        source_block = ParsedBlock(block_type="text", text=markdown, order_index=0)
        provenance_nodes = _position_canonical_nodes(
            [markdown],
            [_make_source_node(source_block, markdown)],
        )
        return CanonicalDocument(
            markdown_content=markdown,
            assets=assets,
            provenance_nodes=provenance_nodes,
            metadata={
                **(parsed_document.metadata or {}),
                "canonical_markdown": True,
                "canonical_source": "native_markdown",
                "document_title": document_title,
            },
            source_format=source_format,
        )

    if not blocks:
        markdown, noise_lines_dropped = _clean_non_native_fallback_content(parsed_document.content)
        source_block = ParsedBlock(block_type="text", text=markdown, order_index=0)
        provenance_nodes = _position_canonical_nodes(
            [markdown],
            [_make_source_node(source_block, markdown)],
        ) if markdown else []
        return CanonicalDocument(
            markdown_content=markdown,
            assets=assets,
            provenance_nodes=provenance_nodes,
            metadata={
                **(parsed_document.metadata or {}),
                "canonical_markdown": True,
                "canonical_source": "parsed_document_content",
                "canonical_noise_blocks_dropped": 0,
                "canonical_noise_lines_dropped": noise_lines_dropped,
                "document_title": document_title,
            },
            source_format=source_format,
        )

    parts: list[str] = []
    provenance_nodes: list[CanonicalSourceNode] = []
    image_ordinal = 0
    repeated_numeric_noise_indexes = _repeated_numeric_noise_block_indexes(blocks)
    for index, block in enumerate(blocks):
        if block.block_type == "text":
            if index in repeated_numeric_noise_indexes:
                noise_blocks_dropped += 1
                continue
            clean_text, dropped_lines, drop_block = _clean_parser_text_block(block)
            noise_lines_dropped += dropped_lines
            if drop_block:
                noise_blocks_dropped += 1
                continue
            value = _as_markdown_text(block, clean_text)
            if value.strip():
                part = value.strip("\n")
                parts.append(part)
                provenance_nodes.append(_make_source_node(block, part))
            continue

        if block.block_type == "table":
            value = _table_markdown(block)
            if value.strip():
                part = value.strip("\n")
                parts.append(part)
                provenance_nodes.append(_make_source_node(block, part))
            continue

        if block.block_type == "image":
            image_ordinal += 1
            asset_id = _asset_id(block, image_ordinal)
            raw_caption = _block_caption(block)
            ocr_text = _block_ocr(block)
            footnote = _block_footnote(block)
            page_extent = _page_extent(blocks, block.page_no)
            normalized_bbox = (
                normalize_bbox(block.bbox, page_extent=page_extent)
                if page_extent is not None
                else None
            )
            spatial_enabled = _is_reliable_mineru_block(block)
            relation = resolve_image_relation(
                caption=raw_caption,
                ocr_text=ocr_text,
                footnote=footnote,
                page_no=block.page_no,
                image_bbox=normalized_bbox,
                raw_image_bbox=valid_bbox(block.bbox),
                normalization_page_extent=page_extent,
                image_order=block.order_index,
                spatial_candidates=_spatial_relation_candidates(
                    blocks,
                    image_index=index,
                    page_no=block.page_no,
                    page_extent=page_extent,
                ),
                bbox_coordinate_space=(
                    "normalized_page_extent_derived_from_mineru" if normalized_bbox else ""
                ),
                enable_spatial=spatial_enabled,
            )
            block_text = normalize_extracted_rich_text(block.text).strip()
            if relation.caption_suppressed and relation_text_key(block_text) == relation_text_key(raw_caption):
                block_text = ""
            alt_text = relation.caption or block_text or f"图片 {image_ordinal}"
            context_header = normalize_extracted_rich_text(block.context_header).strip()
            if (
                relation.caption_suppressed
                and relation_text_key(context_header) == relation_text_key(raw_caption)
            ):
                context_header = ""
            asset_metadata = {
                **dict(_block_meta(block)),
                "image_relation": relation.to_metadata(),
            }
            asset = CanonicalAsset(
                asset_id=asset_id,
                alt_text=alt_text,
                caption=relation.caption,
                caption_method=relation.caption_method,
                binding_score=relation.binding_score,
                ambiguous=relation.ambiguous,
                caption_ambiguous=relation.caption_ambiguous,
                footnote_ambiguous=relation.footnote_ambiguous,
                figure_ids=list(relation.figure_ids),
                deterministic_binding_allowed=relation.deterministic_binding_allowed,
                composite_figure=relation.composite_figure,
                spatial_status=relation.spatial_status,
                normalized_bbox=relation.normalized_bbox,
                bbox_coordinate_space=relation.bbox_coordinate_space,
                selected_candidate_orders=list(relation.selected_candidate_orders),
                footnote=relation.footnote,
                ocr_text=ocr_text,
                vlm_description=_block_vlm(block),
                image_path=_preserve_text(block.image_path),
                image_url=_preserve_text(block.image_url),
                image_base64=_preserve_text(block.image_base64),
                page_no=block.page_no,
                bbox=block.bbox,
                context_header=context_header,
                nearby_before=_nearest_text(blocks, index, -1, page_no=block.page_no),
                nearby_after=_nearest_text(blocks, index, 1, page_no=block.page_no),
                source_block_order=block.order_index,
                metadata=asset_metadata,
            )
            assets[asset_id] = asset
            marker_text = asset.marker
            asset.source_node_id = _source_node_id(block, marker_text)
            parts.append(marker_text)
            provenance_nodes.append(_make_source_node(block, marker_text, asset_id=asset_id))

    markdown = "\n\n".join(part for part in parts if part.strip())
    provenance_nodes = _position_canonical_nodes(parts, provenance_nodes)
    return CanonicalDocument(
        markdown_content=markdown,
        assets=assets,
        provenance_nodes=provenance_nodes,
        metadata={
            **(parsed_document.metadata or {}),
            "canonical_markdown": True,
            "canonical_source": "ordered_parsed_blocks",
            "canonical_asset_count": len(assets),
            "canonical_noise_blocks_dropped": noise_blocks_dropped,
            "canonical_noise_lines_dropped": noise_lines_dropped,
            "document_title": document_title,
        },
        source_format=source_format,
    )


def find_image_asset_refs(text: str) -> list[tuple[str, str, int, int]]:
    """Return ``(asset_id, alt, start, end)`` in source order."""
    refs: list[tuple[str, str, int, int]] = []
    for match in _IMAGE_ASSET_REF_RE.finditer(_preserve_text(text)):
        refs.append((match.group(2), match.group(1), match.start(), match.end()))
    return refs


def strip_image_asset_refs(text: str) -> str:
    return _IMAGE_ASSET_REF_RE.sub("", _preserve_text(text))


__all__ = [
    "CanonicalAsset",
    "CanonicalDocument",
    "CanonicalSourceNode",
    "build_canonical_document",
    "find_image_asset_refs",
    "ground_canonical_provenance",
    "strip_image_asset_refs",
]
