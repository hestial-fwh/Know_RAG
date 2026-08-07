"""Pure document-relation logic with no ORM dependency."""

from .asset_group_resolver import build_member_edges, resolve_asset_groups
from .materializer import materialize_figure_bindings, materialize_relation_summaries, populate_relation_chunks
from .models import CanonicalAssetGroup, CanonicalRelationEdge, CanonicalRelationGraph
from .page_layout_graph import build_page_layout_graph
from .relation_graph_merger import (
    build_relation_graph,
    graph_from_legacy_figure_links,
    merge_group_relations,
    merge_relation_edges,
)
from .shared_context_resolver import resolve_shared_context_edges

__all__ = [
    "CanonicalAssetGroup",
    "CanonicalRelationEdge",
    "CanonicalRelationGraph",
    "build_member_edges",
    "build_page_layout_graph",
    "build_relation_graph",
    "graph_from_legacy_figure_links",
    "materialize_figure_bindings",
    "materialize_relation_summaries",
    "populate_relation_chunks",
    "merge_group_relations",
    "merge_relation_edges",
    "resolve_asset_groups",
    "resolve_shared_context_edges",
]
