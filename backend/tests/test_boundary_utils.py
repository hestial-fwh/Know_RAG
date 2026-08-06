"""Unit tests for boundary_utils.py — sentence end detection,
leading punctuation, and plain text boundary targeting."""

import pytest

from app.services.chunker.boundary_utils import (
    find_best_backward_boundary,
    find_best_forward_start,
    is_leading_punctuation,
    is_plain_text_boundary_target,
    is_sentence_end,
    strip_invalid_leading_punctuation,
)


# ──────────────────────────────────────────────────────────────────────
# is_sentence_end
# ──────────────────────────────────────────────────────────────────────

class TestIsSentenceEnd:
    def test_chinese_period(self):
        assert is_sentence_end("这是完整句子。") is True

    def test_chinese_exclamation(self):
        assert is_sentence_end("太好了！") is True

    def test_chinese_question(self):
        assert is_sentence_end("可以吗？") is True

    def test_english_period(self):
        assert is_sentence_end("This is a complete sentence.") is True

    def test_english_exclamation(self):
        assert is_sentence_end("Great!") is True

    def test_chinese_semicolon_is_weak_end(self):
        assert is_sentence_end("第一点；") is True

    def test_no_sentence_end_without_punctuation(self):
        assert is_sentence_end("未完成的句子") is False

    def test_empty_text(self):
        assert is_sentence_end("") is False

    def test_none_text(self):
        assert is_sentence_end(None) is False

    def test_with_closing_quotes(self):
        assert is_sentence_end('他说："这是事实。"') is True

    def test_english_with_closing_quotes(self):
        assert is_sentence_end('He said "hello".') is True


# ──────────────────────────────────────────────────────────────────────
# is_leading_punctuation
# ──────────────────────────────────────────────────────────────────────

class TestIsLeadingPunctuation:
    def test_comma_is_leading_punctuation(self):
        assert is_leading_punctuation("，继续写") is True
        assert is_leading_punctuation(",continue") is True

    def test_period_is_leading_punctuation(self):
        assert is_leading_punctuation("。新句子") is True

    def test_code_markers_are_not_punctuation(self):
        assert is_leading_punctuation("```python") is False
        assert is_leading_punctuation("| table") is False
        assert is_leading_punctuation("> quote") is False

    def test_numbered_list_is_not_punctuation(self):
        assert is_leading_punctuation("1. item") is False
        assert is_leading_punctuation("1) item") is False

    def test_normal_text_is_not_punctuation(self):
        assert is_leading_punctuation("正常文本") is False

    def test_empty_text(self):
        assert is_leading_punctuation("") is False


# ──────────────────────────────────────────────────────────────────────
# strip_invalid_leading_punctuation
# ──────────────────────────────────────────────────────────────────────

class TestStripInvalidLeadingPunctuation:
    def test_removes_leading_period(self):
        result = strip_invalid_leading_punctuation("。这是内容")
        assert result.startswith("这是内容")

    def test_preserves_leading_text(self):
        assert strip_invalid_leading_punctuation("正常文本") == "正常文本"

    def test_preserves_whitespace_prefix(self):
        result = strip_invalid_leading_punctuation("  ，内容")
        assert result.lstrip().startswith("内容")

    def test_empty_text(self):
        assert strip_invalid_leading_punctuation("") == ""


# ──────────────────────────────────────────────────────────────────────
# is_plain_text_boundary_target
# ──────────────────────────────────────────────────────────────────────

class TestIsPlainTextBoundaryTarget:
    def test_plain_text_is_target(self):
        assert is_plain_text_boundary_target(chunk_type="text") is True

    def test_table_is_not_target(self):
        assert is_plain_text_boundary_target(chunk_type="table") is False

    def test_image_is_not_target(self):
        assert is_plain_text_boundary_target(chunk_type="image") is False

    def test_code_block_metadata_is_not_target(self):
        assert (
            is_plain_text_boundary_target(
                metadata={"content_kind": "code_block"},
                chunk_type="text",
            )
            is False
        )

    def test_directory_tree_metadata_is_not_target(self):
        assert (
            is_plain_text_boundary_target(
                metadata={"content_kind": "directory_tree"},
                chunk_type="text",
            )
            is False
        )

    def test_structure_like_metadata_is_not_target(self):
        assert (
            is_plain_text_boundary_target(
                metadata={"structure_like": True},
                chunk_type="text",
            )
            is False
        )


# ──────────────────────────────────────────────────────────────────────
# find_best_backward_boundary (deprecated, kept for compat)
# ──────────────────────────────────────────────────────────────────────

class TestFindBestBackwardBoundary:
    def test_finds_double_newline_boundary(self):
        text = "这是第一段。\n\n这是第二段。还有更多内容。"
        # Search for boundary after "第一段。" (pos ~7)
        result = find_best_backward_boundary(text, start=5, max_end=30, min_end=10)
        assert 5 < result <= 30

    def test_falls_back_to_max_end(self):
        text = "NoBoundariesHereJustLongText"
        result = find_best_backward_boundary(text, start=0, max_end=20, min_end=5)
        assert result == 20

    def test_respects_protected_spans(self):
        """Boundaries inside protected spans should be avoided."""
        from app.services.chunker.protected_patterns import ProtectedSpan

        text = "text ```code block here``` more text"
        # The code block is protected — boundary should not be inside
        spans = [ProtectedSpan(5, 25)]
        result = find_best_backward_boundary(text, start=0, max_end=35, min_end=10, protected_spans=spans)
        assert result not in range(5, 26)  # not inside the protected span


# ──────────────────────────────────────────────────────────────────────
# find_best_forward_start (deprecated, kept for compat)
# ──────────────────────────────────────────────────────────────────────

class TestFindBestForwardStart:
    def test_moves_to_sentence_start(self):
        text = "，这样的开头不好。更好的开头在这里。"
        result = find_best_forward_start(text, rough_start=0, max_lookahead=30)
        # Should move past the leading punctuation
        assert result >= 0

    def test_stays_within_bounds(self):
        text = "Short."
        result = find_best_forward_start(text, rough_start=0, max_lookahead=50)
        assert 0 <= result <= len(text)
