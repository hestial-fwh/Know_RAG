"""RAGFlow-inspired advanced PDF parser for know_rag.

This module is intentionally project-local.  It does not copy RAGFlow's full
DeepDoc runtime, but it implements the same data contract and the same major
engineering steps: page rendering, layout blocks, reading-order recovery,
table/figure cropping, caption binding, media context binding, and optional
OCR/VLM enrichment downstream.

When a real DeepDoc/MinerU service is configured later, this module can be
replaced behind the same ParsedDocument/ParsedBlock interface.
"""

from __future__ import annotations

import html
import logging
import math
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from app.services.document_parser.media_storage import save_media_bytes
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.document_parser.deepdoc_runtime import DeepDocDetection, DeepDocRuntime, get_deepdoc_runtime
from app.utils.text import sanitize_text_for_postgres

logger = logging.getLogger(__name__)

CAPTION_RE = re.compile(
    r"^\s*((fig\.?|figure|图)\s*[0-9一二三四五六七八九十IVXivx\-\.]*[:：\.]?|"
    r"(table|表)\s*[0-9一二三四五六七八九十IVXivx\-\.]*[:：\.]?)",
    re.I,
)
SECTION_RE = re.compile(r"^\s*((第[一二三四五六七八九十百0-9]+[章节])|([0-9]+(\.[0-9]+){0,4}\s+)|(#)+)\S+")
EQUATION_RE = re.compile(r"[=≈≤≥∑∫√∞α-ωΑ-Ω]|\\frac|\\sum|\\int")


@dataclass
class LayoutBlock:
    layout_type: str
    text: str = ""
    bbox: tuple[float, float, float, float] | None = None
    page_no: int = 0
    order_index: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def x0(self) -> float:
        return float(self.bbox[0]) if self.bbox else 0.0

    @property
    def top(self) -> float:
        return float(self.bbox[1]) if self.bbox else 0.0

    @property
    def x1(self) -> float:
        return float(self.bbox[2]) if self.bbox else 0.0

    @property
    def bottom(self) -> float:
        return float(self.bbox[3]) if self.bbox else 0.0


def _valid_bbox(bbox: Iterable[float], *, width: float, height: float, min_size: float = 6) -> tuple[float, float, float, float] | None:
    try:
        x0, top, x1, bottom = [float(v) for v in bbox]
    except Exception:
        return None
    x0 = max(0.0, min(x0, width))
    x1 = max(0.0, min(x1, width))
    top = max(0.0, min(top, height))
    bottom = max(0.0, min(bottom, height))
    if x1 < x0:
        x0, x1 = x1, x0
    if bottom < top:
        top, bottom = bottom, top
    if x1 - x0 < min_size or bottom - top < min_size:
        return None
    return (x0, top, x1, bottom)


def _bbox_union(items: list[dict]) -> tuple[float, float, float, float] | None:
    if not items:
        return None
    return (
        min(float(it.get("x0", 0)) for it in items),
        min(float(it.get("top", 0)) for it in items),
        max(float(it.get("x1", 0)) for it in items),
        max(float(it.get("bottom", 0)) for it in items),
    )


def _overlap_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, at, ax1, ab = a
    bx0, bt, bx1, bb = b
    return max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ab, bb) - max(at, bt))


def _bbox_area(b: tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _save_crop(page: Any, bbox: tuple[float, float, float, float], *, knowledge_base_id: str, document_id: str, filename_hint: str, resolution: int = 180) -> dict | None:
    try:
        cropped = page.crop(bbox)
        page_image = cropped.to_image(resolution=resolution)
        pil = page_image.original.convert("RGB")
        buf = BytesIO()
        pil.save(buf, format="PNG")
        return save_media_bytes(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            filename_hint=filename_hint,
            data=buf.getvalue(),
            default_ext=".png",
        )
    except Exception as exc:
        logger.info("PDF crop failed page=%s bbox=%s: %s", getattr(page, "page_number", "?"), bbox, exc)
        return None




def _render_page_image(page: Any, resolution: int = 216):
    """Render a pdfplumber page for ONNX layout/TSR inference."""
    try:
        img = page.to_image(resolution=resolution).original.convert("RGB")
        return img
    except Exception as exc:
        logger.info("DeepDoc page render failed page=%s: %s", getattr(page, "page_number", "?"), exc)
        return None


def _layout_type_from_deepdoc(label: str) -> str:
    value = str(label or "").lower().replace(" ", "_").replace("-", "_")
    mapping = {
        "text": "text",
        "title": "title",
        "figure": "figure",
        "image": "figure",
        "figure_caption": "figure_caption",
        "table": "table",
        "table_caption": "table_caption",
        "header": "header",
        "footer": "footer",
        "reference": "reference",
        "equation": "equation",
    }
    return mapping.get(value, value or "text")


def _apply_deepdoc_layout(page: Any, lines: list[LayoutBlock], runtime: DeepDocRuntime, page_image: Any | None = None) -> tuple[list[LayoutBlock], list[DeepDocDetection]]:
    """Apply real DeepDoc layout detections to pdfplumber text lines.

    Text still comes from pdfplumber/pdfminer so we can keep exact selectable PDF
    text when it exists.  The ONNX layout model supplies region types and media
    boxes.  This matches the production split used by RAGFlow: visual layout
    detection + textual boxes + downstream cleanup.
    """
    if not runtime.enabled:
        return lines, []
    page_image = page_image or _render_page_image(page)
    if page_image is None:
        return lines, []
    detections = runtime.predict_layout(page_image, page_width=float(page.width), page_height=float(page.height))
    if not detections:
        return lines, []

    for line in lines:
        if not line.bbox:
            continue
        line_area = _bbox_area(line.bbox)
        best = None
        best_ratio = 0.0
        for det in detections:
            ratio = _overlap_area(line.bbox, det.bbox) / max(line_area, 1.0)
            if ratio > best_ratio:
                best_ratio = ratio
                best = det
        if best and best_ratio >= 0.25:
            ltype = _layout_type_from_deepdoc(best.label)
            if ltype:
                line.layout_type = ltype
                line.metadata["deepdoc_layout_label"] = best.label
                line.metadata["deepdoc_layout_score"] = round(float(best.score), 4)
                line.metadata["layout_source"] = "deepdoc_onnx_or_service"

    # Add figure/equation/table regions that do not carry text boxes.  Vector
    # figures often have no embedded image object, so this is required for true
    # PDF figure extraction.
    existing_bboxes = [line.bbox for line in lines if line.bbox]
    extra: list[LayoutBlock] = []
    for idx, det in enumerate(detections):
        ltype = _layout_type_from_deepdoc(det.label)
        if ltype not in {"figure", "equation", "table"}:
            continue
        area = _bbox_area(det.bbox)
        if area < 1800:
            continue
        if any(_overlap_area(det.bbox, eb) / max(area, 1.0) > 0.7 for eb in existing_bboxes):
            continue
        extra.append(
            LayoutBlock(
                layout_type=ltype,
                text="",
                bbox=det.bbox,
                page_no=int(page.page_number or 0),
                order_index=len(lines) + len(extra),
                metadata={
                    "deepdoc_layout_label": det.label,
                    "deepdoc_layout_score": round(float(det.score), 4),
                    "layout_source": "deepdoc_onnx_or_service",
                },
            )
        )
    return [*lines, *extra], detections


def _words_in_bbox(page: Any, bbox: tuple[float, float, float, float]) -> list[dict]:
    try:
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
    except Exception:
        return []
    x0, top, x1, bottom = bbox
    selected = []
    for w in words:
        wb = (float(w.get("x0", 0)), float(w.get("top", 0)), float(w.get("x1", 0)), float(w.get("bottom", 0)))
        if _overlap_area(wb, bbox) / max(_bbox_area(wb), 1.0) > 0.45:
            selected.append(w)
    return selected


def _crop_pil_for_runtime(page: Any, bbox: tuple[float, float, float, float], resolution: int = 216):
    try:
        return page.crop(bbox).to_image(resolution=resolution).original.convert("RGB")
    except Exception:
        return None


def _table_from_deepdoc_tsr(page: Any, bbox: tuple[float, float, float, float], runtime: DeepDocRuntime) -> tuple[str, str, dict]:
    """Build table HTML/text from TSR row/column detections and PDF words."""
    crop = _crop_pil_for_runtime(page, bbox)
    if crop is None:
        return "", "", {"table_structure": "tsr_crop_render_failed"}
    dets = runtime.predict_tsr(crop, page_width=bbox[2] - bbox[0], page_height=bbox[3] - bbox[1])
    rows = sorted([d.bbox for d in dets if d.label.endswith("row") or d.label.endswith("row_header")], key=lambda b: b[1])
    cols = sorted([d.bbox for d in dets if d.label == "table_column"], key=lambda b: b[0])
    spans = [d.bbox for d in dets if d.label == "table_spanning_cell"]
    if not rows or not cols:
        return "", "", {"table_structure": "tsr_no_rows_or_columns", "tsr_detection_count": len(dets)}

    # Convert TSR crop coordinates to page coordinates.
    def to_page(local: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return (bbox[0] + local[0], bbox[1] + local[1], bbox[0] + local[2], bbox[1] + local[3])

    page_rows = [to_page(r) for r in rows]
    page_cols = [to_page(c) for c in cols]
    words = _words_in_bbox(page, bbox)
    grid: list[list[str]] = []
    for rb in page_rows:
        row_values: list[str] = []
        for cb in page_cols:
            cell = (cb[0], rb[1], cb[2], rb[3])
            cell_words = []
            for w in words:
                wb = (float(w.get("x0", 0)), float(w.get("top", 0)), float(w.get("x1", 0)), float(w.get("bottom", 0)))
                if _overlap_area(wb, cell) / max(_bbox_area(wb), 1.0) > 0.35:
                    cell_words.append(w)
            cell_words = sorted(cell_words, key=lambda ww: (float(ww.get("top", 0)), float(ww.get("x0", 0))))
            row_values.append(sanitize_text_for_postgres(" ".join(str(w.get("text", "")) for w in cell_words).strip()))
        if any(row_values):
            grid.append(row_values)
    if not grid:
        return "", "", {"table_structure": "tsr_no_text_in_cells", "tsr_detection_count": len(dets)}
    html_text, plain = _table_to_html(grid)
    return html_text, plain, {
        "table_structure": "deepdoc_tsr_onnx_or_service",
        "tsr_detection_count": len(dets),
        "tsr_row_count": len(rows),
        "tsr_column_count": len(cols),
        "tsr_spanning_cell_count": len(spans),
    }

def _is_probable_header_footer(line: LayoutBlock, page_height: float) -> bool:
    if line.top < page_height * 0.055:
        return True
    if line.bottom > page_height * 0.945:
        return True
    text = (line.text or "").strip()
    if len(text) <= 6 and re.fullmatch(r"[\-—]*\s*\d+\s*[\-—]*", text):
        return True
    return False


def _classify_line(text: str, bbox: tuple[float, float, float, float], page_width: float, page_height: float) -> str:
    clean = (text or "").strip()
    if not clean:
        return "text"
    if CAPTION_RE.search(clean):
        if re.match(r"^\s*((fig\.?|figure|图))", clean, re.I):
            return "figure_caption"
        return "table_caption"
    if EQUATION_RE.search(clean) and len(clean) < 180:
        return "equation"
    # title heuristic: short line, near left or centered, starts with section pattern, or mostly bold-like unavailable.
    if len(clean) < 120 and (SECTION_RE.search(clean) or bbox[1] < page_height * 0.18):
        return "title"
    return "text"


def _extract_words_as_lines(page: Any) -> list[LayoutBlock]:
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False, extra_attrs=["fontname", "size"]) or []
    if not words:
        text = sanitize_text_for_postgres(page.extract_text() or "")
        if not text:
            return []
        return [LayoutBlock(layout_type="text", text=text, bbox=(0.0, 0.0, float(page.width), float(page.height)), page_no=int(page.page_number or 0))]

    # Group words into visual lines by y-coordinate.  This mirrors RAGFlow's
    # box cleanup/merge stage, but keeps the implementation dependency-light.
    heights = [float(w.get("bottom", 0)) - float(w.get("top", 0)) for w in words]
    line_threshold = max(3.0, (median(heights) if heights else 10.0) * 0.55)
    words = sorted(words, key=lambda w: (float(w.get("top", 0)), float(w.get("x0", 0))))
    lines: list[list[dict]] = []
    for word in words:
        top = float(word.get("top", 0))
        if not lines:
            lines.append([word])
            continue
        last = lines[-1]
        last_top = sum(float(w.get("top", 0)) for w in last) / len(last)
        if abs(top - last_top) <= line_threshold:
            last.append(word)
        else:
            lines.append([word])

    blocks: list[LayoutBlock] = []
    for idx, line_words in enumerate(lines):
        line_words = sorted(line_words, key=lambda w: float(w.get("x0", 0)))
        text = sanitize_text_for_postgres(" ".join(str(w.get("text", "")) for w in line_words).strip())
        if not text:
            continue
        bbox = _bbox_union(line_words)
        if bbox is None:
            continue
        ltype = _classify_line(text, bbox, float(page.width), float(page.height))
        block = LayoutBlock(layout_type=ltype, text=text, bbox=bbox, page_no=int(page.page_number or 0), order_index=idx)
        if _is_probable_header_footer(block, float(page.height)):
            block.layout_type = "header" if block.top < float(page.height) / 2 else "footer"
        blocks.append(block)
    return blocks


def _detect_columns(lines: list[LayoutBlock], page_width: float) -> list[tuple[float, float]]:
    body = [l for l in lines if l.layout_type in {"text", "title", "equation"} and l.bbox]
    if len(body) < 8:
        return [(0.0, page_width)]
    centers = sorted((l.x0 + l.x1) / 2 for l in body)
    gaps = [(centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)]
    if not gaps:
        return [(0.0, page_width)]
    gap, idx = max(gaps, key=lambda item: item[0])
    if gap < page_width * 0.12:
        return [(0.0, page_width)]
    split = (centers[idx] + centers[idx + 1]) / 2
    left_count = sum(1 for c in centers if c < split)
    right_count = len(centers) - left_count
    if min(left_count, right_count) < max(3, len(centers) * 0.18):
        return [(0.0, page_width)]
    return [(0.0, split), (split, page_width)]


def _reading_order(lines: list[LayoutBlock], page_width: float) -> list[LayoutBlock]:
    # Headers/footers/references remain metadata; main text is ordered by detected columns.
    columns = _detect_columns(lines, page_width)
    for line in lines:
        center = (line.x0 + line.x1) / 2 if line.bbox else 0
        line.metadata["column_index"] = 0
        for idx, (left, right) in enumerate(columns):
            if left <= center <= right:
                line.metadata["column_index"] = idx
                break
        line.metadata["column_count"] = len(columns)
    return sorted(lines, key=lambda l: (l.page_no, int(l.metadata.get("column_index", 0)), l.top, l.x0))




def _concat_features(up: LayoutBlock, down: LayoutBlock) -> list[float]:
    """RAGFlow-compatible-ish features for the up/down XGBoost concat model.

    The original RAGFlow model uses geometric, layout and token-boundary cues.
    This vector preserves that spirit and length-stabilizes to 33 features so a
    compatible production model can be plugged in without changing parser code.
    """
    def char_width(b: LayoutBlock) -> float:
        return (b.x1 - b.x0) / max(len(b.text or ""), 1)

    def height(b: LayoutBlock) -> float:
        return max(1.0, b.bottom - b.top)

    w = max(char_width(up), char_width(down), 1.0)
    h = max(height(up), height(down), 1.0)
    y_dis = ((down.top + down.bottom) - (up.top + up.bottom)) / 2
    last = (up.text or "")[-1:] or ""
    first = (down.text or "")[:1] or ""
    up_tail = (up.text or "")[-8:]
    down_head = (down.text or "")[:8]
    feats = [
        float(up.metadata.get("column_index") == down.metadata.get("column_index")),
        y_dis / h,
        float((down.page_no or 0) - (up.page_no or 0)),
        float(up.layout_type == down.layout_type),
        float(up.layout_type == "text"),
        float(down.layout_type == "text"),
        float(up.layout_type == "table"),
        float(down.layout_type == "table"),
        float(bool(re.search(r"([。？！；!?;+)）]|[a-z]\\.)$", up.text or ""))),
        float(bool(re.search(r"[，：‘“、0-9（+\\-]$", up.text or ""))),
        float(bool(re.search(r"(^.?[/,?;:\\]，。；：’”？！》】）-])", down.text or ""))),
        float(bool(re.match(r"[\\(（][^\\(\\)（）]+[）\\)]$", up.text or ""))),
        float(bool(re.search(r"[，,][^。.]+$", up.text or ""))),
        float(bool(re.search(r"[\\(（][^\\)）]+$", up.text or "") and re.search(r"[\\)）]", down.text or ""))),
        float(bool(re.match(r"[0-9一二三四五六七八九十]+[、.）)]", down.text or ""))),
        float(bool(re.match(r"[A-Z]", first))),
        float(bool(re.match(r"[A-Z]", last))),
        float(bool(re.match(r"[a-z0-9]", last))),
        float(bool(re.match(r"[0-9.%,-]+$", down.text or ""))),
        float(up_tail.strip()[-2:] == down_head.strip()[-2:] if len(up_tail.strip()) > 1 and len(down_head.strip()) > 1 else False),
        float(up.x0 > down.x1),
        abs(height(up) - height(down)) / min(height(up), height(down)),
        min(abs(up.x1 - down.x0), abs(up.x0 - down.x1), abs(up.x0 + up.x1 - down.x0 - down.x1) / 2) / w,
        (len(up.text or "") - len(down.text or "")) / max(len(up.text or ""), len(down.text or ""), 1),
        float(len(up_tail + down_head) - len(up_tail) - len(down_head)),
        float(len(down_head) - len(up_tail)),
        float((down_head.split()[-1:] or [""])[0] == (up_tail.split()[-1:] or [""])[0]),
        float(max(int(down.metadata.get("column_index", 0)), int(up.metadata.get("column_index", 0)))),
        float(abs(int(down.metadata.get("column_index", 0)) - int(up.metadata.get("column_index", 0)))),
        float(len((down.text or "").split()) == 1),
        float(len((up.text or "").split()) == 1),
        float(len(up.text or "")),
        float(len(down.text or "")),
    ]
    if len(feats) < 33:
        feats.extend([0.0] * (33 - len(feats)))
    return feats[:33]

def _merge_downward(lines: list[LayoutBlock], runtime: DeepDocRuntime | None = None) -> list[LayoutBlock]:
    # XGBoost-compatible merge point.  If a true model is wired later, it can be
    # invoked here.  The current deterministic merger uses the same cues:
    # layout type, vertical distance, punctuation, x alignment and column.
    merged: list[LayoutBlock] = []
    for line in lines:
        if line.layout_type not in {"text", "title", "equation"}:
            merged.append(line)
            continue
        if not merged:
            merged.append(line)
            continue
        prev = merged[-1]
        same_col = prev.metadata.get("column_index") == line.metadata.get("column_index")
        same_type = prev.layout_type == line.layout_type or {prev.layout_type, line.layout_type} <= {"text", "equation"}
        y_gap = line.top - prev.bottom
        x_close = abs(prev.x0 - line.x0) < max(18, (prev.x1 - prev.x0) * 0.08)
        prev_text = prev.text.strip()
        model_prob = None
        if runtime is not None and getattr(runtime, "_xgb", None):
            model_prob = runtime.concat_probability(_concat_features(prev, line))
        if model_prob is not None:
            should_merge = model_prob >= runtime.merge_threshold
        else:
            should_merge = (
                prev.page_no == line.page_no
                and same_col
                and same_type
                and y_gap >= -2
                and y_gap < 20
                and (x_close or not re.search(r"[。！？!?；;:]$", prev_text))
                and line.layout_type != "title"
            )
        if should_merge:
            sep = "" if re.search(r"[A-Za-z0-9]$", prev.text) and re.match(r"^[A-Za-z0-9]", line.text) else "\n"
            prev.text = sanitize_text_for_postgres(f"{prev.text}{sep}{line.text}")
            if prev.bbox and line.bbox:
                prev.bbox = (min(prev.x0, line.x0), min(prev.top, line.top), max(prev.x1, line.x1), max(prev.bottom, line.bottom))
            prev.metadata["vertical_concat"] = "heuristic_xgb_compatible"
            continue
        merged.append(line)
    return merged


def _table_to_html(rows: list[list[Any]]) -> tuple[str, str]:
    if not rows:
        return "", ""
    plain_rows: list[str] = []
    html_parts = ["<table>"]
    for r_idx, row in enumerate(rows):
        cells = [sanitize_text_for_postgres(str(cell or "").strip()) for cell in row]
        if any(cells):
            plain_rows.append(" | ".join(cells))
        html_parts.append("<tr>")
        c = 0
        while c < len(cells):
            value = cells[c]
            colspan = 1
            # Simple spanning-cell recovery: repeated text or empty continuations
            # after a non-empty cell are treated as horizontal spans.
            while c + colspan < len(cells) and cells[c + colspan] in {"", value} and value:
                colspan += 1
            attrs = f" colspan=\"{colspan}\"" if colspan > 1 else ""
            tag = "th" if r_idx == 0 else "td"
            html_parts.append(f"<{tag}{attrs}>{html.escape(value)}</{tag}>")
            c += colspan
        html_parts.append("</tr>")
    html_parts.append("</table>")
    return "".join(html_parts), "\n".join(plain_rows)


def _nearest_caption(layouts: list[LayoutBlock], bbox: tuple[float, float, float, float], caption_type: str) -> LayoutBlock | None:
    candidates = [l for l in layouts if l.layout_type == caption_type and l.bbox]
    if not candidates:
        return None
    x0, top, x1, bottom = bbox
    best: tuple[float, LayoutBlock] | None = None
    for cap in candidates:
        horizontal = max(0.0, min(x1, cap.x1) - max(x0, cap.x0)) / max(1.0, x1 - x0)
        gap = min(abs(cap.top - bottom), abs(top - cap.bottom))
        if horizontal < 0.15 and gap > 30:
            continue
        score = gap - horizontal * 20
        if best is None or score < best[0]:
            best = (score, cap)
    if best and best[0] < 80:
        return best[1]
    return None


def _context_for_bbox(layouts: list[LayoutBlock], bbox: tuple[float, float, float, float], page_no: int, *, token_chars: int = 800) -> tuple[str, str, str]:
    page_lines = [l for l in layouts if l.page_no == page_no and l.layout_type in {"text", "title", "equation"}]
    before = [l.text for l in page_lines if l.bottom <= bbox[1]]
    after = [l.text for l in page_lines if l.top >= bbox[3]]
    # nearest title before media
    titles = [l.text for l in page_lines if l.layout_type == "title" and l.bottom <= bbox[1]]
    header = titles[-1] if titles else ""
    context_above = sanitize_text_for_postgres("\n".join(before)[-token_chars:])
    context_below = sanitize_text_for_postgres("\n".join(after)[:token_chars])
    return header, context_above, context_below


def _block_from_layout(line: LayoutBlock, order: int) -> ParsedBlock | None:
    if line.layout_type in {"header", "footer", "reference", "figure_caption", "table_caption"}:
        return None
    block_type = "text"
    metadata = {"layout_type": line.layout_type, "column_index": line.metadata.get("column_index"), "column_count": line.metadata.get("column_count")}
    if line.layout_type == "equation":
        metadata["layout_type"] = "equation"
    return ParsedBlock(
        block_type=block_type,
        text=sanitize_text_for_postgres(line.text),
        order_index=order,
        page_no=line.page_no,
        bbox=line.bbox,
        metadata={"parser": "ragflow_advanced_pdf", "source": "layout_text", **metadata},
    )


def _extract_tables(page: Any, layouts: list[LayoutBlock], *, knowledge_base_id: str, document_id: str, order_start: int, runtime: DeepDocRuntime | None = None) -> tuple[list[ParsedBlock], list[tuple[float, float, float, float]], int]:
    blocks: list[ParsedBlock] = []
    bboxes: list[tuple[float, float, float, float]] = []
    order = order_start
    try:
        tables = page.find_tables() or []
    except Exception as exc:
        logger.info("find_tables failed page=%s: %s", getattr(page, "page_number", "?"), exc)
        return blocks, bboxes, order
    for idx, table in enumerate(tables):
        try:
            rows = table.extract() or []
        except Exception:
            rows = []
        html_text, plain = _table_to_html(rows)
        bbox = _valid_bbox(table.bbox, width=float(page.width), height=float(page.height)) if getattr(table, "bbox", None) else None
        tsr_meta = {}
        if bbox and runtime is not None and (runtime.status().tsr_loaded or runtime.status().service_url):
            tsr_html, tsr_plain, tsr_meta = _table_from_deepdoc_tsr(page, bbox, runtime)
            if tsr_plain.strip():
                html_text, plain = tsr_html, tsr_plain
        if not plain.strip():
            continue
        if not bbox:
            continue
        bboxes.append(bbox)
        media = _save_crop(page, bbox, knowledge_base_id=knowledge_base_id, document_id=document_id, filename_hint=f"pdf_page_{page.page_number}_table_{idx}.png")
        caption = _nearest_caption(layouts, bbox, "table_caption")
        header, ctx_above, ctx_below = _context_for_bbox(layouts, bbox, int(page.page_number or 0))
        caption_text = caption.text if caption else ""
        content = sanitize_text_for_postgres("\n".join(x for x in [caption_text, plain] if x))
        metadata = {
            "parser": "ragflow_advanced_pdf",
            "source": "layout_table_crop",
            "layout_type": "table",
            "page_no": int(page.page_number or 0),
            "table_index": idx,
            "caption": caption_text,
            "context_above": ctx_above,
            "context_below": ctx_below,
            "table_structure": tsr_meta.get("table_structure", "html_colspan_heuristic"),
            **tsr_meta,
        }
        if media:
            metadata.update({"relative_path": media["relative_path"], "sha256": media["sha256"], "size": media["size"]})
        blocks.append(
            ParsedBlock(
                block_type="table",
                text=content,
                table_html=html_text,
                order_index=order,
                page_no=int(page.page_number or 0),
                bbox=bbox,
                image_path=media["path"] if media else "",
                image_url=media["relative_path"] if media else "",
                image_base64=media["base64"] if media else "",
                context_header=header,
                metadata=metadata,
            )
        )
        order += 1
    # DeepDoc layout may detect image/ruled/vector tables missed by pdfplumber.
    layout_tables = [l for l in layouts if l.layout_type == "table" and l.bbox]
    for layout_idx, lt in enumerate(layout_tables):
        bbox = lt.bbox
        if not bbox:
            continue
        area = _bbox_area(bbox)
        if any(_overlap_area(bbox, existing) / max(area, 1.0) > 0.65 for existing in bboxes):
            continue
        media = _save_crop(page, bbox, knowledge_base_id=knowledge_base_id, document_id=document_id, filename_hint=f"pdf_page_{page.page_number}_deepdoc_table_{layout_idx}.png")
        html_text = ""
        plain = ""
        tsr_meta = {"table_structure": "deepdoc_layout_table_crop"}
        if runtime is not None and (runtime.status().tsr_loaded or runtime.status().service_url):
            html_text, plain, tsr_meta = _table_from_deepdoc_tsr(page, bbox, runtime)
        if not plain.strip():
            words = _words_in_bbox(page, bbox)
            plain = sanitize_text_for_postgres(" ".join(str(w.get("text", "")) for w in sorted(words, key=lambda w: (float(w.get("top", 0)), float(w.get("x0", 0))))).strip())
            html_text = f"<table><tr><td>{html.escape(plain)}</td></tr></table>" if plain else ""
        caption = _nearest_caption(layouts, bbox, "table_caption")
        header, ctx_above, ctx_below = _context_for_bbox(layouts, bbox, int(page.page_number or 0))
        caption_text = caption.text if caption else ""
        content = sanitize_text_for_postgres("\n".join(x for x in [caption_text, plain] if x)) or f"PDF 第 {int(page.page_number or 0)} 页表格区域"
        meta = {
            "parser": "ragflow_advanced_pdf",
            "source": "deepdoc_layout_table_crop",
            "layout_type": "table",
            "page_no": int(page.page_number or 0),
            "table_index": f"deepdoc-{layout_idx}",
            "caption": caption_text,
            "context_above": ctx_above,
            "context_below": ctx_below,
            "deepdoc_layout_score": lt.metadata.get("deepdoc_layout_score"),
            **tsr_meta,
        }
        if media:
            meta.update({"relative_path": media["relative_path"], "sha256": media["sha256"], "size": media["size"]})
        blocks.append(
            ParsedBlock(
                block_type="table",
                text=content,
                table_html=html_text,
                order_index=order,
                page_no=int(page.page_number or 0),
                bbox=bbox,
                image_path=media["path"] if media else "",
                image_url=media["relative_path"] if media else "",
                image_base64=media["base64"] if media else "",
                context_header=header,
                metadata=meta,
            )
        )
        bboxes.append(bbox)
        order += 1
    return blocks, bboxes, order


def _extract_figures(page: Any, layouts: list[LayoutBlock], table_bboxes: list[tuple[float, float, float, float]], *, knowledge_base_id: str, document_id: str, order_start: int) -> tuple[list[ParsedBlock], int]:
    blocks: list[ParsedBlock] = []
    order = order_start
    images = getattr(page, "images", []) or []
    for idx, image in enumerate(images):
        raw_bbox = (image.get("x0", 0), image.get("top", image.get("y0", 0)), image.get("x1", 0), image.get("bottom", image.get("y1", 0)))
        bbox = _valid_bbox(raw_bbox, width=float(page.width), height=float(page.height), min_size=16)
        if not bbox:
            continue
        area = _bbox_area(bbox)
        if area < 2500:
            continue
        if any(_overlap_area(bbox, tb) / max(area, 1.0) > 0.45 for tb in table_bboxes):
            continue
        media = _save_crop(page, bbox, knowledge_base_id=knowledge_base_id, document_id=document_id, filename_hint=f"pdf_page_{page.page_number}_figure_{idx}.png")
        if not media:
            continue
        caption = _nearest_caption(layouts, bbox, "figure_caption")
        header, ctx_above, ctx_below = _context_for_bbox(layouts, bbox, int(page.page_number or 0))
        caption_text = caption.text if caption else ""
        text = sanitize_text_for_postgres(caption_text or f"PDF 第 {int(page.page_number or 0)} 页图像区域：{media['filename']}")
        blocks.append(
            ParsedBlock(
                block_type="image",
                text=text,
                order_index=order,
                page_no=int(page.page_number or 0),
                bbox=bbox,
                image_path=media["path"],
                image_url=media["relative_path"],
                image_base64=media["base64"],
                context_header=header,
                metadata={
                    "parser": "ragflow_advanced_pdf",
                    "source": "layout_figure_crop",
                    "layout_type": "figure",
                    "page_no": int(page.page_number or 0),
                    "figure_index": idx,
                    "caption": caption_text,
                    "context_above": ctx_above,
                    "context_below": ctx_below,
                    "relative_path": media["relative_path"],
                    "sha256": media["sha256"],
                    "size": media["size"],
                },
            )
        )
        order += 1
    # DeepDoc layout boxes catch vector figures/equations that pdfplumber does not
    # expose as embedded image objects.
    layout_figures = [l for l in layouts if l.layout_type in {"figure", "equation"} and l.bbox]
    existing = [b.bbox for b in blocks if b.bbox]
    for layout_idx, lf in enumerate(layout_figures):
        bbox = lf.bbox
        if not bbox:
            continue
        area = _bbox_area(bbox)
        if area < 1800:
            continue
        if any(_overlap_area(bbox, tb) / max(area, 1.0) > 0.45 for tb in table_bboxes):
            continue
        if any(eb and _overlap_area(bbox, eb) / max(area, 1.0) > 0.65 for eb in existing):
            continue
        media = _save_crop(page, bbox, knowledge_base_id=knowledge_base_id, document_id=document_id, filename_hint=f"pdf_page_{page.page_number}_deepdoc_figure_{layout_idx}.png")
        if not media:
            continue
        caption = _nearest_caption(layouts, bbox, "figure_caption")
        header, ctx_above, ctx_below = _context_for_bbox(layouts, bbox, int(page.page_number or 0))
        caption_text = caption.text if caption else ""
        text = sanitize_text_for_postgres(caption_text or f"PDF 第 {int(page.page_number or 0)} 页{lf.layout_type}区域：{media['filename']}")
        blocks.append(
            ParsedBlock(
                block_type="image",
                text=text,
                order_index=order,
                page_no=int(page.page_number or 0),
                bbox=bbox,
                image_path=media["path"],
                image_url=media["relative_path"],
                image_base64=media["base64"],
                context_header=header,
                metadata={
                    "parser": "ragflow_advanced_pdf",
                    "source": "deepdoc_layout_figure_crop",
                    "layout_type": lf.layout_type,
                    "page_no": int(page.page_number or 0),
                    "figure_index": f"deepdoc-{layout_idx}",
                    "caption": caption_text,
                    "context_above": ctx_above,
                    "context_below": ctx_below,
                    "deepdoc_layout_score": lf.metadata.get("deepdoc_layout_score"),
                    "relative_path": media["relative_path"],
                    "sha256": media["sha256"],
                    "size": media["size"],
                },
            )
        )
        order += 1
    return blocks, order


def _render_scanned_pages(path: Path, *, knowledge_base_id: str, document_id: str, start_order: int, dpi: int = 180) -> list[ParsedBlock]:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        logger.info("pypdfium2 not available; skip scanned PDF rendering: %s", exc)
        return []
    blocks: list[ParsedBlock] = []
    pdf = pdfium.PdfDocument(str(path))
    scale = max(dpi, 72) / 72
    order = start_order
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            bitmap = None
            try:
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil().convert("RGB")
                buf = BytesIO()
                pil.save(buf, format="JPEG", quality=88, optimize=True)
                data = buf.getvalue()
            finally:
                close = getattr(bitmap, "close", None)
                if close:
                    close()
                close = getattr(page, "close", None)
                if close:
                    close()
            media = save_media_bytes(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                filename_hint=f"pdf_page_{page_index + 1}.jpg",
                data=data,
                default_ext=".jpg",
            )
            blocks.append(
                ParsedBlock(
                    block_type="image",
                    text=f"扫描型 PDF 第 {page_index + 1} 页图片：{media['filename']}",
                    order_index=order,
                    page_no=page_index + 1,
                    image_path=media["path"],
                    image_url=media["relative_path"],
                    image_base64=media["base64"],
                    metadata={"parser": "ragflow_advanced_pdf", "source": "pdf_page_render", "layout_type": "scanned_page", "page_no": page_index + 1, **media},
                )
            )
            order += 1
    finally:
        close = getattr(pdf, "close", None)
        if close:
            close()
    return blocks


def parse_pdf_ragflow_advanced(path: Path, *, knowledge_base_id: str, document_id: str, render_scanned_pages: bool = True) -> ParsedDocument:
    import pdfplumber

    parsed_blocks: list[ParsedBlock] = []
    deepdoc_runtime = get_deepdoc_runtime()
    deepdoc_status = deepdoc_runtime.status_dict()
    metadata = {
        "parser": "ragflow_advanced_pdf",
        "pdf_engine": "pdfplumber_layout_advanced",
        "layout_recognizer": "deepdoc_onnx_or_service" if deepdoc_status.get("layout_loaded") or deepdoc_status.get("service_url") else "heuristic_deepdoc_compatible",
        "table_structure_recognizer": "deepdoc_tsr_onnx_or_service" if deepdoc_status.get("tsr_loaded") or deepdoc_status.get("service_url") else "pdfplumber_tsr_html_colspan",
        "vertical_concat_engine": "xgboost_model" if deepdoc_status.get("xgb_loaded") else "heuristic_xgb_compatible",
        "figure_caption_binding": "nearest_caption_with_context",
        "multi_column_ordering": "enabled",
        "pages": 0,
        "table_crop_count": 0,
        "figure_crop_count": 0,
        "caption_bound_count": 0,
        "deepdoc_runtime": deepdoc_status,
    }
    with pdfplumber.open(str(path)) as pdf:
        metadata["pages"] = len(pdf.pages)
        order = 0
        for page in pdf.pages:
            raw_layouts = _extract_words_as_lines(page)
            page_image = _render_page_image(page) if deepdoc_runtime.enabled else None
            raw_layouts, _deepdoc_dets = _apply_deepdoc_layout(page, raw_layouts, deepdoc_runtime, page_image)
            ordered_layouts = _reading_order(raw_layouts, float(page.width))
            merged_layouts = _merge_downward(ordered_layouts, deepdoc_runtime)
            table_blocks, table_bboxes, order = _extract_tables(
                page,
                merged_layouts,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                order_start=order,
                runtime=deepdoc_runtime,
            )
            figure_blocks, order = _extract_figures(
                page,
                merged_layouts,
                table_bboxes,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                order_start=order,
            )
            metadata["table_crop_count"] += len([b for b in table_blocks if b.image_url])
            metadata["figure_crop_count"] += len(figure_blocks)
            metadata["caption_bound_count"] += len([b for b in [*table_blocks, *figure_blocks] if (b.metadata or {}).get("caption")])

            # Text blocks: remove headers/footers/captions and avoid text that lies mostly inside table/figure boxes.
            media_bboxes = [b.bbox for b in [*table_blocks, *figure_blocks] if b.bbox]
            for line in merged_layouts:
                if not line.bbox:
                    continue
                if line.layout_type in {"header", "footer", "reference", "figure_caption", "table_caption"}:
                    continue
                line_area = _bbox_area(line.bbox)
                if line_area and any(_overlap_area(line.bbox, mb) / line_area > 0.6 for mb in media_bboxes):
                    continue
                block = _block_from_layout(line, order)
                if block and block.text.strip():
                    parsed_blocks.append(block)
                    order += 1
            parsed_blocks.extend(table_blocks)
            parsed_blocks.extend(figure_blocks)

    has_text = any(b.block_type in {"text", "table"} and b.index_text().strip() for b in parsed_blocks)
    if not has_text and render_scanned_pages:
        rendered = _render_scanned_pages(path, knowledge_base_id=knowledge_base_id, document_id=document_id, start_order=len(parsed_blocks))
        parsed_blocks.extend(rendered)
        metadata["scanned_pdf_rendered"] = bool(rendered)
        metadata["scanned_page_image_count"] = len(rendered)

    parsed_blocks.sort(key=lambda b: (b.page_no or 0, b.order_index))
    content_parts: list[str] = []
    for block in parsed_blocks:
        if block.block_type == "image":
            caption = (block.metadata or {}).get("caption") or block.text
            content_parts.append(f"![{caption}]({block.image_url})")
        elif block.block_type == "table" and block.image_url:
            content_parts.append(f"{block.index_text()}\n\n![table]({block.image_url})")
        else:
            content_parts.append(block.index_text())
    content = sanitize_text_for_postgres("\n\n".join(part for part in content_parts if part))
    if not content:
        raise ValueError("未能从 PDF 中解析出文本或图片内容，请检查 PDF 是否损坏，或接入 OCR 服务")
    return ParsedDocument(content=content, blocks=parsed_blocks, metadata=metadata)
