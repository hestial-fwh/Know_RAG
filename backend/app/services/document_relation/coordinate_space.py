"""Coordinate-space contracts and validation for image-text binding.

Defines the :class:`CoordinateSpace` enum and :class:`CoordinateContext`
dataclass used to track bbox provenance through the parse → layout →
visual-item pipeline.  All v2 binding improvements depend on this module
to detect and recover from coordinate-space mismatches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any

# BBox type — defined here to avoid circular import with page_layout_graph
BBox = tuple[float, float, float, float]


logger = logging.getLogger(__name__)

# ── epsilon tolerance for [0,1] bounds checking ──────────────────────
_EPS = 1e-6


class CoordinateSpace(str, Enum):
    """Well-known coordinate spaces in the parse → chunk pipeline."""

    PDF_PAGE = "pdf_page"
    MINERU_PAGE = "mineru_page"
    MINERU_IMAGE = "mineru_image"
    NORMALIZED_PAGE = "normalized_page"
    UNKNOWN = "unknown"


@dataclass
class CoordinateContext:
    """Provenance metadata for one bounding box.

    Carried alongside every :class:`PageLayoutNode` so downstream spatial
    matching can detect mismatched extents and re-normalize safely.
    """

    space: CoordinateSpace = CoordinateSpace.UNKNOWN
    extent: BBox | None = None
    page_size: tuple[float, float] | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "space": self.space.value,
            "extent": list(self.extent) if self.extent else None,
            "page_size": list(self.page_size) if self.page_size else None,
        }


def validate_normalized_bbox(bbox: BBox | None) -> BBox | None:
    """Return *bbox* unchanged if all four values are within [0, 1] + ε.

    Returns ``None`` when any coordinate falls outside the expected range,
    which indicates a coordinate-space mismatch that must be resolved
    upstream.
    """
    if bbox is None:
        return None
    for value in bbox:
        if not (-_EPS <= value <= 1.0 + _EPS):
            logger.warning(
                "Normalized bbox out of [0,1] bounds: %s",
                bbox,
            )
            return None
    return bbox


def renormalize_bbox(
    bbox: BBox,
    *,
    from_ctx: CoordinateContext,
    to_ctx: CoordinateContext,
) -> BBox | None:
    """Re-normalize *bbox* from *from_ctx* extent to *to_ctx* extent.

    Both contexts must have non-None extents.  Returns ``None`` when the
    necessary extent data is missing or the result is degenerate.

    Only validates [0,1] bounds when *to_ctx* uses a normalized space.
    """
    from_extent = from_ctx.extent
    to_extent = to_ctx.extent
    if from_extent is None or to_extent is None:
        return None

    from_w = from_extent[2] - from_extent[0]
    from_h = from_extent[3] - from_extent[1]
    to_w = to_extent[2] - to_extent[0]
    to_h = to_extent[3] - to_extent[1]

    if from_w <= 0 or from_h <= 0 or to_w <= 0 or to_h <= 0:
        return None

    result = (
        to_extent[0] + (bbox[0] - from_extent[0]) / from_w * to_w,
        to_extent[1] + (bbox[1] - from_extent[1]) / from_h * to_h,
        to_extent[0] + (bbox[2] - from_extent[0]) / from_w * to_w,
        to_extent[1] + (bbox[3] - from_extent[1]) / from_h * to_h,
    )
    # Only validate bounds when the target space is normalized
    if to_ctx.space == CoordinateSpace.NORMALIZED_PAGE:
        return validate_normalized_bbox(result)
    return result


__all__ = [
    "CoordinateContext",
    "CoordinateSpace",
    "renormalize_bbox",
    "validate_normalized_bbox",
]
