"""Build embedding-ready text from chunk + document — no database dependencies.

Extracted from embedding_service.py to enable pure-logic testing.
All functions only access attributes from their inputs; none touch Session or ORM.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.config import settings
from app.services.content_cleaning import clean_text_for_embedding, should_skip_text_chunk
from app.services.chunker.heading_utils import normalize_context_header
from app.utils.text import sanitize_text_for_postgres


_STRUCTURE_KINDS = {"directory_tree", "code_structure", "project_structure"}
_TREE_PREFIX_RE = re.compile(r"^[\s│├└+`\|\\/-]*(?:──|--)?\s*")
_FILE_OR_PATH_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_@+.-]+(?:/[A-Za-z0-9_@+.-]+)*/?|[A-Za-z0-9_@+.-]+\.(?:py|ts|tsx|js|jsx|vue|md|json|ya?ml|go|java|sql|css|html|toml|ini|env|txt))",
    re.I | re.ASCII,
)


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


def _semantic_body_from_chunk(chunk) -> str:
    """Extract semantic body text from a chunk, handling all chunk types.

    This is a pure function — it only reads attributes from the chunk object.
    No database access.
    """
    content = sanitize_text_for_postgres(getattr(chunk, "content", "") or "")
    chunk_type = (getattr(chunk, "chunk_type", "") or "text").lower()
    metadata = getattr(chunk, "metadata_json", None) or {}
    image_info = getattr(chunk, "image_info", None) or {}
    flags = getattr(chunk, "flags", None) or {}
    parts: list[str] = []

    def add(label: str, value: str):
        value = sanitize_text_for_postgres(value or "").strip()
        if value and value not in parts:
            parts.append(f"{label}：{value}" if label else value)

    if metadata.get("content_kind") == "code_block":
        add("代码说明", _get_text(metadata, ["lead_in_text", "code_lead_in", "description"]))
        add("代码内容", content)
        add("代码补充", _get_text(metadata, ["tail_note_text", "code_tail_note"]))
    elif chunk_type == "image":
        add("图片标题", _get_text(metadata, ["caption", "figure_caption", "title"]) or _get_text(image_info, ["caption", "title"]))
        add("图片描述", _get_text(metadata, ["vlm_description", "description", "image_description"]) or _get_text(image_info, ["vlm_description", "description"]))
        add("OCR文本", _get_text(metadata, ["ocr_text"]) or _get_text(image_info, ["ocr_text"]))
        add("图片内容", content if content and content != "图片" else "")
        add("图片文件", _get_text(image_info, ["filename", "name", "relative_path", "path"]))
    elif chunk_type == "table":
        # Table chunks may have content_kind=code_structure, but they are still
        # table evidence.  Do not run them through the directory-tree parser.
        add("表格标题", _get_text(metadata, ["caption", "table_caption", "title"]) or _get_text(image_info, ["caption", "title"]))
        add("表格内容", content)
        add("表格HTML文本", _plain_text_from_html(_get_text(image_info, ["table_html"])))
        add("OCR文本", _get_text(metadata, ["ocr_text"]) or _get_text(image_info, ["ocr_text"]))
    elif chunk_type == "equation":
        add("公式", _get_text(metadata, ["latex", "equation", "formula"]) or content)
    elif chunk_type == "image_ocr_text":
        add("图片OCR文本", content)
    elif metadata.get("content_kind") in _STRUCTURE_KINDS or flags.get("structure_like") is True:
        semantic = _semantic_body_from_structure_text(content, metadata)
        add("结构说明", semantic or content)
    else:
        add("正文", content)

    return sanitize_text_for_postgres("\n".join(p for p in parts if p.strip()) or content)


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
    chunk_type = (getattr(chunk, "chunk_type", "") or "text").lower()
    if chunk_type == "parent_text":
        return ""

    flags = getattr(chunk, "flags", None) or {}
    metadata = getattr(chunk, "metadata_json", None) or {}
    content = clean_text_for_embedding(_semantic_body_from_chunk(chunk))

    if should_skip_text_chunk(content, chunk_type=chunk_type, flags=flags, metadata=metadata):
        return ""

    title = clean_text_for_embedding(
        getattr(document, "title", "") or getattr(document, "file_name", "")
    ) if document is not None else ""
    context_header = clean_text_for_embedding(
        normalize_context_header(getattr(chunk, "context_header", "") or "")
    )

    parts: list[str] = []
    if title and title not in content:
        parts.append(title)
    if context_header and context_header not in content:
        parts.append(context_header)
    if content:
        parts.append(content)

    return clean_text_for_embedding(
        _truncate_embedding_content("", "", "\n".join(parts))
    )
