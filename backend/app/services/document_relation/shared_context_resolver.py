from __future__ import annotations

from typing import Any

from .models import CanonicalAssetGroup, CanonicalRelationEdge, stable_relation_id
from .page_layout_graph import BBox, PageLayoutGraph, PageLayoutNode


_EXCLUDED_ROLES = {
    "caption",
    "figure_caption",
    "image_caption",
    "footnote",
    "heading",
    "title",
    "code",
    "code_block",
}


def _horizontal_coverage(source: BBox, targets: list[BBox]) -> float:
    left = min(box[0] for box in targets)
    right = max(box[2] for box in targets)
    overlap = max(0.0, min(source[2], right) - max(source[0], left))
    return overlap / max(right - left, 1e-9)


def _candidate_score(node: PageLayoutNode, target_boxes: list[BBox]) -> tuple[float, dict[str, Any]]:
    source = node.normalized_bbox
    if source is None:
        return 0.0, {"node_id": node.node_id, "reason": "missing_source_geometry"}
    target_top = min(box[1] for box in target_boxes)
    gap = target_top - source[3]
    coverage = _horizontal_coverage(source, target_boxes)
    above = gap >= -0.02
    score = max(0.0, min(0.99, 0.65 * coverage + 0.35 * max(0.0, 1.0 - gap)))
    return score, {
        "node_id": node.node_id,
        "bbox": list(node.bbox) if node.bbox else None,
        "normalized_bbox": list(source),
        "horizontal_coverage": round(coverage, 4),
        "vertical_gap": round(gap, 4),
        "above_group": above,
        "source_order": node.source_order,
        "semantic_role": node.semantic_role,
    }


def resolve_shared_context_edges(
    layout_graph: PageLayoutGraph,
    groups: list[CanonicalAssetGroup],
    *,
    assets: dict[str, Any],
) -> list[CanonicalRelationEdge]:
    edges: list[CanonicalRelationEdge] = []
    for group in groups:
        page = layout_graph.pages.get(int(group.page_start or 0))
        if page is None:
            continue
        captions = [
            str(getattr(assets.get(asset_id), "caption", "") or "").strip()
            for asset_id in group.member_asset_ids
        ]
        independent_captions = bool(captions) and all(captions) and len(set(captions)) == len(
            captions
        )
        if independent_captions:
            evidence = {
                "group_id": group.group_id,
                "captions": captions,
                "page_extent_source": page.extent_source,
                "reason": "independent_captions_present",
            }
            edges.append(
                CanonicalRelationEdge(
                    relation_id=stable_relation_id(
                        "shared_context_of",
                        (),
                        (),
                        (group.group_id,),
                        occurrence=evidence,
                    ),
                    relation_type="shared_context_of",
                    target_group_ids=(group.group_id,),
                    status="rejected",
                    binding_method="independent_caption_guard",
                    binding_score=0.0,
                    page_no=page.page_no,
                    section_path=group.section_path,
                    evidence=(evidence,),
                    ambiguity_reasons=("independent_captions_present",),
                    algorithm_version="shared-context-p3-v1",
                )
            )
            continue
        target_nodes = [
            node for node in page.asset_nodes if node.asset_id in group.member_asset_ids
        ]
        target_boxes = [
            node.normalized_bbox for node in target_nodes if node.normalized_bbox is not None
        ]
        if len(target_boxes) != len(target_nodes):
            candidates = [
                node
                for node in page.text_nodes
                if node.semantic_role == "shared_context"
                and (not group.section_path or node.section_path == group.section_path)
            ]
            if not candidates:
                continue
            selected = candidates[0]
            evidence = {
                "group_id": group.group_id,
                "source_node_id": selected.node_id,
                "page_extent_source": page.extent_source,
                "reason": "missing_target_geometry",
            }
            group.shared_context_node_ids = (selected.node_id,)
            edges.append(
                CanonicalRelationEdge(
                    relation_id=stable_relation_id(
                        "shared_context_of",
                        (selected.node_id,),
                        (),
                        (group.group_id,),
                        occurrence=evidence,
                    ),
                    relation_type="shared_context_of",
                    source_node_ids=(selected.node_id,),
                    target_group_ids=(group.group_id,),
                    status="ambiguous",
                    binding_method="missing_geometry_guard",
                    binding_score=0.0,
                    page_no=page.page_no,
                    section_path=group.section_path,
                    evidence=(evidence,),
                    ambiguity_reasons=("missing_target_geometry",),
                    algorithm_version="shared-context-p3-v1",
                )
            )
            continue
        scored: list[tuple[float, PageLayoutNode, dict[str, Any]]] = []
        for node in page.text_nodes:
            if node.semantic_role in _EXCLUDED_ROLES:
                continue
            if group.section_path and node.section_path != group.section_path:
                continue
            score, audit = _candidate_score(node, target_boxes)
            if (
                audit.get("above_group")
                and audit.get("horizontal_coverage", 0.0) >= 0.7
                and audit.get("vertical_gap", 1.0) <= 0.25
            ):
                scored.append((score, node, audit))
        if not scored:
            continue
        scored.sort(key=lambda item: (-item[0], item[1].source_order, item[1].node_id))
        best_score, selected, selected_audit = scored[0]
        tied = len(scored) > 1 and abs(scored[1][0] - best_score) < 0.05
        reasons: list[str] = []
        status = "bound"
        method = "top_text_horizontal_coverage"
        if group.status == "rejected":
            status = "rejected"
            method = "group_rejected_guard"
            reasons.extend(group.ambiguity_reasons)
        elif page.extent_source != "authoritative" or group.status != "bound":
            status = "ambiguous"
            method = "non_authoritative_extent_guard"
            reasons.append(f"{page.extent_source}_page_extent")
        elif tied:
            status = "ambiguous"
            method = "shared_context_candidate_tie"
            reasons.append("multiple_shared_context_candidates")
        evidence = {
            "group_id": group.group_id,
            "page_extent": list(page.extent) if page.extent else None,
            "page_extent_source": page.extent_source,
            "selected": selected_audit,
            "candidates": [audit for _, _, audit in scored],
        }
        group.shared_context_node_ids = (selected.node_id,)
        edges.append(
            CanonicalRelationEdge(
                relation_id=stable_relation_id(
                    "shared_context_of",
                    (selected.node_id,),
                    (),
                    (group.group_id,),
                    occurrence=evidence,
                ),
                relation_type="shared_context_of",
                source_node_ids=(selected.node_id,),
                target_group_ids=(group.group_id,),
                status=status,
                binding_method=method,
                binding_score=round(best_score, 4),
                page_no=page.page_no,
                section_path=group.section_path,
                evidence=(evidence,),
                candidate_targets=tuple(
                    {
                        "node_id": node.node_id,
                        "rule_score": round(score, 4),
                    }
                    for score, node, _ in scored
                ),
                ambiguity_reasons=tuple(reasons),
                algorithm_version="shared-context-p3-v1",
            )
        )
    return edges


__all__ = ["resolve_shared_context_edges"]
