from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.db.models import Chunk, Document
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres

logger = logging.getLogger(__name__)


@dataclass
class EvalIssue:
    target_type: str = "document"
    target_id: str = ""
    page_no: int | None = None
    chunk_index: int | None = None
    score: float | None = None
    issue_level: str = "info"
    issue_type: str = "info"
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    suggestion: str = ""


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return sanitize_text_for_postgres(value)
    try:
        return sanitize_text_for_postgres(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return sanitize_text_for_postgres(str(value))


def flatten_json_text(value: Any, *, limit: int = 12000) -> str:
    """Flatten metadata/image_info into searchable text for diagnostics."""
    pieces: list[str] = []

    def walk(item: Any):
        if len("\n".join(pieces)) > limit:
            return
        if item is None:
            return
        if isinstance(item, str):
            text = item.strip()
            if text:
                pieces.append(text)
            return
        if isinstance(item, (int, float, bool)):
            pieces.append(str(item))
            return
        if isinstance(item, dict):
            for k, v in item.items():
                if isinstance(k, str) and k in {"api_key", "token", "authorization"}:
                    continue
                walk(v)
            return
        if isinstance(item, (list, tuple, set)):
            for v in item:
                walk(v)

    walk(value)
    return safe_text("\n".join(pieces))[:limit]


def chunk_all_text(chunk: Chunk) -> str:
    metadata = flatten_json_text(chunk.metadata_json or {})
    image = flatten_json_text(chunk.image_info or {})
    return safe_text("\n".join([chunk.content or "", chunk.context_header or "", metadata, image]))


def _iter_text_values(value: Any):
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            yield safe_text(value.strip())
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_text_values(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_text_values(item)
        return


def infer_page_no_from_text(text: str) -> int | None:
    text = safe_text(text)
    patterns = [
        r"raster\s+page\s+([0-9]{1,4})",
        r"page\s+([0-9]{1,4})\s*/\s*[0-9]{1,4}",
        r"第\s*([0-9]{1,4})\s*页",
        r"CN-ANCHOR-([0-9]{1,4})-",
        r"EN-CN-MIX-([0-9]{1,4})",
        r"INV-([0-9]{1,4})-[0-9]{1,3}",
        r"line\s+([0-9]{1,4})-[0-9]{1,3}",
        r"SCAN-CALLOUT-([0-9]{1,4})-",
        r"file\s+anchor\s+page-([0-9]{1,4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            try:
                value = int(match.group(1))
                if 1 <= value <= 10000:
                    return value
            except Exception:
                pass
    return None


def chunk_page_no(chunk: Chunk) -> int | None:
    for source in (chunk.metadata_json or {}, chunk.image_info or {}):
        if not isinstance(source, dict):
            continue
        for key in ("page_no", "source_page", "page"):
            value = source.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return infer_page_no_from_text(chunk_all_text(chunk))


def observed_pages_from_text(text: str) -> set[int]:
    pages: set[int] = set()
    text = safe_text(text)
    patterns = [
        r"raster\s+page\s+([0-9]{1,4})",
        r"page\s+([0-9]{1,4})\s*/\s*[0-9]{1,4}",
        r"第\s*([0-9]{1,4})\s*页",
        r"CN-ANCHOR-([0-9]{1,4})-",
        r"EN-CN-MIX-([0-9]{1,4})",
        r"INV-([0-9]{1,4})-[0-9]{1,3}",
        r"line\s+([0-9]{1,4})-[0-9]{1,3}",
        r"SCAN-CALLOUT-([0-9]{1,4})-",
        r"file\s+anchor\s+page-([0-9]{1,4})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            try:
                value = int(match.group(1))
                if 1 <= value <= 10000:
                    pages.add(value)
            except Exception:
                pass
    return pages


def normalize_for_duplicate(text: str) -> str:
    text = re.sub(r"\s+", " ", safe_text(text).strip().lower())
    return text[:600]


def suspicious_char_ratio(text: str) -> float:
    text = safe_text(text)
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        if ch == "\ufffd" or ord(ch) < 32 and ch not in "\n\t\r":
            bad += 1
        elif re.match(r"[\w\s\u4e00-\u9fff,.，。:：;；!！?？()（）\[\]{}<>《》/\\\-—_+*=#%&@'\"|]", ch) is None:
            bad += 1
    return bad / max(len(text), 1)


def long_token_count(text: str) -> int:
    # Long English-like tokens often indicate whitespace deletion, e.g. Raster-onlyOCRtextline001.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-_/]{28,}", safe_text(text))
    return len(tokens)


def joined_word_risk(text: str) -> int:
    """Count joined-word patterns in text.

    Only uses generic patterns that apply to any OCR output:
    - very long concatenated letter strings (40+)
    - repeated letter-number merging
    - missing word boundaries in plausible compound words

    Template-specific patterns (e.g. Raster-onlyOCR, Noisyskewed,
    scannedcontract, Calloutbox) are loaded from the template registry
    and reported separately.
    """
    text = safe_text(text)
    risk = 0
    # Long letter runs that should be separate words
    risk += len(re.findall(r"[A-Za-z]{30,}", text))
    # Digit-letter boundary without space (not inside obvious IDs)
    risk += len(re.findall(r"(?<![A-Za-z])([0-9]{2,})([A-Za-z]{4,})", text))
    return risk


def _template_joined_word_risk(text: str, template_id: str | None = None) -> int:
    """Template-specific joined-word risk.

    Loads patterns from the template registry so the main
    ``joined_word_risk`` stays generic.
    """
    from app.services.ocr.templates import _TEMPLATES

    if not template_id or template_id not in _TEMPLATES:
        return 0
    text = safe_text(text)
    instance = _TEMPLATES[template_id]()
    patterns = instance.glue_risk_patterns()
    return sum(len(re.findall(p, text, flags=re.I)) for p in patterns)


def _chunk_layer_text(chunk: Chunk, key: str) -> str:
    """Extract a single metadata text layer from a chunk."""
    meta = chunk.metadata_json or {}
    info = chunk.image_info or {}
    for source in (meta, info):
        if isinstance(source, dict):
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                return safe_text(val.strip())
    return ""


# ── Layer-wise glue scores ─────────────────────────────────────────

def layer_glue_summary(chunks: list[Chunk], template_id: str | None = None) -> dict[str, int]:
    """Compute glue scores at each OCR layer across a list of chunks.

    Returns a dict with keys like::

        raw_glue_score
        cleaned_glue_score
        normalized_glue_score
        content_glue_score
        template_glue_score
    """
    raw_total = 0
    cleaned_total = 0
    normalized_total = 0
    content_total = 0
    template_total = 0

    for chunk in chunks:
        content = safe_text(chunk.content or "")
        raw = _chunk_layer_text(chunk, "ocr_raw_text") or content
        cleaned = _chunk_layer_text(chunk, "ocr_cleaned_text") or raw
        normalized = _chunk_layer_text(chunk, "ocr_normalized_text") or cleaned

        content_total += joined_word_risk(content) + long_token_count(content)
        raw_total += joined_word_risk(raw) + long_token_count(raw)
        cleaned_total += joined_word_risk(cleaned) + long_token_count(cleaned)
        normalized_total += joined_word_risk(normalized) + long_token_count(normalized)
        template_total += _template_joined_word_risk(content, template_id)

    return {
        "raw_glue_score": raw_total,
        "cleaned_glue_score": cleaned_total,
        "normalized_glue_score": normalized_total,
        "content_glue_score": content_total,
        "template_glue_score": template_total,
    }


def layer_glue_delta(summary: dict[str, int]) -> dict[str, float]:
    """Derive deltas between layers as a quick quality indicator.

    Positive delta_clean_raw means cleaning introduced glue.
    Positive delta_norm_clean means normalisation introduced glue.
    Zero delta_content_raw means content == raw → no effective cleaning.
    """
    raw = summary.get("raw_glue_score", 0)
    cleaned = summary.get("cleaned_glue_score", 0)
    normalized = summary.get("normalized_glue_score", 0)
    content = summary.get("content_glue_score", 0)
    return {
        "clean_vs_raw": cleaned - raw,
        "norm_vs_clean": normalized - cleaned,
        "content_vs_raw": content - raw,
        "content_vs_clean": content - cleaned,
    }


def identifier_detected_count(text: str) -> int:
    """Count normal identifier-like tokens without treating them as issues."""
    text = safe_text(text)
    patterns = [
        r"\b[A-Z]{2,10}-\d{2,4}(?:-\d{1,4})+\b",
        r"\b\d{3}-\d{2}\b",
        r"\bCN-ANCHOR-\d{3}-\d{2}\b",
        r"\bKB-\d{4}-\d{4}\b",
        r"\bINV-\d{3}-\d{2}\b",
    ]
    return sum(len(re.findall(pattern, text, flags=re.I)) for pattern in patterns)


def _has_confusable_letters(match: str) -> bool:
    """True when *match* contains actual confusable letters (O/U/I/L/S).

    Known prefixes (INV, CNANCHOR, SCAN, etc.) are stripped first so they
    don't trigger false positives — e.g.  ``I`` in ``INV`` is a valid prefix
    letter, not an OCR error.
    """
    body = match.upper()
    for prefix in ("INV", "1NV", "CNANCHOR", "SCAN", "CALLOUT", "LINE"):
        if body.startswith(prefix):
            body = body[len(prefix):]
            break
    return bool(re.search(r"[OULIS]", body))


def identifier_format_risk(text: str) -> int:
    """Count clearly damaged identifier forms.

    This deliberately does not flag normal IDs like CN-ANCHOR-001-00 or
    KB-2026-0608.  It focuses on OCR/cleaning damage such as INV00100,
    1NV-UU1-OO, and missing separators in invoice-like fields.
    """
    text = safe_text(text)
    risks = 0

    # Patterns that use [O0U] — digit 0 accidentally matches the class, so
    # clean IDs like INV-001-00 need a confusable-letter filter.
    for pattern in (
        r"\b[1lI]NV[-_ ]?[O0U]{2,}[1lI]?[-_ ]?[O0U]{2}\b",
        r"\bINV[-_ ]?[O0U]{2,}[-_ ]?[O0U]{2}\b",
        r"\b(?:line)?[O0U]{2}[1lI][- ]?[O0U]{2}\b",
    ):
        for m in re.findall(pattern, text, flags=re.I):
            if _has_confusable_letters(m):
                risks += 1

    # Digit-only patterns — always damage (no separators).
    for pattern in (
        r"\bINV\d{5,}\b",
        r"\bSCAN\s*CALLOUT\s*\d+\s*\d+\b",
        r"\bCNANCHOR\d{5,}\b",
    ):
        risks += len(re.findall(pattern, text, flags=re.I))

    return risks



def table_text_weak(chunk: Chunk) -> bool:
    text = safe_text(chunk.content)
    metadata = chunk.metadata_json or {}
    image_info = chunk.image_info or {}
    subtype = ""
    for source in (metadata, image_info):
        if isinstance(source, dict) and isinstance(source.get("table_subtype"), str):
            subtype = source.get("table_subtype", "")
            break
    if subtype in {"pseudo_table", "layout_text", "figure_or_layout"}:
        return True
    if len(text.strip()) < 20:
        return True
    table_html = ""
    if isinstance(image_info, dict):
        table_html = str(image_info.get("table_html") or "")
    has_structure = any(token in text for token in ["\n", "\t", "|"]) or "<table" in table_html.lower()
    if not has_structure and len(text) < 160:
        return True
    # Generalized prose detection: CJK-heavy or sentence-rich text is likely
    # table misclassification even without specific markers.
    cjk_punct = len(re.findall(r"[。！？；：、，""''【】《》（）]", text))
    if cjk_punct / max(len(text), 1) > 0.02:
        return True
    prose_markers = ["页面锚点", "本页用于测试", "RAGFlow parsing stress test", "This page contains", "The goal is to test"]
    if sum(1 for marker in prose_markers if marker.lower() in text.lower()) >= 2:
        return True
    sentence_marks = len(re.findall(r"[。！？.!?；;]", text))
    if sentence_marks >= 5 and not has_structure:
        return True
    return False


def image_url(info: dict) -> str:
    if not isinstance(info, dict):
        return ""
    for key in ("url", "relative_path", "path"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = info.get("image_info")
    if isinstance(nested, dict):
        return image_url(nested)
    return ""


def metadata_has_caption(info: dict, metadata: dict) -> bool:
    keys = ["caption", "figure_caption", "table_caption", "title"]
    for source in (info or {}, metadata or {}):
        for key in keys:
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                return True
    return False


def metadata_has_vlm(info: dict, metadata: dict) -> bool:
    keys = ["vlm_description", "description", "image_description"]
    for source in (info or {}, metadata or {}):
        for key in keys:
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                return True
    return False


def diagnose_no_gt(document: Document, chunks: list[Chunk]) -> tuple[dict, dict, list[EvalIssue]]:
    issues: list[EvalIssue] = []
    counts = Counter((chunk.chunk_type or "text") for chunk in chunks)
    enabled_counts = Counter((chunk.chunk_type or "text") for chunk in chunks if chunk.is_enabled)
    page_counts: dict[int, int] = defaultdict(int)
    duplicate_counter: Counter[str] = Counter()

    total_text_len = 0
    short_chunks = 0
    long_chunks = 0
    noisy_chunks = 0
    glued_chunks = 0
    identifier_detected_chunks = 0
    id_risk_chunks = 0
    missing_page_chunks = 0
    media_without_url = 0
    image_without_caption = 0
    image_without_vlm = 0
    weak_tables = 0
    parent_text_skipped = 0

    for chunk in chunks:
        chunk_type = chunk.chunk_type or "text"
        # Skip parent_text chunks to avoid double-counting with child text chunks
        if chunk_type == "parent_text":
            parent_text_skipped += 1
            continue
        text = chunk_all_text(chunk)
        content = safe_text(chunk.content)
        total_text_len += len(content)
        page_no = chunk_page_no(chunk)
        if page_no is not None:
            page_counts[page_no] += 1
        elif chunk_type in {"text", "table", "image"}:
            missing_page_chunks += 1
            if len(issues) < 180:
                issues.append(EvalIssue(
                    target_type="chunk",
                    target_id=chunk.id,
                    page_no=None,
                    chunk_index=chunk.chunk_index,
                    issue_level="medium",
                    issue_type="missing_page_no",
                    message=f"切片 #{chunk.chunk_index} 缺少 page_no，后续定位和人工复查不方便。",
                    evidence={"chunk_type": chunk_type, "preview": content[:240]},
                    suggestion="建议在解析/切片阶段将 page_no 写入 metadata_json 或 image_info。",
                ))

        if len(content.strip()) < 30 and chunk_type in {"text", "parent_text"}:
            short_chunks += 1
            if len(issues) < 180:
                issues.append(EvalIssue(
                    target_type="chunk",
                    target_id=chunk.id,
                    page_no=page_no,
                    chunk_index=chunk.chunk_index,
                    issue_level="medium",
                    issue_type="short_chunk",
                    message=f"切片 #{chunk.chunk_index} 过短，可能不适合直接入库检索。",
                    evidence={"length": len(content), "preview": content[:240]},
                    suggestion="建议合并相邻短文本或保留更多上下文标题。",
                ))
        if len(content) > 4000:
            long_chunks += 1
            if len(issues) < 180:
                issues.append(EvalIssue(
                    target_type="chunk",
                    target_id=chunk.id,
                    page_no=page_no,
                    chunk_index=chunk.chunk_index,
                    issue_level="medium",
                    issue_type="long_chunk",
                    message=f"切片 #{chunk.chunk_index} 过长，可能混合多个主题或跨页。",
                    evidence={"length": len(content), "preview": content[:240]},
                    suggestion="建议检查切分配置和结构边界。",
                ))

        # For quality warnings, evaluate final chunk content first.  Metadata may
        # contain raw OCR kept for audit and should not by itself create user-facing
        # space-gluing/noise warnings.
        quality_text = content
        noise_ratio = suspicious_char_ratio(quality_text)
        if noise_ratio > 0.03:
            noisy_chunks += 1
            if len(issues) < 180:
                issues.append(EvalIssue(
                    target_type="chunk",
                    target_id=chunk.id,
                    page_no=page_no,
                    chunk_index=chunk.chunk_index,
                    issue_level="high" if noise_ratio > 0.08 else "medium",
                    issue_type="ocr_noise",
                    message=f"切片 #{chunk.chunk_index} 疑似存在 OCR/编码噪声。",
                    evidence={"noise_ratio": round(noise_ratio, 4), "preview": quality_text[:300]},
                    suggestion="建议查看 OCR 原始结果，必要时调整 OCR 引擎或清洗规则。",
                ))

        glue_score = joined_word_risk(quality_text) + long_token_count(quality_text)
        if glue_score > 0:
            glued_chunks += 1
            if len(issues) < 180:
                issues.append(EvalIssue(
                    target_type="chunk",
                    target_id=chunk.id,
                    page_no=page_no,
                    chunk_index=chunk.chunk_index,
                    issue_level="high" if glue_score >= 3 else "medium",
                    issue_type="space_gluing",
                    message=f"切片 #{chunk.chunk_index} 疑似存在空格粘连或清洗过度。",
                    evidence={"glue_score": glue_score, "preview": quality_text[:300]},
                    suggestion="建议保留 OCR 行内空格，避免把单词、编号、字段名强行拼接。",
                ))

        detected_ids = identifier_detected_count(quality_text)
        if detected_ids > 0:
            identifier_detected_chunks += 1
        id_risk = identifier_format_risk(quality_text)
        if id_risk > 0:
            id_risk_chunks += 1
            if len(issues) < 180:
                issues.append(EvalIssue(
                    target_type="chunk",
                    target_id=chunk.id,
                    page_no=page_no,
                    chunk_index=chunk.chunk_index,
                    issue_level="high",
                    issue_type="identifier_format_risk",
                    message=f"切片 #{chunk.chunk_index} 疑似存在编号/发票号格式破坏。",
                    evidence={"risk_count": id_risk, "identifier_detected_count": detected_ids, "preview": quality_text[:300]},
                    suggestion="编号、合同号、发票号类字段应优先保留连字符和数字结构。",
                ))

        dup_key = normalize_for_duplicate(content)
        if len(dup_key) > 50:
            duplicate_counter[dup_key] += 1

        if chunk_type in {"image", "table"}:
            info = chunk.image_info or {}
            if not image_url(info):
                media_without_url += 1
                if len(issues) < 180:
                    issues.append(EvalIssue(
                        target_type=chunk_type,
                        target_id=chunk.id,
                        page_no=page_no,
                        chunk_index=chunk.chunk_index,
                        issue_level="medium",
                        issue_type="media_url_missing",
                        message=f"{chunk_type} 切片 #{chunk.chunk_index} 缺少可预览的裁剪图 URL。",
                        evidence={"image_info": info, "preview": content[:240]},
                        suggestion="建议检查 media_storage 和 image_info.url 写入。",
                    ))
        if chunk_type == "image":
            if not metadata_has_caption(chunk.image_info or {}, chunk.metadata_json or {}):
                image_without_caption += 1
            if not metadata_has_vlm(chunk.image_info or {}, chunk.metadata_json or {}):
                image_without_vlm += 1
                if len(issues) < 180:
                    issues.append(EvalIssue(
                        target_type="image",
                        target_id=chunk.id,
                        page_no=page_no,
                        chunk_index=chunk.chunk_index,
                        issue_level="low",
                        issue_type="image_vlm_weak",
                        message=f"图片切片 #{chunk.chunk_index} 未发现有效 VLM 描述。",
                        evidence={"image_info": chunk.image_info or {}, "preview": content[:240]},
                        suggestion="如果该图片需要参与检索，建议启用 Image2Text/VLM 描述。",
                    ))
        if chunk_type == "table" and table_text_weak(chunk):
            weak_tables += 1
            if len(issues) < 180:
                issues.append(EvalIssue(
                    target_type="table",
                    target_id=chunk.id,
                    page_no=page_no,
                    chunk_index=chunk.chunk_index,
                    issue_level="medium",
                    issue_type="weak_table_content",
                    message=f"表格切片 #{chunk.chunk_index} 内容较弱，可能是误判或结构还原不充分。",
                    evidence={"preview": content[:300], "image_info": chunk.image_info or {}},
                    suggestion="建议查看表格裁剪图和表格 HTML/Markdown。",
                ))

    duplicate_groups = sum(1 for _, count in duplicate_counter.items() if count > 1)
    duplicate_total = sum(count for _, count in duplicate_counter.items() if count > 1)
    if duplicate_groups:
        issues.append(EvalIssue(
            target_type="document",
            target_id=document.id,
            issue_level="medium",
            issue_type="duplicate_text",
            message="检测到重复文本，可能包含页眉页脚、重复 OCR 或重复入库。",
            evidence={"duplicate_groups": duplicate_groups, "duplicate_chunks": duplicate_total},
            suggestion="建议检查页眉页脚过滤、重复 chunk 去重和解析回写逻辑。",
        ))

    total_chunks = len(chunks)
    summary = {
        "document_id": document.id,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "chunk_count": total_chunks,
        "chunk_type_counts": dict(counts),
        "enabled_chunk_type_counts": dict(enabled_counts),
        "page_count_observed": len(page_counts),
        "avg_chunk_length": round(total_text_len / max(total_chunks, 1), 2),
        "short_chunks": short_chunks,
        "long_chunks": long_chunks,
        "noisy_chunks": noisy_chunks,
        "space_glued_chunks": glued_chunks,
        "identifier_detected_chunks": identifier_detected_chunks,
        "identifier_suspicious_chunks": id_risk_chunks,
        "identifier_broken_chunks": id_risk_chunks,
        "identifier_risk_chunks": id_risk_chunks,
        "missing_page_chunks": missing_page_chunks,
        "media_without_url": media_without_url,
        "image_without_caption": image_without_caption,
        "image_without_vlm": image_without_vlm,
        "weak_tables": weak_tables,
        "parent_text_skipped": parent_text_skipped,
        "duplicate_groups": duplicate_groups,
        "duplicate_chunks": duplicate_total,
        "issue_count": len(issues),
        **layer_glue_summary(chunks),
    }

    penalties = 0.0
    penalties += min(25, short_chunks / max(total_chunks, 1) * 55)
    penalties += min(20, noisy_chunks / max(total_chunks, 1) * 70)
    penalties += min(20, glued_chunks / max(total_chunks, 1) * 70)
    penalties += min(20, id_risk_chunks / max(total_chunks, 1) * 80)
    penalties += min(10, missing_page_chunks / max(total_chunks, 1) * 25)
    penalties += min(8, media_without_url * 2)
    penalties += min(8, weak_tables * 1.5)
    penalties += min(8, duplicate_groups * 2)
    score = max(0.0, round(100.0 - penalties, 2))

    if score >= 80:
        risk_level = "low"
        recommendation = "解析质量风险较低，可以进入向量化；建议仍抽样检查关键页面。"
    elif score >= 60:
        risk_level = "medium"
        recommendation = "解析结果可用但建议复查，重点查看短 chunk、OCR 噪声和图表元数据。"
    elif score >= 40:
        risk_level = "high"
        recommendation = "不建议直接向量化。建议先修复 OCR/清洗/chunk 问题并重新解析。"
    else:
        risk_level = "critical"
        recommendation = "解析质量风险很高，当前结果不适合入库检索。"

    score_json = {
        "score": score,
        "risk_level": risk_level,
        "recommendation": recommendation,
        "penalties": round(penalties, 2),
        "metrics": summary,
    }
    return summary, score_json, issues


def _dict_text_value(source: dict | None, keys: list[str]) -> str:
    if not isinstance(source, dict):
        return ""
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return safe_text(value.strip())
    for value in source.values():
        if isinstance(value, dict):
            found = _dict_text_value(value, keys)
            if found:
                return found
    return ""


def chunk_strict_eval_text(chunk: Chunk) -> str:
    """Text sources for strict OCR/template evaluation.

    Strict metrics may use final chunk content plus raw/cleaned OCR audit fields.
    They deliberately exclude ocr_normalized_text, because normalized text is a
    recovery/correction view and would hide OCR or cleaning quality problems.
    """
    parts = [safe_text(chunk.content)]
    meta = chunk.metadata_json or {}
    image_info = chunk.image_info or {}
    for source in (meta, image_info):
        for key in ("ocr_raw_text", "ocr_cleaned_text", "ocr_text"):
            value = _dict_text_value(source, [key])
            if value:
                parts.append(value)
    return "\n".join(dict.fromkeys([part for part in parts if part]))


def chunk_recoverable_eval_text(chunk: Chunk) -> str:
    """Text sources for recoverable metrics.

    Only OCR normalized fields are used here.  This keeps strict metrics honest
    while allowing the report to show what could be recovered by field-level
    normalization/correction.
    """
    parts: list[str] = []
    meta = chunk.metadata_json or {}
    image_info = chunk.image_info or {}
    for source in (meta, image_info):
        value = _dict_text_value(source, ["ocr_normalized_text"])
        if value:
            parts.append(value)
    return "\n".join(dict.fromkeys([part for part in parts if part]))


def relaxed_invoice_text(text: str) -> str:
    value = safe_text(text).upper()
    value = re.sub(r"\b[1IL]NV", "INV", value)
    value = value.replace("O", "0").replace("U", "0")
    value = re.sub(r"[^A-Z0-9]", "", value)
    return value


def relaxed_numeric_text(text: str) -> str:
    value = safe_text(text).upper()
    value = value.replace("O", "0").replace("U", "0").replace("I", "1").replace("L", "1")
    return re.sub(r"[^0-9]", "", value)


def compact_alnum(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", safe_text(text).upper())


def normalized_plain(text: str) -> str:
    return re.sub(r"\s+", " ", safe_text(text).lower()).strip()


def filename_page_hint(filename: str) -> int | None:
    for pattern in [r"_(\d+)p\b", r"-(\d+)p\b", r"(\d+)p\.pdf$", r"page[_-]?(\d+)"]:
        match = re.search(pattern, filename, flags=re.I)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return None
    return None


def _actual_page_count_hint(document: Document, observed_pages: set[int], options: dict) -> int:
    metadata = document.metadata_json or {}
    candidates = [
        options.get("page_count"),
        metadata.get("page_count"),
        metadata.get("pdf_page_count"),
        metadata.get("page_count_observed"),
        metadata.get("total_pages"),
    ]
    for candidate in candidates:
        try:
            value = int(candidate) if candidate else 0
            if value > 0:
                return value
        except Exception:
            pass
    if observed_pages:
        return max(observed_pages)
    hint = filename_page_hint(document.file_name)
    if hint:
        logger.info(
            "page count fallback from filename '%s' → %d; prefer document metadata or parsed page count",
            document.file_name, hint,
        )
        return hint
    return 25


def _page_count_source_label(document: Document, observed_pages: set[int], options: dict) -> str:
    """Return a human-readable label describing where the page count came from."""
    metadata = document.metadata_json or {}
    for key in ("page_count", "pdf_page_count", "page_count_observed", "total_pages"):
        if metadata.get(key):
            return f"metadata:{key}"
    if options.get("page_count"):
        return "options:page_count"
    if observed_pages:
        return "observed_from_text"
    if filename_page_hint(document.file_name):
        return "filename_hint"
    return "default_25"


def diagnose_scanned_contract(document: Document, chunks: list[Chunk], options: dict | None = None) -> tuple[dict, dict, list[EvalIssue]]:
    options = options or {}
    strict_text = "\n".join(chunk_strict_eval_text(chunk) for chunk in chunks)
    recovery_text = "\n".join(chunk_recoverable_eval_text(chunk) for chunk in chunks)
    combined_for_page = "\n".join([strict_text, recovery_text])

    strict_plain = normalized_plain(strict_text)
    strict_upper = safe_text(strict_text).upper()
    strict_compact = compact_alnum(strict_text)
    recovery_upper = safe_text(recovery_text).upper()
    recovery_compact = compact_alnum(recovery_text)
    relaxed_invoice_recovery = relaxed_invoice_text(recovery_text)
    relaxed_numeric_recovery = relaxed_numeric_text(recovery_text)

    observed_pages = observed_pages_from_text(combined_for_page)
    page_count = _actual_page_count_hint(document, observed_pages, options)
    lines_per_page = int(options.get("lines_per_page") or 33)
    callouts_per_page = int(options.get("callouts_per_page") or 5)

    page_title_exact = 0
    page_recognized_pages: set[int] = set()
    line_exact = 0
    line_recoverable = 0
    invoice_exact = 0
    invoice_recoverable = 0
    keyword_hits = Counter()
    callout_exact = 0
    callout_recoverable = 0
    issues: list[EvalIssue] = []

    missing_invoices: list[dict] = []
    missing_lines: list[dict] = []
    missing_pages: list[dict] = []
    missing_callouts: list[dict] = []

    for page in range(1, page_count + 1):
        p3 = f"{page:03d}"
        title = f"raster page {p3}"
        title_compact = compact_alnum(title)
        page_has_evidence = page in observed_pages
        if title in strict_plain or title_compact in strict_compact:
            page_title_exact += 1
            page_has_evidence = True

        for line in range(lines_per_page):
            l2 = f"{line:02d}"
            line_token = f"{p3}-{l2}"
            line_token_compact = compact_alnum(line_token)
            invoice = f"INV-{p3}-{l2}"
            invoice_relaxed = relaxed_invoice_text(invoice)

            if line_token in strict_upper or line_token_compact in strict_compact:
                line_exact += 1
                page_has_evidence = True
            elif line_token_compact in recovery_compact or (p3 + l2) in relaxed_numeric_recovery:
                line_recoverable += 1
                page_has_evidence = True
                if len(missing_lines) < 30:
                    missing_lines.append({"expected": line_token, "reason": "strict_missing_normalized_seen"})
            else:
                if len(missing_lines) < 30:
                    missing_lines.append({"expected": line_token, "reason": "missing"})

            if invoice in strict_upper:
                invoice_exact += 1
                page_has_evidence = True
            elif invoice_relaxed in relaxed_invoice_recovery or compact_alnum(invoice) in recovery_compact:
                invoice_recoverable += 1
                page_has_evidence = True
                if len(missing_invoices) < 40:
                    missing_invoices.append({"expected": invoice, "reason": "strict_missing_normalized_seen"})
            else:
                if len(missing_invoices) < 40:
                    missing_invoices.append({"expected": invoice, "reason": "missing"})

        for c in range(callouts_per_page):
            expected_a = f"SCAN-CALLOUT-{page}-{c}"
            expected_b = f"SCAN-CALLOUT-{p3}-{c}"
            expected_a_compact = compact_alnum(expected_a)
            expected_b_compact = compact_alnum(expected_b)
            if expected_a in strict_upper or expected_b in strict_upper:
                callout_exact += 1
                page_has_evidence = True
            elif expected_a_compact in recovery_compact or expected_b_compact in recovery_compact:
                callout_recoverable += 1
                page_has_evidence = True
            else:
                if len(missing_callouts) < 25:
                    missing_callouts.append({"expected": expected_a, "reason": "missing"})

        if page_has_evidence:
            page_recognized_pages.add(page)
        elif len(missing_pages) < 20:
            missing_pages.append({"page": p3, "reason": "no_title_or_structured_evidence"})

    total_lines = page_count * lines_per_page
    total_callouts = page_count * callouts_per_page
    keyword_defs = {
        "OCR-CANDIDATE": total_lines,
        "invoice number": total_lines,
        "vector index check": total_lines,
        "Raster-only OCR text line": total_lines,
        "Callout box": total_callouts,
    }
    keyword_text = "\n".join([strict_text, recovery_text])
    for keyword, expected in keyword_defs.items():
        keyword_hits[keyword] = len(re.findall(re.escape(keyword), keyword_text, flags=re.I))

    def rate(value: int, total: int) -> float:
        return round(value / max(total, 1), 4)

    keyword_rates = {k: rate(min(v, keyword_defs[k]), keyword_defs[k]) for k, v in keyword_hits.items()}
    page_title_strict_rate = rate(page_title_exact, page_count)
    page_rate = rate(len(page_recognized_pages), page_count)
    line_rate = rate(line_exact, total_lines)
    line_recoverable_rate = rate(line_exact + line_recoverable, total_lines)
    invoice_rate = rate(invoice_exact, total_lines)
    invoice_recoverable_rate = rate(invoice_exact + invoice_recoverable, total_lines)
    callout_rate = rate(callout_exact, total_callouts)
    callout_recoverable_rate = rate(callout_exact + callout_recoverable, total_callouts)
    keyword_avg = round(sum(keyword_rates.values()) / max(len(keyword_rates), 1), 4)

    recoverable_boost = max(0.0, (line_recoverable_rate - line_rate) * 0.35 + (invoice_recoverable_rate - invoice_rate) * 0.45 + (callout_recoverable_rate - callout_rate) * 0.2)
    whitespace_quality = max(0, 1 - (joined_word_risk(strict_text) + long_token_count(strict_text)) / max(total_lines, 1))
    # space_gluing is an auxiliary issue; prioritize line/invoice/callout core field hit rates
    score = round(
        page_rate * 10
        + page_title_strict_rate * 5
        + line_rate * 22
        + invoice_rate * 33
        + callout_rate * 9
        + keyword_avg * 15
        + recoverable_boost * 10
        + whitespace_quality * 6,
        2,
    )

    if score >= 80:
        risk_level = "low"
        recommendation = "模板字段命中较好，可以考虑向量化；建议抽样复核少量页面。"
    elif score >= 60:
        risk_level = "medium"
        recommendation = "模板字段有较多缺失，建议复查 OCR 与清洗后再向量化。"
    elif score >= 40:
        risk_level = "high"
        recommendation = "不建议直接向量化。关键编号或固定关键词命中率偏低。"
    else:
        risk_level = "critical"
        recommendation = "扫描 OCR 模板评测较差，当前解析结果不适合入库检索。"

    if missing_pages:
        issues.append(EvalIssue(
            target_type="document",
            target_id=document.id,
            issue_level="high" if page_rate < 0.7 else "medium",
            issue_type="page_title_missing",
            message="部分页面标题没有严格命中，或只能通过正文编号推断页面存在。",
            evidence={"samples": missing_pages[:20], "page_title_exact": page_title_exact, "page_recognized": len(page_recognized_pages), "page_count": page_count},
            suggestion="如果正文编号已覆盖页面，可优先优化页面标题 OCR/清洗；评测中已将结构化页码证据作为部分命中。",
        ))
    if missing_invoices:
        issues.append(EvalIssue(
            target_type="document",
            target_id=document.id,
            issue_level="high",
            issue_type="invoice_missing_or_damaged",
            message="发票号/编号字段命中率偏低，可能影响按编号检索。",
            evidence={"samples": missing_invoices[:40], "exact_hits": invoice_exact, "recoverable_hits": invoice_recoverable, "expected": total_lines},
            suggestion="编号类字段应保留 INV-001-00 这种连字符结构；纠错结果仅用于恢复命中，不应覆盖 OCR 原文。",
        ))
    if missing_lines:
        issues.append(EvalIssue(
            target_type="document",
            target_id=document.id,
            issue_level="medium",
            issue_type="line_id_missing_or_damaged",
            message="line 编号严格命中不足，可能存在 0/O、1/I、空格或连字符问题。",
            evidence={"samples": missing_lines[:30], "exact_hits": line_exact, "recoverable_hits": line_recoverable, "expected": total_lines},
            suggestion="检查 OCR 对数字和连字符的识别稳定性；可恢复命中仅代表规则纠错后可找回。",
        ))
    if missing_callouts:
        issues.append(EvalIssue(
            target_type="document",
            target_id=document.id,
            issue_level="medium",
            issue_type="callout_missing",
            message="Callout 框内文字识别不足。",
            evidence={"samples": missing_callouts[:25], "exact_hits": callout_exact, "recoverable_hits": callout_recoverable, "expected": total_callouts},
            suggestion="Callout 通常依赖 OCR，建议查看该区域裁剪和 OCR box。",
        ))

    generic_summary, _, generic_issues = diagnose_no_gt(document, chunks)
    issues.extend(generic_issues[:80])

    summary = {
        "document_id": document.id,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "template": "scanned_contract_ocr",
        "page_count_expected": page_count,
        "page_count_source": _page_count_source_label(document, observed_pages, options),
        "page_count_observed_from_text": len(observed_pages),
        "observed_pages_sample": sorted(observed_pages)[:30],
        "lines_per_page": lines_per_page,
        "callouts_per_page": callouts_per_page,
        "total_lines_expected": total_lines,
        "total_callouts_expected": total_callouts,
        "page_title_exact_hits": page_title_exact,
        "page_recognized_hits": len(page_recognized_pages),
        "line_exact_hits": line_exact,
        "line_recoverable_only_hits": line_recoverable,
        "invoice_exact_hits": invoice_exact,
        "invoice_recoverable_only_hits": invoice_recoverable,
        "callout_exact_hits": callout_exact,
        "callout_recoverable_only_hits": callout_recoverable,
        "keyword_hits": dict(keyword_hits),
        "keyword_rates": keyword_rates,
        "generic": generic_summary,
        "issue_count": len(issues),
        "template_glue_score": _template_joined_word_risk(strict_text, "scanned_contract_ocr"),
    }
    score_json = {
        "score": score,
        "risk_level": risk_level,
        "recommendation": recommendation,
        "metrics": {
            "page_recognition_rate": page_rate,
            "page_title_strict_rate": page_title_strict_rate,
            "line_id_rate": line_rate,
            "line_id_recoverable_rate": line_recoverable_rate,
            "invoice_rate": invoice_rate,
            "invoice_recoverable_rate": invoice_recoverable_rate,
            "callout_rate": callout_rate,
            "callout_recoverable_rate": callout_recoverable_rate,
            "keyword_avg_rate": keyword_avg,
            "recoverable_boost": round(recoverable_boost, 4),
            "whitespace_quality": round(whitespace_quality, 4),
            "recoverable_counts": {
                "line": line_recoverable,
                "invoice": invoice_recoverable,
                "callout": callout_recoverable,
            },
        },
    }
    return summary, score_json, issues

def issue_to_dict(issue: EvalIssue) -> dict:
    return sanitize_json_for_postgres({
        "target_type": issue.target_type,
        "target_id": issue.target_id,
        "page_no": issue.page_no,
        "chunk_index": issue.chunk_index,
        "score": issue.score,
        "issue_level": issue.issue_level,
        "issue_type": issue.issue_type,
        "message": safe_text(issue.message),
        "evidence": issue.evidence or {},
        "suggestion": safe_text(issue.suggestion),
    })
