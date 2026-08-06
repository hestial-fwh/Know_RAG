"""Unit tests for heading_utils.py — section heading detection, caption detection,
page noise detection, and heading path formatting."""

import pytest

from app.services.chunker.heading_utils import (
    heading_path_to_header,
    is_caption_text,
    is_page_noise_text,
    looks_like_section_heading,
    make_continuation_header,
    normalize_context_header,
    normalized_heading_text,
    strip_continuation_suffix,
)


# ──────────────────────────────────────────────────────────────────────
# is_caption_text
# ──────────────────────────────────────────────────────────────────────

class TestIsCaptionText:
    def test_chinese_figure_caption(self):
        assert is_caption_text("图1 实验流程图") is True
        assert is_caption_text("图 2-1 结果") is True
        assert is_caption_text("图表3 示意图") is True

    def test_english_figure_caption(self):
        assert is_caption_text("Figure 1. Experimental setup") is True
        assert is_caption_text("Fig.2 Results") is True

    def test_chinese_table_caption(self):
        assert is_caption_text("表1 实验数据") is True
        assert is_caption_text("表 2-3 统计结果") is True

    def test_english_table_caption(self):
        assert is_caption_text("Table 1. Experimental data") is True

    def test_continuation_table_caption(self):
        assert is_caption_text("续表2") is True
        assert is_caption_text("续表 3") is True
        assert is_caption_text("表2（续）") is True
        assert is_caption_text("表 4（续）") is True
        assert is_caption_text("Continued Table 2") is True

    def test_continuation_with_html_sub_tag(self):
        """MinerU output like '续表 <sub>2</sub>' should be detected."""
        assert is_caption_text("续表 <sub>2</sub>") is True

    def test_normal_text_is_not_caption(self):
        assert is_caption_text("这是一段普通文本") is False
        assert is_caption_text("实验结果分析") is False

    def test_long_text_is_not_caption(self):
        long_text = "这是一个很长的文本" * 20  # > 260 chars
        assert is_caption_text(long_text) is False

    def test_empty_text(self):
        assert is_caption_text("") is False
        assert is_caption_text(None) is False


# ──────────────────────────────────────────────────────────────────────
# is_page_noise_text
# ──────────────────────────────────────────────────────────────────────

class TestIsPageNoiseText:
    def test_page_number_patterns(self):
        assert is_page_noise_text("Page 3 of 15") is True
        assert is_page_noise_text("第 5 页") is True
        assert is_page_noise_text("42") is True  # bare number

    def test_journal_boilerplate(self):
        assert is_page_noise_text("第 35 卷 第 2 期") is True

    def test_normal_text_is_not_page_noise(self):
        assert is_page_noise_text("实验结果") is False
        assert is_page_noise_text("Introduction") is False

    def test_overflow_text_is_not_page_noise(self):
        long_text = "这是一个很长的内容" * 10  # > 80 chars
        assert is_page_noise_text(long_text) is False


# ──────────────────────────────────────────────────────────────────────
# looks_like_section_heading
# ──────────────────────────────────────────────────────────────────────

class TestLooksLikeSectionHeading:
    def test_markdown_heading(self):
        assert looks_like_section_heading("## 实验方法") is True
        assert looks_like_section_heading("# Introduction") is True
        # Empty heading not meaningful
        assert looks_like_section_heading("# ") is False

    def test_chinese_numbered_section(self):
        assert looks_like_section_heading("第一章 绪论") is True
        assert looks_like_section_heading("第3章 实验部分") is True

    def test_dotted_numbered_section(self):
        assert looks_like_section_heading("2.1 实验材料") is True
        assert looks_like_section_heading("3.2.1 数据分析方法") is True

    def test_chinese_enum_heading(self):
        # Chinese enumeration headings with explicit whitespace separator.
        assert looks_like_section_heading("一、 引言") is True
        assert looks_like_section_heading("二、 结果与讨论") is True

    def test_english_section(self):
        assert looks_like_section_heading("Chapter 1 Introduction") is True
        assert looks_like_section_heading("section 2.3 Methods") is True

    def test_all_caps_heading(self):
        assert looks_like_section_heading("RESULTS AND DISCUSSION") is True
        assert looks_like_section_heading("MATERIALS AND METHODS") is True

    def test_parser_heading_metadata(self):
        assert looks_like_section_heading("任意内容", metadata={"is_heading": True}) is True

    def test_parser_raw_type_title(self):
        assert looks_like_section_heading("任意内容", raw_type="title") is True

    def test_parser_raw_type_heading(self):
        assert looks_like_section_heading("任意内容", raw_type="heading") is True

    def test_section_title_raw_type(self):
        assert looks_like_section_heading("任意内容", raw_type="section_title") is True

    def test_caption_is_not_heading(self):
        assert looks_like_section_heading("图1 实验流程图") is False
        assert looks_like_section_heading("Table 1 Results") is False

    def test_page_number_is_not_heading(self):
        assert looks_like_section_heading("42") is False
        assert looks_like_section_heading("Page 3") is False

    def test_sentence_like_text_is_not_heading(self):
        """OCR fragments that end with sentence markers are not headings."""
        assert looks_like_section_heading("实验结果如图1所示。") is False

    def test_long_text_is_not_heading(self):
        long_text = "A" * 200  # > 160 chars
        assert looks_like_section_heading(long_text) is False

    def test_pure_digits_are_not_heading(self):
        assert looks_like_section_heading("12345") is False


# ──────────────────────────────────────────────────────────────────────
# normalized_heading_text
# ──────────────────────────────────────────────────────────────────────

class TestNormalizedHeadingText:
    def test_strips_and_sanitizes(self):
        result = normalized_heading_text("  标题文本  ")
        assert result == "标题文本"

    def test_empty_text(self):
        assert normalized_heading_text("") == ""
        assert normalized_heading_text(None) == ""


# ──────────────────────────────────────────────────────────────────────
# strip_continuation_suffix / make_continuation_header
# ──────────────────────────────────────────────────────────────────────

class TestContinuationHeader:
    def test_strips_single_continuation(self):
        assert strip_continuation_suffix("表2（续）") == "表2"

    def test_strips_multiple_continuations(self):
        assert strip_continuation_suffix("表2（续）（续）") == "表2"

    def test_no_continuation_returns_original(self):
        assert strip_continuation_suffix("正常标题") == "正常标题"

    def test_make_continuation_header_adds_suffix(self):
        assert make_continuation_header("表2") == "表2（续）"

    def test_make_continuation_header_deduplicates(self):
        result = make_continuation_header("表2（续）")
        assert result == "表2（续）"  # Only one continuation marker


# ──────────────────────────────────────────────────────────────────────
# normalize_context_header
# ──────────────────────────────────────────────────────────────────────

class TestNormalizeContextHeader:
    def test_collapses_duplicate_continuation(self):
        result = normalize_context_header("表2（续）（续）")
        assert result == "表2（续）" or result.count("（续）") == 1

    def test_normal_header_unchanged(self):
        assert normalize_context_header("实验结果") == "实验结果"

    def test_empty_header(self):
        assert normalize_context_header("") == ""
        assert normalize_context_header(None) == ""


# ──────────────────────────────────────────────────────────────────────
# heading_path_to_header
# ──────────────────────────────────────────────────────────────────────

class TestHeadingPathToHeader:
    def test_list_path_joined_with_arrows(self):
        result = heading_path_to_header(["第一章", "实验方法", "色谱条件"])
        assert " > " in result
        assert "第一章" in result
        assert "色谱条件" in result

    def test_tuple_path(self):
        result = heading_path_to_header(("A", "B", "C"))
        assert "A > B > C" == result

    def test_string_path(self):
        assert heading_path_to_header("单一标题") == "单一标题"

    def test_empty_path(self):
        assert heading_path_to_header([]) == ""
        assert heading_path_to_header(None) == ""

    def test_mixed_empty_elements(self):
        result = heading_path_to_header(["第一章", "", "   ", "结论"])
        assert "第一章" in result
        assert "结论" in result
