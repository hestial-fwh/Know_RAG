from types import SimpleNamespace

from app.services.chunk_service import _rebuild_canonical_weknora_parent_child
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import CanonicalAsset, CanonicalSourceNode
from app.services.document_parser.mineru_adapter import _content_list_to_blocks
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.document_relation.asset_group_resolver import (
    build_member_edges,
    resolve_asset_groups,
)
from app.services.document_relation.page_layout_graph import build_page_layout_graph
from app.services.document_relation.shared_context_resolver import (
    resolve_shared_context_edges,
)


def _asset(
    asset_id: str,
    bbox,
    *,
    section: str = "S",
    caption: str = "",
    authoritative: bool = True,
    order: int = 1,
):
    metadata = {"section_path": section}
    if authoritative:
        metadata.update(
            {
                "page_width": 1000.0,
                "page_height": 1000.0,
                "page_extent": [0.0, 0.0, 1000.0, 1000.0],
                "page_extent_source": "authoritative",
            }
        )
    return CanonicalAsset(
        asset_id=asset_id,
        caption=caption,
        page_no=1,
        bbox=bbox,
        source_block_order=order,
        source_node_id=f"node-{asset_id}",
        metadata=metadata,
    )


def _source(
    node_id: str = "shared",
    *,
    bbox=(40, 60, 960, 140),
    section: str = "S",
    role: str = "shared_context",
):
    return CanonicalSourceNode(
        node_id=node_id,
        node_type="text",
        source_order=0,
        canonical_text="以下作品展示同一主题",
        text_hash="hash",
        page_no=1,
        bbox=bbox,
        section_path=section,
        semantic_role=role,
    )


def _resolve(assets, nodes):
    layout = build_page_layout_graph(assets=assets, provenance_nodes=nodes)
    groups = resolve_asset_groups(layout)
    member_edges = build_member_edges(groups)
    shared_edges = resolve_shared_context_edges(layout, groups, assets=assets)
    return layout, groups, member_edges, shared_edges


def _cfg():
    return ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
    )


def test_mineru_adapter_preserves_only_valid_authoritative_page_extent():
    blocks = _content_list_to_blocks(
        [
            {
                "type": "image",
                "page_idx": 0,
                "bbox": [50, 100, 450, 500],
                "page_width": 612,
                "page_height": 792,
                "section_path": "S",
            },
            {
                "type": "text",
                "text": "invalid extent",
                "page_idx": 1,
                "bbox": [0, 0, 10, 10],
                "page_width": "bad",
                "page_height": 792,
            },
        ],
        {},
        knowledge_base_id="kb",
        document_id="doc",
    )

    assert blocks[0].metadata["page_extent"] == [0.0, 0.0, 612.0, 792.0]
    assert blocks[0].metadata["page_extent_source"] == "authoritative"
    assert blocks[0].metadata["section_path"] == "S"
    assert "page_extent" not in blocks[1].metadata


def test_authoritative_same_section_pair_forms_group_and_shared_context():
    assets = {
        "left": _asset("left", (50, 200, 470, 700), order=1),
        "right": _asset("right", (530, 200, 950, 700), order=2),
    }

    layout, groups, member_edges, shared_edges = _resolve(assets, [_source()])

    assert layout.pages[1].extent_source == "authoritative"
    assert len(groups) == 1
    assert groups[0].status == "bound"
    assert groups[0].group_type == "horizontal_series"
    assert [edge.status for edge in member_edges] == ["bound", "bound"]
    assert len(shared_edges) == 1
    assert shared_edges[0].status == "bound"
    assert shared_edges[0].source_node_ids == ("shared",)
    assert groups[0].shared_context_node_ids == ("shared",)


def test_layout_without_group_level_evidence_stays_ambiguous():
    vertical_assets = {
        "upper": _asset("upper", (200, 100, 800, 400), order=1),
        "lower": _asset("lower", (200, 550, 800, 850), order=2),
    }
    _, vertical_groups, vertical_edges, _ = _resolve(vertical_assets, [])

    assert vertical_groups[0].status == "ambiguous"
    assert vertical_groups[0].group_type == "vertical_series"
    assert "missing_group_level_evidence" in vertical_groups[0].ambiguity_reasons
    assert all(edge.status == "ambiguous" for edge in vertical_edges)

    grid_assets = {
        "a": _asset("a", (50, 100, 450, 400), order=1),
        "b": _asset("b", (550, 100, 950, 400), order=2),
        "c": _asset("c", (50, 550, 450, 850), order=3),
        "d": _asset("d", (550, 550, 950, 850), order=4),
    }
    _, grid_groups, _, _ = _resolve(grid_assets, [])

    assert grid_groups[0].status == "ambiguous"
    assert "non_unique_layout_partition" in grid_groups[0].ambiguity_reasons


def test_overlapping_assets_never_form_a_bound_series():
    overlapping_assets = {
        "base": _asset("base", (100, 100, 800, 800), order=1),
        "overlay": _asset("overlay", (200, 200, 700, 700), order=2),
    }

    _, groups, member_edges, _ = _resolve(overlapping_assets, [])

    assert groups[0].status == "ambiguous"
    assert groups[0].group_type == "candidate_group"
    assert "overlapping_member_geometry" in groups[0].ambiguity_reasons
    assert all(edge.status == "ambiguous" for edge in member_edges)


def test_independent_captions_never_become_shared_context():
    assets = {
        "left": _asset("left", (50, 200, 470, 700), caption="图1 输入", order=1),
        "right": _asset("right", (530, 200, 950, 700), caption="图2 输出", order=2),
    }

    _, groups, _, shared_edges = _resolve(assets, [_source()])

    assert groups[0].status == "bound"
    assert len(shared_edges) == 1
    assert shared_edges[0].status == "rejected"
    assert shared_edges[0].binding_method == "independent_caption_guard"


def test_cross_section_assets_are_rejected_before_group_binding():
    assets = {
        "left": _asset("left", (50, 200, 470, 700), section="A", order=1),
        "right": _asset("right", (530, 200, 950, 700), section="B", order=2),
    }

    _, groups, member_edges, _ = _resolve(assets, [_source(section="A")])

    assert groups[0].status == "rejected"
    assert groups[0].grouping_method == "section_boundary_guard"
    assert all(edge.status == "rejected" for edge in member_edges)


def test_derived_extent_and_missing_bbox_never_produce_bound_group():
    derived_assets = {
        "left": _asset("left", (50, 200, 470, 700), authoritative=False, order=1),
        "right": _asset("right", (530, 200, 950, 700), authoritative=False, order=2),
    }
    _, derived_groups, _, derived_shared = _resolve(derived_assets, [_source()])

    assert derived_groups[0].status == "ambiguous"
    assert "derived_page_extent" in derived_groups[0].ambiguity_reasons
    assert derived_shared[0].status == "ambiguous"

    missing_assets = {
        "left": _asset("left", None, order=1),
        "right": _asset("right", (530, 200, 950, 700), order=2),
    }
    _, missing_groups, _, missing_shared = _resolve(missing_assets, [_source()])

    assert missing_groups[0].status == "ambiguous"
    assert "missing_member_geometry" in missing_groups[0].ambiguity_reasons
    assert missing_shared[0].status == "ambiguous"


def test_phase3_shadow_flag_preserves_chunks_and_writes_group_graph(monkeypatch):
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="text",
                text="以下作品展示同一主题",
                order_index=0,
                page_no=1,
                bbox=(40, 60, 960, 140),
                metadata={
                    "parser": "mineru",
                    "role": "shared_context",
                    "section_path": "S",
                },
            ),
            ParsedBlock(
                block_type="image",
                text="左侧作品",
                order_index=1,
                page_no=1,
                bbox=(50, 200, 470, 700),
                metadata={
                    "parser": "mineru",
                    "section_path": "S",
                    "page_extent": [0, 0, 1000, 1000],
                    "page_extent_source": "authoritative",
                },
            ),
            ParsedBlock(
                block_type="image",
                text="右侧作品",
                order_index=2,
                page_no=1,
                bbox=(530, 200, 950, 700),
                metadata={
                    "parser": "mineru",
                    "section_path": "S",
                    "page_extent": [0, 0, 1000, 1000],
                    "page_extent_source": "authoritative",
                },
            ),
        ],
    )
    document = SimpleNamespace(
        id="phase3-doc",
        knowledge_base_id="phase3-kb",
        title="Phase 3",
        file_type="pdf",
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_graph_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_materialization_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_asset_group_enabled",
        False,
    )
    off_artifact = {}
    off_chunks, _ = _rebuild_canonical_weknora_parent_child(
        document, parsed, "pdf", _cfg(), artifact_out=off_artifact
    )

    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_asset_group_enabled",
        True,
    )
    on_artifact = {}
    on_chunks, _ = _rebuild_canonical_weknora_parent_child(
        document, parsed, "pdf", _cfg(), artifact_out=on_artifact
    )

    def shape(chunks):
        return [(chunk.chunk_type, chunk.content, chunk.start_at, chunk.end_at) for chunk in chunks]

    assert shape(on_chunks) == shape(off_chunks)
    assert off_artifact["relation_graph_json"]["groups"] == []
    assert len(on_artifact["relation_graph_json"]["groups"]) == 1
    assert on_artifact["relation_graph_json"]["groups"][0]["status"] == "bound"
    assert on_artifact["algorithm_version"] == "canonical-relation-graph-p3-v1"
    assert any(
        edge["relation_type"] == "shared_context_of"
        and edge["status"] == "bound"
        for edge in on_artifact["relation_graph_json"]["edges"]
    )
    assert all("group_ids" not in (chunk.metadata_json or {}) for chunk in on_chunks)

    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_materialization_enabled",
        True,
    )
    materialized_artifact = {}
    materialized_chunks, _ = _rebuild_canonical_weknora_parent_child(
        document,
        parsed,
        "pdf",
        _cfg(),
        artifact_out=materialized_artifact,
    )
    image_chunks = [
        chunk for chunk in materialized_chunks if chunk.chunk_type == "image"
    ]
    assert len(image_chunks) == 2
    assert all((chunk.metadata_json or {}).get("group_ids") for chunk in image_chunks)
    assert all(
        any(
            summary["relation_type"] == "member_of_group"
            for summary in chunk.metadata_json["relation_summaries"]
        )
        for chunk in image_chunks
    )


def test_phase3_group_failure_keeps_base_chunks_and_graph(monkeypatch):
    parsed = ParsedDocument(
        content="",
        blocks=[
            ParsedBlock(
                block_type="image",
                text="普通图片",
                order_index=0,
                page_no=1,
                bbox=(0, 0, 100, 100),
                metadata={"parser": "mineru", "section_path": "S"},
            )
        ],
    )
    document = SimpleNamespace(
        id="phase3-fail-open",
        knowledge_base_id="phase3-kb",
        title="Phase 3 fail open",
        file_type="pdf",
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_relation_graph_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.chunk_service.settings.document_asset_group_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.chunk_service.resolve_asset_groups",
        lambda layout: (_ for _ in ()).throw(RuntimeError("resolver failed")),
    )
    artifact = {}

    chunks, inserted = _rebuild_canonical_weknora_parent_child(
        document,
        parsed,
        "pdf",
        _cfg(),
        artifact_out=artifact,
    )

    assert inserted == len(chunks)
    assert any(chunk.chunk_type == "image" for chunk in chunks)
    assert artifact["relation_graph_json"]["groups"] == []
    assert artifact["algorithm_version"] == "canonical-relation-graph-p2-v1"
