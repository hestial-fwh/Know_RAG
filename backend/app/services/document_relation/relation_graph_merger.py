from __future__ import annotations

from typing import Any, Iterable

from .models import (
    CanonicalAssetGroup,
    CanonicalRelationEdge,
    CanonicalRelationGraph,
    stable_relation_id,
)


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


def _nearest_text_provenance_nodes(
    asset: Any,
    provenance_nodes: list[Any],
    *,
    max_candidates: int = 3,
    max_source_order_distance: int = 10,
) -> tuple[str, ...]:
    """Find the nearest text provenance nodes on the same page as *asset*.

    When an image asset has a caption or footnote but no accepted spatial
    candidates, this fallback finds text nodes that are likely to be the
    semantic context for the caption, instead of self-referencing the
    image's own provenance node.

    Selection criteria (in priority order):
    1. Same page
    2. Non-image node type
    3. Has non-empty canonical_text
    4. Source-order proximity to the asset's source_block_order
    """
    asset_page = getattr(asset, "page_no", None)
    asset_order = getattr(asset, "source_block_order", 0) or 0

    text_nodes: list[tuple[int, Any]] = []
    for node in provenance_nodes:
        if getattr(node, "page_no", None) != asset_page:
            continue
        if getattr(node, "node_type", "") == "image":
            continue
        if not (getattr(node, "canonical_text", "") or "").strip():
            continue
        distance = abs((getattr(node, "source_order", 0) or 0) - asset_order)
        if distance <= max_source_order_distance:
            text_nodes.append((distance, node))

    if not text_nodes:
        return ()

    text_nodes.sort(key=lambda item: (item[0], item[1].source_order))
    return tuple(
        str(node.node_id)
        for _, node in text_nodes[:max_candidates]
        if getattr(node, "node_id", "")
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
    # ── Fallback: nearest text provenance nodes instead of self-referencing ──
    # Previously this fell back to ``asset.source_node_id`` — the image's own
    # provenance node.  That produced self-referencing edges that wrote
    # ``outgoing_asset_chunk_ids`` pointing to the image chunk itself.
    # Now it searches for the nearest text nodes on the same page, creating
    # genuine text ↔ image relations.
    #
    # IMPORTANT: when the fallback is used (no accepted spatial candidates),
    # we cannot be certain the nearest text node IS the caption/footnote.
    # It may be regular prose, a heading, or a description.  Downgrading
    # to ``relative_reference`` + ``ambiguous`` keeps the relation type
    # semantically honest and prevents weak edges from entering the strong
    # binding path (outgoing_asset_chunk_ids → relation_chunks →
    # binding_related_chunks → binding_aware).
    using_fallback = False
    if not source_node_ids:
        source_node_ids = _nearest_text_provenance_nodes(asset, provenance_nodes)
        using_fallback = bool(source_node_ids)
    if not source_node_ids:
        # No text nodes found on the same page — the caption has no
        # determinable text context.  Skip the edge entirely rather
        # than producing a self-referencing stub.
        return None
    ambiguous = bool(
        relation_meta.get("ambiguous")
        or relation_meta.get(f"{kind}_ambiguous")
    )
    # Fallback edges are always ambiguous — we cannot verify the nearest
    # text node genuinely contains the caption/footnote text.
    if using_fallback:
        ambiguous = True
    reasons = tuple(
        str(reason)
        for reason in relation_meta.get("ambiguity_reasons") or ()
        if kind in str(reason) or not str(reason).startswith("spatial_")
    )
    method = str(getattr(asset, "caption_method", "") or "parser_caption")
    if kind == "footnote":
        method = "spatial_footnote" if accepted_orders else "parser_footnote"
    # Distinguish the fallback from explicit spatial-candidate matches.
    if using_fallback:
        method = f"nearest_text_fallback_{kind}"
    # Fallback edges use ``relative_reference`` instead of
    # ``caption_of`` / ``footnote_of`` because the nearest text
    # node is not a confirmed caption/footnote block — it is a
    # proximity-based association.
    effective_relation_type = "relative_reference" if using_fallback else relation_type
    occurrence = {
        "kind": kind,
        "text": text_value,
        "source_order": getattr(asset, "source_block_order", None),
    }
    return CanonicalRelationEdge(
        relation_id=stable_relation_id(
            effective_relation_type,
            source_node_ids,
            (str(asset.asset_id),),
            occurrence=occurrence,
        ),
        relation_type=effective_relation_type,
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


def _same_section_proximity_edges(
    assets: dict[str, Any],
    provenance_nodes: list[Any],
    *,
    max_source_order_distance: int = 8,
    skip_asset_ids: set[str] | None = None,
) -> list[CanonicalRelationEdge]:
    """Generate ``relative_reference`` edges for images without figure IDs.

    When an image has no explicit figure reference ("图X") but shares a page
    and section with nearby text nodes, this creates a moderate-confidence
    proximity-based relation.  This fills the gap for technical illustrations,
    architecture diagrams, and other unnumbered figures.

    Guards:
    - Image must have NO ``figure_ids`` (explicit references are covered by
      FigureLinker).
    - Image must not already have a caption_of / footnote_of edge (passed via
      *skip_asset_ids*).
    - Text node must be on the same page and share at least a page-level
      section heading.
    - Source-order distance must be within *max_source_order_distance*.
    - Pure-visual pages (no text nodes at all on the page) are skipped.
    """
    skip_asset_ids = skip_asset_ids or set()
    edges: list[CanonicalRelationEdge] = []

    # Index text provenance nodes by page and section
    text_by_page: dict[int, list[Any]] = {}
    for node in provenance_nodes:
        if getattr(node, "node_type", "") == "image":
            continue
        if not (getattr(node, "canonical_text", "") or "").strip():
            continue
        page = getattr(node, "page_no", None)
        if page is None:
            continue
        text_by_page.setdefault(int(page), []).append(node)

    for asset in assets.values():
        # Skip images that already have explicit figure references
        figure_ids = getattr(asset, "figure_ids", None) or []
        if figure_ids:
            continue

        # Skip images already covered by caption_of / footnote_of edges
        asset_id = str(getattr(asset, "asset_id", ""))
        if asset_id in skip_asset_ids:
            continue

        asset_page = getattr(asset, "page_no", None)
        if asset_page is None:
            continue
        asset_page = int(asset_page)
        asset_order = getattr(asset, "source_block_order", 0) or 0
        asset_section = str(getattr(asset, "context_header", "") or "").strip()

        page_texts = text_by_page.get(asset_page, [])
        if not page_texts:
            continue

        # Find nearby text nodes with matching section
        nearby: list[tuple[int, Any]] = []
        for node in page_texts:
            distance = abs((getattr(node, "source_order", 0) or 0) - asset_order)
            if distance > max_source_order_distance:
                continue
            node_section = str(getattr(node, "section_path", "") or "").strip()
            # Section match: either empty (page-level text) or shares a prefix
            if asset_section and node_section:
                if not (
                    asset_section.startswith(node_section)
                    or node_section.startswith(asset_section)
                ):
                    continue
            nearby.append((distance, node))

        if not nearby:
            continue

        nearby.sort(key=lambda item: (item[0], item[1].source_order))
        # Take up to 2 nearest text nodes
        selected = nearby[:2]
        occurrence = {
            "kind": "same_section_proximity",
            "source_order": asset_order,
            "page_no": asset_page,
            "section_path": asset_section,
        }
        source_node_ids = tuple(
            str(node.node_id)
            for _, node in selected
            if getattr(node, "node_id", "")
        )
        if not source_node_ids:
            continue

        min_distance = selected[0][0] if selected else 0
        # Confidence decreases with distance
        proximity_score = max(0.3, 0.85 - min_distance * 0.06)

        edges.append(
            CanonicalRelationEdge(
                relation_id=stable_relation_id(
                    "relative_reference",
                    source_node_ids,
                    (asset_id,),
                    occurrence=occurrence,
                ),
                relation_type="relative_reference",
                source_node_ids=source_node_ids,
                target_asset_ids=(asset_id,),
                status="ambiguous",
                binding_method="same_section_proximity",
                binding_score=round(proximity_score, 4),
                score_kind="heuristic_rule_score",
                page_no=asset_page,
                section_path=asset_section,
                evidence=(
                    {
                        **occurrence,
                        "source_node_ids": list(source_node_ids),
                        "distances": [d for d, _ in selected],
                        "proximity_score": round(proximity_score, 4),
                    },
                ),
                candidate_targets=(),
                ambiguity_reasons=("proximity_based_no_explicit_reference",),
                algorithm_version="same-section-proximity-v1",
            )
        )

    return edges


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
    # Collect asset IDs already covered by caption/footnote edges so
    # proximity edges don't create redundant relations for the same assets.
    covered_asset_ids: set[str] = set()
    for edge in image_edges:
        covered_asset_ids.update(edge.target_asset_ids)
    explicit_edges = [
        _explicit_edge_from_metadata(link.to_metadata())
        for link in getattr(figure_links, "links", ()) or ()
    ]
    # Also skip assets that have explicit FigureLink bindings.
    for edge in explicit_edges:
        covered_asset_ids.update(edge.target_asset_ids)
    # Non-explicit proximity edges for images without figure IDs or
    # caption/footnote coverage.
    proximity_edges = _same_section_proximity_edges(
        assets, provenance_nodes, skip_asset_ids=covered_asset_ids,
    )
    return CanonicalRelationGraph(
        edges=merge_relation_edges(image_edges, explicit_edges, proximity_edges)
    )


def merge_group_relations(
    graph: CanonicalRelationGraph,
    *,
    groups: Iterable[CanonicalAssetGroup],
    edges: Iterable[CanonicalRelationEdge],
) -> CanonicalRelationGraph:
    groups_by_id = {group.group_id: group for group in (*graph.groups, *groups)}
    return CanonicalRelationGraph(
        edges=merge_relation_edges(graph.edges, edges),
        groups=sorted(
            groups_by_id.values(),
            key=lambda group: (
                group.page_start if group.page_start is not None else 10**9,
                group.group_id,
            ),
        ),
        schema_version=graph.schema_version,
        algorithm_version="document-relation-graph-p3-v1",
        coordinate_space=graph.coordinate_space,
    )


__all__ = [
    "build_relation_graph",
    "graph_from_legacy_figure_links",
    "merge_group_relations",
    "merge_relation_edges",
]
