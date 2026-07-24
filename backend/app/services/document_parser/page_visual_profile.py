from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from typing import Any

from app.services.document_parser.types import ParsedBlock, ParsedDocument


BBox = tuple[float, float, float, float]


def _bbox(value: Any) -> BBox | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        box = tuple(float(item) for item in value[:4])
    except (TypeError, ValueError):
        return None
    if not all(isfinite(item) for item in box):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _area(box: BBox | None) -> float:
    if box is None:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _page_extent(block: ParsedBlock, fallback: BBox) -> BBox:
    metadata = block.metadata or {}
    extent = _bbox(metadata.get("page_extent"))
    if extent is not None:
        return extent
    try:
        width = float(metadata.get("page_width") or 0.0)
        height = float(metadata.get("page_height") or 0.0)
    except (TypeError, ValueError):
        return fallback
    if width > 0.0 and height > 0.0:
        return (0.0, 0.0, width, height)
    return fallback


def _block_page_coverage(block: ParsedBlock, fallback_extent: BBox) -> float:
    metadata = block.metadata or {}
    if metadata.get("visual_role") == "page_primary_visual":
        return 1.0
    if metadata.get("source") in {"pdf_page_render", "pdf_page_visual_guard"}:
        return 1.0
    extent = _page_extent(block, fallback_extent)
    return min(1.0, _area(_bbox(block.bbox)) / max(_area(extent), 1.0))


@dataclass(frozen=True)
class PageVisualProfile:
    page_no: int
    page_width: float
    page_height: float
    native_text_chars: int
    embedded_image_count: int
    dominant_image_bbox: BBox | None
    dominant_image_ratio: float
    parser_image_count: int
    parser_full_page_image_count: int
    decision: str
    heuristic_score: float
    reasons: tuple[str, ...] = ()
    coordinate_space: str = "pdf_page"

    @property
    def page_extent(self) -> BBox:
        return (0.0, 0.0, self.page_width, self.page_height)

    def to_metadata(self) -> dict[str, Any]:
        value = asdict(self)
        value["dominant_image_bbox"] = (
            list(self.dominant_image_bbox) if self.dominant_image_bbox else None
        )
        value["reasons"] = list(self.reasons)
        return value


def _image_bbox(image: dict[str, Any]) -> BBox | None:
    return _bbox(
        (
            image.get("x0"),
            image.get("top", image.get("y0")),
            image.get("x1"),
            image.get("bottom", image.get("y1")),
        )
    )


def profile_pdf_pages(
    path: Path,
    parsed_document: ParsedDocument,
    *,
    dominant_image_threshold: float = 0.70,
    existing_image_threshold: float = 0.65,
) -> list[PageVisualProfile]:
    """Compare source PDF page facts with parser image coverage.

    The profiler is read-only. It does not render pages or mutate parser output.
    Scores are deterministic heuristic scores, never probability/confidence.
    """
    import pdfplumber

    dominant_threshold = min(0.98, max(0.50, float(dominant_image_threshold)))
    existing_threshold = min(0.98, max(0.50, float(existing_image_threshold)))
    blocks_by_page: dict[int, list[ParsedBlock]] = {}
    for block in parsed_document.blocks or []:
        if block.page_no is not None:
            blocks_by_page.setdefault(int(block.page_no), []).append(block)

    profiles: list[PageVisualProfile] = []
    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            page_extent = (0.0, 0.0, float(page.width), float(page.height))
            page_area = max(_area(page_extent), 1.0)
            image_boxes = [
                box
                for box in (_image_bbox(image) for image in (page.images or []))
                if box is not None
            ]
            dominant_bbox = max(image_boxes, key=_area, default=None)
            dominant_ratio = min(1.0, _area(dominant_bbox) / page_area)
            parser_images = [
                block
                for block in blocks_by_page.get(page_no, [])
                if block.block_type == "image"
            ]
            parser_full_count = sum(
                _block_page_coverage(block, page_extent) >= existing_threshold
                for block in parser_images
            )
            native_text = page.extract_text() or ""
            native_text_chars = len("".join(native_text.split()))
            reasons: list[str] = []
            if dominant_ratio >= dominant_threshold:
                reasons.append("dominant_embedded_image")
                if native_text_chars == 0:
                    reasons.append("no_native_pdf_text")
                if parser_full_count:
                    decision = "already_present"
                    reasons.append("parser_full_page_image_present")
                else:
                    decision = "preserve"
                    reasons.append("parser_full_page_image_missing")
            elif dominant_ratio >= max(0.50, dominant_threshold - 0.15):
                decision = "ambiguous"
                reasons.append("medium_coverage_embedded_image")
            else:
                decision = "normal"
                reasons.append("no_dominant_embedded_image")
            profiles.append(
                PageVisualProfile(
                    page_no=page_no,
                    page_width=float(page.width),
                    page_height=float(page.height),
                    native_text_chars=native_text_chars,
                    embedded_image_count=len(image_boxes),
                    dominant_image_bbox=dominant_bbox,
                    dominant_image_ratio=round(dominant_ratio, 6),
                    parser_image_count=len(parser_images),
                    parser_full_page_image_count=parser_full_count,
                    decision=decision,
                    heuristic_score=round(dominant_ratio, 6),
                    reasons=tuple(reasons),
                )
            )
    return profiles


__all__ = ["BBox", "PageVisualProfile", "profile_pdf_pages"]
