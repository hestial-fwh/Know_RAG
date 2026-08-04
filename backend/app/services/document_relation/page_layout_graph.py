from __future__ import annotations

from dataclasses import dataclass, field
import logging
from math import isfinite
from statistics import median as _median
from typing import Any

from app.services.document_relation.coordinate_space import (
    CoordinateContext,
    CoordinateSpace,
    validate_normalized_bbox,
)


logger = logging.getLogger(__name__)

# ── v2 constants ─────────────────────────────────────────────────────
_ANOMALOUS_EXTENT_RATIO = 3.0
_NORMALIZE_EPS = 1e-6

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


def _asset_section(asset: Any) -> str:
    metadata = getattr(asset, "metadata", None) or {}
    section = str(metadata.get("section_path") or "").strip()
    if section:
        return section
    heading_path = metadata.get("heading_path")
    if isinstance(heading_path, (list, tuple)):
        section = " > ".join(
            str(item).strip() for item in heading_path if str(item).strip()
        )
        if section:
            return section
    context = str(getattr(asset, "context_header", "") or "").strip()
    caption = str(getattr(asset, "caption", "") or "").strip()
    return context if context and context != caption else ""


def _authoritative_extent(assets: list[Any], *, v2_enabled: bool = False) -> BBox | None:
    candidates: list[BBox] = []
    for asset in assets:
        metadata = getattr(asset, "metadata", None) or {}
        if metadata.get("page_extent_source") != "authoritative":
            continue
        extent = _bbox(metadata.get("page_extent"))
        if extent is not None:
            candidates.append(extent)
    if not candidates:
        return None
    first = candidates[0]
    if any(candidate != first for candidate in candidates[1:]):
        return None
    # v2: detect discordant authorative extents (e.g. image-native coords
    # masquerading as page extent)
    if v2_enabled and (first[0] < 0 or first[1] < 0):
        logger.warning(
            "Authoritative extent has negative origin — discarding as discordant: %s",
            first,
        )
        return None
    return first


def _anomalous_extent(
    boxes: list[BBox],
    *,
    max_ratio: float = _ANOMALOUS_EXTENT_RATIO,
) -> BBox | None:
    """Return ``None`` when the extent spanned by *boxes* appears anomalous.

    A derived extent whose width or height exceeds *max_ratio* × the
    median box dimension typically means image-native bboxes have
    polluted a page-extent calculation.  Callers should fall back to a
    text-only extent.
    """
    if len(boxes) < 3:
        return None
    widths = [box[2] - box[0] for box in boxes if box[2] > box[0]]
    heights = [box[3] - box[1] for box in boxes if box[3] > box[1]]
    if not widths or not heights:
        return None
    med_w = _median(widths)
    med_h = _median(heights)
    if med_w <= 0 or med_h <= 0:
        return None
    extent = _derived_extent(boxes)
    if extent is None:
        return None
    extent_w = extent[2] - extent[0]
    extent_h = extent[3] - extent[1]
    if extent_w > max_ratio * med_w or extent_h > max_ratio * med_h:
        return extent
    return None


def _derived_extent(
    boxes: list[BBox],
    *,
    text_boxes: list[BBox] | None = None,
    v2_enabled: bool = False,
) -> BBox | None:
    if not boxes:
        return None
    extent = (
        min(0.0, min(box[0] for box in boxes)),
        min(0.0, min(box[1] for box in boxes)),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
    # v2: detect anomalous extent caused by image-native bboxes polluting
    # the page-extent computation — fall back to text-only extent.
    if v2_enabled and text_boxes:
        anomalous = _anomalous_extent(boxes)
        if anomalous is not None:
            text_extent = (
                min(0.0, min(box[0] for box in text_boxes)),
                min(0.0, min(box[1] for box in text_boxes)),
                max(box[2] for box in text_boxes),
                max(box[3] for box in text_boxes),
            )
            logger.info(
                "Anomalous derived extent %s (median w=%.0f h=%.0f) — "
                "falling back to text-only extent %s",
                extent,
                _median([box[2] - box[0] for box in boxes if box[2] > box[0]]),
                _median([box[3] - box[1] for box in boxes if box[3] > box[1]]),
                text_extent,
            )
            return text_extent
    return extent


def normalize_layout_bbox(
    box: BBox | None,
    extent: BBox | None,
    *,
    validate: bool = True,
) -> BBox | None:
    if box is None or extent is None:
        return None
    width = extent[2] - extent[0]
    height = extent[3] - extent[1]
    if width <= 0.0 or height <= 0.0:
        return None
    normalized = (
        (box[0] - extent[0]) / width,
        (box[1] - extent[1]) / height,
        (box[2] - extent[0]) / width,
        (box[3] - extent[1]) / height,
    )
    if validate:
        if not all(
            -_NORMALIZE_EPS <= value <= 1.0 + _NORMALIZE_EPS
            for value in normalized
        ):
            logger.warning(
                "normalize_layout_bbox produced out-of-range result — "
                "raw=%s extent=%s normalized=%s",
                box,
                extent,
                normalized,
            )
            return None
    return normalized


@dataclass(frozen=True)
class PageLayoutNode:
    node_id: str
    node_kind: str
    page_no: int
    source_order: int
    bbox: BBox | None
    normalized_bbox: BBox | None
    section_path: str = ""
    semantic_role: str = ""
    source_block_id: str = ""
    text: str = ""
    asset_id: str = ""
    exclude_from_asset_group: bool = False
    coordinate_context: CoordinateContext = field(default_factory=CoordinateContext)


@dataclass
class PageLayoutPage:
    page_no: int
    extent: BBox | None
    extent_source: str
    nodes: list[PageLayoutNode] = field(default_factory=list)

    @property
    def asset_nodes(self) -> list[PageLayoutNode]:
        return [node for node in self.nodes if node.node_kind == "asset"]

    @property
    def text_nodes(self) -> list[PageLayoutNode]:
        return [node for node in self.nodes if node.node_kind == "text"]


@dataclass
class PageLayoutGraph:
    pages: dict[int, PageLayoutPage] = field(default_factory=dict)
    coordinate_space: str = "mineru_page"


def build_page_layout_graph(
    *,
    assets: dict[str, Any],
    provenance_nodes: list[Any],
    v2_enabled: bool = False,
) -> PageLayoutGraph:
    page_numbers = {
        int(page_no)
        for page_no in [
            *(getattr(asset, "page_no", None) for asset in assets.values()),
            *(getattr(node, "page_no", None) for node in provenance_nodes),
        ]
        if page_no is not None
    }
    graph = PageLayoutGraph()
    for page_no in sorted(page_numbers):
        page_assets = [
            asset
            for asset in assets.values()
            if getattr(asset, "page_no", None) == page_no
        ]
        page_sources = [
            node
            for node in provenance_nodes
            if getattr(node, "page_no", None) == page_no
        ]
        raw_boxes = [
            box
            for box in [
                *(_bbox(getattr(asset, "bbox", None)) for asset in page_assets),
                *(_bbox(getattr(node, "bbox", None)) for node in page_sources),
            ]
            if box is not None
        ]
        # v2: keep text-only boxes for anomalous-extent fallback
        text_boxes = [
            box
            for box in [
                *(_bbox(getattr(node, "bbox", None)) for node in page_sources
                   if getattr(node, "node_type", "") != "image"),
            ]
            if box is not None
        ]
        extent = _authoritative_extent(page_assets, v2_enabled=v2_enabled)
        extent_source = "authoritative" if extent is not None else "derived"
        if extent is None:
            extent = _derived_extent(
                raw_boxes,
                text_boxes=text_boxes or None,
                v2_enabled=v2_enabled,
            )
        if extent is None:
            extent_source = "missing"
        # v2: build coordinate context shared by all nodes on this page
        node_ctx = CoordinateContext(
            space=CoordinateSpace.MINERU_PAGE,
            extent=extent,
        )
        page = PageLayoutPage(
            page_no=page_no, extent=extent, extent_source=extent_source
        )
        for asset in sorted(
            page_assets,
            key=lambda item: (
                getattr(item, "source_block_order", 0),
                str(item.asset_id),
            ),
        ):
            raw_bbox = _bbox(getattr(asset, "bbox", None))
            asset_metadata = getattr(asset, "metadata", None) or {}
            page.nodes.append(
                PageLayoutNode(
                    node_id=str(getattr(asset, "source_node_id", "") or asset.asset_id),
                    node_kind="asset",
                    page_no=page_no,
                    source_order=int(getattr(asset, "source_block_order", 0) or 0),
                    bbox=raw_bbox,
                    normalized_bbox=normalize_layout_bbox(raw_bbox, extent),
                    section_path=_asset_section(asset),
                    semantic_role=str(
                        asset_metadata.get("visual_role")
                        or asset_metadata.get("semantic_role")
                        or "figure"
                    ),
                    source_block_id=str(
                        asset_metadata.get("source_block_id") or ""
                    ),
                    text=str(getattr(asset, "caption", "") or ""),
                    asset_id=str(asset.asset_id),
                    exclude_from_asset_group=bool(
                        asset_metadata.get("exclude_from_asset_group")
                    ),
                    coordinate_context=node_ctx,
                )
            )
        for node in sorted(
            page_sources, key=lambda item: getattr(item, "source_order", 0)
        ):
            if getattr(node, "node_type", "") == "image":
                continue
            raw_bbox = _bbox(getattr(node, "bbox", None))
            page.nodes.append(
                PageLayoutNode(
                    node_id=str(getattr(node, "node_id", "") or ""),
                    node_kind="text",
                    page_no=page_no,
                    source_order=int(getattr(node, "source_order", 0) or 0),
                    bbox=raw_bbox,
                    normalized_bbox=normalize_layout_bbox(raw_bbox, extent),
                    section_path=str(getattr(node, "section_path", "") or ""),
                    semantic_role=str(getattr(node, "semantic_role", "") or ""),
                    source_block_id=str(
                        getattr(node, "source_block_id", "") or ""
                    ),
                    text=str(getattr(node, "canonical_text", "") or ""),
                    coordinate_context=node_ctx,
                )
            )
        page.nodes.sort(
            key=lambda node: (node.source_order, node.node_kind, node.node_id)
        )
        graph.pages[page_no] = page
    return graph


__all__ = [
    "BBox",
    "PageLayoutGraph",
    "PageLayoutNode",
    "PageLayoutPage",
    "build_page_layout_graph",
    "normalize_layout_bbox",
]
