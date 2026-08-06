"""Content cleaning helpers for chunk storage, embedding and keyword indexing.

The parser/adapters may provide display-style text such as::

    文档标题：...
    上下文：...
    正文：...
    图片URL：http://...

Those labels are useful for UI rendering, but they should not be persisted as
chunk.content or embedded/indexed as semantic text.  This module keeps the
semantic value and removes transport/display noise.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any

from app.core.config import settings

from app.utils.text import sanitize_text_for_postgres

# ---------------------------------------------------------------------------
# OCR quarantine constants — shared across content_cleaning, embedding_service,
# keyword_index_service, chunk_policy_resolver, and canonical_parent_child.
# ---------------------------------------------------------------------------
OCR_IMAGE_MODE_SHADOW_AUXILIARY = "shadow_auxiliary"
OCR_IMAGE_MODE_NORMAL = "normal"
METADATA_KEY_IMAGE_OCR_MODE = "chunk_policy_image_ocr_mode"
SKIP_REASON_VISUAL_OCR_QUARANTINE = "visual_ocr_quarantine"

MIN_CHILD_TEXT_CHARS = 120
MIN_PARENT_TEXT_CHARS = 200

_URL_RE = re.compile(r"https?://\S+", re.I)
_FILE_PATH_RE = re.compile(
    r"(?:(?:/files/|files/|kb/[0-9a-fA-F-]+/documents/[0-9a-fA-F-]+/media/|/data/|[A-Za-z]:\\)\S+)",
    re.I,
)
_MEDIA_FILENAME_RE = re.compile(r"\S+\.(?:png|jpg|jpeg|webp|gif|bmp|tif|tiff)(?:\?\S*)?$", re.I)

_DOMAIN_ONLY_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+){1,}(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?$",
    re.I,
)
_EMAIL_ONLY_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)
_LOCAL_PATH_ONLY_RE = re.compile(
    r"^(?:[A-Za-z]:\\|/data/|/files/|files/|kb/[0-9a-fA-F-]+/documents/|minio://)\S+$",
    re.I,
)

# Placeholder-only visual/table/equation chunks should never be embedded.
_MEDIA_PLACEHOLDER_COMPACT = {
    "图", "图片", "图像", "图表", "示意图", "表", "表格", "公式", "方程", "插图",
    "image", "figure", "fig", "chart", "graph", "plot", "table", "equation", "formula",
}

# Tags that often appear inside extracted scientific text.  For text content we
# keep the inner value but remove the markup, e.g. TGF-β<sub>1</sub> -> TGF-β1.
_INLINE_HTML_TAG_RE = re.compile(
    r"</?(?:sub|sup|span|font|b|strong|i|em|u|s|strike|del|ins|small|mark|"
    r"ruby|rt|rp|q|cite|abbr|dfn|time|var|samp|kbd)[^>]*>",
    re.I,
)
_FENCED_CODE_LINE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_GENERIC_MARKUP_TAG_RE = re.compile(
    r"</?[A-Za-z][A-Za-z0-9:._-]*(?:\s+[^<>]*?)?\s*/?>",
    re.I,
)



def is_url_or_path_only_text(text: Any) -> bool:
    """Return True for standalone URLs/domains/emails/local media paths.

    These are usually page headers/footers or resource pointers.  They should
    not create standalone chunks.  The actual resource URL is stored in
    image_info / metadata, not in chunk.content.
    """
    raw = sanitize_text_for_postgres(str(text or "")).strip()
    if not raw:
        return True
    if len(raw) > 300:
        return False
    value = raw.strip().strip("<>《》【】[]（）()。；;，,、 \t\n\r")
    if not value:
        return True
    if _URL_RE.fullmatch(value):
        return True
    if _DOMAIN_ONLY_RE.fullmatch(value):
        return True
    if _EMAIL_ONLY_RE.fullmatch(value):
        return True
    if _LOCAL_PATH_ONLY_RE.fullmatch(value):
        return True
    if _MEDIA_FILENAME_RE.fullmatch(value):
        return True
    # If cleaning removes everything, the original text was only transport noise.
    cleaned = _URL_RE.sub("", value)
    cleaned = _FILE_PATH_RE.sub("", cleaned)
    cleaned = cleaned.strip().strip("。；;，,、")
    return not bool(cleaned)


def is_standalone_noise_text(text: Any) -> bool:
    """Noise predicate for parsed text blocks before chunk creation."""
    if is_url_or_path_only_text(text):
        return True
    cleaned = clean_chunk_content_for_storage(text, chunk_type="text")
    if not cleaned:
        return True
    return is_low_information_text(cleaned)


def _strip_html_fragment(value: str, *, chunk_type: str) -> str:
    kind = (chunk_type or "text").lower()
    if kind in {"code", "code_block"}:
        return value

    if kind in {"table", "table_body"} or re.search(r"<\s*table\b|<\s*tr\b|<\s*t[dh]\b", value, re.I):
        value = re.sub(r"<\s*/\s*t[dh]\s*>", " | ", value, flags=re.I)
        value = re.sub(r"<\s*/\s*tr\s*>", "\n", value, flags=re.I)
        value = re.sub(r"<\s*/\s*(?:table|thead|tbody|tfoot|caption)\s*>", "\n", value, flags=re.I)
        value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.I)
        value = re.sub(r"<[^>]+>", " ", value)
        return value

    value = re.sub(r"<!--[\s\S]*?-->", " ", value)
    value = re.sub(
        r"<\s*(?:script|style|noscript|svg)\b[^>]*>[\s\S]*?<\s*/\s*(?:script|style|noscript|svg)\s*>",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.I)
    value = re.sub(
        r"<\s*/\s*(?:p|div|li|ul|ol|section|article|header|footer|blockquote|pre|h[1-6])\s*>",
        "\n",
        value,
        flags=re.I,
    )
    value = _INLINE_HTML_TAG_RE.sub("", value)
    value = re.sub(r"<\s*[!?][^>]*>", "", value)
    value = _GENERIC_MARKUP_TAG_RE.sub("", value)
    return value


def _strip_inline_html(text: str, *, chunk_type: str = "text") -> str:
    """Remove extracted rich-text markup while preserving fenced source code."""
    value = unescape(str(text or ""))
    if "<" not in value or ">" not in value:
        return value
    kind = (chunk_type or "text").lower()
    if kind in {"table", "table_body", "code", "code_block"}:
        return _strip_html_fragment(value, chunk_type=kind)

    output: list[str] = []
    plain_lines: list[str] = []
    fence = ""

    def flush_plain_lines() -> None:
        if not plain_lines:
            return
        output.append(_strip_html_fragment("".join(plain_lines), chunk_type=kind))
        plain_lines.clear()

    for line in value.splitlines(keepends=True):
        match = _FENCED_CODE_LINE_RE.match(line)
        if fence:
            output.append(line)
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= len(fence)
            ):
                fence = ""
            continue
        if match:
            flush_plain_lines()
            marker = match.group(1)
            fence = marker
            output.append(line)
            continue
        plain_lines.append(line)
    flush_plain_lines()
    return "".join(output)


def normalize_extracted_rich_text(text: Any, *, chunk_type: str = "text") -> str:
    """Normalize parser presentation markup before canonical splitting."""
    value = _strip_inline_html(str(text or ""), chunk_type=chunk_type)
    value = re.sub(r"[ \t\u00a0]+", " ", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return sanitize_text_for_postgres(value.strip())

_DROP_LINE_LABELS = {
    "文档标题",
    "文件名",
    "文件",
    "图片url",
    "图片URL",
    "url",
    "URL",
    "图片文件",
    "文件路径",
    "本地路径",
    "media",
    "路径",
    "上下文",
    "前文",
    "后文",
    "绑定标题",
}

_KEEP_VALUE_LABELS = {
    "正文",
    "内容",
    "图片标题",
    "图片描述",
    "图片内容",
    "OCR摘录",
    "OCR文本",
    "表格标题",
    "表格内容",
    "表格HTML文本",
    "公式",
    "公式内容",
    "摘要",
    "关键词",
}

_LOW_INFO_EXACT = {
    "引言", "引 言", "introduction",
    "参考文献", "参 考 文 献", "references", "reference",
    "结论", "结 论", "讨论", "致谢", "摘要", "关键词",
    "材料", "方法", "结果", "结果与分析", "总结与展望", "结语与展望", "结束语", "结语",
    "材料与方法", "实验", "实验方法", "结构鉴定", "提取与分离",
}

_LOW_INFO_SECTION_RE = re.compile(
    r"^\s*(?:第?[一二三四五六七八九十百]+章|第?\d+(?:\.\d+)*\s*)?"
    r"(?:引\s*言|参考文献|参\s*考\s*文\s*献|结\s*论|结语|结束语|总结与展望|结语与展望|"
    r"讨论|材料|方法|材料与方法|实验|实验方法|结果|结果与分析|结构鉴定|提取与分离)\s*[:：。．.]?$",
    re.I,
)
_SECTION_TITLE_RE = re.compile(r"^\s*\d+(?:\.\d+)*\s*[\u4e00-\u9fffA-Za-z（）()、·\-\s]{1,24}\s*$")
# After sentence-unit packing refactor, chunks should not start with punctuation.
# We only strip leading whitespace here.  Punctuation stripping was the old
# pseudo-fix that masked bad sentence boundaries — it is no longer the primary
# mechanism.  The postprocess_chunk_boundaries function handles genuine edge
# cases via merge-first repair.
_LEADING_WHITESPACE_RE = re.compile(r"^[\s]+")
_CJK_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])")
_CJK_PUNCT_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[，。！？；：、])|(?<=[，。！？；：、])[ \t]+(?=[\u4e00-\u9fff])")


def _normalize_lines(text: str, *, chunk_type: str = "text") -> list[str]:
    value = _strip_inline_html(str(text or ""), chunk_type=chunk_type)
    value = sanitize_text_for_postgres(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _URL_RE.sub("", value)
    value = _FILE_PATH_RE.sub("", value)
    value = re.sub(r"[ \t\u00a0]+", " ", value)
    return [line.strip() for line in value.split("\n")]


def _split_label(line: str) -> tuple[str, str] | None:
    if "：" in line:
        label, value = line.split("：", 1)
    elif ":" in line:
        label, value = line.split(":", 1)
    else:
        return None
    return label.strip(), value.strip()


def _strip_wrapper_line(line: str, *, drop_context_lines: bool = True) -> str:
    line = line.strip()
    if not line:
        return ""
    if _MEDIA_FILENAME_RE.fullmatch(line):
        return ""
    pair = _split_label(line)
    if not pair:
        return line
    label, value = pair
    label_norm = label.strip()
    label_lower = label_norm.lower()
    if label_norm in _DROP_LINE_LABELS or label_lower in {x.lower() for x in _DROP_LINE_LABELS}:
        return ""
    if label_norm in _KEEP_VALUE_LABELS or label_lower in {x.lower() for x in _KEEP_VALUE_LABELS}:
        return value
    # Unknown short Chinese labels are often display wrappers.  Keep the value
    # when the label itself is not semantically important.
    if len(label_norm) <= 8 and re.search(r"[\u4e00-\u9fff]", label_norm):
        return value
    return line


def clean_chunk_content_for_storage(text: Any, *, chunk_type: str = "text") -> str:
    """Clean text before persisting into chunks.content.

    It removes display labels and URLs, but keeps semantic values such as body,
    table content, image description and OCR text.
    """
    lines: list[str] = []
    normalized_text = _strip_inline_html(str(text or ""), chunk_type=chunk_type)
    for line in _normalize_lines(normalized_text, chunk_type=chunk_type):
        cleaned = _strip_wrapper_line(line)
        if cleaned:
            lines.append(cleaned)
    value = "\n".join(lines)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    return sanitize_text_for_postgres(value.strip())


def normalize_chunk_text(text: str) -> str:
    """Normalize generic prose chunk text without touching code/path syntax."""
    value = sanitize_text_for_postgres(str(text or ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = _CJK_SPACE_RE.sub("", value)
    value = _CJK_PUNCT_SPACE_RE.sub("", value)
    value = value.strip()
    # Only strip leading whitespace — punctuation should be handled by
    # sentence-unit packing, not by regex post-processing.
    value = _LEADING_WHITESPACE_RE.sub("", value).strip()
    return sanitize_text_for_postgres(value)


def clean_text_for_embedding(text: Any, *, chunk_type: str = "text") -> str:
    """Clean text before embedding/BM25 indexing."""
    value = clean_chunk_content_for_storage(str(text or ""), chunk_type=chunk_type)
    value = _URL_RE.sub("", value)
    value = _FILE_PATH_RE.sub("", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return sanitize_text_for_postgres(value.strip())


def compact_text_for_label_check(text: Any) -> str:
    value = sanitize_text_for_postgres(str(text or ""))
    value = re.sub(r"[\s\u3000]+", "", value)
    value = value.strip("：:。．.、;；")
    return value


def is_low_information_text(text: Any) -> bool:
    """Return True for obvious section headings / labels that should not be indexed alone."""
    raw = sanitize_text_for_postgres(str(text or "")).strip()
    if not raw:
        return True
    cleaned = clean_text_for_embedding(raw)
    compact = compact_text_for_label_check(cleaned)
    if not compact:
        return True
    lower = compact.lower()
    if lower in _LOW_INFO_EXACT:
        return True
    if len(compact) <= 2:
        return True
    if len(compact) <= 12 and _LOW_INFO_SECTION_RE.match(cleaned):
        return True
    if len(compact) <= 24 and _SECTION_TITLE_RE.match(cleaned):
        # Treat short numbered titles as non-indexable standalone chunks.
        # Their text is still represented in context_header / heading_path.
        return True
    # Text under 30 chars without sentence punctuation is almost certainly a
    # metadata fragment (patent number, page-count line, isolated label), not a
    # retrievable sentence.
    if len(compact) < 30 and not re.search(r"[。！？!?；;，,]", cleaned):
        return True
    return False


def _is_placeholder_media_content(text: Any) -> bool:
    compact = compact_text_for_label_check(text).lower()
    compact = compact.strip("[]【】（）()<>《》")
    if not compact:
        return True
    return compact in _MEDIA_PLACEHOLDER_COMPACT




# Metadata/flag keys that describe embedding skip decisions.  These keys must
# not be propagated from an orphan short chunk into a merged, meaningful chunk.
_EMBEDDING_SKIP_METADATA_KEYS = {
    "skip_embedding_recommended",
    "embedding_skip_recommended",
    "skip_reason",
    "embedding_skip_reason",
    "orphan_short_chunk",
    "orphan_short_heading",
    "low_information",
    "low_content_chunk",
}

_STALE_SKIP_REASONS = {
    "orphan_short_chunk",
    "orphan_short_heading",
    "low_information_text",
    "embedding_content_empty",
}

_LEGACY_VISUAL_OCR_SKIP_REASON = SKIP_REASON_VISUAL_OCR_QUARANTINE


def clear_embedding_skip_metadata(mapping: dict | None) -> dict:
    """Return a copy of metadata/flags without embedding-skip decisions.

    This is intentionally used when merging short chunks into a larger target.
    The target should keep useful provenance such as absorbed_orphan_texts, but
    must not inherit skip_embedding_recommended/skip_reason from the absorbed
    fragment.
    """
    cleaned = dict(mapping or {})
    for key in _EMBEDDING_SKIP_METADATA_KEYS:
        cleaned.pop(key, None)
    return cleaned


def merge_metadata_without_embedding_skip(base: dict | None, extra: dict | None) -> dict:
    """Merge two metadata dicts without propagating skip flags from ``extra``."""
    merged = clear_embedding_skip_metadata(base)
    merged.update(clear_embedding_skip_metadata(extra))
    return merged


def looks_meaningful_for_embedding(text: Any, *, chunk_type: str = "text") -> bool:
    """Conservative positive check used to clear stale skip flags.

    It must be stricter than "not empty" but looser than length-only filtering:
    short but complete technical/list/code/table chunks should be embeddable.
    """
    raw = sanitize_text_for_postgres(str(text or "")).strip()
    if not raw or is_url_or_path_only_text(raw):
        return False
    cleaned = clean_text_for_embedding(raw)
    compact = compact_text_for_label_check(cleaned)
    if not compact:
        return False

    kind = (chunk_type or "text").lower()
    media_kinds = {"table", "table_body", "image", "chart", "graph", "plot", "figure", "fig", "equation", "formula"}
    if kind in media_kinds:
        return not _is_placeholder_media_content(cleaned) and (len(compact) >= 8 or bool(re.search(r"[|\t\n，,。；;：:]", cleaned)))

    if kind in {"text", "parent_text"}:
        if is_low_information_text(cleaned):
            return False
        if len(compact) >= 80:
            return True
        # Lists with multiple items are meaningful even when not very long.
        if len(re.findall(r"(?m)^\s*(?:[-*+]\s+|\d+[.)、]\s+)", cleaned)) >= 2:
            return True
        # JSON/code/config snippets often lack sentence punctuation but are useful.
        if "```" in cleaned or re.search(r"[{}\[\]]", cleaned) and re.search(r"[:=]", cleaned):
            return True
        if re.search(r"[A-Za-z_][A-Za-z0-9_]*\.(?:py|ts|js|vue|md|json|yaml|yml|html|css)", cleaned):
            return True
        if len(compact) >= 40 and re.search(r"[。！？!?；;，,]", cleaned):
            return True
        return False

    return bool(compact)


def clear_stale_embedding_skip_flags(
    *,
    content: Any,
    chunk_type: str = "text",
    flags: dict | None = None,
    metadata: dict | None = None,
) -> tuple[dict, dict]:
    """Clear stale skip flags when the final merged chunk is meaningful.

    Only stale reasons created during orphan/short-chunk processing are cleared.
    Trusted hard skips such as placeholder_image/table remain intact unless the
    final content is no longer a placeholder.
    """
    new_flags = dict(flags or {})
    new_meta = dict(metadata or {})
    reason = _skip_reason_from(new_flags, new_meta)
    skip_flag = _truthy(new_flags.get("skip_embedding_recommended")) or _truthy(new_meta.get("skip_embedding_recommended"))
    if not skip_flag:
        return new_flags, new_meta

    kind = (chunk_type or "text").lower()
    if looks_meaningful_for_embedding(content, chunk_type=kind) and (reason in _STALE_SKIP_REASONS or not reason):
        new_flags = clear_embedding_skip_metadata(new_flags)
        new_meta = clear_embedding_skip_metadata(new_meta)
    return new_flags, new_meta


def clear_legacy_visual_ocr_skip_flags(
    *,
    flags: dict | None = None,
    metadata: dict | None = None,
) -> tuple[dict, dict, bool]:
    """Remove DCP-4's obsolete whole-chunk visual OCR skip decision.

    DCP-4R quarantines only the explicit ``visual_ocr`` content channel.  Old
    rows may still carry ``skip_reason=visual_ocr_quarantine`` in metadata,
    flags, or both.  Keeping that trusted whole-chunk marker would make a safe
    field-level composer return no content on every re-embedding attempt.
    """

    recovered = False

    def clean(mapping: dict | None) -> dict:
        nonlocal recovered
        result = dict(mapping or {})
        reason = str(
            result.get("skip_reason") or result.get("embedding_skip_reason") or ""
        ).strip()
        if reason != _LEGACY_VISUAL_OCR_SKIP_REASON:
            return result
        recovered = True
        for key in (
            "skip_embedding_recommended",
            "embedding_skip_recommended",
            "skip_reason",
            "embedding_skip_reason",
        ):
            result.pop(key, None)
        return result

    return clean(flags), clean(metadata), recovered

_TRUSTED_SKIP_REASONS = {
    "placeholder_table",
    "placeholder_image",
    "placeholder_equation",
    "placeholder_media",
    "empty_content",
    "low_information_text",
    "orphan_short_heading",
    "orphan_short_chunk",
    "page_noise",
    "header_footer_noise",
    "pure_url_or_path",
}


def field_content_admission_enabled() -> bool:
    """Return whether DCP-4R field-level admission is enabled.

    The legacy quarantine flag is accepted as a compatibility trigger, but its
    semantics are deliberately narrowed to the OCR field.  This makes existing
    deployments safe immediately instead of retaining the old whole-chunk skip.
    """

    return bool(
        getattr(settings, "document_field_content_admission_enabled", False)
        or settings.document_visual_ocr_quarantine_enabled
    )


def is_visual_ocr_quarantined(
    metadata: dict | None, *, enabled: bool | None = None
) -> bool:
    """Return True when a chunk's OCR text should be quarantined from embedding.

    Only active when DOCUMENT_VISUAL_OCR_QUARANTINE_ENABLED is True and the
    chunk was produced by a visual-collection policy (calligraphy, artwork,
    paintings).  Scanned-prose and poster-page OCR text are never quarantined
    because their ``image_ocr_mode`` remains ``"normal"``.
    """
    if enabled is None:
        enabled = field_content_admission_enabled()
    if not enabled:
        return False
    metadata = metadata or {}
    return metadata.get(METADATA_KEY_IMAGE_OCR_MODE) == OCR_IMAGE_MODE_SHADOW_AUXILIARY


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def _skip_reason_from(flags: dict | None, metadata: dict | None) -> str:
    for mapping in (flags or {}, metadata or {}):
        value = mapping.get("skip_reason") or mapping.get("embedding_skip_reason")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def should_skip_text_chunk(text: Any, *, chunk_type: str = "text", flags: dict | None = None, metadata: dict | None = None) -> bool:
    """Return True when a chunk should not be embedded/indexed.

    This function is the final conservative gate before embedding.  It should
    reject obvious noise/placeholders, but it must not reject meaningful short
    sentences only because they are shorter than ``MIN_CHILD_TEXT_CHARS``.

    Important behavior:
    - Explicit ``skip_embedding_recommended`` is only a hard skip when it carries
      a trusted ``skip_reason`` such as ``placeholder_table`` or
      ``orphan_short_heading``.
    - Old chunks may have ``skip_embedding_recommended=true`` without a reason;
      for those we re-check the actual content and allow meaningful text/code
      to be embedded.
    - Table/image/equation chunks are skipped only when their content is empty
      or a pure placeholder such as "表格" / "图片" / "公式".
    """
    flags = flags or {}
    metadata = metadata or {}
    kind = (chunk_type or "text").lower()
    cleaned = clean_text_for_embedding(text, chunk_type=kind)
    compact = compact_text_for_label_check(cleaned)

    if is_url_or_path_only_text(text):
        return True

    skip_flag = _truthy(flags.get("skip_embedding_recommended")) or _truthy(metadata.get("skip_embedding_recommended"))
    skip_reason = _skip_reason_from(flags, metadata)

    if skip_flag and skip_reason in _TRUSTED_SKIP_REASONS:
        return True

    if not compact:
        return True

    media_kinds = {"table", "table_body", "image", "chart", "graph", "plot", "figure", "fig", "equation", "formula"}
    if kind in media_kinds:
        if _is_placeholder_media_content(cleaned):
            return True
        if len(compact) < 8 and not re.search(r"[。！？!?；;，,|\t\n]", cleaned):
            return True
        return False

    if kind in {"text", "parent_text"}:
        return is_low_information_text(cleaned)

    # Unknown/custom chunk types: only skip empty or explicit trusted-noise.
    return False
