from __future__ import annotations

from .models import (
    CanonicalAssetGroup,
    CanonicalRelationEdge,
    stable_group_id,
    stable_relation_id,
)
from .page_layout_graph import BBox, PageLayoutGraph, PageLayoutNode


def _overlap_ratio(first: BBox, second: BBox, *, axis: str) -> float:
    if axis == "x":
        overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
        denominator = max(min(first[2] - first[0], second[2] - second[0]), 1e-9)
    else:
        overlap = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
        denominator = max(min(first[3] - first[1], second[3] - second[1]), 1e-9)
    return overlap / denominator


def _orientation(nodes: list[PageLayoutNode]) -> tuple[str, str]:
    boxes = [node.normalized_bbox for node in nodes]
    if any(box is None for box in boxes):
        return "candidate_group", "missing_member_geometry"
    concrete = [box for box in boxes if box is not None]
    horizontal = all(
        _overlap_ratio(first, second, axis="y") >= 0.5
        for index, first in enumerate(concrete)
        for second in concrete[index + 1 :]
    )
    vertical = all(
        _overlap_ratio(first, second, axis="x") >= 0.5
        for index, first in enumerate(concrete)
        for second in concrete[index + 1 :]
    )
    if horizontal and not vertical:
        return "horizontal_series", ""
    if vertical and not horizontal:
        return "vertical_series", ""
    if horizontal and vertical:
        return "candidate_group", "overlapping_member_geometry"
    return "multi_panel_candidate", "non_unique_layout_partition"


def resolve_asset_groups(
    layout_graph: PageLayoutGraph,
) -> list[CanonicalAssetGroup]:
    groups: list[CanonicalAssetGroup] = []
    for page in layout_graph.pages.values():
        nodes = [node for node in page.asset_nodes if not node.exclude_from_asset_group]
        if len(nodes) < 2:
            continue
        member_ids = tuple(node.asset_id for node in nodes)
        sections = {node.section_path for node in nodes if node.section_path}
        group_type, layout_reason = _orientation(nodes)
        reasons: list[str] = []
        status = "bound"
        method = "same_page_layout_with_shared_context"
        score = 0.9
        section_path = next(iter(sections)) if len(sections) == 1 else ""
        shared_context_nodes = [
            node
            for node in page.text_nodes
            if node.semantic_role == "shared_context"
            and (not section_path or node.section_path == section_path)
        ]
        if len(sections) > 1:
            status = "rejected"
            method = "section_boundary_guard"
            score = 0.0
            reasons.append("section_mismatch")
        elif any(not node.section_path for node in nodes):
            status = "ambiguous"
            method = "missing_section_evidence_guard"
            score = 0.25
            reasons.append("missing_section_evidence")
        elif any(node.bbox is None for node in nodes):
            status = "ambiguous"
            method = "missing_geometry_guard"
            score = 0.0
            reasons.append("missing_member_geometry")
        elif page.extent_source != "authoritative":
            status = "ambiguous"
            method = "non_authoritative_extent_guard"
            score = 0.25
            reasons.append(f"{page.extent_source}_page_extent")
        elif layout_reason:
            status = "ambiguous"
            method = "layout_partition_ambiguous"
            score = 0.4
            reasons.append(layout_reason)
        elif not shared_context_nodes:
            status = "ambiguous"
            method = "missing_group_level_evidence_guard"
            score = 0.4
            reasons.append("missing_group_level_evidence")
        evidence = {
            "page_no": page.page_no,
            "page_extent": list(page.extent) if page.extent else None,
            "page_extent_source": page.extent_source,
            "group_type": group_type,
            "shared_context_candidate_node_ids": [
                node.node_id for node in shared_context_nodes
            ],
            "member_assets": [
                {
                    "asset_id": node.asset_id,
                    "bbox": list(node.bbox) if node.bbox else None,
                    "normalized_bbox": (
                        list(node.normalized_bbox) if node.normalized_bbox else None
                    ),
                    "source_order": node.source_order,
                    "section_path": node.section_path,
                }
                for node in nodes
            ],
        }
        groups.append(
            CanonicalAssetGroup(
                group_id=stable_group_id(
                    group_type,
                    member_ids,
                    page_no=page.page_no,
                    section_path=section_path,
                ),
                group_type=group_type,
                member_asset_ids=member_ids,
                page_start=page.page_no,
                page_end=page.page_no,
                section_path=section_path,
                grouping_method=method,
                grouping_score=score,
                status=status,
                evidence=(evidence,),
                ambiguity_reasons=tuple(reasons),
            )
        )
    return groups


def build_member_edges(
    groups: list[CanonicalAssetGroup],
) -> list[CanonicalRelationEdge]:
    edges: list[CanonicalRelationEdge] = []
    for group in groups:
        evidence = {
            "group_id": group.group_id,
            "group_type": group.group_type,
            "page_extent_source": group.evidence[0].get("page_extent_source"),
            "group_status": group.status,
        }
        for asset_id in group.member_asset_ids:
            edges.append(
                CanonicalRelationEdge(
                    relation_id=stable_relation_id(
                        "member_of_group",
                        (),
                        (asset_id,),
                        (group.group_id,),
                        occurrence=evidence,
                    ),
                    relation_type="member_of_group",
                    target_asset_ids=(asset_id,),
                    target_group_ids=(group.group_id,),
                    status=group.status,
                    binding_method=group.grouping_method,
                    binding_score=group.grouping_score,
                    page_no=group.page_start,
                    section_path=group.section_path,
                    evidence=(evidence,),
                    ambiguity_reasons=group.ambiguity_reasons,
                    algorithm_version="asset-group-p3-v1",
                )
            )
    return edges


__all__ = ["build_member_edges", "resolve_asset_groups"]
