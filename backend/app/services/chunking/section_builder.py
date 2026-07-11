"""Group ParsedBlock[] → SectionDraft[] for parent-child chunk construction.

Extracted from chunk_service.py — all functions are pure logic operating on
ParsedBlock objects.  No database dependency.
"""

from __future__ import annotations

from typing import Any

from app.services.chunker.heading_utils import (
    heading_level_from_metadata,
    is_caption_text,
    is_page_noise_text,
    looks_like_section_heading,
    make_continuation_header,
    normalize_context_header,
    strip_continuation_suffix,
)
from app.services.content_cleaning import (
    clean_chunk_content_for_storage,
    is_low_information_text,
    is_url_or_path_only_text,
    should_skip_text_chunk,
)
from app.utils.text import sanitize_text_for_postgres

# ---------------------------------------------------------------------------
# Constants (migrated from chunk_service.py)
# ---------------------------------------------------------------------------

_NOISE_BLOCK_TYPES = {"header", "footer", "page_number"}
_IMAGE_LIKE_RAW_TYPES = {"image", "figure", "fig", "chart", "graph", "plot"}
_TABLE_LIKE_RAW_TYPES = {"table", "table_body"}
_EQUATION_LIKE_RAW_TYPES = {"equation", "formula", "inline_equation", "interline_equation", "display_equation"}
_HEADING_RAW_TYPES = {"title", "heading", "section_title"}
_CAPTION_KEYWORDS = {
    "image": ("图", "figure", "fig.", "fig ", "chart", "plot", "graph"),
    "table": ("表", "table"),
    "equation": ("公式", "equation", "formula", "eq."),
}

MIN_CHILD_TEXT_CHARS = 120
MIN_PARENT_TEXT_CHARS = 200


# ---------------------------------------------------------------------------
# Block-type classifiers (pure functions on ParsedBlock)
# ---------------------------------------------------------------------------


def raw_block_type(block: Any) -> str:
    meta = getattr(block, "metadata", None) or {}
    for key in ("raw_block_type", "type", "block_type", "semantic_type", "subtype"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return (getattr(block, "block_type", None) or "text").strip().lower()


def is_noise_block(block: Any) -> bool:
    rtype = raw_block_type(block)
    if rtype in _NOISE_BLOCK_TYPES:
        return True
    meta = getattr(block, "metadata", None) or {}
    role = str(meta.get("role") or meta.get("layout_role") or "").strip().lower()
    text = sanitize_text_for_postgres(
        getattr(block, "text", "") or getattr(block, "index_text", lambda: "")() or getattr(block, "context_header", "")
    )
    if role in _NOISE_BLOCK_TYPES or is_page_noise_text(text):
        return True
    if is_url_or_path_only_text(text):
        return True
    return False


def is_equation_block(block: Any) -> bool:
    rtype = raw_block_type(block)
    if rtype in _EQUATION_LIKE_RAW_TYPES:
        return True
    meta = getattr(block, "metadata", None) or {}
    for key in ("latex", "equation", "formula"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def is_heading_block(block: Any) -> bool:
    rtype = raw_block_type(block)
    meta = getattr(block, "metadata", None) or {}
    text = sanitize_text_for_postgres(
        getattr(block, "text", "") or getattr(block, "context_header", "")
    )
    return looks_like_section_heading(text, meta, rtype)


def heading_level(block: Any) -> int:
    return heading_level_from_metadata(getattr(block, "metadata", None))


def normalized_child_type(block: Any) -> str:
    rtype = raw_block_type(block)
    btype = getattr(block, "block_type", None) or "text"
    if btype == "table" or rtype in _TABLE_LIKE_RAW_TYPES:
        return "table"
    if btype == "image" or rtype in _IMAGE_LIKE_RAW_TYPES:
        return "image"
    if is_equation_block(block):
        return "equation"
    return "text"


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------


def _first_text_deep(mapping: dict[str, Any] | None, keys: list[str]) -> str:
    """Find first non-empty string matching a key in a deeply nested dict."""
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text_for_postgres(value.strip())
    for value in mapping.values():
        if isinstance(value, dict):
            found = _first_text_deep(value, keys)
            if found:
                return found
    return ""


def block_context_title(block: Any) -> str:
    meta = getattr(block, "metadata", None) or {}
    return sanitize_text_for_postgres(
        getattr(block, "context_header", "")
        or _first_text_deep(meta, ["context_header", "caption", "figure_caption", "table_caption", "title", "image_caption"])
        or ""
    )


def heading_path_text(path: list[str]) -> str:
    return " > ".join(normalize_context_header(item) for item in path if item)


def parent_context_header(section: dict[str, Any]) -> str:
    heading = normalize_context_header(heading_path_text(section.get("heading_path") or []))
    if heading:
        return heading
    title = sanitize_text_for_postgres(section.get("title") or "")
    if title:
        return normalize_context_header(title)
    page_start = section.get("page_start")
    if page_start:
        return f"第 {page_start} 页内容"
    return "文档内容"


# ---------------------------------------------------------------------------
# Block parent text (compact summary per block type)
# ---------------------------------------------------------------------------


def _short_preview(text: str, limit: int = 600) -> str:
    text = sanitize_text_for_postgres(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _compose_semantic_parts(parts: list[tuple[str, str]], *, fallback: str = "") -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for label, value in parts:
        value = sanitize_text_for_postgres(value or "").strip()
        if not value:
            continue
        import re
        normalized = re.sub(r"\s+", " ", value).strip().lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        lines.append(f"{label}：{value}" if label else value)
    if not lines and fallback:
        lines.append(fallback)
    return sanitize_text_for_postgres("\n".join(lines))


def _block_ocr_text(block: Any) -> str:
    meta = getattr(block, "metadata", None) or {}
    image_info = meta.get("image_info") if isinstance(meta.get("image_info"), dict) else {}
    for source in (meta, image_info):
        value = _first_text_deep(source, ["ocr_raw_text", "ocr_cleaned_text", "ocr_text"])
        if value:
            return value
    return ""


def _infer_page_no_from_text(text: str | None) -> int | None:
    import re
    text = sanitize_text_for_postgres(text or "")
    if not text:
        return None
    patterns = [
        r"raster\s+page\s+([0-9]{1,4})",
        r"page\s+([0-9]{1,4})\s*/\s*[0-9]{1,4}",
        r"第\s*([0-9]{1,4})\s*页",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            try:
                value = int(m.group(1))
                if 1 <= value <= 10000:
                    return value
            except Exception:
                pass
    return None


def _page_no_from_sources(*sources: Any) -> int | None:
    for source in sources:
        if source is None:
            continue
        if isinstance(source, int):
            return source if source > 0 else None
        if isinstance(source, str):
            if source.isdigit() and int(source) > 0:
                return int(source)
            inferred = _infer_page_no_from_text(source)
            if inferred is not None:
                return inferred
            continue
        if isinstance(source, dict):
            for key in ("page_no", "source_page", "page", "page_index"):
                value = source.get(key)
                if isinstance(value, int):
                    return value if value > 0 else None
                if isinstance(value, str) and value.isdigit():
                    return int(value)
    return None


def block_page_no(block: Any, content_hint: str = "") -> int | None:
    meta = getattr(block, "metadata", None) or {}
    image_info = meta.get("image_info") if isinstance(meta.get("image_info"), dict) else {}
    return _page_no_from_sources(
        getattr(block, "page_no", None), meta, image_info,
        getattr(block, "text", ""), content_hint,
        getattr(block, "context_header", ""),
    )


def _block_index_content(block: Any, file_type: str) -> str:
    """Build semantic index content for visual/table blocks (no URLs in content)."""
    from app.services.content_cleaning import _plain_text_from_html as _html_to_text
    meta = getattr(block, "metadata", None) or {}
    if getattr(block, "block_type", "") == "table":
        table_text = getattr(block, "text", "") or _html_to_text(getattr(block, "table_html", ""))
        subtype = _first_text_deep(meta, ["table_subtype"])
        label = "表格内容"
        if subtype in {"pseudo_table", "layout_text"}:
            label = "疑似表格/排版文本"
        elif subtype == "key_value_table":
            label = "键值表内容"
        return clean_chunk_content_for_storage(
            _compose_semantic_parts(
                [
                    ("表格标题", _first_text_deep(meta, ["caption", "table_caption", "title", "image_caption"])),
                    (label, table_text),
                    ("OCR文本", _first_text_deep(meta, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"])),
                ],
                fallback="",
            ),
            chunk_type="table",
        )
    if getattr(block, "block_type", "") == "image":
        ocr_preview = _short_preview(
            _first_text_deep(meta, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"]), 600
        )
        return clean_chunk_content_for_storage(
            _compose_semantic_parts(
                [
                    ("图片标题", _first_text_deep(meta, ["caption", "figure_caption", "title", "image_caption"])),
                    ("图片描述", _first_text_deep(meta, ["vlm_description", "description", "image_description"])),
                    ("OCR摘录", ocr_preview),
                ],
                fallback="",
            ),
            chunk_type="image",
        )
    return clean_chunk_content_for_storage(getattr(block, "text", ""), chunk_type="text")


def block_text_for_context(block: Any, file_type: str = "") -> str:
    btype = getattr(block, "block_type", "")
    if btype in {"image", "table"}:
        return _block_index_content(block, file_type)
    return sanitize_text_for_postgres(
        getattr(block, "index_text", lambda: "")()
        or getattr(block, "text", "")
        or getattr(block, "context_header", "")
    )


def block_parent_text(block: Any, file_type: str) -> str:
    """Build compact parent-text representation for a block."""
    meta = getattr(block, "metadata", None) or {}
    raw_text = (getattr(block, "index_text", lambda: "")() or getattr(block, "text", ""))
    rtype = raw_block_type(block)

    # Code block
    if rtype in {"code", "code_block", "fenced_code", "pre", "source_code"} or (
        meta.get("content_kind") == "code_block"
    ):
        lead = sanitize_text_for_postgres(meta.get("lead_in_text") or "")
        tail = sanitize_text_for_postgres(meta.get("tail_note_text") or "")
        preview = _short_preview(raw_text, 1200)
        return sanitize_text_for_postgres("\n\n".join(part for part in [lead, preview, tail] if part))

    # Structure-like text
    from app.services.chunker.protected_patterns import is_structure_like_text, looks_like_directory_tree
    source = "\n".join(part for part in [getattr(block, "context_header", ""), raw_text] if part)
    if is_structure_like_text(source):
        content = clean_chunk_content_for_storage(raw_text, chunk_type="text")
        return _short_preview(content, 1800)

    # Table
    if getattr(block, "block_type", "") == "table" or rtype in _TABLE_LIKE_RAW_TYPES:
        caption = _first_text_deep(meta, ["caption", "table_caption", "title", "image_caption"])
        from app.services.content_cleaning import _plain_text_from_html as _html_to_text
        table_text = clean_chunk_content_for_storage(
            getattr(block, "text", "") or _html_to_text(getattr(block, "table_html", "")),
            chunk_type="table",
        )
        preview = _short_preview(table_text, 320)
        return _compose_semantic_parts(
            [("表格标题", caption), ("表格摘要", preview)], fallback=preview
        )

    # Image
    if getattr(block, "block_type", "") == "image" or rtype in _IMAGE_LIKE_RAW_TYPES:
        caption = _first_text_deep(meta, ["caption", "figure_caption", "title", "image_caption"])
        desc = _first_text_deep(meta, ["vlm_description", "description", "image_description"])
        ocr = _short_preview(_block_ocr_text(block), 180)
        page = block_page_no(block, getattr(block, "text", "") or getattr(block, "context_header", ""))
        return _compose_semantic_parts(
            [
                ("图片标题", caption),
                ("图片描述", desc),
                ("OCR摘录", ocr),
                ("页码", str(page) if page is not None else ""),
            ],
            fallback=caption or desc or (f"第 {page} 页图片" if page is not None else ""),
        )

    content = clean_chunk_content_for_storage(raw_text, chunk_type="text")
    if not content:
        return ""
    if rtype in _EQUATION_LIKE_RAW_TYPES:
        return content
    return content


def section_content_len(blocks: list[Any], file_type: str) -> int:
    total = 0
    for block in blocks:
        total += len(block_parent_text(block, file_type)) + 2
    return total


# ---------------------------------------------------------------------------
# Main section builder
# ---------------------------------------------------------------------------


def make_structured_sections(
    blocks: list[Any],
    file_type: str,
    parent_chunk_size: int = 4096,
) -> list[dict[str, Any]]:
    """Group parsed blocks into parent sections.

    Headings update context only and are not kept as standalone child blocks.
    Low-information section labels are also kept out of section blocks.
    """
    sections: list[dict[str, Any]] = []
    heading_stack: list[str] = []
    current: dict[str, Any] | None = None
    parent_max = max(parent_chunk_size or 4096, 512)

    def start_section(title: str = "", strategy: str = "synthetic") -> dict[str, Any]:
        nonlocal current
        current = {
            "title": normalize_context_header(title or ""),
            "heading_path": [normalize_context_header(item) for item in heading_stack],
            "blocks": [],
            "strategy": strategy,
            "page_start": None,
            "page_end": None,
            "skipped_heading_child_count": 0,
            "skipped_low_info_text_count": 0,
        }
        sections.append(current)
        return current

    def ensure_section(block: Any) -> dict[str, Any]:
        nonlocal current
        if current is None:
            title = block_context_title(block)
            current = start_section(title=title, strategy="synthetic")
        return current

    def update_section_page(sec: dict[str, Any], block: Any) -> None:
        page = block_page_no(
            block,
            (getattr(block, "index_text", lambda: "")() or getattr(block, "text", "")
             or _block_index_content(block, file_type)),
        )
        if page is not None:
            sec["page_start"] = page if sec.get("page_start") is None else min(sec["page_start"], page)
            sec["page_end"] = page if sec.get("page_end") is None else max(sec["page_end"], page)

    for block in sorted(blocks, key=lambda item: getattr(item, "order_index", 0)):
        if is_noise_block(block):
            continue

        raw_text_for_hint = (
            getattr(block, "index_text", lambda: "")()
            or getattr(block, "text", "")
            or getattr(block, "context_header", "")
            or ""
        )
        text_hint = clean_chunk_content_for_storage(raw_text_for_hint, chunk_type="text")
        ntype = normalized_child_type(block)

        # Drop text blocks that are pure transport/page noise before they can
        # create a synthetic parent or standalone child.  Examples: page URLs,
        # raw /files paths, and empty text after URL/path stripping.
        if ntype == "text" and (
            not text_hint
            or is_url_or_path_only_text(raw_text_for_hint)
            or should_skip_text_chunk(text_hint, chunk_type="text", metadata=getattr(block, "metadata", None) or {})
        ):
            sec = current if current is not None else None
            if sec is not None:
                sec["skipped_low_info_text_count"] = int(sec.get("skipped_low_info_text_count") or 0) + 1
                update_section_page(sec, block)
            continue

        if is_heading_block(block) and text_hint:
            level = heading_level(block)
            while len(heading_stack) >= level:
                heading_stack.pop()
            heading_stack.append(normalize_context_header(text_hint))
            sec = start_section(title=text_hint, strategy="heading")
            sec["skipped_heading_child_count"] += 1
            update_section_page(sec, block)
            continue

        if ntype == "text" and text_hint and is_low_information_text(text_hint):
            sec = ensure_section(block)
            sec["skipped_low_info_text_count"] += 1
            update_section_page(sec, block)
            continue

        sec = ensure_section(block)
        block_len = len(block_parent_text(block, file_type))
        if sec.get("blocks") and section_content_len(sec["blocks"], file_type) + block_len > parent_max:
            title = parent_context_header(sec)
            sec = start_section(
                title=make_continuation_header(title or "文档内容"), strategy="size_split"
            )
        sec["blocks"].append(block)
        update_section_page(sec, block)

    # Filter: keep only sections with meaningful blocks
    filtered: list[dict[str, Any]] = []
    for section in sections:
        # Note: code_context_merger is called by the caller after section building
        blocks_in_section = section.get("blocks") or []
        if not blocks_in_section:
            continue
        has_semantic = False
        for block in blocks_in_section:
            ctype = normalized_child_type(block)
            text = block_parent_text(block, file_type)
            if ctype in {"image", "table"} or (text and not is_low_information_text(text)):
                has_semantic = True
                break
        if has_semantic:
            filtered.append(section)
    return filtered
