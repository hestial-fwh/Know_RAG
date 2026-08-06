"""Unit tests for content_cleaning.py — text cleaning, low-information detection,
skip decisions, and OCR quarantine."""

from unittest.mock import MagicMock

import pytest

from app.services.content_cleaning import (
    METADATA_KEY_IMAGE_OCR_MODE,
    OCR_IMAGE_MODE_NORMAL,
    OCR_IMAGE_MODE_SHADOW_AUXILIARY,
    SKIP_REASON_VISUAL_OCR_QUARANTINE,
    _is_placeholder_media_content,
    clean_chunk_content_for_storage,
    clean_text_for_embedding,
    clear_embedding_skip_metadata,
    clear_legacy_visual_ocr_skip_flags,
    clear_stale_embedding_skip_flags,
    compact_text_for_label_check,
    field_content_admission_enabled,
    is_low_information_text,
    is_standalone_noise_text,
    is_url_or_path_only_text,
    is_visual_ocr_quarantined,
    looks_meaningful_for_embedding,
    merge_metadata_without_embedding_skip,
    normalize_chunk_text,
    normalize_extracted_rich_text,
    should_skip_text_chunk,
)


# ──────────────────────────────────────────────────────────────────────
# is_url_or_path_only_text
# ──────────────────────────────────────────────────────────────────────

class TestIsUrlOrPathOnlyText:
    def test_http_url_is_identified(self):
        assert is_url_or_path_only_text("https://example.com/doc.pdf") is True

    def test_local_file_path_is_identified(self):
        assert is_url_or_path_only_text("D:\\data\\images\\fig1.png") is True

    def test_media_filename_is_identified(self):
        assert is_url_or_path_only_text("screenshot_2024.png") is True

    def test_email_is_identified(self):
        assert is_url_or_path_only_text("admin@example.com") is True

    def test_minio_style_path_is_identified(self):
        assert is_url_or_path_only_text(
            "kb/a1b2c3d4-e5f6-7890-abcd-ef1234567890/documents/f1e2d3c4/fig1.jpg"
        ) is True

    def test_empty_string_is_url_like(self):
        assert is_url_or_path_only_text("") is True

    def test_none_is_url_like(self):
        assert is_url_or_path_only_text(None) is True

    def test_normal_paragraph_is_not_url(self):
        assert is_url_or_path_only_text("这是一段正常的文本内容") is False

    def test_long_text_over_300_chars_is_not_url(self):
        long_text = (
            "这是一个很长的段落" * 30
        )  # > 300 chars
        assert is_url_or_path_only_text(long_text) is False

    def test_url_inside_text_is_not_pure_url(self):
        assert is_url_or_path_only_text("请访问 https://example.com 查看详情") is False


# ──────────────────────────────────────────────────────────────────────
# is_low_information_text
# ──────────────────────────────────────────────────────────────────────

class TestIsLowInformationText:
    def test_empty_string(self):
        assert is_low_information_text("") is True

    def test_none_input(self):
        assert is_low_information_text(None) is True

    def test_two_char_text(self):
        assert is_low_information_text("ab") is True
        assert is_low_information_text("测试") is True

    def test_exact_low_info_heading(self):
        """引言/参考文献/结论 etc. should be low info."""
        for label in ["引言", "参考文献", "结论", "致谢", "摘要", "关键词", "讨论"]:
            assert is_low_information_text(label) is True, f"{label!r} should be low-info"

    def test_introduction_is_low_info(self):
        assert is_low_information_text("introduction") is True

    def test_section_like_heading_with_number(self):
        assert is_low_information_text("第一章 引言") is True

    def test_materials_and_methods(self):
        assert is_low_information_text("材料与方法") is True

    def test_experimental_methods(self):
        assert is_low_information_text("实验方法") is True

    def test_numbered_section_title_short(self):
        """Short numbered title like '3.1 实验方法' should be low-info."""
        assert is_low_information_text("3.1 实验方法") is True

    def test_numbered_section_title_too_long(self):
        """Longer numbered title should be treated carefully."""
        # 3.2 蛋白质结构域与信号通路分析 — 25 chars, is a section title
        # This is on the boundary; we check it's handled
        title = "3.2 蛋白质结构域与信号通路分析"
        # Should match _SECTION_TITLE_RE
        result = is_low_information_text(title)
        # This is a judgement call — it matches the numbered section pattern
        assert result is True  # Still low-info due to section title match

    def test_short_text_without_punctuation_is_low_info(self):
        """Text <30 chars without sentence punctuation is metadata fragment."""
        assert is_low_information_text("专利号 CN123456789A") is True
        assert is_low_information_text("第 3 页") is True
        assert is_low_information_text("2024年12月") is True

    def test_short_text_with_punctuation_is_not_low_info(self):
        """Text <30 chars WITH sentence punctuation is potentially meaningful."""
        assert is_low_information_text("结果如图3所示。") is False
        assert is_low_information_text("P < 0.05，差异显著。") is False

    def test_long_enough_text_is_not_low_info(self):
        """Text >=30 chars with content should not be low-info."""
        text = "本研究采用高效液相色谱法测定样品中的多酚含量并进行了方法学验证"
        assert len(text) >= 30
        assert is_low_information_text(text) is False

    def test_chinese_section_starts_with_number_dot(self):
        """Section titles like '2.3.1 色谱条件' are low-info."""
        assert is_low_information_text("2.3.1 色谱条件") is True

    def test_english_all_caps_heading_like(self):
        """Short ALL CAPS text that looks like a heading."""
        # "RESULTS AND DISCUSSION" is 22 chars, no CJK punctuation
        result = is_low_information_text("RESULTS AND DISCUSSION")
        # It's >=30? No, 22 chars. But it's English, not CJK.
        # The <30 no-punctuation rule catches it
        assert result is True


# ──────────────────────────────────────────────────────────────────────
# compact_text_for_label_check
# ──────────────────────────────────────────────────────────────────────

class TestCompactTextForLabelCheck:
    def test_removes_whitespace(self):
        assert compact_text_for_label_check("图 片 标 题") == "图片标题"

    def test_removes_colon_and_period(self):
        assert compact_text_for_label_check("：测试：") == "测试"

    def test_normal_text(self):
        assert compact_text_for_label_check("正常文本内容") == "正常文本内容"

    def test_empty_input(self):
        assert compact_text_for_label_check("") == ""
        assert compact_text_for_label_check(None) == ""


# ──────────────────────────────────────────────────────────────────────
# clean_chunk_content_for_storage
# ──────────────────────────────────────────────────────────────────────

class TestCleanChunkContentForStorage:
    def test_removes_display_labels(self):
        """Labels like 文档标题：/ 文件名：/ 图片URL： should be stripped."""
        text = "文档标题：实验报告\n正文：这是实验数据"
        cleaned = clean_chunk_content_for_storage(text)
        assert "文档标题" not in cleaned
        assert "实验数据" in cleaned or "正文" in cleaned

    def test_removes_url_lines(self):
        text = "图片URL：https://example.com/img.png\n正文：有效内容"
        cleaned = clean_chunk_content_for_storage(text)
        assert "https://example.com" not in cleaned

    def test_preserves_body_text(self):
        text = "正文：本研究采用高效液相色谱法"
        cleaned = clean_chunk_content_for_storage(text)
        assert "高效液相色谱法" in cleaned

    def test_empty_input_returns_empty(self):
        assert clean_chunk_content_for_storage("") == ""
        assert clean_chunk_content_for_storage(None) == ""

    def test_only_label_lines_returns_empty(self):
        text = "文档标题：报告\n文件名：test.pdf\n图片URL：http://example.com/img.png"
        cleaned = clean_chunk_content_for_storage(text)
        assert cleaned == "" or "报告" in cleaned  # context-dependent

    def test_keeps_table_content_label_value(self):
        """表格内容：xxx should keep the value."""
        text = "表格内容：\n| A | B |\n|---|---|"
        cleaned = clean_chunk_content_for_storage(text)
        assert "A" in cleaned

    def test_collapses_excess_newlines(self):
        text = "第一段\n\n\n\n\n第二段"
        cleaned = clean_chunk_content_for_storage(text)
        assert "\n\n\n\n" not in cleaned


# ──────────────────────────────────────────────────────────────────────
# normalize_chunk_text
# ──────────────────────────────────────────────────────────────────────

class TestNormalizeChunkText:
    def test_removes_cjk_spaces(self):
        """Spaces between CJK characters should be removed."""
        assert "高效液相" in normalize_chunk_text("高效 液相")

    def test_normalizes_line_endings(self):
        text = "第一行\r\n第二行\r第三行"
        result = normalize_chunk_text(text)
        assert "\r" not in result
        assert "\n" in result or "第一行" in result

    def test_trims_and_sanitizes(self):
        assert normalize_chunk_text("  文本内容  ") == "文本内容"


# ──────────────────────────────────────────────────────────────────────
# normalize_extracted_rich_text
# ──────────────────────────────────────────────────────────────────────

class TestNormalizeExtractedRichText:
    def test_strips_html_tags_from_prose(self):
        text = "TGF-β<sub>1</sub> signaling pathway"
        result = normalize_extracted_rich_text(text, chunk_type="text")
        assert "<sub>" not in result
        assert "TGF-β1" in result or "signaling" in result

    def test_preserves_fenced_code(self):
        text = "说明文字\n```python\ndef foo():\n    pass\n```\n后续内容"
        result = normalize_extracted_rich_text(text, chunk_type="text")
        assert "```python" in result
        assert "def foo" in result

    def test_preserves_code_in_code_block_type(self):
        text = "<div>content</div>"
        result = normalize_extracted_rich_text(text, chunk_type="code_block")
        # Code block type should preserve XML/tag content
        assert "<div>" in result or "div" in result

    def test_collapses_multiple_newlines(self):
        text = "段落一\n\n\n\n段落二"
        result = normalize_extracted_rich_text(text)
        assert "\n\n\n\n" not in result

    def test_normalizes_unicode_spaces(self):
        text = "正文 内容  "
        result = normalize_extracted_rich_text(text)
        assert " " not in result


# ──────────────────────────────────────────────────────────────────────
# clean_text_for_embedding
# ──────────────────────────────────────────────────────────────────────

class TestCleanTextForEmbedding:
    def test_removes_urls(self):
        text = "参考 https://example.com/doc 了解更多"
        cleaned = clean_text_for_embedding(text)
        assert "https://example.com" not in cleaned

    def test_removes_file_paths(self):
        text = "文件位于 /files/doc1/chunk_001.txt"
        cleaned = clean_text_for_embedding(text)
        assert "/files/" not in cleaned

    def test_preserves_semantic_text(self):
        text = "本研究建立了新的分析方法"
        cleaned = clean_text_for_embedding(text)
        assert "分析方法" in cleaned


# ──────────────────────────────────────────────────────────────────────
# should_skip_text_chunk
# ──────────────────────────────────────────────────────────────────────

class TestShouldSkipTextChunk:
    def test_empty_content_should_skip(self):
        assert should_skip_text_chunk("") is True

    def test_url_only_should_skip(self):
        assert should_skip_text_chunk("https://example.com/file.pdf") is True

    def test_low_information_heading_should_skip(self):
        assert should_skip_text_chunk("参考文献") is True

    def test_meaningful_text_should_not_skip(self):
        text = "实验结果表明该方法具有良好的线性关系（R² > 0.999），可以满足实际样品分析要求。"
        assert should_skip_text_chunk(text) is False

    def test_trusted_skip_reason_placeholder_table(self):
        assert (
            should_skip_text_chunk(
                "",
                chunk_type="table",
                flags={"skip_embedding_recommended": True},
                metadata={"skip_reason": "placeholder_table"},
            )
            is True
        )

    def test_trusted_skip_reason_placeholder_image(self):
        assert (
            should_skip_text_chunk(
                "",
                chunk_type="image",
                flags={"skip_embedding_recommended": True},
                metadata={"skip_reason": "placeholder_image"},
            )
            is True
        )

    def test_skip_without_reason_uses_content_check(self):
        """A skip flag without a trusted reason should re-check content."""
        # Empty content → skip
        assert (
            should_skip_text_chunk(
                "",
                flags={"skip_embedding_recommended": True},
            )
            is True
        )
        # Meaningful content → don't skip
        assert (
            should_skip_text_chunk(
                "有效文本内容包含足够的语义信息供检索使用，包括完整的实验结果。",
                flags={"skip_embedding_recommended": True},
            )
            is False
        )

    def test_table_with_placeholder_content_should_skip(self):
        assert should_skip_text_chunk("表格", chunk_type="table") is True
        assert should_skip_text_chunk("图", chunk_type="image") is True

    def test_table_with_real_content_should_not_skip(self):
        assert should_skip_text_chunk("| Name | Value |\n|------|-------|\n| A | 1 |", chunk_type="table") is False

    def test_image_with_vlm_description_should_not_skip(self):
        text = "这是一张展示蛋白质三维结构的示意图，图中α-螺旋用红色表示"
        assert should_skip_text_chunk(text, chunk_type="image") is False


# ──────────────────────────────────────────────────────────────────────
# looks_meaningful_for_embedding
# ──────────────────────────────────────────────────────────────────────

class TestLooksMeaningfulForEmbedding:
    def test_empty_is_not_meaningful(self):
        assert looks_meaningful_for_embedding("") is False

    def test_low_info_text_is_not_meaningful(self):
        assert looks_meaningful_for_embedding("参考文献") is False

    def test_long_prose_is_meaningful(self):
        text = (
            "本研究采用分子对接和分子动力学模拟方法，系统研究了配体与受体的相互作用机制，"
            "揭示了关键氨基酸残基在结合过程中的重要作用。"
        )
        assert looks_meaningful_for_embedding(text) is True

    def test_list_text_is_meaningful(self):
        text = (
            "1. 第一项内容包括详细的实验设计和分组方案\n"
            "2. 第二项内容包括数据采集和处理方法\n"
            "3. 第三项内容包括结果的统计分析与可视化"
        )
        assert looks_meaningful_for_embedding(text) is True

    def test_code_snippet_is_meaningful(self):
        text = '{"name": "test", "value": 42}'
        assert looks_meaningful_for_embedding(text) is True

    def test_filename_reference_is_meaningful(self):
        text = "请参考 main.py 中的实现以了解具体处理流程和调用参数配置"
        assert looks_meaningful_for_embedding(text) is True

    def test_table_chunk_with_content_is_meaningful(self):
        assert looks_meaningful_for_embedding("### 实验数据统计\n\n表中列出各组实验结果", chunk_type="table") is True

    def test_short_table_content_is_not_meaningful(self):
        assert looks_meaningful_for_embedding("abc", chunk_type="table") is False


# ──────────────────────────────────────────────────────────────────────
# is_standalone_noise_text
# ──────────────────────────────────────────────────────────────────────

class TestIsStandaloneNoiseText:
    def test_url_only_is_noise(self):
        assert is_standalone_noise_text("https://example.com/img.png") is True

    def test_heading_only_is_noise(self):
        assert is_standalone_noise_text("参考文献") is True

    def test_meaningful_text_is_not_noise(self):
        assert is_standalone_noise_text("实验结果详见下表。") is False


# ──────────────────────────────────────────────────────────────────────
# _is_placeholder_media_content
# ──────────────────────────────────────────────────────────────────────

class TestIsPlaceholderMediaContent:
    def test_chinese_placeholders(self):
        assert _is_placeholder_media_content("图片") is True
        assert _is_placeholder_media_content("表格") is True
        assert _is_placeholder_media_content("公式") is True
        assert _is_placeholder_media_content("图") is True

    def test_english_placeholders(self):
        assert _is_placeholder_media_content("image") is True
        assert _is_placeholder_media_content("figure") is True
        assert _is_placeholder_media_content("table") is True
        assert _is_placeholder_media_content("equation") is True

    def test_real_content_is_not_placeholder(self):
        assert _is_placeholder_media_content("图1 实验结果示意图") is False
        assert _is_placeholder_media_content("Table 1. Experimental Results") is False


# ──────────────────────────────────────────────────────────────────────
# clear_embedding_skip_metadata
# ──────────────────────────────────────────────────────────────────────

class TestClearEmbeddingSkipMetadata:
    def test_removes_skip_flags(self):
        meta = {
            "skip_embedding_recommended": True,
            "skip_reason": "orphan_short_chunk",
            "useful_key": "retained",
        }
        cleaned = clear_embedding_skip_metadata(meta)
        assert "skip_embedding_recommended" not in cleaned
        assert "skip_reason" not in cleaned
        assert cleaned["useful_key"] == "retained"

    def test_none_input_returns_empty_dict(self):
        assert clear_embedding_skip_metadata(None) == {}

    def test_empty_input_returns_empty_dict(self):
        assert clear_embedding_skip_metadata({}) == {}


# ──────────────────────────────────────────────────────────────────────
# merge_metadata_without_embedding_skip
# ──────────────────────────────────────────────────────────────────────

class TestMergeMetadataWithoutEmbeddingSkip:
    def test_merges_without_propagating_skip(self):
        base = {"page_no": 3, "skip_reason": "orphan_short_chunk"}
        extra = {"author": "test", "skip_embedding_recommended": True}
        merged = merge_metadata_without_embedding_skip(base, extra)
        assert "skip_reason" not in merged
        assert "skip_embedding_recommended" not in merged
        assert merged["page_no"] == 3
        assert merged["author"] == "test"


# ──────────────────────────────────────────────────────────────────────
# clear_stale_embedding_skip_flags
# ──────────────────────────────────────────────────────────────────────

class TestClearStaleEmbeddingSkipFlags:
    def test_clears_stale_skip_for_meaningful_content(self):
        flags, meta = clear_stale_embedding_skip_flags(
            content=(
                "本研究建立了完整的实验方法体系，包括样品前处理、色谱分离和"
                "质谱检测三个主要步骤。方法的线性范围、检出限和定量限均满足要求。"
            ),
            flags={"skip_embedding_recommended": True},
            metadata={"skip_reason": "orphan_short_chunk"},
        )
        assert "skip_embedding_recommended" not in flags
        assert "skip_reason" not in meta

    def test_preserves_trusted_skip_for_placeholder(self):
        flags, meta = clear_stale_embedding_skip_flags(
            content="图片",
            chunk_type="image",
            flags={"skip_embedding_recommended": True},
            metadata={"skip_reason": "placeholder_image"},
        )
        # Trusted skip reasons are NOT in STALE_SKIP_REASONS, so they persist
        # (this function only clears stale reasons)
        assert flags.get("skip_embedding_recommended") is True

    def test_no_skip_flag_is_noop(self):
        flags, meta = clear_stale_embedding_skip_flags(
            content="任意文本",
            flags={},
            metadata={},
        )
        assert flags == {}
        assert meta == {}


# ──────────────────────────────────────────────────────────────────────
# clear_legacy_visual_ocr_skip_flags
# ──────────────────────────────────────────────────────────────────────

class TestClearLegacyVisualOcrSkipFlags:
    def test_clears_legacy_visual_ocr_skip(self):
        flags, meta, recovered = clear_legacy_visual_ocr_skip_flags(
            flags={"skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE, "skip_embedding_recommended": True},
            metadata={"skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE},
        )
        assert "skip_reason" not in flags
        assert "skip_reason" not in meta
        assert recovered is True

    def test_leaves_other_reasons_intact(self):
        flags, meta, recovered = clear_legacy_visual_ocr_skip_flags(
            flags={"skip_reason": "placeholder_table"},
            metadata={"skip_reason": "placeholder_table"},
        )
        assert flags["skip_reason"] == "placeholder_table"
        assert recovered is False


# ──────────────────────────────────────────────────────────────────────
# is_visual_ocr_quarantined
# ──────────────────────────────────────────────────────────────────────

class TestIsVisualOcrQuarantined:
    def test_shadow_auxiliary_is_quarantined(self):
        assert (
            is_visual_ocr_quarantined(
                {METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
                enabled=True,
            )
            is True
        )

    def test_normal_mode_is_not_quarantined(self):
        assert (
            is_visual_ocr_quarantined(
                {METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_NORMAL},
                enabled=True,
            )
            is False
        )

    def test_disabled_flag_never_quarantines(self):
        assert (
            is_visual_ocr_quarantined(
                {METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
                enabled=False,
            )
            is False
        )

    def test_empty_metadata_is_safe(self):
        assert is_visual_ocr_quarantined(None, enabled=True) is False
        assert is_visual_ocr_quarantined({}, enabled=True) is False


# ──────────────────────────────────────────────────────────────────────
# field_content_admission_enabled
# ──────────────────────────────────────────────────────────────────────

class TestFieldContentAdmissionEnabled:
    def test_returns_boolean(self):
        result = field_content_admission_enabled()
        assert isinstance(result, bool)
