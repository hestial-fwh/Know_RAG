"""Deterministic page regions, visual items and conservative text relations."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any

from app.services.document_relation.page_layout_graph import (
    BBox,
    PageLayoutGraph,
    PageLayoutNode,
)


ALGORITHM_VERSION = "document-visual-items-dcp11-v1"


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _area(box: BBox | None) -> float:
    if box is None:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _center(box: BBox) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _axis_overlap(first: BBox, second: BBox, *, axis: str) -> float:
    if axis == "x":
        overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
        denominator = max(min(first[2] - first[0], second[2] - second[0]), 1e-9)
    else:
        overlap = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
        denominator = max(min(first[3] - first[1], second[3] - second[1]), 1e-9)
    return overlap / denominator


def _vertical_gap(first: BBox, second: BBox) -> float:
    if first[3] < second[1]:
        return second[1] - first[3]
    if second[3] < first[1]:
        return first[1] - second[3]
    return 0.0


def _horizontal_gap(first: BBox, second: BBox) -> float:
    if first[2] < second[0]:
        return second[0] - first[2]
    if second[2] < first[0]:
        return first[0] - second[2]
    return 0.0


def _contains_center(container: BBox, candidate: BBox) -> bool:
    x, y = _center(candidate)
    return container[0] <= x <= container[2] and container[1] <= y <= container[3]


def _intersection_over_union(first: BBox | None, second: BBox | None) -> float:
    if first is None or second is None:
        return 0.0
    intersection = (
        max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
        * max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    )
    union = _area(first) + _area(second) - intersection
    return intersection / union if union > 0 else 0.0


def _asset_signature(asset: Any) -> str:
    if asset is None:
        return ""
    metadata = getattr(asset, "metadata", None) or {}
    return str(
        metadata.get("sha256")
        or getattr(asset, "image_path", "")
        or getattr(asset, "image_url", "")
        or ""
    ).strip()


def _group_duplicate_local_nodes(
    nodes: list[PageLayoutNode],
    assets: dict[str, Any],
) -> list[list[PageLayoutNode]]:
    groups: list[list[PageLayoutNode]] = []
    for node in nodes:
        signature = _asset_signature(assets.get(node.asset_id))
        matched_group: list[PageLayoutNode] | None = None
        for group in groups:
            representative = group[0]
            overlap = _intersection_over_union(
                representative.normalized_bbox,
                node.normalized_bbox,
            )
            representative_signature = _asset_signature(
                assets.get(representative.asset_id)
            )
            if overlap >= 0.995 or (
                overlap >= 0.9
                and signature
                and signature == representative_signature
            ):
                matched_group = group
                break
        if matched_group is None:
            groups.append([node])
        else:
            matched_group.append(node)
    return groups


def _map_relative_box(container: BBox, relative: list[Any]) -> BBox | None:
    if len(relative) < 4:
        return None
    try:
        values = tuple(float(value) for value in relative[:4])
    except (TypeError, ValueError):
        return None
    if not (0 <= values[0] < values[2] <= 1 and 0 <= values[1] < values[3] <= 1):
        return None
    width = container[2] - container[0]
    height = container[3] - container[1]
    return (
        container[0] + values[0] * width,
        container[1] + values[1] * height,
        container[0] + values[2] * width,
        container[1] + values[3] * height,
    )


@dataclass(frozen=True)
class LayoutRegion:
    region_id: str
    page_no: int
    region_kind: str
    normalized_bbox: BBox | None
    source: str
    asset_ids: tuple[str, ...] = ()
    source_node_ids: tuple[str, ...] = ()
    source_block_ids: tuple[str, ...] = ()
    status: str = "candidate"
    evidence: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "page_no": self.page_no,
            "region_kind": self.region_kind,
            "normalized_bbox": (
                list(self.normalized_bbox) if self.normalized_bbox else None
            ),
            "source": self.source,
            "asset_ids": list(self.asset_ids),
            "source_node_ids": list(self.source_node_ids),
            "source_block_ids": list(self.source_block_ids),
            "status": self.status,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class VisualItem:
    visual_item_id: str
    page_no: int
    normalized_bbox: BBox | None
    asset_ids: tuple[str, ...]
    source_block_ids: tuple[str, ...]
    region_ids: tuple[str, ...]
    status: str
    evidence: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "visual_item_id": self.visual_item_id,
            "page_no": self.page_no,
            "normalized_bbox": (
                list(self.normalized_bbox) if self.normalized_bbox else None
            ),
            "asset_ids": list(self.asset_ids),
            "source_block_ids": list(self.source_block_ids),
            "region_ids": list(self.region_ids),
            "status": self.status,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class VisualTextRelation:
    relation_id: str
    page_no: int
    source_node_id: str
    source_block_id: str
    visual_item_ids: tuple[str, ...]
    relation_type: str
    status: str
    score: float
    evidence: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "page_no": self.page_no,
            "source_node_id": self.source_node_id,
            "source_block_id": self.source_block_id,
            "visual_item_ids": list(self.visual_item_ids),
            "relation_type": self.relation_type,
            "status": self.status,
            "score": self.score,
            "score_kind": "heuristic_rule_score",
            "evidence": list(self.evidence),
        }


@dataclass
class PageVisualLayout:
    page_no: int
    extent_source: str
    regions: list[LayoutRegion] = field(default_factory=list)
    visual_items: list[VisualItem] = field(default_factory=list)
    text_relations: list[VisualTextRelation] = field(default_factory=list)
    fallback_reason: str = ""
    suppressed_asset_ids: set[str] = field(default_factory=set)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "extent_source": self.extent_source,
            "regions": [region.to_metadata() for region in self.regions],
            "visual_items": [item.to_metadata() for item in self.visual_items],
            "text_relations": [
                relation.to_metadata() for relation in self.text_relations
            ],
            "fallback_reason": self.fallback_reason,
            "suppressed_asset_ids": sorted(self.suppressed_asset_ids),
            "summary": {
                "region_count": len(self.regions),
                "visual_item_count": len(self.visual_items),
                "ambiguous_text_count": sum(
                    relation.status == "ambiguous"
                    for relation in self.text_relations
                ),
                "unbound_text_count": sum(
                    relation.status == "unbound"
                    for relation in self.text_relations
                ),
                "duplicate_fallback_count": sum(
                    region.status == "duplicate_fallback"
                    for region in self.regions
                ),
                "suppressed_asset_count": len(self.suppressed_asset_ids),
            },
        }


@dataclass
class DocumentVisualLayout:
    pages: list[PageVisualLayout]
    shadow_only: bool = True
    algorithm_version: str = ALGORITHM_VERSION

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "algorithm_version": self.algorithm_version,
            "coordinate_space": "normalized_page",
            "shadow_only": self.shadow_only,
            "pages": [page.to_metadata() for page in self.pages],
            "summary": {
                "page_count": len(self.pages),
                "region_count": sum(len(page.regions) for page in self.pages),
                "visual_item_count": sum(
                    len(page.visual_items) for page in self.pages
                ),
                "ambiguous_text_count": sum(
                    relation.status == "ambiguous"
                    for page in self.pages
                    for relation in page.text_relations
                ),
                "unsegmented_page_count": sum(
                    item.status == "unsegmented_page_visual"
                    for page in self.pages
                    for item in page.visual_items
                ),
                "duplicate_fallback_count": sum(
                    region.status == "duplicate_fallback"
                    for page in self.pages
                    for region in page.regions
                ),
            },
        }


def _region_from_asset(
    page_no: int,
    node: PageLayoutNode,
    *,
    status: str = "candidate",
    related_nodes: list[PageLayoutNode] | None = None,
) -> LayoutRegion:
    grouped_nodes = related_nodes or [node]
    asset_ids = tuple(grouped_node.asset_id for grouped_node in grouped_nodes)
    source_node_ids = tuple(
        grouped_node.node_id for grouped_node in grouped_nodes
    )
    source_block_ids = tuple(
        grouped_node.source_block_id
        for grouped_node in grouped_nodes
        if grouped_node.source_block_id
    )
    evidence = ["parser_asset_bbox"]
    if len(grouped_nodes) > 1:
        evidence.append("overlapping_assets_collapsed")
    return LayoutRegion(
        region_id=_stable_id(
            "region",
            {
                "page_no": page_no,
                "asset_ids": asset_ids,
                "bbox": node.normalized_bbox,
            },
        ),
        page_no=page_no,
        region_kind="visual",
        normalized_bbox=node.normalized_bbox,
        source="parser_asset",
        asset_ids=asset_ids,
        source_node_ids=source_node_ids,
        source_block_ids=source_block_ids,
        status=status,
        evidence=tuple(evidence),
    )


def _candidate_regions_from_asset(
    page_no: int,
    node: PageLayoutNode,
    asset: Any,
    *,
    related_nodes: list[PageLayoutNode] | None = None,
) -> list[LayoutRegion]:
    box = node.normalized_bbox
    if box is None:
        return []
    metadata = getattr(asset, "metadata", None) or {}
    candidates = metadata.get("layout_region_candidates")
    if not isinstance(candidates, list):
        return []
    method = str(
        metadata.get("layout_candidate_method") or "upstream_layout_candidate"
    )
    regions: list[LayoutRegion] = []
    grouped_nodes = related_nodes or [node]
    asset_ids = tuple(grouped_node.asset_id for grouped_node in grouped_nodes)
    source_node_ids = tuple(
        grouped_node.node_id for grouped_node in grouped_nodes
    )
    source_block_ids = tuple(
        grouped_node.source_block_id
        for grouped_node in grouped_nodes
        if grouped_node.source_block_id
    )
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, (list, tuple)):
            continue
        mapped = _map_relative_box(box, list(candidate))
        if mapped is None:
            continue
        regions.append(
            LayoutRegion(
                region_id=_stable_id(
                    "region",
                    {
                        "page_no": page_no,
                        "asset_ids": asset_ids,
                        "candidate_index": index,
                        "bbox": mapped,
                    },
                ),
                page_no=page_no,
                region_kind="visual_candidate",
                normalized_bbox=mapped,
                source=method,
                asset_ids=asset_ids,
                source_node_ids=source_node_ids,
                source_block_ids=source_block_ids,
                status="candidate",
                evidence=("full_page_raster_candidate", method),
            )
        )
    return regions


def _item_from_region(region: LayoutRegion, *, status: str) -> VisualItem:
    return VisualItem(
        visual_item_id=_stable_id(
            "visual-item",
            {
                "page_no": region.page_no,
                "region_id": region.region_id,
                "asset_ids": region.asset_ids,
            },
        ),
        page_no=region.page_no,
        normalized_bbox=region.normalized_bbox,
        asset_ids=region.asset_ids,
        source_block_ids=region.source_block_ids,
        region_ids=(region.region_id,),
        status=status,
        evidence=region.evidence,
    )


def _score_text_visual_candidate_v2(
    node: PageLayoutNode,
    item: VisualItem,
    spatial_score: float,
    spatial_reason: str,
    *,
    semantic_role: str = "",
) -> tuple[float, str, list[str]]:
    """Score text-visual binding for non-caption text (art books etc.).

    Explicit bindable roles always score 1.0.  For ordinary descriptive
    text the score combines spatial proximity, text length, and layout
    characteristics.
    """
    text = (node.text or "").strip()
    text_len = len(text)

    # Explicit roles get maximum score — keep existing priority
    if semantic_role in {
        "caption",
        "figure_caption",
        "image_caption",
        "inscription",
        "visual_ocr_candidate",
    }:
        return 1.0, spatial_reason, [semantic_role]

    base = spatial_score * 0.65

    # Short descriptive text (10-200 chars) — typical art-book labels
    if 10 <= text_len <= 200:
        base += 0.12
    elif text_len < 10:
        base -= 0.10  # too short to be meaningful

    # Body-text paragraphs near images are not deliberate descriptions;
    # penalise them to avoid false-positive ambiguous bindings (DCP11 VI-09).
    if semantic_role in {"body_text", "paragraph", "text"}:
        base *= 0.75

    # Sparse dense layout suggests deliberate placement
    if node.normalized_bbox is not None and item.normalized_bbox is not None:
        text_area = (node.normalized_bbox[2] - node.normalized_bbox[0]) * (
            node.normalized_bbox[3] - node.normalized_bbox[1]
        )
        if text_area > 0 and text_len / max(text_area, 1e-9) > 500:
            base += 0.05

    base = min(0.94, max(0.0, base))
    evidence = [
        f"v2_text_bind_score:{base:.4f}",
        f"len:{text_len}",
        f"role:{semantic_role}",
        f"spatial:{spatial_score:.4f}",
    ]
    return base, spatial_reason, evidence


def _text_relation(
    page_no: int,
    node: PageLayoutNode,
    items: list[VisualItem],
    *,
    v2_enabled: bool = False,
) -> VisualTextRelation:
    base = {
        "page_no": page_no,
        "source_node_id": node.node_id,
        "source_block_id": node.source_block_id,
    }
    semantic_role = (node.semantic_role or "").strip().lower()
    explicit_caption = semantic_role in {
        "caption",
        "figure_caption",
        "image_caption",
    }
    explicit_inscription = semantic_role in {
        "inscription",
        "visual_ocr_candidate",
    }
    bindable_role = explicit_caption or explicit_inscription
    if node.normalized_bbox is None or not items:
        return VisualTextRelation(
            relation_id=_stable_id("visual-text", {**base, "type": "unbound"}),
            page_no=page_no,
            source_node_id=node.node_id,
            source_block_id=node.source_block_id,
            visual_item_ids=(),
            relation_type="unbound",
            status="unbound",
            score=0.0,
            evidence=("missing_geometry_or_visual_item",),
        )
    text = (node.text or "").strip()
    # v2: raise the long-body-text guard to 500 chars to allow
    # typical art-book descriptive labels (author, title, dimensions,
    # medium, etc.) which are typically 20-200 chars.
    _long_text_threshold = 500 if v2_enabled else 200
    if len(text) > _long_text_threshold and not bindable_role:
        return VisualTextRelation(
            relation_id=_stable_id("visual-text", {**base, "type": "unbound_long"}),
            page_no=page_no,
            source_node_id=node.node_id,
            source_block_id=node.source_block_id,
            visual_item_ids=(),
            relation_type="unbound",
            status="unbound",
            score=0.0,
            evidence=("long_body_text_guard",),
        )
    scored: list[tuple[float, VisualItem, str]] = []
    for item in items:
        box = item.normalized_bbox
        if box is None:
            continue
        if _contains_center(box, node.normalized_bbox):
            scored.append((1.0, item, "inside_visual_region"))
            continue
        overlap = _axis_overlap(box, node.normalized_bbox, axis="x")
        gap = _vertical_gap(box, node.normalized_bbox)
        score = 0.65 * overlap + max(0.0, 0.35 - gap * 3)
        if overlap >= 0.3 and gap <= 0.12:
            scored.append((round(score, 6), item, "near_aligned_text"))
            continue
        if len(items) >= 2:
            y_overlap = _axis_overlap(box, node.normalized_bbox, axis="y")
            side_gap = _horizontal_gap(box, node.normalized_bbox)
            if y_overlap >= 0.3 and side_gap <= 0.15:
                side_score = 0.55 * y_overlap + max(0.0, 0.25 - side_gap * 2)
                scored.append(
                    (round(side_score, 6), item, "between_visual_items")
                )
    scored.sort(key=lambda value: (-value[0], value[1].visual_item_id))
    if not scored:
        return VisualTextRelation(
            relation_id=_stable_id("visual-text", {**base, "type": "unbound"}),
            page_no=page_no,
            source_node_id=node.node_id,
            source_block_id=node.source_block_id,
            visual_item_ids=(),
            relation_type="unbound",
            status="unbound",
            score=0.0,
            evidence=("no_strong_spatial_candidate",),
        )
    top_score = scored[0][0]
    tied = [entry for entry in scored if top_score - entry[0] <= 0.08]
    if len(tied) > 1:
        target_ids = tuple(entry[1].visual_item_id for entry in tied)
        relation_type = (
            "shared_context"
            if node.normalized_bbox[0]
            <= min(
                item.normalized_bbox[0]
                for _, item, _ in tied
                if item.normalized_bbox
            )
            and node.normalized_bbox[2]
            >= max(
                item.normalized_bbox[2]
                for _, item, _ in tied
                if item.normalized_bbox
            )
            else "ambiguous"
        )
        return VisualTextRelation(
            relation_id=_stable_id(
                "visual-text",
                {**base, "type": relation_type, "targets": target_ids},
            ),
            page_no=page_no,
            source_node_id=node.node_id,
            source_block_id=node.source_block_id,
            visual_item_ids=target_ids,
            relation_type=relation_type,
            status="ambiguous",
            score=top_score,
            evidence=("multiple_equal_spatial_candidates",),
        )
    if not bindable_role:
        # v2: scoring-based binding for non-caption descriptive text
        # (author, title, dimensions, medium — common in art books)
        if v2_enabled:
            best_score, best_reason, v2_evidence = 0.0, "", []
            best_item: VisualItem | None = None
            for _score, candidate_item, reason in scored:
                v2_score, _reason, evidence = _score_text_visual_candidate_v2(
                    node,
                    candidate_item,
                    spatial_score=_score,
                    spatial_reason=reason,
                    semantic_role=semantic_role,
                )
                if v2_score > best_score:
                    best_score, best_reason, v2_evidence = v2_score, reason, evidence
                    best_item = candidate_item
            if best_item is not None:
                if best_score >= 0.70:
                    return VisualTextRelation(
                        relation_id=_stable_id(
                            "visual-text",
                            {
                                **base,
                                "type": "v2_bound",
                                "target": best_item.visual_item_id,
                            },
                        ),
                        page_no=page_no,
                        source_node_id=node.node_id,
                        source_block_id=node.source_block_id,
                        visual_item_ids=(best_item.visual_item_id,),
                        relation_type="description",
                        status="bound",
                        score=best_score,
                        evidence=tuple(v2_evidence),
                    )
                if best_score >= 0.60:
                    return VisualTextRelation(
                        relation_id=_stable_id(
                            "visual-text",
                            {
                                **base,
                                "type": "v2_ambiguous",
                                "target": best_item.visual_item_id,
                            },
                        ),
                        page_no=page_no,
                        source_node_id=node.node_id,
                        source_block_id=node.source_block_id,
                        visual_item_ids=(best_item.visual_item_id,),
                        relation_type="description",
                        status="ambiguous",
                        score=best_score,
                        evidence=tuple(v2_evidence),
                    )
        # v1 / fallback: non-bindable role → unbound
        return VisualTextRelation(
            relation_id=_stable_id(
                "visual-text",
                {**base, "type": "unbound_semantic_role"},
            ),
            page_no=page_no,
            source_node_id=node.node_id,
            source_block_id=node.source_block_id,
            visual_item_ids=(),
            relation_type="unbound",
            status="unbound",
            score=top_score,
            evidence=("semantic_role_not_bindable", semantic_role or "unknown"),
        )
    _, item, reason = scored[0]
    relation_type = "inscription" if explicit_inscription else "caption"
    return VisualTextRelation(
        relation_id=_stable_id(
            "visual-text",
            {**base, "type": relation_type, "target": item.visual_item_id},
        ),
        page_no=page_no,
        source_node_id=node.node_id,
        source_block_id=node.source_block_id,
        visual_item_ids=(item.visual_item_id,),
        relation_type=relation_type,
        status="bound",
        score=top_score,
        evidence=(reason,),
    )


def resolve_document_visual_layout(
    layout_graph: PageLayoutGraph,
    *,
    assets: dict[str, Any],
    shadow_only: bool = True,
    v2_enabled: bool = False,
) -> DocumentVisualLayout:
    pages: list[PageVisualLayout] = []
    for page_no in sorted(layout_graph.pages):
        source_page = layout_graph.pages[page_no]
        page = PageVisualLayout(
            page_no=page_no,
            extent_source=source_page.extent_source,
        )
        # Relation-graph grouping may exclude page-primary fallback assets, but
        # visual layout still needs them for deduplication and conservative
        # unsegmented-page handling.
        asset_nodes = list(source_page.asset_nodes)
        full_page_nodes = [
            node
            for node in asset_nodes
            if (
                source_page.extent_source == "authoritative"
                and _area(node.normalized_bbox) >= 0.8
            )
            or node.semantic_role == "page_primary_visual"
        ]
        local_nodes = [node for node in asset_nodes if node not in full_page_nodes]
        if local_nodes:
            for node_group in _group_duplicate_local_nodes(local_nodes, assets):
                region = _region_from_asset(
                    page_no,
                    node_group[0],
                    related_nodes=node_group,
                )
                page.regions.append(region)
                page.visual_items.append(_item_from_region(region, status="resolved"))
            for node in full_page_nodes:
                page.regions.append(
                    _region_from_asset(page_no, node, status="duplicate_fallback")
                )
                # v2: track suppressed assets so chunk creation can filter them
                page.suppressed_asset_ids.add(node.asset_id)
        elif full_page_nodes:
            node = next(
                (
                    candidate
                    for candidate in full_page_nodes
                    if _candidate_regions_from_asset(
                        page_no,
                        candidate,
                        assets.get(candidate.asset_id),
                    )
                ),
                full_page_nodes[0],
            )
            candidate_regions = _candidate_regions_from_asset(
                page_no,
                node,
                assets.get(node.asset_id),
                related_nodes=full_page_nodes,
            )
            if candidate_regions:
                page.regions.extend(candidate_regions)
                page.visual_items.extend(
                    _item_from_region(region, status="candidate")
                    for region in candidate_regions
                )
                page.fallback_reason = "full_page_raster_split_candidates"
            else:
                region = _region_from_asset(
                    page_no,
                    node,
                    related_nodes=full_page_nodes,
                )
                page.regions.append(region)
                page.visual_items.append(
                    _item_from_region(
                        region,
                        status="unsegmented_page_visual",
                    )
                )
                page.fallback_reason = "no_reliable_internal_partition"
        else:
            for node in asset_nodes:
                region = _region_from_asset(page_no, node)
                page.regions.append(region)
                page.visual_items.append(_item_from_region(region, status="resolved"))
        for node in source_page.text_nodes:
            page.regions.append(
                LayoutRegion(
                    region_id=_stable_id(
                        "region",
                        {
                            "page_no": page_no,
                            "source_node_id": node.node_id,
                            "bbox": node.normalized_bbox,
                        },
                    ),
                    page_no=page_no,
                    region_kind="text",
                    normalized_bbox=node.normalized_bbox,
                    source="parser_text",
                    source_node_ids=(node.node_id,),
                    source_block_ids=(
                        (node.source_block_id,) if node.source_block_id else ()
                    ),
                    status="observed",
                    evidence=(
                        node.semantic_role or "parser_text",
                    ),
                )
            )
        page.text_relations.extend(
            _text_relation(page_no, node, page.visual_items, v2_enabled=v2_enabled)
            for node in source_page.text_nodes
        )
        pages.append(page)
    return DocumentVisualLayout(pages=pages, shadow_only=shadow_only)


__all__ = [
    "ALGORITHM_VERSION",
    "DocumentVisualLayout",
    "LayoutRegion",
    "PageVisualLayout",
    "VisualItem",
    "VisualTextRelation",
    "resolve_document_visual_layout",
]
