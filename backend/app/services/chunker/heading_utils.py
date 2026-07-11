"""Heading/caption/page-noise helpers inspired by WeKnora structure signals."""

from __future__ import annotations

import re
from typing import Any

from app.utils.text import sanitize_text_for_postgres


MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+\S+")
CHINESE_SECTION_RE = re.compile(r"^\s*第[一二三四五六七八九十百千万0-9]+[章节篇部]\s+\S+")
NUMBERED_SECTION_RE = re.compile(r"^\s*(?:\d+\.){1,5}\d*\s+\S+")
CHINESE_ENUM_HEADING_RE = re.compile(r"^\s*[一二三四五六七八九十]+[、.．]\s+\S+")
ENGLISH_SECTION_RE = re.compile(r"^\s*(chapter|section|part)\s+([0-9]+|[ivxlcdm]+)\b", re.I)
ALL_CAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 ,/&()_-]{2,80}$")
FIGURE_CAPTION_RE = re.compile(r"^\s*(图|图表|figure|fig\.)\s*[\d一二三四五六七八九十]+(?:[.\-]\d+)?\b", re.I)
TABLE_CAPTION_RE = re.compile(r"^\s*(表|table)\s*[\d一二三四五六七八九十]+(?:[.\-]\d+)?\b", re.I)
PAGE_NOISE_RE = re.compile(r"^\s*(?:page\s+\d+\s+(?:of|/)\s+\d+|第\s*\d+\s*页|页码\s*\d*|\d{1,4})\s*$", re.I)
SENTENCE_END_RE = re.compile(r"[。！？!?；;]$")
CONTINUATION_SUFFIX_RE = re.compile(r"(?:（续）)+$")


def normalized_heading_text(text: str) -> str:
    return sanitize_text_for_postgres(text or "").strip()


def strip_continuation_suffix(header: str) -> str:
    """Remove all repeated trailing continuation markers from a header."""
    value = normalized_heading_text(header)
    while CONTINUATION_SUFFIX_RE.search(value):
        value = CONTINUATION_SUFFIX_RE.sub("", value).strip()
    return value


def make_continuation_header(header: str) -> str:
    """Return a header with exactly one trailing continuation marker."""
    base = strip_continuation_suffix(header) or "文档内容"
    return f"{base}（续）"


def normalize_context_header(header: str) -> str:
    """Collapse repeated continuation markers to at most one trailing marker."""
    value = normalized_heading_text(header)
    if not value:
        return ""
    if "（续）" not in value:
        return value
    return make_continuation_header(value)


def is_caption_text(text: str) -> bool:
    """Detect figure/table captions so they do not become section headings."""
    value = normalized_heading_text(text)
    if not value or len(value) > 260:
        return False
    return bool(FIGURE_CAPTION_RE.search(value) or TABLE_CAPTION_RE.search(value))


def is_page_noise_text(text: str) -> bool:
    """Detect page headers/footers/page numbers before reflow or heading logic."""
    value = normalized_heading_text(text)
    if not value or len(value) > 80:
        return False
    return bool(PAGE_NOISE_RE.search(value))


def heading_level_from_metadata(metadata: dict[str, Any] | None) -> int:
    meta = metadata or {}
    raw = meta.get("text_level") if meta.get("text_level") is not None else meta.get("level")
    try:
        return max(1, min(int(raw), 6))
    except Exception:
        return 1


def looks_like_section_heading(text: str, metadata: dict[str, Any] | None = None, raw_type: str = "") -> bool:
    """Strict WeKnora-style heading predicate.

    Positive signals are explicit parser heading types/flags or recognizable
    Markdown/numbered/Chinese/English section titles.  Captions, page numbers,
    pure digits and sentence-like OCR fragments are rejected even when a parser
    emits noisy level metadata.
    """
    value = normalized_heading_text(text)
    if not value or len(value) > 160:
        return False
    if is_caption_text(value) or is_page_noise_text(value) or value.isdigit():
        return False
    if SENTENCE_END_RE.search(value) and not MARKDOWN_HEADING_RE.search(value):
        return False

    meta = metadata or {}
    raw = (raw_type or "").strip().lower()
    if raw in {"title", "heading", "section_title"} or meta.get("is_heading") is True:
        return True

    patterns = (
        MARKDOWN_HEADING_RE,
        CHINESE_SECTION_RE,
        NUMBERED_SECTION_RE,
        CHINESE_ENUM_HEADING_RE,
        ENGLISH_SECTION_RE,
        ALL_CAPS_HEADING_RE,
    )
    return any(pattern.search(value) for pattern in patterns)


def heading_path_to_header(path: list[str] | tuple[str, ...] | str | None) -> str:
    if isinstance(path, str):
        return normalize_context_header(path)
    if not path:
        return ""
    return " > ".join(normalize_context_header(item) for item in path if normalized_heading_text(item))
