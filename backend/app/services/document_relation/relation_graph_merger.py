from __future__ import annotations

from typing import Any, Iterable

from .models import CanonicalRelationEdge, CanonicalRelationGraph, stable_relation_id


_STATUS_PRIORITY = {
    "overridden": 5,
    "rejected": 4,
    "bound": 3,
    "ambiguous": 2,
    "unmatched": 1,
}
_TYPE_PRIORITY = {
    "caption_of": 1,
    "footnote_of": 2,
    "explicit_reference": 3,
    "shared_context_of": 4,
    "member_of_group": 5,
    "relative_reference": 6,
}


def _ordered_dicts(values: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(sorted(value.items()))
        if marker in seen:
            continue
        seen.add(marker)
        output.append(dict(value))
    return tuple(output)


def _edge_sort_key(edge: CanonicalRelationEdge) -> tuple[Any, ...]:
    occurrence = edge.evidence[0] if edge.evidence else {}
    return (
        edge.page_no if edge.page_no is not None else 10**9,
        occurrence.get("source_order") if occurrence.get("source_order") is not None else 10**9,
        occurrence.get("start") if occurrence.get("start") is not None else 10**9,
        _TYPE_PRIORITY.get(edge.relation_type, 99),
        edge.relation_id,
    )


def merge_relation_edges(
    *edge_collections: Iterable[CanonicalRelationEdge],
) -> list[CanonicalRelationEdge]:
    merged: dict[str, CanonicalRelationEdge] = {}
    for edge in (item for collection in edge_collections for item in collection):
        current = merged.get(edge.relation_id)
        if current is None:
            merged[edge.relation_id] = edge
            continue
        preferred = max((current, edge), key=lambda item: _STATUS_PRIORITY[item.status])
        merged[edge.relation_id] = CanonicalRelationEdge(
            relation_id=preferred.relation_id,
            relation_type=preferred.relation_type,
            source_node_ids=(*current.source_node_ids, *edge.source_node_ids),
            target_asset_ids=(*current.target_asset_ids, *edge.target_asset_ids),
            target_group_ids=(*current.target_group_ids, *edge.target_group_ids),
            status=preferred.status,
            binding_method=preferred.binding_method,
            binding_score=preferred.binding_score,
            score_kind=preferred.score_kind,
            page_no=preferred.page_no,
            section_path=preferred.section_path,
            evidence=_ordered_dicts((*current.evidence, *edge.evidence)),
            candidate_targets=_ordered_dicts(
                (*current.candidate_targets, *edge.candidate_targets)
            ),
            ambiguity_reasons=(*current.ambiguity_reasons, *edge.ambiguity_reasons),
            algorithm_version=preferred.algorithm_version,
        )
    return sorted(merged.values(), key=_edge_sort_key)


def _nodes_for_orders(provenance_nodes: list[Any], orders: Iterable[int]) -> tuple[str, ...]:
    wanted = set(orders)
    return tuple(
        str(node.node_id)
        for node in sorted(provenance_nodes, key=lambda item: item.source_order)
        if node.source_order in wanted and getattr(node, "node_id", "")
    )


def _asset_relation_edge(
    asset: Any,
    provenance_nodes: list[Any],
    relation_type: str,
) -> CanonicalRelationEdge | None:
    kind = "caption" if relation_type == "caption_of" else "footnote"
    text_value = str(getattr(asset, kind, "") or "").strip()
    if not text_value:
        return None
    relation_meta = (getattr(asset, "metadata", None) or {}).get("image_relation") or {}
    candidates = [
        dict(item)
        for item in relation_meta.get("spatial_candidates") or ()
        if item.get("kind") == kind
    ]
    accepted_orders = [
        int(item["source_order"])
        for item in candidates
        if item.get("accepted") and item.get("source_order") is not None
    ]
    source_node_ids = _nodes_for_orders(provenance_nodes, accepted_orders)
    if not source_node_ids and getattr(asset, "source_node_id", ""):
        source_node_ids = (str(asset.source_node_id),)
    ambiguous = bool(
        relation_meta.get("ambiguous")
        or relation_meta.get(f"{kind}_ambiguous")
    )
    reasons = tuple(
        str(reason)
        for reason in relation_meta.get("ambiguity_reasons") or ()
        if kind in str(reason) or not str(reason).startswith("spatial_")
    )
    method = str(getattr(asset, "caption_method", "") or "parser_caption")
    if kind == "footnote":
        method = "spatial_footnote" if accepted_orders else "parser_footnote"
    occurrence = {
        "kind": kind,
        "text": text_value,
        "source_order": getattr(asset, "source_block_order", None),
    }
    return CanonicalRelationEdge(
        relation_id=stable_relation_id(
            relation_type,
            source_node_ids,
            (str(asset.asset_id),),
            occurrence=occurrence,
        ),
        relation_type=relation_type,
        source_node_ids=source_node_ids,
        target_asset_ids=(str(asset.asset_id),),
        status="ambiguous" if ambiguous else "bound",
        binding_method=method,
        binding_score=float(getattr(asset, "binding_score", 0.0) or 0.0),
        score_kind="heuristic_rule_score",
        page_no=getattr(asset, "page_no", None),
        section_path=str(getattr(asset, "context_header", "") or ""),
        evidence=(
            {
                **occurrence,
                "bbox": list(asset.bbox) if getattr(asset, "bbox", None) else None,
                "coordinate_space": str(getattr(asset, "bbox_coordinate_space", "") or ""),
                "spatial_status": str(getattr(asset, "spatial_status", "") or ""),
            },
        ),
        candidate_targets=tuple(candidates),
        ambiguity_reasons=reasons,
        algorithm_version="image-relation-v1",
    )


def _explicit_edge_from_metadata(link: dict[str, Any]) -> CanonicalRelationEdge:
    occurrence = {
        "figure_id": str(link.get("figure_id") or ""),
        "raw_text": str(link.get("raw_text") or ""),
        "start": link.get("start"),
        "end": link.get("end"),
        "source_order": link.get("source_order"),
        "bbox": link.get("bbox"),
        "coordinate_space": "normalized_canonical_markdown",
    }
    source_node_ids = tuple(
        value for value in (str(link.get("source_node_id") or ""),) if value
    )
    target_asset_ids = tuple(str(item) for item in link.get("asset_ids") or () if item)
    candidate_targets = tuple(
        dict(item) for item in link.get("evidence") or () if isinstance(item, dict)
    )
    if not candidate_targets:
        candidate_targets = tuple(
            {"asset_id": str(asset_id)}
            for asset_id in link.get("candidate_asset_ids") or ()
            if asset_id
        )
    ambiguity_reason = str(link.get("ambiguity_reason") or "")
    return CanonicalRelationEdge(
        relation_id=stable_relation_id(
            "explicit_reference",
            source_node_ids,
            target_asset_ids,
            occurrence=occurrence,
        ),
        relation_type="explicit_reference",
        source_node_ids=source_node_ids,
        target_asset_ids=target_asset_ids,
        status=str(link.get("status") or "unmatched"),
        binding_method=str(link.get("binding_method") or "explicit_reference_unmatched"),
        binding_score=float(link.get("binding_score") or 0.0),
        score_kind=str(link.get("score_kind") or "heuristic_rule_score"),
        page_no=link.get("page_no"),
        section_path=str(link.get("section") or ""),
        evidence=(occurrence,),
        candidate_targets=candidate_targets,
        ambiguity_reasons=(ambiguity_reason,) if ambiguity_reason else (),
        algorithm_version="explicit-reference-v1",
    )


def graph_from_legacy_figure_links(payload: dict[str, Any] | None) -> CanonicalRelationGraph:
    value = payload or {}
    edges = [
        _explicit_edge_from_metadata(item)
        for item in value.get("links") or ()
        if isinstance(item, dict)
    ]
    return CanonicalRelationGraph(
        edges=merge_relation_edges(edges),
        coordinate_space=str(
            value.get("coordinate_space") or "normalized_canonical_markdown"
        ),
    )


def build_relation_graph(
    *,
    assets: dict[str, Any],
    provenance_nodes: list[Any],
    figure_links: Any,
) -> CanonicalRelationGraph:
    image_edges: list[CanonicalRelationEdge] = []
    for asset in sorted(assets.values(), key=lambda item: (item.source_block_order, item.asset_id)):
        for relation_type in ("caption_of", "footnote_of"):
            edge = _asset_relation_edge(asset, provenance_nodes, relation_type)
            if edge is not None:
                image_edges.append(edge)
    explicit_edges = [
        _explicit_edge_from_metadata(link.to_metadata())
        for link in getattr(figure_links, "links", ()) or ()
    ]
    return CanonicalRelationGraph(edges=merge_relation_edges(image_edges, explicit_edges))


__all__ = [
    "build_relation_graph",
    "graph_from_legacy_figure_links",
    "merge_relation_edges",
]
