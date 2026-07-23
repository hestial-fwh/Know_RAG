from types import SimpleNamespace
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

    assert [edge.relation_type for edge in first.edges] == [
        "explicit_reference",
        "caption_of",
        "footnote_of",
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
    footnote = next(edge for edge in graph.edges if edge.relation_type == "footnote_of")

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
