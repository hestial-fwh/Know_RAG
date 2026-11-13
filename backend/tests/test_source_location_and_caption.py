"""页码贯通、长表来源、占位图题恢复与精确图号召回的边界回归。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.source_location import project_source_location, location_fields, enrich_existing_locations
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.document_parser.image_relation_resolver import resolve_image_relation, SpatialRelationCandidate
from app.services.chunker.canonical_parent_child import build_canonical_parent_child_plan
from app.services.chunker.models import ChunkerConfig
from app.services.llm_context_builder import build_evidence_groups_payload, build_cloud_flat_chunks_payload
from app.services import keyword_index_service as keywords
from test_knowledge_index_foundation import foundation


def node(page=1, start=0, end=4, node_id="n"):
    return {"node_id": node_id, "page_no": page, "normalized_start": start,
            "normalized_end": end, "bbox": [0, 0, 20, 20]}


def test_discontinuous_pages_and_block_precision():
    result = project_source_location(0, 10, [node(2), node(4, 6, 10, "b")], "正文内容\n\n另外内容")
    assert result["source_pages"] == [2, 4]
    assert result["page_no"] is None and result["page_range"] == "2、4"
    assert result["page_location_status"] == "resolved"
    assert result["source_spans"][0]["bbox_precision"] == "source_block"


def test_unknown_source_and_uncovered_content_do_not_become_single_page():
    unknown = project_source_location(0, 10, [node(2), node(None, 6, 10, "b")])
    assert unknown["page_no"] is None and unknown["page_location_status"] == "partial"
    gap = project_source_location(0, 10, [node(2)], "第7页未知内容正文。")
    assert gap["source_pages"] == [2] and gap["page_location_status"] == "partial"
    assert project_source_location(0, 10, [], "第7页未知内容正文。")["page_no"] is None


@pytest.mark.parametrize("value,expected", [({"page_idx": 0}, 1), ({"page_no": 0}, None),
                                          ({"page_no": True}, None)])
def test_legacy_page_fields_are_normalized(value, expected):
    assert location_fields(value)["page_no"] == expected
    assert location_fields(location_fields(value))["page_no"] == expected


def test_source_pages_override_legacy_single_page_and_parent_hint():
    value = {"page_no": 1, "metadata": {"source_pages": [2, 4], "chunk_policy_page_no": 2}}
    assert location_fields(value)["page_no"] is None
    assert location_fields(value)["page_range"] == "2、4"
    assert keywords._page_no(value["metadata"]) is None


def test_printed_page_label_is_kept_separate_from_pdf_page():
    parsed = ParsedDocument("", blocks=[ParsedBlock("text", "正文说明。" * 30, page_no=4, metadata={"page_label": "ii"})])
    plan = build_canonical_parent_child_plan(build_canonical_document(parsed, file_type="pdf"), ChunkerConfig())
    assert plan.children[0].metadata["page_no"] == 4
    assert plan.children[0].metadata["source_spans"][0]["page_label"] == "ii"


def test_old_result_uses_only_current_scoped_ledger_and_does_not_write():
    db = Mock()
    db.scalars.return_value = [SimpleNamespace(document_id="doc", parse_revision="r2",
        canonical_markdown="正文内容", provenance_json={"nodes": [node(4)]})]
    meta = {"canonical_parse_revision": "r2", "source_position_is_exact": True}
    current = {"chunk_id": "c", "document_id": "doc", "start_at": 0, "end_at": 4, "metadata": meta}
    stale = {**current, "chunk_id": "old", "metadata": {**meta, "canonical_parse_revision": "r1"}}
    enrich_existing_locations(db, "kb", [current, stale], [])
    assert current["page_no"] == 4 and current["metadata"]["source_pages"] == [4]
    assert "source_pages" not in meta and "page_no" not in stale
    assert not db.commit.called and not db.add.called


def test_old_location_backfill_reads_real_scoped_orm_ledger(foundation):
    from app.db.models import DocumentCanonicalArtifact

    with foundation.sessions() as db:
        artifact = db.get(DocumentCanonicalArtifact, "doc")
        artifact.canonical_markdown = "正文内容"
        artifact.provenance_json = {"nodes": [node(4)]}
        db.commit()
        item = {"chunk_id": "doc-chunk", "document_id": "doc", "start_at": 0, "end_at": 4,
                "metadata": {"canonical_parse_revision": "rev1", "source_position_is_exact": True}}
        enrich_existing_locations(db, "other-kb", [item], [])
        assert "page_no" not in item
        enrich_existing_locations(db, "kb", [item], [])
        assert item["page_no"] == 4
        assert not db.dirty and not db.new
        assert artifact.provenance_json == {"nodes": [node(4)]}


def test_long_table_keeps_source_after_repeated_headers_are_inserted():
    table = "|编号|说明|\n|---|---|\n" + "\n".join(f"|{i}|项目说明内容{i}。|" for i in range(35))
    parsed = ParsedDocument("", blocks=[ParsedBlock("table", table, page_no=3)])
    canonical = build_canonical_document(parsed, file_type="pdf")
    plan = build_canonical_parent_child_plan(canonical, ChunkerConfig(enable_parent_child=True,
        child_chunk_size=150, parent_chunk_size=800))
    tables = [child for child in plan.children if child.chunk_type == "table"]
    assert len(tables) > 1
    assert all(child.metadata["source_pages"] == [3] for child in tables)
    assert all(child.metadata["source_spans"] for child in tables)
    assert any(node.resolution == "normalized_table_rows" for node in plan.provenance_nodes)


def test_same_header_at_different_pages_does_not_move_row_locations():
    blocks = [ParsedBlock("table", f"|编号|说明|\n|---|---|\n|{page}|第{page}页数据|", order, page)
              for order, page in enumerate((2, 4))]
    canonical = build_canonical_document(ParsedDocument("", blocks=blocks), file_type="pdf")
    plan = build_canonical_parent_child_plan(canonical, ChunkerConfig(child_chunk_size=150))
    tables = [c for c in plan.children if c.chunk_type == "table"]
    assert tables
    assert {page for c in tables for page in c.metadata["source_pages"]} == {2, 4}
    for child in tables:
        assert child.metadata["source_pages"] == sorted({span["page_no"] for span in child.metadata["source_spans"]})
        if len(child.metadata["source_pages"]) > 1:
            assert child.metadata["page_no"] is None and child.metadata["page_range"] == "2、4"


def test_cross_page_context_preserves_location_in_both_return_modes():
    location = project_source_location(0, 10, [node(2), node(4, 6, 10, "b")])
    item = {"chunk_id": "c", "id": "c", "document_id": "doc", "chunk_type": "text",
            "content": "跨页正文说明，介绍奖助学金申请与发放流程以及办理条件。" * 4, "score": .9, "metadata": location}
    retrieval = {"query": "正文", "results": [item], "meta": {"top_k": 1}}
    for payload in (build_evidence_groups_payload(retrieval, {"include_context": False}),
                    build_cloud_flat_chunks_payload(retrieval, {"return_mode": "chunks"})):
        chunk = payload["chunks"][0]
        assert chunk["page_no"] is None and chunk["page_range"] == "2、4"
        assert chunk["source_pages"] == [2, 4] and len(chunk["source_spans"]) == 2


def image(order=0, page=2, bbox=None, caption="PDF 图片", parser="mineru"):
    return ParsedBlock("image", "PDF 图片", order, page, bbox, image_url="/files/target.png",
                       metadata={"parser": parser, "caption": caption})


def text(value="图10-2 奖助学金申请与发放", order=2, page=2, bbox=None, **meta):
    return ParsedBlock("text", value, order, page, bbox, metadata=meta)


def first_asset(blocks):
    return next(iter(build_canonical_document(ParsedDocument("", blocks=blocks), file_type="pdf").assets.values()))


def test_generic_caption_does_not_block_spatial_caption_recovery():
    candidate = SpatialRelationCandidate("图10-2 奖助学金申请", "caption", 2, (.1, .51, .8, .56), 2)
    relation = resolve_image_relation(caption="PDF 图片", page_no=2, image_bbox=(.1, .1, .8, .5),
                                      spatial_candidates=[candidate], enable_spatial=True)
    assert relation.figure_ids == ["figure:10-2"]
    assert relation.caption_method == "spatial_bbox"


def test_q22_caption_after_intermediate_text_is_bound_without_geometry():
    asset = first_asset([image(), text("材料缺失：退回学生补正后重新提交", order=1), text()])
    assert asset.figure_ids == ["figure:10-2"]
    assert asset.caption_method == "same_page_reading_order"
    assert asset.selected_candidate_orders == [2] and asset.deterministic_binding_allowed


@pytest.mark.parametrize("disabled", [False, True])
def test_short_bound_figure_survives_context_text_heuristics_but_not_explicit_skip(disabled):
    item = {"chunk_id": "img", "chunk_type": "image", "content": "图片标题：图10-2 奖助学金审批",
            "metadata": {"asset_id": "asset", "caption": "图10-2 奖助学金审批", "figure_ids": ["figure:10-2"],
                         "deterministic_binding_allowed": True, "skip_embedding_recommended": disabled,
                         "page_no": 2}}
    payload = build_evidence_groups_payload({"results": [item]}, {})
    if disabled:
        assert not payload["media"]
    else:
        assert payload["media"][0]["page_no"] == 2
        assert payload["media"][0]["figure_ids"] == ["figure:10-2"]


@pytest.mark.parametrize("blocks", [
    [image(), text(page=3)],
    [image(), text("## 下一节", order=1), text()],
    [image(), text(), image(order=3)],
    [image(), text(), text("图10-3 其他流程", order=3)],
    [image(parser="other"), text()],
    [image(bbox=(0, 0, 100, 100)), text(bbox=(500, 0, 600, 20))],
])
def test_unproved_caption_candidates_are_not_forced(blocks):
    asset = first_asset(blocks)
    assert not asset.figure_ids and not asset.deterministic_binding_allowed


def test_valid_parser_caption_is_not_overwritten():
    asset = first_asset([image(caption="图10-1 明确图题"), text()])
    assert asset.figure_ids == ["figure:10-1"] and asset.caption_method == "parser_caption"


@pytest.mark.parametrize("caption", ["Figure 2", "Fig. 2", "图2"])
def test_numbered_figure_caption_is_not_a_generic_placeholder(caption):
    relation = resolve_image_relation(caption=caption)
    assert relation.caption == caption and relation.figure_ids == ["figure:2"]


def test_generic_neighbor_caption_is_not_bound():
    asset = first_asset([image(), text("PDF 图片", is_caption=True)])
    assert not asset.caption and not asset.deterministic_binding_allowed


def test_exact_figure_query_preserves_kb_and_enable_filters():
    body = keywords.build_bm25_search_body("kb", keywords.prepare_bm25_query("图10-2说明什么"), 5)
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"knowledge_base_id": "kb"}} in filters
    assert {"term": {"is_enabled": True}} in filters
    alternatives = body["query"]["bool"]["must"][0]["bool"]["should"]
    assert alternatives[1]["terms"]["keywords.keyword"] == ["figure:10-2"]


@pytest.mark.parametrize("admit_caption,allowed,expected", [(True, True, True), (False, True, False), (True, False, False)])
def test_figure_keyword_respects_content_admission(monkeypatch, admit_caption, allowed, expected):
    monkeypatch.setattr(keywords, "build_content_admission", lambda *args: SimpleNamespace(
        content="说明", index_sources=("caption",) if admit_caption else (), channel_text=lambda source: "图10-2 奖助学金" if source == "caption" else ""))
    chunk = SimpleNamespace(id="c", knowledge_id="doc", chunk_index=0, chunk_type="image", parent_chunk_id=None,
        status="ready", is_enabled=True, context_header="", metadata_json={
            "figure_ids": ["figure:10-2"], "deterministic_binding_allowed": allowed})
    document = SimpleNamespace(id="doc", knowledge_base_id="kb", title="", file_name="a.pdf")
    result = keywords.chunk_to_es_doc(chunk, document)
    assert ("figure:10-2" in result["keywords"]) is expected
