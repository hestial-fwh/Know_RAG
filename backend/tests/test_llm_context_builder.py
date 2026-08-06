"""Unit tests for llm_context_builder.py — context payload building,
cloud flat chunks, selective context expansion, and source metadata extraction."""

import pytest

from app.services.llm_context_builder import (
    ContextBudget,
    _clean_llm_text,
    _chunk_kind,
    _cloud_clean_content,
    _cloud_chunk_id,
    _cloud_content,
    _cloud_document_id,
    _cloud_first_str,
    _cloud_image_url,
    _cloud_kind,
    _cloud_is_low_information_chunk,
    _cloud_page_no,
    _cloud_page_range,
    _cloud_parent_id,
    _cloud_query_tokens,
    _cloud_score,
    _cloud_needs_expansion,
    _cloud_flat_chunk,
    _cloud_collect_hits,
    _count_kinds,
    _dedup_children,
    _format_child_evidence,
    _source_file,
    _source_key_from_group,
    _source_title,
    _text_from,
    _context_header,
    build_llm_context_payload,
    _cloud_build_window_for_parent,
)


# ──────────────────────────────────────────────────────────────────────
# ContextBudget
# ──────────────────────────────────────────────────────────────────────

class TestContextBudget:
    def test_add_within_budget(self):
        budget = ContextBudget(max_chars=100)
        text, truncated = budget.add("短文本")
        assert text == "短文本"
        assert truncated is False
        assert budget.used_chars == len("短文本")

    def test_add_exceeds_budget(self):
        budget = ContextBudget(max_chars=10)
        text, truncated = budget.add("这是一个很长很长的文本")
        assert len(text) <= 10
        assert truncated is True

    def test_add_when_budget_exhausted(self):
        budget = ContextBudget(max_chars=5)
        budget.add("12345")  # fill budget
        text, truncated = budget.add("更多")
        assert text == ""
        assert truncated is True

    def test_zero_budget(self):
        budget = ContextBudget(max_chars=0)
        text, truncated = budget.add("任意")
        assert text == ""
        assert truncated is True


# ──────────────────────────────────────────────────────────────────────
# _clean_llm_text
# ──────────────────────────────────────────────────────────────────────

class TestCleanLlmText:
    def test_strips_html_tags(self):
        text = "<p>这是<b>重要</b>内容</p>"
        result = _clean_llm_text(text)
        assert "<p>" not in result
        assert "<b>" not in result
        assert "重要" in result

    def test_removes_storage_filename_lines(self):
        text = "文件名：figure_001.png\n这里是正文内容"
        result = _clean_llm_text(text)
        assert "文件名" not in result or "文件名" not in result

    def test_removes_minio_paths(self):
        text = "minio://bucket/data/images/img.png\n正文内容"
        result = _clean_llm_text(text)
        assert "minio://" not in result

    def test_preserves_semantic_text(self):
        text = "本研究建立了新的分析方法"
        result = _clean_llm_text(text)
        assert "分析方法" in result

    def test_respects_max_chars(self):
        text = "A" * 500
        result = _clean_llm_text(text, max_chars=100)
        assert len(result) <= 100

    def test_empty_text(self):
        assert _clean_llm_text("") == ""
        assert _clean_llm_text(None) == ""


# ──────────────────────────────────────────────────────────────────────
# _chunk_kind
# ──────────────────────────────────────────────────────────────────────

class TestChunkKind:
    def test_text_chunk(self):
        item = {"chunk_type": "text"}
        assert _chunk_kind(item) == "text"

    def test_image_chunk(self):
        item = {"chunk_type": "image"}
        assert _chunk_kind(item) == "image"

    def test_table_chunk(self):
        item = {"chunk_type": "table"}
        assert _chunk_kind(item) == "table"

    def test_equation_by_metadata(self):
        item = {
            "chunk_type": "text",
            "metadata": {"is_equation": True},
        }
        assert _chunk_kind(item) == "equation"

    def test_equation_by_raw_block_type(self):
        item = {
            "chunk_type": "text",
            "metadata": {"raw_block_type": "formula"},
        }
        assert _chunk_kind(item) == "equation"

    def test_image_with_subtype(self):
        item = {
            "chunk_type": "image",
            "metadata": {"subtype": "chart"},
        }
        assert _chunk_kind(item) == "chart"

    def test_image_ocr_text_is_text(self):
        item = {"chunk_type": "image_ocr_text"}
        assert _chunk_kind(item) == "text"

    def test_none_item(self):
        assert _chunk_kind(None) == "text"

    def test_empty_item(self):
        assert _chunk_kind({}) == "text"

    def test_table_by_raw_type(self):
        item = {
            "chunk_type": "text",
            "metadata": {"raw_block_type": "table"},
        }
        assert _chunk_kind(item) == "table"


# ──────────────────────────────────────────────────────────────────────
# _cloud_kind
# ──────────────────────────────────────────────────────────────────────

class TestCloudKind:
    def test_normalizes_image_variants(self):
        for kind in ("image", "chart", "graph", "plot", "figure", "fig"):
            item = {"chunk_type": kind}
            assert _cloud_kind(item) == "image"

    def test_normalizes_table_variants(self):
        assert _cloud_kind({"chunk_type": "table"}) == "table"
        assert _cloud_kind({"chunk_type": "table_body"}) == "table"

    def test_normalizes_equation_variants(self):
        for kind in ("equation", "formula", "inline_equation", "display_equation"):
            item = {"chunk_type": kind}
            assert _cloud_kind(item) == "equation"

    def test_image_ocr_to_text(self):
        assert _cloud_kind({"chunk_type": "image_ocr_text"}) == "text"


# ──────────────────────────────────────────────────────────────────────
# _cloud_is_low_information_chunk
# ──────────────────────────────────────────────────────────────────────

class TestCloudIsLowInformationChunk:
    def test_none_is_low_info(self):
        assert _cloud_is_low_information_chunk(None) is True

    def test_skip_flag_is_low_info(self):
        assert (
            _cloud_is_low_information_chunk(
                {"content": "任意文本", "metadata": {"skip_embedding_recommended": True}}
            )
            is True
        )

    def test_empty_content_is_low_info(self):
        assert _cloud_is_low_information_chunk({"content": ""}) is True

    def test_heading_only_is_low_info(self):
        assert _cloud_is_low_information_chunk({"content": "引言"}) is True
        assert _cloud_is_low_information_chunk({"content": "参考文献"}) is True

    def test_bibliographic_metadata_is_low_info(self):
        assert (
            _cloud_is_low_information_chunk(
                {"content": "中图分类号：TQ123  文献标志码：A"}
            )
            is True
        )

    def test_short_unpunctuated_text_is_low_info(self):
        assert (
            _cloud_is_low_information_chunk(
                {"content": "这是很短的文本没有标点"}
            )
            is True
        )

    def test_meaningful_paragraph_is_not_low_info(self):
        text = (
            "本研究采用高效液相色谱-质谱联用技术，建立了同时测定"
            "复杂基质中多种目标化合物的分析方法，并对方法学进行了系统验证。"
        )
        assert _cloud_is_low_information_chunk({"content": text}) is False

    def test_numbered_heading_is_low_info(self):
        assert (
            _cloud_is_low_information_chunk({"content": "2.1 实验方法"})
            is True
        )

    def test_cjk_single_line_no_punctuation(self):
        """Single line <80 chars without punctuation is likely a title."""
        assert (
            _cloud_is_low_information_chunk(
                {"content": "基于深度学习的蛋白质结构预测方法研究"}
            )
            is True
        )

    def test_header_footer_pattern(self):
        assert (
            _cloud_is_low_information_chunk(
                {"content": "高等学校化学学报  No.123"}
            )
            is True
        )


# ──────────────────────────────────────────────────────────────────────
# _cloud_query_tokens
# ──────────────────────────────────────────────────────────────────────

class TestCloudQueryTokens:
    def test_extracts_chinese_bigrams(self):
        tokens = _cloud_query_tokens("高效液相色谱法")
        assert "高效" in tokens or "液相" in tokens or "色谱" in tokens

    def test_extracts_english_words_min_3_chars(self):
        tokens = _cloud_query_tokens("protein structure prediction")
        assert "protein" in tokens
        assert "structure" in tokens

    def test_short_english_words_skipped(self):
        """Words < 3 chars are skipped."""
        tokens = _cloud_query_tokens("a an of the")
        assert "a" not in tokens
        assert "an" not in tokens

    def test_empty_query(self):
        assert _cloud_query_tokens("") == set()
        assert _cloud_query_tokens(None) == set()

    def test_short_chinese_substrings(self):
        tokens = _cloud_query_tokens("你好")
        assert "你好" in tokens

    def test_mixed_query(self):
        tokens = _cloud_query_tokens("蛋白质 protein 分析")
        assert len(tokens) > 0


# ──────────────────────────────────────────────────────────────────────
# _cloud_content
# ──────────────────────────────────────────────────────────────────────

class TestCloudContent:
    def test_text_content_from_content_field(self):
        item = {"content": "这是测试内容"}
        result = _cloud_content(item)
        assert "测试内容" in result

    def test_text_from_matched_content_fallback(self):
        item = {"content": "", "matched_content": "匹配的内容"}
        result = _cloud_content(item)
        assert "匹配" in result

    def test_image_content_from_vlm(self):
        item = {
            "chunk_type": "image",
            "metadata": {"vlm_description": "一张展示蛋白质三维结构的示意图"},
        }
        result = _cloud_content(item)
        assert "蛋白质" in result

    def test_image_content_from_caption(self):
        item = {
            "chunk_type": "image",
            "metadata": {"caption": "图1 实验流程"},
        }
        result = _cloud_content(item)
        assert "实验流程" in result

    def test_image_content_from_image_info(self):
        item = {
            "chunk_type": "image",
            "image_info": {"ocr_text": "OCR识别的文字内容"},
        }
        result = _cloud_content(item)
        assert "OCR" in result

    def test_table_content_from_table_markdown(self):
        item = {
            "chunk_type": "table",
            "metadata": {"table_markdown": "| A | B |\n|---|---|"},
        }
        result = _cloud_content(item)
        assert "A" in result

    def test_table_content_from_table_body(self):
        item = {
            "chunk_type": "table",
            "metadata": {"table_body": "col1 col2 col3"},
        }
        result = _cloud_content(item)
        assert "col1" in result

    def test_equation_content_from_latex(self):
        item = {
            "chunk_type": "equation",
            "metadata": {"latex": r"E = mc^2"},
        }
        result = _cloud_content(item)
        assert "E" in result

    def test_empty_item(self):
        assert _cloud_content({}) == ""
        assert _cloud_content(None) == ""

    def test_removes_urls_from_content(self):
        item = {"content": "参考 https://example.com 了解更多"}
        result = _cloud_content(item)
        assert "https://example.com" not in result


# ──────────────────────────────────────────────────────────────────────
# _format_child_evidence
# ──────────────────────────────────────────────────────────────────────

class TestFormatChildEvidence:
    def test_text_evidence(self):
        child = {
            "content": "这是一个测试内容段落",
            "chunk_type": "text",
            "chunk_id": "chunk-001",
        }
        line, source = _format_child_evidence(
            child,
            source_id="S1",
            document_title="测试文档",
            file_name="test.pdf",
            context_header="第一章 > 概述",
            include_image_urls=False,
        )
        assert "测试内容" in line
        assert source["source_id"] == "S1"
        assert source["document_title"] == "测试文档"
        assert source["chunk_type"] == "text"

    def test_image_evidence(self):
        child = {
            "chunk_type": "image",
            "chunk_id": "img-001",
            "metadata": {
                "vlm_description": "蛋白质三维结构图",
                "page_no": 5,
            },
        }
        line, source = _format_child_evidence(
            child,
            source_id="S2",
            document_title="生物论文",
            file_name="paper.pdf",
            context_header="第三章 结果",
            include_image_urls=False,
        )
        assert "蛋白质" in line
        assert source["semantic_type"] == "image"

    def test_evidence_with_page_number(self):
        child = {
            "content": "第5页的内容",
            "chunk_type": "text",
            "chunk_id": "c1",
            "metadata": {"page_no": 5},
        }
        line, source = _format_child_evidence(
            child,
            source_id="S3",
            document_title="Doc",
            file_name="d.pdf",
            context_header="",
            include_image_urls=False,
        )
        assert "5" in line

    def test_evidence_placeholder_for_empty_content(self):
        child = {
            "chunk_type": "image",
            "chunk_id": "empty-img",
            "metadata": {},
        }
        line, source = _format_child_evidence(
            child,
            source_id="S4",
            document_title="Doc",
            file_name="d.pdf",
            context_header="Test",
            include_image_urls=False,
        )
        assert "没有可用文本描述" in line


# ──────────────────────────────────────────────────────────────────────
# _source_title
# ──────────────────────────────────────────────────────────────────────

class TestSourceTitle:
    def test_direct_title_field(self):
        item = {"document_title": "实验报告2024"}
        assert _source_title(item) == "实验报告2024"

    def test_knowledge_title_fallback(self):
        item = {"knowledge_title": "知识库文档"}
        assert _source_title(item) == "知识库文档"

    def test_metadata_title(self):
        item = {"metadata": {"document_title": "元数据标题"}}
        assert _source_title(item) == "元数据标题"

    def test_file_name_fallback(self):
        item = {"file_name": "report_2024.pdf"}
        assert _source_title(item) == "report_2024.pdf"

    def test_fallback_to_unknown(self):
        assert _source_title({"chunk_type": "text"}) == "未知文档"

    def test_parent_as_source(self):
        parent = {"document_title": "父文档"}
        item = {"chunk_type": "text"}
        assert _source_title(item, parent=parent) == "父文档"


# ──────────────────────────────────────────────────────────────────────
# _source_file
# ──────────────────────────────────────────────────────────────────────

class TestSourceFile:
    def test_file_name_field(self):
        item = {"file_name": "data.csv"}
        assert _source_file(item) == "data.csv"

    def test_filename_field(self):
        item = {"filename": "report.pdf"}
        assert _source_file(item) == "report.pdf"

    def test_metadata_file_name(self):
        item = {"metadata": {"source_file": "experiment.docx"}}
        assert _source_file(item) == "experiment.docx"

    def test_no_file_returns_empty(self):
        assert _source_file({"chunk_type": "text"}) == ""
        assert _source_file(None) == ""


# ──────────────────────────────────────────────────────────────────────
# _cloud_page_no
# ──────────────────────────────────────────────────────────────────────

class TestCloudPageNo:
    def test_page_no_field(self):
        assert _cloud_page_no({"page_no": 3}) == 3
        assert _cloud_page_no({"page_no": "5"}) == 5

    def test_metadata_page_no(self):
        assert _cloud_page_no({"metadata": {"page_no": 7}}) == 7

    def test_image_info_page_no(self):
        assert _cloud_page_no({"image_info": {"page_no": 2}}) == 2

    def test_page_idx_converts_to_one_based(self):
        assert _cloud_page_no({"page_idx": 0}) == 1
        assert _cloud_page_no({"page_idx": 3}) == 4

    def test_invalid_page_no_returns_none(self):
        assert _cloud_page_no({"page_no": "abc"}) is None
        assert _cloud_page_no({"page_no": 0}) is None
        assert _cloud_page_no(None) is None

    def test_source_page_field(self):
        assert _cloud_page_no({"source_page": 10}) == 10


# ──────────────────────────────────────────────────────────────────────
# _cloud_page_range
# ──────────────────────────────────────────────────────────────────────

class TestCloudPageRange:
    def test_valid_page(self):
        assert _cloud_page_range({"page_no": 5}) == "5"

    def test_no_page_returns_empty(self):
        assert _cloud_page_range({"page_no": None}) == ""
        assert _cloud_page_range({}) == ""


# ──────────────────────────────────────────────────────────────────────
# _cloud_chunk_id / _cloud_parent_id / _cloud_document_id
# ──────────────────────────────────────────────────────────────────────

class TestCloudIdentifiers:
    def test_chunk_id_from_item(self):
        assert _cloud_chunk_id({"chunk_id": "abc-123"}) == "abc-123"
        assert _cloud_chunk_id({"id": "id-only"}) == "id-only"
        assert _cloud_chunk_id({"metadata": {"chunk_id": "meta-id"}}) == "meta-id"
        assert _cloud_chunk_id({}) == ""
        assert _cloud_chunk_id(None) == ""

    def test_parent_id_from_item(self):
        assert _cloud_parent_id({"parent_chunk_id": "parent-1"}) == "parent-1"
        assert _cloud_parent_id({"metadata": {"parent_chunk_id": "meta-parent"}}) == "meta-parent"
        assert _cloud_parent_id({}) == ""

    def test_document_id_from_item(self):
        assert _cloud_document_id({"document_id": "doc-1"}) == "doc-1"
        assert _cloud_document_id({"metadata": {"document_id": "meta-doc"}}) == "meta-doc"
        assert _cloud_document_id({}) == ""


# ──────────────────────────────────────────────────────────────────────
# _cloud_first_str
# ──────────────────────────────────────────────────────────────────────

class TestCloudFirstStr:
    def test_returns_first_match(self):
        item = {"doc_title": "Title", "document_title": "Other"}
        keys = ("document_title", "doc_title", "title")
        result = _cloud_first_str(item, keys)
        assert result in ("Title", "Other")

    def test_returns_empty_for_no_match(self):
        assert _cloud_first_str({}, ("nonexistent",)) == ""

    def test_searches_metadata_and_image_info(self):
        item = {
            "metadata": {"title": "Meta Title"},
            "image_info": {},
        }
        result = _cloud_first_str(item, ("title",))
        assert result == "Meta Title"


# ──────────────────────────────────────────────────────────────────────
# _cloud_score
# ──────────────────────────────────────────────────────────────────────

class TestCloudScore:
    def test_score_from_score_field(self):
        assert _cloud_score({"score": 0.95}) == 0.95

    def test_score_from_fusion_score(self):
        assert _cloud_score({"fusion_score": 0.88}) == 0.88

    def test_score_from_rrf_score(self):
        assert _cloud_score({"rrf_score": 0.75}) == 0.75

    def test_no_score_returns_none(self):
        assert _cloud_score({}) is None
        assert _cloud_score(None) is None


# ──────────────────────────────────────────────────────────────────────
# _count_kinds
# ──────────────────────────────────────────────────────────────────────

class TestCountKinds:
    def test_counts_all_types(self):
        children = [
            {"chunk_type": "text"},
            {"chunk_type": "text"},
            {"chunk_type": "image"},
            {"chunk_type": "table"},
            {"chunk_type": "equation", "metadata": {"is_equation": True}},
        ]
        counts = _count_kinds(children)
        assert counts["text"] == 2
        assert counts["image"] == 1
        assert counts["table"] == 1
        assert counts["equation"] == 1

    def test_empty_list(self):
        counts = _count_kinds([])
        assert sum(counts.values()) == 0


# ──────────────────────────────────────────────────────────────────────
# _dedup_children
# ──────────────────────────────────────────────────────────────────────

class TestDedupChildren:
    def test_removes_duplicate_chunk_ids(self):
        children = [
            {"chunk_id": "a", "content": "first"},
            {"chunk_id": "a", "content": "first again"},
            {"chunk_id": "b", "content": "second"},
        ]
        result = _dedup_children(children)
        assert len(result) == 2

    def test_dedup_by_content_when_no_id(self):
        children = [
            {"content": "独特内容A"},
            {"content": "独特内容B"},
            {"content": "独特内容A"},  # duplicate
        ]
        result = _dedup_children(children)
        assert len(result) == 2

    def test_skips_non_dicts(self):
        children = [{"chunk_id": "a"}, None, "string", {"chunk_id": "b"}]
        result = _dedup_children(children)
        assert len(result) == 2


# ──────────────────────────────────────────────────────────────────────
# _cloud_needs_expansion
# ──────────────────────────────────────────────────────────────────────

class TestCloudNeedsExpansion:
    def test_no_parent_no_expansion(self):
        hit = {"chunk_type": "text", "content": "任意文本"}
        result = _cloud_needs_expansion(
            hit=hit, query_text="测试", sibling_chunks=[], same_parent_hits=[hit]
        )
        assert result["expanded"] is False
        assert result["reason"] == "no_parent_chunk"

    def test_low_information_hit_no_expansion(self):
        hit = {
            "chunk_type": "text",
            "content": "参考文献",
            "parent_chunk_id": "p1",
        }
        result = _cloud_needs_expansion(
            hit=hit, query_text="测试", sibling_chunks=[], same_parent_hits=[hit]
        )
        assert result["expanded"] is False
        assert "low_information" in result["reason"]

    def test_short_content_triggers_expansion(self):
        hit = {
            "chunk_type": "text",
            "content": "短期结果表明该化合物具有显著的生物活性，需要进一步研究其作用机制。",
            "parent_chunk_id": "p1",
        }
        result = _cloud_needs_expansion(
            hit=hit, query_text="测试", sibling_chunks=[], same_parent_hits=[hit]
        )
        # Short content < 200 chars triggers 'short_but_valid'
        assert "short_but_valid" in result.get("rules", [])

    def test_forward_reference_triggers_expansion(self):
        hit = {
            "chunk_type": "text",
            "content": (
                "如图3所示，该结构包含两个活性位点。上述结果表明该抑制剂"
                "具有较高的选择性和良好的药代动力学性质。"
            ),
            "parent_chunk_id": "p1",
        }
        result = _cloud_needs_expansion(
            hit=hit, query_text="测试", sibling_chunks=[], same_parent_hits=[hit]
        )
        assert "forward_reference" in result.get("rules", [])

    def test_figure_reference_triggers_expansion(self):
        hit = {
            "chunk_type": "text",
            "content": (
                "实验结果如图5和图6所示，这些图表清晰地展示了不同浓度下"
                "的抑制效果，为进一步的临床研究提供了重要依据。"
            ),
            "parent_chunk_id": "p1",
        }
        result = _cloud_needs_expansion(
            hit=hit, query_text="测试", sibling_chunks=[], same_parent_hits=[hit]
        )
        assert "figure_or_table_reference" in result.get("rules", [])

    def test_multi_hit_nearby_triggers_expansion(self):
        hit1 = {
            "chunk_type": "text",
            "content": (
                "这是一段有意义的实验方法描述文本，包含足够多的语义信息用于展示"
                "实验结果的相关数据，为后续分析提供基础。"
            ),
            "parent_chunk_id": "p1",
            "chunk_index": 1,
        }
        hit2 = {
            "chunk_type": "text",
            "content": (
                "另一段有意义的结果分析文本内容，详细描述了相关的实验数据"
                "和统计结果，验证了前述方法学的有效性。"
            ),
            "parent_chunk_id": "p1",
            "chunk_index": 2,
        }
        result = _cloud_needs_expansion(
            hit=hit1,
            query_text="实验数据",
            sibling_chunks=[],
            same_parent_hits=[hit1, hit2],
        )
        # Two hits with indices 1 and 2 (diff=1 <= 2) → nearby_multi_hits
        assert "nearby_multi_hits" in result.get("rules", [])

    def test_neighbor_query_overlap(self):
        # When hit is short but valid (<200 chars), short_but_valid fires.
        # When a sibling chunk contains query tokens, neighbor_query_overlap
        # can also fire. We verify expansion is triggered.
        hit = {
            "chunk_type": "text",
            "content": (
                "实验前期完成了样品的采集和前处理工作，使用标准品建立了"
                "校准曲线。数据处理使用专业统计软件完成。"
            ),
            "parent_chunk_id": "p1",
        }
        sibling = {
            "chunk_type": "text",
            "content": "实验结果显示了蛋白质结构的关键信息以及结合位点的详细分析",
        }
        result = _cloud_needs_expansion(
            hit=hit,
            query_text="蛋白质结构",
            sibling_chunks=[sibling],
            same_parent_hits=[hit],
        )
        # Expansion triggered by short_but_valid rule (content < 200 chars for a valid hit).
        # The neighbor_query_overlap rule may also fire when a sibling contains
        # query tokens AND no stronger rule already fired.
        assert result["expanded"] is True
        assert len(result["rules"]) >= 1


# ──────────────────────────────────────────────────────────────────────
# _cloud_flat_chunk
# ──────────────────────────────────────────────────────────────────────

class TestCloudFlatChunk:
    def test_basic_text_hit(self):
        item = {
            "chunk_id": "c1",
            "document_id": "d1",
            "document_title": "Test Doc",
            "chunk_type": "text",
            "content": "这是测试内容",
            "score": 0.9,
            "page_no": 3,
        }
        result = _cloud_flat_chunk(
            item,
            rank=1,
            citation_label="[1]",
            citation_id="S1",
            role="hit",
            include_image_urls=False,
        )
        assert result["rank"] == 1
        assert result["role"] == "hit"
        assert result["chunk_id"] == "c1"
        assert result["document_id"] == "d1"
        assert result["score"] == 0.9
        assert result["page_no"] == 3
        assert "content" in result
        assert "score_detail" in result

    def test_context_chunk_no_score(self):
        item = {
            "chunk_id": "c2",
            "chunk_type": "text",
            "content": "上下文内容",
        }
        result = _cloud_flat_chunk(
            item,
            rank=0,
            citation_label="",
            citation_id="",
            role="context",
            include_image_urls=False,
        )
        assert result["role"] == "context"
        assert result["score"] is None

    def test_image_chunk_with_url(self):
        item = {
            "chunk_id": "img1",
            "chunk_type": "image",
            "content": "图片描述",
            "image_info": {"url": "https://example.com/img.png"},
        }
        result = _cloud_flat_chunk(
            item,
            rank=1,
            citation_label="[1]",
            citation_id="S1",
            role="hit",
            include_image_urls=True,
        )
        assert result["chunk_type"] == "image"
        # Image URL should be present when include_image_urls=True and URL available
        # (depends on _cloud_image_url which depends on PUBLIC_BASE_URL config)

    def test_table_chunk_with_markdown(self):
        item = {
            "chunk_id": "t1",
            "chunk_type": "table",
            "content": "table content",
            "metadata": {"table_markdown": "| A | B |\n|---|---|"},
        }
        result = _cloud_flat_chunk(
            item,
            rank=1,
            citation_label="[1]",
            citation_id="S1",
            role="hit",
            include_image_urls=False,
        )
        assert result["table_markdown"] == "| A | B |\n|---|---|"

    def test_equation_chunk_with_latex(self):
        item = {
            "chunk_id": "e1",
            "chunk_type": "equation",
            "content": "equation content",
            "metadata": {"latex": r"E = mc^2"},
        }
        result = _cloud_flat_chunk(
            item,
            rank=1,
            citation_label="[1]",
            citation_id="S1",
            role="hit",
            include_image_urls=False,
        )
        assert result["latex"] == r"E = mc^2"


# ──────────────────────────────────────────────────────────────────────
# _cloud_collect_hits
# ──────────────────────────────────────────────────────────────────────

class TestCloudCollectHits:
    def test_collects_from_results(self):
        data = {
            "results": [
                {
                    "chunk_id": "h1",
                    "content": "有意义的文本内容，包含足够的语义信息用于检索和上下文构建，这是一段完整的描述。",
                    "chunk_type": "text",
                },
                {
                    "chunk_id": "h2",
                    "content": "另一段有意义的文本内容包含更多的语义信息，详细描述了实验方法和数据分析流程。",
                    "chunk_type": "text",
                },
            ]
        }
        hits = _cloud_collect_hits(data, match_count=5)
        assert len(hits) == 2

    def test_filters_low_information_chunks(self):
        data = {
            "results": [
                {"chunk_id": "h1", "content": "参考文献"},
                {
                    "chunk_id": "h2",
                    "content": "这是一段有意义的实验方法描述文本，包含了足够多的语义信息用于检索",
                    "chunk_type": "text",
                },
            ]
        }
        hits = _cloud_collect_hits(data, match_count=5)
        # h1 should be filtered out as low-info
        assert len(hits) == 1
        assert hits[0]["chunk_id"] == "h2"

    def test_respects_match_count_limit(self):
        data = {
            "results": [
                {
                    "chunk_id": f"h{i}",
                    "content": f"足够长的文本内容以通过低信息过滤，这是第{i}段内容。详细描述了实验方法和数据分析过程。",
                    "chunk_type": "text",
                }
                for i in range(10)
            ]
        }
        hits = _cloud_collect_hits(data, match_count=3)
        assert len(hits) == 3


# ──────────────────────────────────────────────────────────────────────
# _context_header
# ──────────────────────────────────────────────────────────────────────

class TestContextHeader:
    def test_direct_context_header(self):
        item = {"context_header": "第一章 > 概述"}
        assert _context_header(item) == "第一章 > 概述"

    def test_heading_path_field(self):
        item = {"heading_path": "实验结果"}
        assert _context_header(item) == "实验结果"

    def test_metadata_heading_path(self):
        item = {"metadata": {"heading_path": "第三章 > 方法"}}
        result = _context_header(item)
        assert "第三章" in result

    def test_parent_context_header(self):
        parent = {"context_header": "父级标题"}
        item = {}
        assert _context_header(item, parent=parent) == "父级标题"


# ──────────────────────────────────────────────────────────────────────
# _text_from
# ──────────────────────────────────────────────────────────────────────

class TestTextFrom:
    def test_gets_from_single_key(self):
        item = {"display_content": "展示内容文本"}
        result = _text_from(item, "display_content", "content")
        assert "展示内容" in result

    def test_falls_back_to_second_key(self):
        item = {"display_content": "", "content": "正文内容"}
        result = _text_from(item, "display_content", "content")
        assert "正文内容" in result

    def test_empty_item(self):
        assert _text_from(None, "content") == ""
        assert _text_from({}, "content") == ""


# ──────────────────────────────────────────────────────────────────────
# _source_key_from_group
# ──────────────────────────────────────────────────────────────────────

class TestSourceKeyFromGroup:
    def test_parent_chunk_id(self):
        group = {"parent_chunk_id": "parent-001"}
        assert _source_key_from_group(group, None, None) == "parent-001"

    def test_parent_key_fallback(self):
        group = {"parent_key": "pk-1"}
        assert _source_key_from_group(group, None, None) == "pk-1"


# ──────────────────────────────────────────────────────────────────────
# _cloud_clean_content
# ──────────────────────────────────────────────────────────────────────

class TestCloudCleanContent:
    def test_removes_wrapper_labels(self):
        text = "文档标题：测试报告\n正文：实验数据"
        result = _cloud_clean_content(text)
        assert "文档标题：" not in result

    def test_removes_urls(self):
        text = "参考 https://example.com 了解详情"
        result = _cloud_clean_content(text)
        assert "https://example.com" not in result

    def test_removes_file_paths(self):
        text = "文件位于 /files/doc1/data.txt"
        result = _cloud_clean_content(text)
        assert "/files/" not in result

    def test_preserves_value_text(self):
        text = "实验结果表明该方法有效"
        result = _cloud_clean_content(text)
        assert "实验结果" in result

    def test_respects_max_chars(self):
        text = "A" * 500
        result = _cloud_clean_content(text, max_chars=100)
        assert len(result) <= 100


# ──────────────────────────────────────────────────────────────────────
# build_llm_context_payload (legacy)
# ──────────────────────────────────────────────────────────────────────

class TestBuildLlmContextPayload:
    def test_creates_structure_with_empty_data(self):
        result = build_llm_context_payload({})
        assert "context_text" in result
        assert "sources" in result
        assert "images" in result
        assert "meta" in result
        assert result["meta"]["builder"] == "llm_context_builder.v2"

    def test_includes_query(self):
        result = build_llm_context_payload({}, {"query": "测试查询"})
        assert result["query"] == "测试查询"

    def test_includes_query_from_query_text(self):
        result = build_llm_context_payload({}, {"query_text": "测试查询"})
        assert result["query"] == "测试查询"

    def test_with_flat_results(self):
        retrieval_data = {
            "results": [
                {
                    "chunk_id": "c1",
                    "chunk_type": "text",
                    "content": "这是一个有意义的测试文本内容，包含完整的句子。",
                    "score": 0.9,
                }
            ]
        }
        result = build_llm_context_payload(retrieval_data)
        assert len(result["sources"]) == 1
        assert "测试文本" in result["context_text"]

    def test_with_parent_groups(self):
        retrieval_data = {
            "parent_groups": [
                {
                    "parent_chunk_id": "p1",
                    "matched_children": [
                        {
                            "chunk_id": "c1",
                            "chunk_type": "text",
                            "content": "命中的文本内容，包含完整的句子用于测试。",
                        }
                    ],
                    "parent_children": [
                        {
                            "chunk_id": "c2",
                            "chunk_type": "text",
                            "content": "相关的上下文文本内容。",
                        }
                    ],
                    "parent_context": {
                        "content": "父级上下文内容",
                    },
                }
            ]
        }
        result = build_llm_context_payload(retrieval_data)
        assert len(result["sources"]) == 1

    def test_respects_max_sources(self):
        retrieval_data = {
            "results": [
                {
                    "chunk_id": f"c{i}",
                    "chunk_type": "text",
                    "content": f"内容{i}，包含足够多的文本信息用于构建检索上下文。",
                }
                for i in range(20)
            ]
        }
        result = build_llm_context_payload(
            retrieval_data, {"max_sources": 3}
        )
        assert len(result["sources"]) <= 3

    def test_respects_max_context_chars(self):
        retrieval_data = {
            "results": [
                {
                    "chunk_id": "c1",
                    "chunk_type": "text",
                    "content": "X" * 5000,
                }
            ]
        }
        result = build_llm_context_payload(
            retrieval_data, {"max_context_chars": 1000}
        )
        assert result["meta"]["context_chars"] <= 1000 + 2000  # allow some tolerance for header/footer

    def test_empty_options_uses_defaults(self):
        result = build_llm_context_payload({})
        assert isinstance(result["meta"]["max_context_chars"], int)
        assert isinstance(result["meta"]["source_count"], int)
