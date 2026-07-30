from __future__ import annotations

from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
import hashlib
from pathlib import Path
import re
from typing import Any

from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres


ALGORITHM_VERSION = "mineru-ocr-admission-v1"
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_VALID_RE = re.compile(r"[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff]")
_PUNCTUATION_RE = re.compile(r"[，。！？；：,.!?;:]")
_SPACE_RE = re.compile(r"\s+")
_CALLIGRAPHY_TERMS = (
    "书法",
    "草书",
    "行书",
    "楷书",
    "隶书",
    "篆书",
    "手卷",
    "法帖",
    "calligraphy",
)
_CJK_FUNCTION_CHARS = frozenset("的了是在有和与为及其于中对将由等这那者之而以")
_HARD_NOISE_RAW_TYPES = {"page_number", "page-number", "pagination"}
_SOFT_NOISE_RAW_TYPES = {"header", "footer"}


def _compact(value: str) -> str:
    return _SPACE_RE.sub("", sanitize_text_for_postgres(value or "")).casefold()


def _ngrams(value: str, size: int = 3) -> set[str]:
    if len(value) < size:
        return {value} if value else set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _native_match_score(block_text: str, native_text: str) -> float:
    block = _compact(block_text)
    native = _compact(native_text)
    if not block or not native:
        return 0.0
    if block in native:
        return 1.0
    if len(block) <= 8:
        return 0.0
    block_grams = _ngrams(block)
    native_grams = _ngrams(native)
    containment = len(block_grams & native_grams) / max(len(block_grams), 1)
    sequence = SequenceMatcher(None, block, native, autojunk=False).ratio()
    return round(max(containment, sequence), 6)


def _native_text_by_page(path: Path) -> dict[int, str]:
    import pdfplumber

    result: dict[int, str] = {}
    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            result[page_no] = sanitize_text_for_postgres(page.extract_text() or "")
    return result


def _is_mineru_document(document: ParsedDocument) -> bool:
    metadata = document.metadata or {}
    parser = str(
        metadata.get("parser_actual")
        or metadata.get("parser")
        or metadata.get("parser_requested")
        or ""
    ).lower()
    return "mineru" in parser


def _copy_block(block: ParsedBlock, metadata: dict[str, Any]) -> ParsedBlock:
    return replace(block, metadata=sanitize_json_for_postgres(metadata))


def annotate_mineru_block_origins(
    path: Path,
    document: ParsedDocument,
) -> ParsedDocument:
    """Label MinerU text blocks with block-level native/OCR provenance.

    Page-level ``native_text_chars`` cannot distinguish a printed introduction
    from calligraphy OCR on the same page. This pass aligns every MinerU text
    block with the authoritative PDF text layer before any reflow occurs.
    """

    if path.suffix.lower() != ".pdf" or not _is_mineru_document(document):
        return document
    try:
        native_pages = _native_text_by_page(path)
    except Exception as exc:
        metadata = sanitize_json_for_postgres(
            {
                **(document.metadata or {}),
                "mineru_block_origin": {
                    "algorithm_version": ALGORITHM_VERSION,
                    "status": "degraded",
                    "reason": str(exc)[:300],
                },
            }
        )
        return replace(document, metadata=metadata)

    counts: Counter[str] = Counter()
    blocks: list[ParsedBlock] = []
    for block in document.blocks or []:
        metadata = dict(block.metadata or {})
        if (
            block.block_type != "text"
            or str(metadata.get("source") or "") != "mineru_content_list"
        ):
            blocks.append(block)
            continue
        raw_type = str(metadata.get("raw_block_type") or "").strip().lower()
        page_no = int(block.page_no or 0)
        native_text = native_pages.get(page_no, "")
        score = _native_match_score(block.text, native_text)
        if raw_type in _HARD_NOISE_RAW_TYPES:
            origin = "parser_noise"
        elif score >= 0.55:
            origin = "native_text"
        elif native_text and score >= 0.20:
            origin = "mixed_or_ambiguous"
        elif raw_type in _SOFT_NOISE_RAW_TYPES:
            origin = "parser_noise"
        else:
            origin = "mineru_ocr"
        counts[origin] += 1
        metadata.update(
            {
                "text_origin": origin,
                "text_origin_algorithm": ALGORITHM_VERSION,
                "native_text_match_score": score,
                "native_text_page_chars": len(_compact(native_text)),
            }
        )
        blocks.append(_copy_block(block, metadata))

    summary = {
        "algorithm_version": ALGORITHM_VERSION,
        "status": "profiled",
        "block_counts": dict(sorted(counts.items())),
        "page_count": len(native_pages),
    }
    return replace(
        document,
        blocks=blocks,
        metadata=sanitize_json_for_postgres(
            {**(document.metadata or {}), "mineru_block_origin": summary}
        ),
    )


def _page_visual_facts(document: ParsedDocument) -> dict[int, dict[str, Any]]:
    profile = (document.metadata or {}).get("page_visual_profile") or {}
    return {
        int(item["page_no"]): item
        for item in profile.get("decisions") or []
        if isinstance(item, dict) and item.get("page_no") is not None
    }


def _page_semantic_text(document: ParsedDocument) -> dict[int, str]:
    result: dict[int, list[str]] = {}
    for block in document.blocks or []:
        if block.block_type != "image" or block.page_no is None:
            continue
        metadata = block.metadata or {}
        values = (
            block.context_header,
            str(metadata.get("caption") or ""),
            str(metadata.get("vlm_description") or ""),
        )
        result.setdefault(int(block.page_no), []).extend(value for value in values if value)
    return {page_no: "\n".join(values).lower() for page_no, values in result.items()}


def _bbox_shape(block: ParsedBlock) -> tuple[float, float, float]:
    if not block.bbox:
        return 0.0, 0.0, 0.0
    try:
        width = max(float(block.bbox[2]) - float(block.bbox[0]), 0.0)
        height = max(float(block.bbox[3]) - float(block.bbox[1]), 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0
    return width, height, height / max(width, 1.0)


def _text_quality(text: str) -> tuple[float, dict[str, Any]]:
    compact = _compact(text)
    if not compact:
        return 0.0, {"empty_text": True}
    valid_ratio = len(_VALID_RE.findall(compact)) / len(compact)
    punctuation_ratio = len(_PUNCTUATION_RE.findall(compact)) / len(compact)
    cjk_chars = _CJK_RE.findall(compact)
    cjk_ratio = len(cjk_chars) / len(compact)
    function_ratio = (
        sum(char in _CJK_FUNCTION_CHARS for char in cjk_chars)
        / max(len(cjk_chars), 1)
    )
    diversity = len(set(compact)) / len(compact)
    replacement_ratio = compact.count("\ufffd") / len(compact)
    fragmented = (
        len(cjk_chars) >= 60
        and cjk_ratio >= 0.70
        and punctuation_ratio < 0.006
        and function_ratio < 0.06
    )
    score = (
        0.42 * valid_ratio
        + 0.18 * min(diversity / 0.35, 1.0)
        + 0.20 * min(punctuation_ratio / 0.03, 1.0)
        + 0.20 * min(function_ratio / 0.12, 1.0)
        - 0.50 * replacement_ratio
        - (0.45 if fragmented else 0.0)
    )
    return round(min(max(score, 0.0), 1.0), 6), {
        "valid_character_ratio": round(valid_ratio, 6),
        "punctuation_ratio": round(punctuation_ratio, 6),
        "cjk_ratio": round(cjk_ratio, 6),
        "cjk_function_character_ratio": round(function_ratio, 6),
        "character_diversity": round(diversity, 6),
        "replacement_character_ratio": round(replacement_ratio, 6),
        "unpunctuated_cjk_fragmentation": fragmented,
    }


def _ocr_candidates_by_page(document: ParsedDocument) -> dict[int, list[ParsedBlock]]:
    result: dict[int, list[ParsedBlock]] = {}
    for block in document.blocks or []:
        if (
            block.block_type == "text"
            and block.page_no is not None
            and (block.metadata or {}).get("text_origin") == "mineru_ocr"
        ):
            result.setdefault(int(block.page_no), []).append(block)
    return result


def _admission_for_block(
    block: ParsedBlock,
    *,
    page_facts: dict[str, Any],
    semantic_text: str,
    parallel_vertical_count: int,
) -> tuple[str, float, list[str], dict[str, Any]]:
    quality, evidence = _text_quality(block.text)
    score = quality
    reasons: list[str] = []
    width, height, aspect = _bbox_shape(block)
    punctuation_ratio = float(evidence["punctuation_ratio"])
    horizontal_printed = (
        width > 0
        and height > 0
        and width / max(height, 1.0) >= 2.2
        and punctuation_ratio >= 0.008
    )
    vertical = aspect >= 1.55
    calligraphy_hint = any(term in semantic_text for term in _CALLIGRAPHY_TERMS)
    visual_dominant = (
        float(page_facts.get("dominant_image_ratio") or 0.0) >= 0.65
        or page_facts.get("decision") in {"preserve", "already_present"}
    )
    unpunctuated_visual_text = (
        visual_dominant
        and len(_compact(block.text)) >= 60
        and float(evidence["cjk_ratio"]) >= 0.70
        and punctuation_ratio < 0.006
        and not horizontal_printed
    )

    if evidence["unpunctuated_cjk_fragmentation"]:
        reasons.append("unpunctuated_cjk_fragmentation")
    if vertical:
        score -= 0.12
        reasons.append("vertical_text_block")
    if parallel_vertical_count >= 3 and vertical:
        score -= 0.24
        reasons.append("vertical_parallel_text_blocks")
    if visual_dominant:
        reasons.append("visual_dominant_page")
    if unpunctuated_visual_text:
        score -= 0.18
        reasons.append("unpunctuated_visual_text_without_native_match")
    if calligraphy_hint:
        reasons.append("calligraphy_semantic_hint")
    if visual_dominant and calligraphy_hint and not horizontal_printed:
        score -= 0.20
    if horizontal_printed:
        score += 0.24
        reasons.append("horizontal_punctuated_printed_region")
    if len(_compact(block.text)) >= 80 and punctuation_ratio >= 0.015:
        score += 0.10
        reasons.append("continuous_punctuated_prose")

    score = round(min(max(score, 0.0), 1.0), 6)
    if calligraphy_hint and visual_dominant:
        score = min(score, 0.41)
    elif unpunctuated_visual_text:
        score = min(score, 0.55)
    if score >= 0.66:
        admission = "normal"
    elif score >= 0.42:
        admission = "auxiliary"
    else:
        admission = "shadow"
    if not reasons:
        reasons.append("text_quality_score")
    evidence.update(
        {
            "bbox_width": round(width, 3),
            "bbox_height": round(height, 3),
            "bbox_aspect_ratio": round(aspect, 6),
            "parallel_vertical_count": parallel_vertical_count,
            "calligraphy_hint": calligraphy_hint,
            "visual_dominant": visual_dominant,
            "horizontal_printed": horizontal_printed,
        }
    )
    return admission, score, reasons, evidence


def _audit_entry(block: ParsedBlock, metadata: dict[str, Any]) -> dict[str, Any]:
    text = sanitize_text_for_postgres(block.text or "")
    return {
        "page_no": block.page_no,
        "order_index": block.order_index,
        "bbox": list(block.bbox) if block.bbox else None,
        "text_origin": metadata.get("text_origin"),
        "ocr_admission": metadata.get("ocr_admission"),
        "ocr_quality_score": metadata.get("ocr_quality_score"),
        "ocr_admission_reasons": list(metadata.get("ocr_admission_reasons") or []),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text[:2000],
        "text_truncated": len(text) > 2000,
    }


def apply_mineru_ocr_admission(
    document: ParsedDocument,
    *,
    enabled: bool,
) -> ParsedDocument:
    """Build the production ParsedDocument projection before block reflow."""

    if not _is_mineru_document(document):
        return document
    if not enabled:
        return replace(
            document,
            metadata=sanitize_json_for_postgres(
                {
                    **(document.metadata or {}),
                    "mineru_ocr_admission": {
                        "algorithm_version": ALGORITHM_VERSION,
                        "enabled": False,
                        "production_projection_applied": False,
                    },
                }
            ),
        )

    page_facts = _page_visual_facts(document)
    page_semantics = _page_semantic_text(document)
    candidates = _ocr_candidates_by_page(document)
    vertical_counts = {
        page_no: sum(_bbox_shape(block)[2] >= 1.55 for block in blocks)
        for page_no, blocks in candidates.items()
    }
    kept: list[ParsedBlock] = []
    audit: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    auxiliary_by_page: dict[int, list[dict[str, Any]]] = {}

    for block in document.blocks or []:
        metadata = dict(block.metadata or {})
        origin = str(metadata.get("text_origin") or "")
        if block.block_type != "text" or origin not in {
            "mineru_ocr",
            "mixed_or_ambiguous",
            "parser_noise",
        }:
            kept.append(block)
            continue
        if origin == "parser_noise":
            admission, score, reasons, evidence = (
                "shadow",
                0.0,
                ["parser_header_footer_or_page_number"],
                {},
            )
        elif origin == "mixed_or_ambiguous":
            admission, score, reasons, evidence = (
                "auxiliary",
                float(metadata.get("native_text_match_score") or 0.0),
                ["native_text_origin_ambiguous"],
                {},
            )
        else:
            page_no = int(block.page_no or 0)
            admission, score, reasons, evidence = _admission_for_block(
                block,
                page_facts=page_facts.get(page_no, {}),
                semantic_text=page_semantics.get(page_no, ""),
                parallel_vertical_count=vertical_counts.get(page_no, 0),
            )
        metadata.update(
            {
                "ocr_admission_algorithm": ALGORITHM_VERSION,
                "ocr_admission": admission,
                "ocr_quality_score": score,
                "ocr_admission_reasons": reasons,
                "ocr_quality_evidence": evidence,
                "production_text_included": admission == "normal",
            }
        )
        classified = _copy_block(block, metadata)
        counts[admission] += 1
        audit.append(_audit_entry(classified, metadata))
        if admission == "normal":
            kept.append(classified)
        elif admission == "auxiliary" and block.page_no is not None:
            auxiliary_by_page.setdefault(int(block.page_no), []).append(
                _audit_entry(classified, metadata)
            )

    for index, block in enumerate(kept):
        if block.block_type != "image" or block.page_no is None:
            continue
        auxiliary = auxiliary_by_page.get(int(block.page_no), [])
        if not auxiliary:
            continue
        metadata = dict(block.metadata or {})
        metadata["ocr_auxiliary_blocks"] = auxiliary
        metadata["ocr_auxiliary_admission"] = "audit_only"
        kept[index] = _copy_block(block, metadata)

    for order_index, block in enumerate(kept):
        block.order_index = order_index
    content = sanitize_text_for_postgres(
        "\n\n".join(
            block.index_text() or block.text or block.table_html
            for block in kept
            if block.index_text() or block.text or block.table_html
        )
    )
    summary = {
        "algorithm_version": ALGORITHM_VERSION,
        "enabled": True,
        "production_projection_applied": True,
        "input_block_count": len(document.blocks or []),
        "output_block_count": len(kept),
        "admission_counts": dict(sorted(counts.items())),
        "excluded_block_count": sum(
            count for key, count in counts.items() if key != "normal"
        ),
        "blocks": audit,
    }
    return ParsedDocument(
        content=content,
        blocks=kept,
        metadata=sanitize_json_for_postgres(
            {**(document.metadata or {}), "mineru_ocr_admission": summary}
        ),
        source_content=document.source_content,
    )


__all__ = [
    "ALGORITHM_VERSION",
    "annotate_mineru_block_origins",
    "apply_mineru_ocr_admission",
]
