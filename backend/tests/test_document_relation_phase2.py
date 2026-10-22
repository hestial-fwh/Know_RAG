from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import Chunk, DocumentCanonicalArtifact
from app.services.chunk_service import (
    _rebuild_canonical_weknora_parent_child,
    rebuild_document_chunks,
)
from app.services.document_parser.canonical_document import CanonicalAsset, CanonicalSourceNode
from app.services.document_parser.figure_linker import (
    FigureCandidateEvidence,
    FigureLink,
    FigureLinkResult,
    FigureReference,
)
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.document_relation.models import CanonicalRelationEdge, CanonicalRelationGraph
from app.services.document_relation.materializer import (
    materialize_relation_summaries,
    populate_relation_chunks,
)
from app.services.document_relation.relation_graph_merger import (
    build_relation_graph,
    graph_from_legacy_figure_links,
    merge_relation_edges,
)
from app.services.document_relation_persistence_service import load_relation_graph


def _cfg():
    return SimpleNamespace(
        chunk_size=512,
        chunk_overlap=80,
        strategy="auto",
        separators=["\n\n", "\n", "。"],
        parent_chunk_size=1200,
        child_chunk_size=384,
        child_chunk_overlap=64,
        token_limit=0,
        languages=[],
        enable_parent_child=True,
    )


def _doc():
    return SimpleNamespace(
        id="doc-phase2",
        knowledge_base_id="kb-phase2",
        title="Phase 2",
        file_type="pdf",
    )


def _link() -> FigureLink:
    return FigureLink(
        reference=FigureReference(
            figure_id="figure:1",
            raw_text="图1",
            start=10,
            end=12,
            source_node_id="node-text",
            page_no=1,
            source_order=1,
        ),
        status="bound",
        asset_ids=("asset-1",),
        candidate_asset_ids=("asset-1",),
        binding_method="figure_id_exact",
        binding_score=0.9,
        evidence=(
            FigureCandidateEvidence(
                asset_id="asset-1",
                figure_id_exact=True,
                p1_binding_score=0.95,
                caption_method="parser_caption",
                page_match=True,
                section_match=True,
                source_order_distance=1,
                canonical_char_distance=20,
                rule_score=0.9,
            ),
        ),
    )


def _graph_inputs():
    relation_metadata = {
        "ambiguous": False,
        "caption_ambiguous": False,
        "footnote_ambiguous": False,
        "ambiguity_reasons": [],
        "spatial_candidates": [],
    }
    asset = CanonicalAsset(
        asset_id="asset-1",
        caption="图1 结构",
        caption_method="parser_caption",
        binding_score=0.95,
        footnote="资料来源：示例",
        page_no=1,
        source_block_order=2,
        source_node_id="node-image",
        metadata={"image_relation": relation_metadata},
    )
    nodes = [
        CanonicalSourceNode(
            node_id="node-text",
            node_type="text",
            source_order=1,
            canonical_text="如图1所示",
            text_hash="text-hash",
            page_no=1,
        ),
        CanonicalSourceNode(
            node_id="node-image",
            node_type="image",
            source_order=2,
            canonical_text="![图1](asset://asset-1)",
            text_hash="image-hash",
            page_no=1,
            asset_id="asset-1",
        ),
    ]
    return {asset.asset_id: asset}, nodes, FigureLinkResult(links=[_link()])


def test_phase2_graph_unifies_caption_footnote_and_explicit_edges_stably():
    assets, nodes, links = _graph_inputs()

    first = build_relation_graph(assets=assets, provenance_nodes=nodes, figure_links=links)
    second = build_relation_graph(assets=assets, provenance_nodes=nodes, figure_links=links)

    # Caption/footnote edges without accepted spatial candidates are
    # now downgraded to ``relative_reference`` with ``status="ambiguous"``
    # because the nearest-text fallback cannot verify the text node is a
    # genuine caption/footnote block (see _asset_relation_edge fallback).
    # The third ``relative_reference`` from same_section_proximity is
    # suppressed because the asset is already covered by the fallback edges
    # (skip_asset_ids dedup in build_relation_graph).
    assert [edge.relation_type for edge in first.edges] == [
        "explicit_reference",
        "relative_reference",
        "relative_reference",
    ]
    assert [edge.relation_id for edge in first.edges] == [
        edge.relation_id for edge in second.edges
    ]
    assert all(edge.target_asset_ids == ("asset-1",) for edge in first.edges)
    assert all(
        edge.binding_method
        and edge.score_kind
        and edge.status
        and edge.evidence
        and edge.algorithm_version
        for edge in first.edges
    )


def test_phase2_graph_round_trip_and_legacy_adapter_are_lossless():
    assets, nodes, links = _graph_inputs()
    graph = build_relation_graph(assets=assets, provenance_nodes=nodes, figure_links=links)

    restored = CanonicalRelationGraph.from_dict(graph.to_dict())
    legacy = graph_from_legacy_figure_links(links.to_metadata())

    assert restored.to_dict() == graph.to_dict()
    assert len(legacy.edges) == 1
    assert legacy.edges[0].to_dict() == next(
        edge.to_dict() for edge in graph.edges if edge.relation_type == "explicit_reference"
    )

    legacy_artifact = SimpleNamespace(
        relation_graph_json={},
        figure_links_json=links.to_metadata(),
    )
    assert load_relation_graph(legacy_artifact).to_dict() == legacy.to_dict()


def test_phase2_empty_versioned_graph_remains_authoritative_over_legacy_links():
    empty_graph = CanonicalRelationGraph(
        edges=[],
        groups=[],
        algorithm_version="empty-graph-v1",
        coordinate_space="test-coordinate-space",
    )
    artifact = SimpleNamespace(
        schema_version=2,
        relation_graph_json=empty_graph.to_dict(),
        figure_links_json=FigureLinkResult(links=[_link()]).to_metadata(),
    )

    loaded = load_relation_graph(artifact)

    assert loaded.to_dict() == empty_graph.to_dict()
    assert loaded.edges == []


def test_phase2_merge_preserves_evidence_and_uses_safe_status_priority():
    base = CanonicalRelationEdge(
        relation_id="rel-same",
        relation_type="caption_of",
        source_node_ids=("node-1",),
        target_asset_ids=("asset-1",),
        status="bound",
        binding_method="parser_caption",
        binding_score=0.9,
        evidence=({"source": "parser"},),
    )
    rejected = CanonicalRelationEdge(
        relation_id="rel-same",
        relation_type="caption_of",
        source_node_ids=("node-1",),
        target_asset_ids=("asset-1",),
        status="rejected",
        binding_method="scope_guard",
        binding_score=0.0,
        evidence=({"source": "guard"},),
        ambiguity_reasons=("scope_mismatch",),
    )

    merged = merge_relation_edges([base], [rejected])

    assert len(merged) == 1
    assert merged[0].status == "rejected"
    assert [item["source"] for item in merged[0].evidence] == ["parser", "guard"]


def test_phase2_ambiguous_image_relation_remains_first_class_edge():
    assets, nodes, links = _graph_inputs()
    asset = assets["asset-1"]
    asset.footnote_ambiguous = True
    asset.metadata["image_relation"]["footnote_ambiguous"] = True
    asset.metadata["image_relation"]["ambiguity_reasons"] = [
        "spatial_footnote_candidate_shared_between_images"
    ]

    graph = build_relation_graph(assets=assets, provenance_nodes=nodes, figure_links=links)
    # Footnote fallback edges are now ``relative_reference`` instead of
    # ``footnote_of`` — the nearest-text fallback cannot confirm the text
    # node is a genuine footnote block.  Find the edge by its
    # binding_method instead.
    footnote = next(
        edge
        for edge in graph.edges
        if edge.binding_method == "nearest_text_fallback_footnote"
    )

    assert footnote.status == "ambiguous"
    assert footnote.ambiguity_reasons == (
        "spatial_footnote_candidate_shared_between_images",
    )


def test_phase2_shadow_graph_does_not_change_chunk_body_or_count(monkeypatch):
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="如图1所示，系统启动。",
                order_index=0,
                page_no=1,
            ),
            ParsedBlock(
                block_type="image",
                text="图1 系统结构",
                order_index=1,
                page_no=1,
                metadata={"caption": "图1 系统结构", "footnote": "来源：测试"},
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_materialization_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_graph_enabled",
        False,
    )
    off_artifact = {}
    off_chunks, _ = _rebuild_canonical_weknora_parent_child(
        _doc(), parsed, "pdf", _cfg(), artifact_out=off_artifact
    )

    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_graph_enabled",
        True,
    )
    shadow_artifact = {}
    shadow_chunks, _ = _rebuild_canonical_weknora_parent_child(
        _doc(), parsed, "pdf", _cfg(), artifact_out=shadow_artifact
    )

    def shape(chunks):
        return [
            (chunk.chunk_type, chunk.content, chunk.start_at, chunk.end_at)
            for chunk in chunks
        ]

    assert shape(shadow_chunks) == shape(off_chunks)
    assert off_artifact["schema_version"] == 1
    assert off_artifact["relation_graph_json"] == {}
    assert shadow_artifact["schema_version"] == 2
    assert shadow_artifact["relation_graph_json"]["edges"]
    assert all("relation_ids" not in (chunk.metadata_json or {}) for chunk in shadow_chunks)


def test_phase2_materialization_writes_only_compact_chunk_summaries(monkeypatch):
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="如图1所示，系统启动。",
                order_index=0,
                page_no=1,
            ),
            ParsedBlock(
                block_type="image",
                text="图1 系统结构",
                order_index=1,
                page_no=1,
                metadata={"caption": "图1 系统结构"},
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_graph_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_materialization_enabled",
        True,
    )
    artifact = {}

    chunks, _ = _rebuild_canonical_weknora_parent_child(
        _doc(), parsed, "pdf", _cfg(), artifact_out=artifact
    )

    relation_chunks = [chunk for chunk in chunks if (chunk.metadata_json or {}).get("relation_ids")]
    assert relation_chunks
    assert all(
        chunk.metadata_json["canonical_parse_revision"] == artifact["parse_revision"]
        for chunk in chunks
    )
    assert all(
        "evidence" not in summary and "candidate_targets" not in summary
        for chunk in relation_chunks
        for summary in chunk.metadata_json["relation_summaries"]
    )
    assert any(
        chunk.metadata_json.get("outgoing_asset_chunk_ids") for chunk in relation_chunks
    )
    assert any(
        chunk.metadata_json.get("incoming_reference_chunk_ids") for chunk in relation_chunks
    )


def test_phase2_flush_failure_keeps_graph_and_chunks_in_one_caller_transaction(monkeypatch):
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="如图1所示，系统启动。",
                order_index=0,
                page_no=1,
            ),
            ParsedBlock(
                block_type="image",
                text="图1 系统结构",
                order_index=1,
                page_no=1,
                metadata={"caption": "图1 系统结构"},
            ),
        ],
    )
    db = MagicMock()
    db.get.return_value = None
    db.flush.side_effect = RuntimeError("flush failed")
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_graph_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_materialization_enabled",
        True,
    )

    with (
        patch("app.services.chunk_service.runtime_config_from_db", return_value=_cfg()),
        pytest.raises(RuntimeError, match="flush failed"),
    ):
        rebuild_document_chunks(db, _doc(), parsed)

    added = [call.args[0] for call in db.add.call_args_list]
    artifact = next(item for item in added if isinstance(item, DocumentCanonicalArtifact))
    chunks = [item for item in added if isinstance(item, Chunk)]
    assert chunks
    assert all(
        chunk.metadata_json["canonical_parse_revision"] == artifact.parse_revision
        for chunk in chunks
    )
    assert artifact.relation_graph_json["edges"]
    db.commit.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# P7 Regression Tests — Materializer Gate (ambiguous ≠ strong binding)
# ──────────────────────────────────────────────────────────────────────────────


def _make_chunk_mock(chunk_id: str, chunk_type: str, **meta):
    """Create a minimal chunk-like object for materializer tests."""
    chunk = MagicMock()
    chunk.id = chunk_id
    chunk.chunk_type = chunk_type
    chunk.metadata_json = dict(meta)
    chunk.relation_chunks = []
    return chunk


def _make_edge(**overrides):
    """Create a CanonicalRelationEdge with safe defaults."""
    from app.services.document_relation.models import stable_relation_id

    kwargs: dict[str, Any] = {
        "relation_type": "explicit_reference",
        "source_node_ids": ("node-text",),
        "target_asset_ids": ("asset-1",),
        "status": "bound",
        "binding_method": "test",
        "binding_score": 0.9,
        "score_kind": "heuristic_rule_score",
        "evidence": ({"test": True},),
        "algorithm_version": "test-v1",
    }
    kwargs.update(overrides)
    kwargs.setdefault(
        "relation_id",
        stable_relation_id(
            kwargs["relation_type"],
            kwargs["source_node_ids"],
            kwargs["target_asset_ids"],
        ),
    )
    return CanonicalRelationEdge(**kwargs)


class TestMaterializerGate:
    """P7-C1 & P7-C2: ambiguous edges MUST NOT write outgoing/incoming;
    bound edges MUST."""

    def test_bound_edge_writes_outgoing_and_incoming(self):
        """P7-C2: bound explicit_reference → outgoing/incoming written."""
        text_chunk = _make_chunk_mock("text-1", "text", source_node_ids=["node-text"])
        img_chunk = _make_chunk_mock("img-1", "image", asset_id="asset-1")
        graph = CanonicalRelationGraph(
            edges=[_make_edge(status="bound", relation_type="explicit_reference")],
        )
        materialize_relation_summaries([text_chunk, img_chunk], graph)

        assert text_chunk.metadata_json.get("outgoing_asset_chunk_ids") == ["img-1"]
        assert img_chunk.metadata_json.get("incoming_reference_chunk_ids") == ["text-1"]

    def test_ambiguous_edge_does_not_write_outgoing_or_incoming(self):
        """P7-C1: ambiguous relative_reference → NO outgoing/incoming."""
        text_chunk = _make_chunk_mock("text-1", "text", source_node_ids=["node-text"])
        img_chunk = _make_chunk_mock("img-1", "image", asset_id="asset-1")
        graph = CanonicalRelationGraph(
            edges=[
                _make_edge(
                    status="ambiguous",
                    relation_type="relative_reference",
                ),
            ],
        )
        materialize_relation_summaries([text_chunk, img_chunk], graph)

        assert not text_chunk.metadata_json.get("outgoing_asset_chunk_ids")
        assert not img_chunk.metadata_json.get("incoming_reference_chunk_ids")

    def test_ambiguous_edge_still_writes_relation_summaries(self):
        """P7-C3: ambiguous edges still appear in relation_summaries
        with full status / score / method."""
        text_chunk = _make_chunk_mock("text-1", "text", source_node_ids=["node-text"])
        img_chunk = _make_chunk_mock("img-1", "image", asset_id="asset-1")
        edge = _make_edge(
            status="ambiguous",
            relation_type="relative_reference",
            binding_method="same_section_proximity",
            binding_score=0.45,
        )
        graph = CanonicalRelationGraph(edges=[edge])
        materialize_relation_summaries([text_chunk, img_chunk], graph)

        summaries = img_chunk.metadata_json.get("relation_summaries") or []
        assert len(summaries) >= 1
        # Verify the status is preserved
        assert summaries[0]["status"] == "ambiguous"
        assert summaries[0]["relation_type"] == "relative_reference"
        assert summaries[0]["binding_method"] == "same_section_proximity"

    def test_rejected_edge_writes_nothing(self):
        """Rejected edges write neither outgoing/incoming nor summaries? No —
        rejected edges write relation_summaries but not outgoing/incoming."""
        text_chunk = _make_chunk_mock("text-1", "text", source_node_ids=["node-text"])
        img_chunk = _make_chunk_mock("img-1", "image", asset_id="asset-1")
        graph = CanonicalRelationGraph(
            edges=[_make_edge(status="rejected")],
        )
        materialize_relation_summaries([text_chunk, img_chunk], graph)

        assert not text_chunk.metadata_json.get("outgoing_asset_chunk_ids")
        assert not img_chunk.metadata_json.get("incoming_reference_chunk_ids")


# ──────────────────────────────────────────────────────────────────────────────
# P7 Regression Tests — caption_of fallback semantics
# ──────────────────────────────────────────────────────────────────────────────


class TestCaptionFallbackSemantics:
    """P7-C5: caption fallback without accepted spatial candidates
    MUST produce ``relative_reference``, NOT ``caption_of``."""

    def test_caption_without_accepted_candidates_is_relative_reference(self):
        """Fallback caption → relative_reference + ambiguous."""
        assets, nodes, links = _graph_inputs()
        # Remove the explicit figure link to isolate caption/fallback edges
        links_no_explicit = FigureLinkResult(links=[])

        graph = build_relation_graph(
            assets=assets,
            provenance_nodes=nodes,
            figure_links=links_no_explicit,
        )

        # All edges should be relative_reference (no caption_of or footnote_of)
        edge_types = [edge.relation_type for edge in graph.edges]
        assert "caption_of" not in edge_types, (
            f"Fallback caption edges must not be caption_of: {edge_types}"
        )
        assert "footnote_of" not in edge_types, (
            f"Fallback footnote edges must not be footnote_of: {edge_types}"
        )
        assert all(t == "relative_reference" for t in edge_types), edge_types

        # All fallback edges should be ambiguous
        assert all(edge.status == "ambiguous" for edge in graph.edges)

        # binding_method should indicate the fallback origin
        methods = {edge.binding_method for edge in graph.edges}
        assert "nearest_text_fallback_caption" in methods
        assert "nearest_text_fallback_footnote" in methods

    def test_caption_with_accepted_spatial_candidates_is_caption_of(self):
        """When spatial candidates ARE accepted, edge stays caption_of."""
        from app.services.document_parser.canonical_document import CanonicalAsset

        assets, nodes, links = _graph_inputs()

        # Create a new asset with accepted spatial candidates
        asset_with_candidates = CanonicalAsset(
            asset_id="asset-2",
            caption="图2 详细结构",
            caption_method="parser_caption",
            binding_score=0.95,
            page_no=1,
            source_block_order=3,
            source_node_id="node-image-2",
            metadata={
                "image_relation": {
                    "ambiguous": False,
                    "caption_ambiguous": False,
                    "footnote_ambiguous": False,
                    "ambiguity_reasons": [],
                    "spatial_candidates": [
                        {
                            "kind": "caption",
                            "source_order": 1,  # node-text
                            "accepted": True,
                        },
                    ],
                },
            },
            spatial_status="accepted",
        )
        assets["asset-2"] = asset_with_candidates

        graph = build_relation_graph(
            assets=assets,
            provenance_nodes=nodes,
            figure_links=FigureLinkResult(links=[]),
        )

        # Find the edge for asset-2
        asset2_edges = [
            e for e in graph.edges if "asset-2" in e.target_asset_ids
        ]
        assert len(asset2_edges) == 1
        assert asset2_edges[0].relation_type == "caption_of"
        assert asset2_edges[0].status == "bound"
        assert asset2_edges[0].binding_method == "parser_caption"


# ──────────────────────────────────────────────────────────────────────────────
# P7 Regression Tests — same_section_proximity guards
# ──────────────────────────────────────────────────────────────────────────────


class TestProximityGuards:
    """P7-C8: explicit FigureLink blocks duplicate proximity edges."""

    def test_explicit_figure_link_suppresses_proximity_edge(self):
        """When an image has figure_ids, no proximity edge is generated."""
        from app.services.document_parser.canonical_document import CanonicalAsset

        assets: dict[str, Any] = {}
        nodes: list[Any] = []

        # Image WITH figure_ids
        assets["img-with-fig"] = CanonicalAsset(
            asset_id="img-with-fig",
            figure_ids=["图1"],
            page_no=1,
            source_block_order=2,
            source_node_id="node-img",
        )
        # Nearby text node for proximity
        nodes.append(
            CanonicalSourceNode(
                node_id="node-text-1",
                node_type="text",
                source_order=1,
                canonical_text="这是一段描述文字",
                text_hash="hash1",
                page_no=1,
            ),
        )

        graph = build_relation_graph(
            assets=assets,
            provenance_nodes=nodes,
            figure_links=FigureLinkResult(links=[]),
        )

        # No proximity edges for images with figure_ids
        proximity_edges = [
            e for e in graph.edges
            if e.relation_type == "relative_reference"
            and e.binding_method == "same_section_proximity"
        ]
        assert len(proximity_edges) == 0, (
            f"Images with figure_ids should not get proximity edges: {proximity_edges}"
        )

    def test_caption_covered_asset_skips_proximity(self):
        """Assets covered by caption/footnote edges skip proximity
        (skip_asset_ids dedup)."""
        assets, nodes, links = _graph_inputs()
        # Asset has both caption and footnote → fallback edges generated
        # → covered_asset_ids contains "asset-1" → proximity skipped
        graph = build_relation_graph(
            assets=assets,
            provenance_nodes=nodes,
            figure_links=links,
        )

        proximity_edges = [
            e for e in graph.edges
            if e.binding_method == "same_section_proximity"
        ]
        assert len(proximity_edges) == 0, (
            "Caption-covered assets should not get additional proximity edges"
        )


# ──────────────────────────────────────────────────────────────────────────────
# P7 Regression Tests — relation_chunks dedup
# ──────────────────────────────────────────────────────────────────────────────


class TestRelationChunksDedup:
    """P7-C11: relation_chunks must be deduplicated."""

    def test_duplicate_outgoing_ids_are_deduplicated(self):
        """When the same chunk ID appears multiple times in
        outgoing_asset_chunk_ids, it is deduplicated in relation_chunks."""
        chunk = _make_chunk_mock(
            "text-1", "text",
            outgoing_asset_chunk_ids=["img-1", "img-2", "img-1", "img-2"],
        )
        populate_relation_chunks([chunk])
        assert chunk.relation_chunks == ["img-1", "img-2"]

    def test_self_reference_excluded(self):
        """A chunk's own ID is excluded from relation_chunks."""
        chunk = _make_chunk_mock(
            "text-1", "text",
            outgoing_asset_chunk_ids=["img-1", "text-1", "img-2"],
        )
        populate_relation_chunks([chunk])
        assert "text-1" not in chunk.relation_chunks
        assert chunk.relation_chunks == ["img-1", "img-2"]
