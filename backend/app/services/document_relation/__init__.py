"""Pure document-relation logic with no ORM dependency."""

from .materializer import materialize_figure_bindings

__all__ = ["materialize_figure_bindings"]
from .models import CanonicalAssetGroup, CanonicalRelationEdge, CanonicalRelationGraph
from .relation_graph_merger import build_relation_graph, graph_from_legacy_figure_links


__all__ = [
    "CanonicalAssetGroup",
    "CanonicalRelationEdge",
    "CanonicalRelationGraph",
    "build_relation_graph",
    "graph_from_legacy_figure_links",
]
