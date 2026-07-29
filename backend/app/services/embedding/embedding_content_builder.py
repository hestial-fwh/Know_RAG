"""Build embedding-ready text from chunk + document — no database dependencies.

Extracted from embedding_service.py to enable pure-logic testing.
All functions only access attributes from their inputs; none touch Session or ORM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.content_cleaning import (
    clean_text_for_embedding,
    clear_legacy_visual_ocr_skip_flags,
    is_visual_ocr_quarantined,
    should_skip_text_chunk,
)
from app.services.chunker.heading_utils import normalize_context_header
from app.utils.text import sanitize_text_for_postgres


_STRUCTURE_KINDS = {"directory_tree", "code_structure", "project_structure"}
_TREE_PREFIX_RE = re.compile(r"^[\s│├└+`\|\\/-]*(?:──|--)?\s*")
_FILE_OR_PATH_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_@+.-]+(?:/[A-Za-z0-9_@+.-]+)*/?|[A-Za-z0-9_@+.-]+\.(?:py|ts|tsx|js|jsx|vue|md|json|ya?ml|go|java|sql|css|html|toml|ini|env|txt))",
    re.I | re.ASCII,
)
CONTENT_ADMISSION_VERSION = "content-admission-v2"
_IMAGE_CONTENT_FIELD_RE = re.compile(
    r"(?m)^(章节|图片标题|图片脚注|上下文|图片描述|图片文字|图片内容|图片)[：:]\s*"
)
_IMAGE_CONTENT_FIELD_SOURCES = {
    "章节": "related_text",
    "图片标题": "caption",
    "图片脚注": "related_text",
    "上下文": "related_text",
    "图片描述": "vlm_description",
    "图片文字": "visual_ocr",
    "图片内容": "image_content",
    "图片": "image_content",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_embedding_content(title_part: str, context_part: str, body: str) -> str:
    max_chars = max(settings.embedding_content_max_chars or 6000, 1000)
    prefix = "\n\n".join(part for part in [title_part, context_part] if part)
    if not prefix:
        return body[:max_chars].strip()
    remaining = max(max_chars - len(prefix) - 2, 0)
    return f"{prefix}\n\n{body[:remaining]}".strip()


def _plain_text_from_html(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<\s*/\s*(tr|p|div|li|table|caption|h[1-6])\s*>", "\n", value, flags=re.I)
    text = re.sub(r"<\s*/?\s*(td|th)[^>]*>", "\t", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return sanitize_text_for_postgres(text.strip())


def _get_text(mapping: dict[str, Any] | None, keys: list[str]) -> str:
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_text_for_postgres(value.strip())
    return ""


# ---------------------------------------------------------------------------
# Semantic body extraction
# ---------------------------------------------------------------------------


def _is_probable_structure_tree(text: str) -> bool:
    r"""Return True only for real directory/tree-like structures.

    Some chunks are tagged as ``project_structure`` / ``code_structure`` even
    when the content is actually a normal numbered list or table sentence, for
    example::

        1. 在 `model_config_service.py` 中添加配置

    The old parser treated Chinese words as ``\w`` file tokens and collapsed
    such lines into ``1.`` / ``2.`` / ``任务``.  This function keeps structure
    parsing only for actual tree/path lists and lets ordinary prose/table text
    pass through unchanged.
    """
    lines = [sanitize_text_for_postgres(x).strip() for x in (text or "").splitlines()]
    lines = [x for x in lines if x and not x.startswith("```")]
    if not lines:
        return False

    tree_marker_count = 0
    path_like_count = 0
    for line in lines:
        if re.search(r"[│├└─]", line):
            tree_marker_count += 1

        stripped = _TREE_PREFIX_RE.sub("", line).strip()
        # Remove list numbering before judging path-like lines.
        stripped = re.sub(r"^\s*\d+(?:\.\d+)*[.)、．]?\s*", "", stripped)
        stripped_no_tick = stripped.strip("` ")
        # A path-like structure line should be mostly a path/file name, with an
        # optional short comment.  It should not be a full Chinese prose line.
        left = re.split(r"\s+#|\s+//|：|:\s+", stripped_no_tick, 1)[0].strip("` ")
        if _FILE_OR_PATH_RE.fullmatch(left) or ("/" in left and not re.search(r"[，。！？；]", left)):
            path_like_count += 1

    if tree_marker_count >= 2:
        return True
    if len(lines) >= 3 and path_like_count >= max(3, int(len(lines) * 0.6)):
        return True
    return False


def _semantic_body_from_structure_text(text: str, metadata: dict) -> str:
    """Convert real directory-tree/code-structure text into embedding-friendly lines.

    If the content is not a real tree/path list, preserve the original semantic
    text.  This prevents valid short numbered lists and table-derived code
    structures from being collapsed into meaningless tokens.
    """
    original = sanitize_text_for_postgres(text or "").strip()
    if not original:
        return ""

    if not _is_probable_structure_tree(original):
        header = _get_text(metadata, ["structure_summary", "context_header", "title"])
        if header and header not in original:
            return sanitize_text_for_postgres(f"{header}\n{original}")
        return original

    lines: list[str] = []
    current_dirs: list[str] = []
    for raw_line in original.splitlines():
        line = sanitize_text_for_postgres(raw_line).strip()
        if not line or line.startswith("```"):
            continue
        stripped = _TREE_PREFIX_RE.sub("", line).strip()
        stripped = re.sub(r"^\s*\d+(?:\.\d+)*[.)、．]?\s*", "", stripped)
        if not stripped:
            continue
        comment = ""
        for marker in (" # ", " // ", "：", ": "):
            if marker in stripped:
                left, right = stripped.split(marker, 1)
                if left.strip():
                    stripped = left.strip()
                    comment = right.strip()
                    break
        match = _FILE_OR_PATH_RE.search(stripped)
        item = match.group("path").strip() if match else stripped
        item = item.strip("` ").rstrip("/")
        if not item:
            continue
        if "/" in item and "." not in item.rsplit("/", 1)[-1]:
            current_dirs = [part for part in item.split("/") if part]
        elif "." not in item and not comment:
            current_dirs = [*current_dirs[:-1], item] if current_dirs else [item]
        context = "/".join(current_dirs)
        if comment:
            lines.append(f"{item}：{comment}")
        elif context and item not in context:
            lines.append(f"{context}/{item}")
        else:
            lines.append(item)

    header = _get_text(metadata, ["structure_summary", "context_header", "title"])
    result: list[str] = []
    seen: set[str] = set()
    for value in [header, *lines]:
        value = sanitize_text_for_postgres(value or "").strip()
        if not value:
            continue
        key = re.sub(r"\s+", " ", value).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return sanitize_text_for_postgres("\n".join(result) or original)


def _split_labeled_image_content(content: str) -> dict[str, tuple[str, ...]]:
    """Split Canonical image content into independently admissible fields.

    Canonical image children intentionally store a readable composite such as
    ``章节：...`` / ``上下文：...`` / ``图片文字：...``.  Treating that string
    as one field either leaks OCR or drops reliable neighbouring prose.
    """

    matches = list(_IMAGE_CONTENT_FIELD_RE.finditer(content))
    if not matches:
        return {}
    channels: dict[str, list[str]] = {}

    def append(source: str, value: str) -> None:
        value = sanitize_text_for_postgres(value or "").strip()
        if value and value not in channels.setdefault(source, []):
            channels[source].append(value)

    prefix = content[: matches[0].start()].strip()
    if prefix:
        append("image_content", prefix)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        append(
            _IMAGE_CONTENT_FIELD_SOURCES[match.group(1)],
            content[match.end() : end],
        )
    return {source: tuple(values) for source, values in channels.items() if values}


@dataclass(frozen=True)
class ContentAdmissionResult:
    """Field-level semantic content and its explainable admission decision."""

    content: str
    channels: dict[str, tuple[str, ...]]
    index_sources: tuple[str, ...]
    shadow_sources: tuple[str, ...]
    excluded_reasons: dict[str, str]
    legacy_skip_recovered: bool = False

    def channel_text(self, source: str) -> str:
        return "\n".join(self.channels.get(source, ())).strip()

    def to_metadata(self) -> dict[str, Any]:
        source_names = [*self.index_sources, *self.shadow_sources]
        return {
            "version": CONTENT_ADMISSION_VERSION,
            "decision": "index" if self.content else "skip",
            "index_sources": list(self.index_sources),
            "shadow_sources": list(self.shadow_sources),
            "excluded_reasons": dict(self.excluded_reasons),
            "legacy_visual_ocr_skip_recovered": self.legacy_skip_recovered,
            "channels": {
                source: {
                    "status": "shadow_auxiliary"
                    if source in self.shadow_sources
                    else "normal",
                    "char_count": len(self.channel_text(source)),
                    "preview": self.channel_text(source)[:240],
                }
                for source in source_names
            },
        }


def _content_channels_from_chunk(chunk) -> dict[str, tuple[str, ...]]:
    """Extract semantic fields without deciding whether the whole chunk lives."""

    content = sanitize_text_for_postgres(getattr(chunk, "content", "") or "")
    chunk_type = (getattr(chunk, "chunk_type", "") or "text").lower()
    metadata = getattr(chunk, "metadata_json", None) or {}
    image_info = getattr(chunk, "image_info", None) or {}
    flags = getattr(chunk, "flags", None) or {}
    channels: dict[str, list[str]] = {}

    def add(source: str, value: str):
        value = sanitize_text_for_postgres(value or "").strip()
        if not value:
            return
        values = channels.setdefault(source, [])
        if value not in values:
            values.append(value)

    if metadata.get("content_kind") == "code_block":
        add("parser_text", _get_text(metadata, ["lead_in_text", "code_lead_in", "description"]))
        add("parser_text", content)
        add("parser_text", _get_text(metadata, ["tail_note_text", "code_tail_note"]))
    elif chunk_type == "image":
        caption = _get_text(
            metadata, ["caption", "figure_caption", "title"]
        ) or _get_text(image_info, ["caption", "title"])
        description = _get_text(
            metadata, ["vlm_description", "description", "image_description"]
        ) or _get_text(image_info, ["vlm_description", "description"])
        ocr_text = _get_text(
            metadata, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"]
        ) or _get_text(
            image_info, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"]
        )
        add("caption", caption)
        add("vlm_description", description)
        add("visual_ocr", ocr_text)
        labeled_channels = _split_labeled_image_content(content)
        if labeled_channels:
            for source, values in labeled_channels.items():
                for value in values:
                    add(source, value)
        elif (
            content
            and content not in {"图片", "图像", "image"}
            and content not in {caption, description, ocr_text}
        ):
            # Unknown legacy image content remains fail-open: only an explicit
            # OCR field or a Canonical ``图片文字`` label is sufficient evidence
            # to quarantine it.
            add("image_content", content)
    elif chunk_type == "table":
        # Table chunks may have content_kind=code_structure, but they are still
        # table evidence.  Do not run them through the directory-tree parser.
        add(
            "caption",
            _get_text(metadata, ["caption", "table_caption", "title"])
            or _get_text(image_info, ["caption", "title"]),
        )
        add("table_text", content)
        add(
            "table_text",
            _plain_text_from_html(_get_text(image_info, ["table_html"])),
        )
        add(
            "visual_ocr",
            _get_text(
                metadata, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"]
            )
            or _get_text(
                image_info, ["ocr_cleaned_text", "ocr_raw_text", "ocr_text"]
            ),
        )
    elif chunk_type == "equation":
        add("parser_text", _get_text(metadata, ["latex", "equation", "formula"]) or content)
    elif chunk_type == "image_ocr_text":
        add("visual_ocr", content)
    elif metadata.get("content_kind") in _STRUCTURE_KINDS or flags.get("structure_like") is True:
        semantic = _semantic_body_from_structure_text(content, metadata)
        add("parser_text", semantic or content)
    else:
        text_origin = str(
            metadata.get("text_origin")
            or metadata.get("content_origin")
            or metadata.get("source_text_origin")
            or ""
        ).strip().lower()
        semantic_role = str(metadata.get("semantic_role") or "").strip().lower()
        content_role = str(metadata.get("content_role") or "").strip().lower()
        if (
            text_origin in {"visual_ocr", "image_ocr", "ocr_vision"}
            or semantic_role in {"visual_ocr_candidate", "image_ocr_text"}
            or content_role == "image_ocr_text"
        ):
            add("visual_ocr", content)
        elif text_origin in {"native_text", "pdf_native_text"}:
            add("native_text", content)
        else:
            # Unknown/parser text is deliberately fail-open.  A page-level
            # visual policy is not enough evidence to relabel it as OCR.
            add("parser_text", content)

    return {source: tuple(values) for source, values in channels.items() if values}


_CHANNEL_LABELS = {
    "native_text": "原生文本",
    "parser_text": "正文",
    "caption": "图片标题",
    "vlm_description": "图片描述",
    "visual_ocr": "OCR文本",
    "related_text": "关联文本",
    "image_content": "图片内容",
    "table_text": "表格内容",
}


def _compose_channel_body(
    channels: dict[str, tuple[str, ...]], sources: list[str]
) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for source in sources:
        label = _CHANNEL_LABELS.get(source, "")
        for value in channels.get(source, ()):
            key = re.sub(r"\s+", " ", value).strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            parts.append(f"{label}：{value}" if label else value)
    return sanitize_text_for_postgres("\n".join(parts))


def build_content_admission(chunk, document) -> ContentAdmissionResult:
    """Compose shared embedding/BM25 content with field-level OCR admission."""

    chunk_type = (getattr(chunk, "chunk_type", "") or "text").lower()
    if chunk_type == "parent_text":
        return ContentAdmissionResult("", {}, (), (), {})

    flags = getattr(chunk, "flags", None) or {}
    metadata = getattr(chunk, "metadata_json", None) or {}
    clean_flags, clean_metadata, recovered = clear_legacy_visual_ocr_skip_flags(
        flags=flags,
        metadata=metadata,
    )
    channels = _content_channels_from_chunk(chunk)
    shadow_sources: list[str] = []
    excluded_reasons: dict[str, str] = {}
    if "visual_ocr" in channels and is_visual_ocr_quarantined(metadata):
        shadow_sources.append("visual_ocr")
        excluded_reasons["visual_ocr"] = "visual_ocr_shadow_auxiliary"

    index_sources = [
        source for source in channels if source not in set(shadow_sources)
    ]
    body = _compose_channel_body(channels, index_sources)
    content_kind = str(metadata.get("content_kind") or "").lower()
    cleaning_type = "code_block" if content_kind == "code_block" else chunk_type
    body = clean_text_for_embedding(body, chunk_type=cleaning_type)

    # A short caption or artwork name is a meaningful image retrieval key even
    # when the generic media placeholder gate would reject it by length.
    short_reliable_visual = (
        chunk_type == "image"
        and any(source in index_sources for source in ("caption", "vlm_description"))
        and bool(body)
    )
    if not short_reliable_visual and should_skip_text_chunk(
        body,
        chunk_type=cleaning_type,
        flags=clean_flags,
        metadata=clean_metadata,
    ):
        body = ""

    title = (
        clean_text_for_embedding(
            getattr(document, "title", "") or getattr(document, "file_name", "")
        )
        if document is not None
        else ""
    )
    context_header = clean_text_for_embedding(
        normalize_context_header(getattr(chunk, "context_header", "") or "")
    )
    parts: list[str] = []
    if title and title not in body:
        parts.append(title)
    if context_header and context_header not in body:
        parts.append(context_header)
    if body:
        parts.append(body)

    final_content = ""
    if body:
        final_content = clean_text_for_embedding(
            _truncate_embedding_content("", "", "\n".join(parts)),
            chunk_type=cleaning_type,
        )

    return ContentAdmissionResult(
        content=final_content,
        channels=channels,
        index_sources=tuple(index_sources),
        shadow_sources=tuple(shadow_sources),
        excluded_reasons=excluded_reasons,
        legacy_skip_recovered=recovered,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_embedding_content(chunk, document) -> str:
    """Build the text that should be embedded for a chunk.

    This composes: document title + context_header + semantic chunk body,
    truncates to the configured max, and returns cleaned text.
    Returns "" for parent_text chunks or low-information chunks.

    This is pure logic — it only reads attributes, never touches the database.
    """
    return build_content_admission(chunk, document).content
