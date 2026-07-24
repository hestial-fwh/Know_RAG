from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Document
from app.services.storage import ensure_local_document_file
from app.services.document_parser.mineru_adapter import (
    get_mineru_pdf_config,
    is_mineru_supported_format,
    parse_document_with_mineru,
)
from app.services.document_parser.page_visual_enricher import (
    attach_pdf_page_visual_profile,
    enrich_pdf_page_visuals,
)
from app.services.document_parser.ragflow_format_adapter import (
    SUPPORTED_SUFFIXES,
    parse_binary_fallback,
    parse_document_with_ragflow,
    parse_markdown,
)
from app.services.document_parser.types import ParsedDocument
from app.utils.text import sanitize_text_for_postgres


def _document_parse_options(document: Document) -> dict:
    meta = getattr(document, "metadata_json", None) or {}
    options = meta.get("parse_options") if isinstance(meta, dict) else {}
    return options if isinstance(options, dict) else {}


def _document_parser_engine(document: Document) -> str:
    options = _document_parse_options(document)
    engine = (
        (
            options.get("parser_engine")
            or options.get("parse_mode")
            or getattr(settings, "document_parser_engine", "auto")
            or "auto"
        )
        .strip()
        .lower()
    )
    if engine == "deepdoc":
        return "ragflow"
    if engine in {"auto", "mineru", "ragflow", "legacy"}:
        return engine
    return "auto"


def _document_mineru_profile(document: Document) -> str:
    options = _document_parse_options(document)
    profile = (
        (
            options.get("mineru_profile")
            or getattr(settings, "mineru_runtime_profile", "cpu")
            or "cpu"
        )
        .strip()
        .lower()
    )
    return "gpu" if profile in {"gpu", "cuda", "vlm"} else "cpu"


def _option_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "off", "no"}
    return bool(value)


def _page_visual_preservation_enabled(document: Document) -> bool:
    if not getattr(settings, "document_page_visual_preservation_enabled", False):
        return False
    options = _document_parse_options(document)
    requested = options.get(
        "preserve_page_visuals",
        options.get("enable_media_crop"),
    )
    return _option_bool(requested, True)


def _maybe_preserve_pdf_page_visuals(
    path: Path,
    document: Document,
    suffix: str,
    parsed: ParsedDocument,
) -> ParsedDocument:
    if suffix != "pdf":
        return parsed
    dominant_image_threshold = getattr(
        settings,
        "document_page_visual_min_coverage",
        0.70,
    )
    if not _page_visual_preservation_enabled(document):
        return attach_pdf_page_visual_profile(
            path,
            parsed,
            dominant_image_threshold=dominant_image_threshold,
        )
    return enrich_pdf_page_visuals(
        path,
        parsed,
        knowledge_base_id=document.knowledge_base_id,
        document_id=document.id,
        dominant_image_threshold=dominant_image_threshold,
        render_dpi=getattr(settings, "document_page_visual_render_dpi", 144),
    )


def resolve_parser_engine(
    file_type: str, db: Session | None = None, document: Document | None = None
) -> str:
    """Determine which parser engine to use for a given file type.

    Per-document upload options override global settings.  This makes the
    frontend parser selection effective instead of cosmetic.
    """
    engine = (
        _document_parser_engine(document)
        if document is not None
        else (getattr(settings, "document_parser_engine", "auto") or "auto")
        .strip()
        .lower()
    )
    profile = (
        _document_mineru_profile(document)
        if document is not None
        else getattr(settings, "mineru_runtime_profile", "cpu")
    )

    if file_type.lower().lstrip(".") in {"md", "markdown"}:
        return "native_markdown"

    if engine in {"mineru", "auto"}:
        if is_mineru_supported_format(file_type):
            if get_mineru_pdf_config(db, profile=profile):
                return "mineru"
        if engine == "mineru":
            return "ragflow"
        return "ragflow"

    if engine in {"deepdoc", "ragflow"}:
        return "ragflow"

    return "ragflow"


def parse_local_document(
    document: Document, db: Session | None = None
) -> ParsedDocument:
    """Parse a local document using the configured parser engine.

    Dispatch logic:
    - If engine is ``mineru`` and format is supported → call MinerU /file_parse
    - If MinerU fails → fallback to DeepDoc/RAGFlow (unless fallback is disabled)
    - If engine is ``ragflow``/``deepdoc`` → use original RAGFlow/DeepDoc chain
    - Unknown formats → binary fallback with metadata

    All fallback paths record ``parser_requested``, ``parser_actual``,
    ``fallback_used`` and ``fallback_reason`` in metadata.
    """
    path = ensure_local_document_file(document)
    suffix = document.file_type.lower().lstrip(".")
    engine = resolve_parser_engine(suffix, db, document=document)
    mineru_profile = _document_mineru_profile(document)

    # ── Native Markdown path ─────────────────────────────────────────────
    if engine == "native_markdown":
        parsed = parse_markdown(path)
        parsed.metadata["parser_requested"] = _document_parser_engine(document)
        parsed.metadata["parser_actual"] = parsed.metadata.get(
            "parser", "native_markdown_ordered"
        )
        parsed.metadata["fallback_used"] = False
        parsed.metadata["document_parser_engine"] = "native_markdown"
        return parsed

    # ── MinerU path ──────────────────────────────────────────────────────
    if engine == "mineru":
        mineru_cfg = get_mineru_pdf_config(db, profile=mineru_profile)
        if not mineru_cfg:
            # Should not happen — resolve_parser_engine already checked
            return _fallback_to_ragflow(
                path,
                document,
                suffix,
                requested="mineru",
                reason="MinerU config not found at parse time",
            )

        try:
            parsed = parse_document_with_mineru(
                path,
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                config=mineru_cfg,
                file_type=suffix,
                runtime_profile=mineru_profile,
            )
            parsed.metadata["parser_requested"] = "mineru"
            parsed.metadata["parser_actual"] = "mineru"
            parsed.metadata["fallback_used"] = False
            parsed.metadata["document_parser_engine"] = _document_parser_engine(
                document
            )
            parsed.metadata["mineru_runtime_profile"] = mineru_profile
            parsed.metadata["mineru_backend"] = mineru_cfg.backend
            parsed.metadata["mineru_parse_method"] = mineru_cfg.parse_method
            parsed.metadata["mineru_base_url"] = mineru_cfg.base_url
            return _maybe_preserve_pdf_page_visuals(path, document, suffix, parsed)
        except Exception as mineru_exc:
            fallback_enabled = getattr(settings, "mineru_fallback_to_deepdoc", True)
            if not fallback_enabled:
                raise
            return _fallback_to_ragflow(
                path,
                document,
                suffix,
                requested="mineru",
                reason=str(mineru_exc)[:1000],
                mineru_base_url=mineru_cfg.base_url,
            )

    # ── RAGFlow / DeepDoc path ───────────────────────────────────────────
    return _parse_with_ragflow(path, document, suffix)


def _parse_with_ragflow(path: Path, document: Document, suffix: str) -> ParsedDocument:
    """Parse document using the original RAGFlow/DeepDoc chain."""
    if suffix not in SUPPORTED_SUFFIXES:
        fallback = parse_binary_fallback(
            path,
            parser="ragflow_generic_attachment",
            reason="未知格式已保存为附件，并生成可检索占位切片",
        )
        fallback.metadata["parser_requested"] = _document_parser_engine(document)
        fallback.metadata["parser_actual"] = fallback.metadata.get(
            "parser", "ragflow_generic_attachment"
        )
        fallback.metadata["fallback_used"] = False
        fallback.metadata["document_parser_engine"] = _document_parser_engine(document)
        return fallback

    try:
        parsed = parse_document_with_ragflow(
            path,
            file_type=suffix,
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.id,
        )
        parsed.metadata["parser_requested"] = _document_parser_engine(document)
        parsed.metadata["parser_actual"] = parsed.metadata.get("parser", "ragflow")
        parsed.metadata["fallback_used"] = False
        parsed.metadata["document_parser_engine"] = _document_parser_engine(document)
        return _maybe_preserve_pdf_page_visuals(path, document, suffix, parsed)
    except Exception as exc:
        # A supported format failing at runtime is a real parse failure, not an
        # unsupported attachment.  Returning searchable placeholder text here
        # used to make the document look successfully parsed and polluted BM25
        # and embedding inputs with the exception message.
        reason = sanitize_text_for_postgres(str(exc))[:1000] or "document parser failed"
        raise ValueError(f"文档解析器执行失败：{reason}") from exc


def _fallback_to_ragflow(
    path: Path,
    document: Document,
    suffix: str,
    *,
    requested: str,
    reason: str,
    mineru_base_url: str = "",
) -> ParsedDocument:
    """Fallback from MinerU to RAGFlow/DeepDoc with full metadata recording."""
    parsed = _parse_with_ragflow(path, document, suffix)
    parsed.metadata["parser_requested"] = requested
    parsed.metadata["parser_actual"] = parsed.metadata.get("parser", "ragflow")
    parsed.metadata["fallback_used"] = True
    parsed.metadata["fallback_reason"] = reason
    parsed.metadata["parser_attempted"] = requested
    parsed.metadata["mineru_pdf_enabled"] = False
    if mineru_base_url:
        parsed.metadata["mineru_base_url"] = mineru_base_url
    return parsed


def parse_local_file(storage_path: str, file_type: str) -> str:
    """Legacy text-only parser kept for older call sites/tests."""
    dummy = type("D", (), {})()
    dummy.storage_path = storage_path
    dummy.file_type = file_type
    dummy.knowledge_base_id = "legacy"
    dummy.id = "legacy"
    parsed = parse_local_document(dummy)
    return sanitize_text_for_postgres(parsed.content)
