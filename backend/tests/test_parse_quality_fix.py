"""Tests for Fix7: OCR module isolation & layer-wise glue scores."""

import unittest
from types import SimpleNamespace

from app.plugins.parse_eval.diagnostics import (
    _chunk_layer_text,
    _template_joined_word_risk,
    chunk_strict_eval_text,
    chunk_recoverable_eval_text,
    diagnose_no_gt,
    identifier_detected_count,
    identifier_format_risk,
    infer_page_no_from_text,
    joined_word_risk,
    layer_glue_summary,
)
from app.services.chunk_service import _light_clean_ocr_text, _normalize_ocr_for_recovery, _ocr_layer_metadata
from app.services.document_parser.ragflow_format_adapter import _table_structure_assessment
from app.services.ocr import layered_ocr_metadata, normalize_identifiers, repair_text
from app.services.ocr.templates import get_template
from app.services.ocr.templates.scanned_contract_ocr import ScannedContractOcrNormalizer


class ParseQualityFixTests(unittest.TestCase):
    """Existing baseline tests (updated for module isolation)."""

    def test_normal_identifier_is_not_risk(self):
        text = "CN-ANCHOR-001-00 KB-2026-0608 INV-001-00"
        self.assertGreater(identifier_detected_count(text), 0)
        self.assertEqual(identifier_format_risk(text), 0)

    def test_broken_invoice_is_risk(self):
        self.assertGreater(identifier_format_risk("1NV-UU1-OO"), 0)

    def test_page_inference(self):
        self.assertEqual(infer_page_no_from_text("Raster-only OCR text line 020-32"), 20)
        self.assertEqual(infer_page_no_from_text("页面锚点 CN-ANCHOR-003-00"), 3)

    def test_key_value_rows_are_table(self):
        rows = [
            ["字段 Field", "值 Value", "说明 Note"],
            ["知识库", "KB-MIX-2026", "中文字段"],
            ["Parser", "RAGFlow", "English field"],
        ]
        kind, subtype, _, _ = _table_structure_assessment(rows, "字段 Field 值 Value 说明 Note", "")
        self.assertEqual(kind, "table")
        self.assertIn(subtype, {"key_value_table", "real_table"})

    def test_long_body_rows_are_not_table(self):
        rows = [["本页用于测试中文、英文、数字和标点混合的 PDF 解析效果。 页面锚点 CN-ANCHOR-001-00。"],
                ["RAGFlow parsing stress test paragraph with deliberately repeated anchors. This page contains section markers."]]
        kind, subtype, _, _ = _table_structure_assessment(rows, "\n".join(r[0] for r in rows), "")
        self.assertEqual(kind, "image")
        self.assertIn(subtype, {"pseudo_table", "figure_or_layout"})

    # ── Updated: generic clean does NOT fix template patterns ──────────

    def test_generic_clean_does_not_fix_scanned_patterns(self):
        """Generic _light_clean_ocr_text should NOT fix scanned-contract patterns."""
        raw = "Raster-onlyOCRtextline002-00:OCR-CANDIDATE,invoice numberINV-002-00,vector.indexcheck"
        cleaned = _light_clean_ocr_text(raw)
        # Generic repair only does CJK boundary + whitespace — NOT Raster-only fix
        self.assertNotIn("Raster-only OCR text line", cleaned)
        # But generic normalize_identifiers can recover INV
        normalized = _normalize_ocr_for_recovery(raw)
        self.assertIn("INV-002-00", normalized)

    def test_ocr_layering_content_not_overwritten(self):
        """OCR normalized text must not be used to overwrite chunk.content."""
        raw = "1NV-O06-O1"
        meta = _ocr_layer_metadata(raw, enabled=True)
        # Raw must be preserved exactly
        self.assertEqual(meta["ocr_raw_text"], raw)
        # Normalized should recover the identifier (via generic identifier normalizer)
        self.assertIn("INV-006-01", meta["ocr_normalized_text"])
        # Normalized and cleaned should differ
        self.assertNotEqual(meta.get("ocr_cleaned_text", ""), meta.get("ocr_normalized_text", ""))

    def test_page_inference_various_patterns(self):
        """page_no should be inferable from multiple text patterns."""
        self.assertEqual(infer_page_no_from_text("Noisy skewed scanned contract - raster page 011"), 11)
        self.assertEqual(infer_page_no_from_text("page 2/20"), 2)
        self.assertEqual(infer_page_no_from_text("第 003 页"), 3)
        self.assertEqual(infer_page_no_from_text("CN-ANCHOR-005-00"), 5)
        self.assertEqual(infer_page_no_from_text("EN-CN-MIX-007"), 7)
        self.assertEqual(infer_page_no_from_text("INV-020-32"), 20)
        self.assertEqual(infer_page_no_from_text("SCAN-CALLOUT-1-4"), 1)

    def test_cjk_english_mixed_prose_not_table(self):
        """CJK-heavy prose rows should not be classified as tables."""
        rows = [
            ["本政策文件旨在规范中英文混合文档的处理流程，确保所有扫描件必须经过 OCR 识别后进行质量评估，评估指标包括行号命中率、发票号命中率和页面识别率等多项关键指标。"],
            ["所有扫描件必须在入库前完成 OCR 识别和质量评估流程，包括但不限于：行号格式校验、发票号命中校验、Callout 框识别校验以及页面标题严格匹配等多个环节的逐项检查。"],
            ["评估指标体系涵盖行号命中率、发票号命中率、Callout 框识别率和页面识别率等核心指标，同时还包括关键词保留率、空格粘连检测、编号格式完整性等辅助质量评估维度。"],
        ]
        text = "\n".join(r[0] for r in rows)
        kind, subtype, _, _ = _table_structure_assessment(rows, text, "")
        self.assertEqual(kind, "image")
        self.assertIn(subtype, {"pseudo_table", "figure_or_layout"})

    def test_real_table_still_classified_correctly(self):
        """Short structured Field/Value/Note rows should still be table."""
        rows = [
            ["字段", "值", "说明"],
            ["知识库", "KB-001", "中文知识库"],
            ["状态", "active", "启用状态"],
        ]
        text = "字段 值 说明\n知识库 KB-001\n状态 active"
        kind, subtype, _, _ = _table_structure_assessment(rows, text, "")
        self.assertEqual(kind, "table")

    def test_strict_excludes_normalized_text(self):
        """Strict eval text should include content + raw OCR but not normalized."""
        chunk = SimpleNamespace(
            content="INV-001-00 line 001-00",
            context_header="",
            metadata_json={
                "ocr_raw_text": "1NV-001-00",
                "ocr_cleaned_text": "1NV-001-00",
                "ocr_normalized_text": "INV-001-00 line 001-00 recovered",
            },
            image_info={},
        )
        strict = chunk_strict_eval_text(chunk)
        recoverable = chunk_recoverable_eval_text(chunk)
        self.assertIn("INV-001-00", strict)
        self.assertIn("1NV-001-00", strict)
        self.assertNotIn("recovered", strict)
        self.assertIn("recovered", recoverable)

    def test_parent_text_skipped_in_quality_checks(self):
        """parent_text chunks should be skipped in diagnose_no_gt."""
        doc = SimpleNamespace(id="doc1", file_name="test.pdf", file_type="pdf", metadata_json={})
        parent_chunk = SimpleNamespace(
            id="p1", chunk_type="parent_text", content="same text here INV-001-00",
            metadata_json={}, image_info={}, is_enabled=True, chunk_index=0,
            context_header="", pre_chunk_id=None, next_chunk_id=None,
        )
        child_chunk = SimpleNamespace(
            id="c1", chunk_type="text", content="same text here INV-001-00",
            metadata_json={}, image_info={}, is_enabled=True, chunk_index=1,
            context_header="", pre_chunk_id=None, next_chunk_id=None,
        )
        summary, _, _ = diagnose_no_gt(doc, [parent_chunk, child_chunk])
        self.assertEqual(summary.get("parent_text_skipped", 0), 1)
        self.assertEqual(summary["chunk_count"], 2)


# ── Fix7: Module isolation tests ───────────────────────────────────────

class ModuleIsolationTests(unittest.TestCase):
    """Verify that generic code stays generic and template code stays isolated."""

    # ── Generic repair_text ────────────────────────────────────────

    def test_generic_repair_cjk_boundary(self):
        """CJK/English boundary spacing."""
        result = repair_text("中文English混排")
        self.assertIn("中文 English 混排", result)

    def test_generic_repair_no_template_patterns(self):
        """Template-specific patterns like Raster-only are NOT repaired."""
        result = repair_text("Raster-onlyOCRtextline")
        # Generic repair does NOT fix this — it only does boundary + whitespace
        self.assertIn("Raster-onlyOCRtextline", result)

    def test_generic_repair_whitespace_only(self):
        """Simple text passes through unchanged."""
        result = repair_text("Hello world. This is normal text.")
        self.assertEqual(result, "Hello world. This is normal text.")

    def test_generic_repair_invoice_untouched(self):
        """Generic repair does NOT do O/0 or I/1 substitutions."""
        result = repair_text("Invoice INV-O09-O1")
        self.assertNotIn("INV-009-01", result)
        self.assertIn("INV-O09-O1", result)

    # ── Generic normalize_identifiers ──────────────────────────────

    def test_normalize_inv_o09_o1(self):
        """INV-O09-O1 should be recoverable via generic identifier normalizer."""
        result = normalize_identifiers("INV-O09-O1")
        self.assertIn("INV-009-01", result)

    def test_normalize_inv_1nv(self):
        """1NV-O06-O1 should become INV-006-01."""
        result = normalize_identifiers("1NV-O06-O1")
        self.assertIn("INV-006-01", result)

    def test_normalize_does_not_affect_plain_english(self):
        """Plain English text should not be modified by identifier normalizer."""
        result = normalize_identifiers("OpenAI API")
        self.assertEqual(result, "OpenAI API")

    def test_normalize_invoice_text_not_affected(self):
        """The word 'Invoice' itself should not be modified."""
        result = normalize_identifiers("Invoice number")
        self.assertEqual(result, "Invoice number")

    def test_normalize_order_id_untouched(self):
        """Order IDs with clean characters should stay unchanged."""
        result = normalize_identifiers("Order-1234-56")
        self.assertIn("Order-1234-56", result)

    # ── Template vs generic isolation ──────────────────────────────

    def test_raster_only_fixable_only_by_template(self):
        """Raster-onlyOCRtextline should NOT be fixed by generic code."""
        generic_normalized = normalize_identifiers(repair_text("Raster-onlyOCRtextline"))
        self.assertNotIn("Raster-only OCR text line", generic_normalized)

    def test_raster_only_fixable_by_scanned_template(self):
        """Raster-onlyOCRtextline SHOULD be fixed by the scanned_contract template."""
        tmpl = get_template("scanned_contract_ocr")
        self.assertIsNotNone(tmpl)
        result = tmpl.repair_ocr("Raster-onlyOCRtextline")
        self.assertIn("Raster-only OCR text line", result)

    def test_template_id_isolation(self):
        """get_template returns None for unknown template."""
        self.assertIsNone(get_template("generic_no_gt"))
        self.assertIsNotNone(get_template("scanned_contract_ocr"))

    def test_layered_without_template(self):
        """layered_ocr_metadata without template_id should not fix template patterns."""
        meta = layered_ocr_metadata("Raster-onlyOCRtextline", enabled=True)
        self.assertIn("Raster-onlyOCRtextline", meta.get("ocr_cleaned_text", ""))
        self.assertIn("Raster-onlyOCRtextline", meta.get("ocr_normalized_text", ""))

    def test_layered_with_template(self):
        """layered_ocr_metadata with scanned_contract_ocr should fix template patterns in normalized."""
        meta = layered_ocr_metadata("Raster-onlyOCRtextline", enabled=True, template_id="scanned_contract_ocr")
        self.assertIn("Raster-onlyOCRtextline", meta.get("ocr_cleaned_text", ""))
        self.assertIn("Raster-only OCR text line", meta.get("ocr_normalized_text", ""))

    # ── Layer-wise glue scores ────────────────────────────────────

    def test_layer_glue_summary_has_keys(self):
        """layer_glue_summary returns expected keys."""
        chunk = SimpleNamespace(
            content="test content",
            metadata_json={"ocr_raw_text": "raw gluedtexthere", "ocr_cleaned_text": "clean text"},
            image_info={},
        )
        result = layer_glue_summary([chunk])
        for key in ("raw_glue_score", "cleaned_glue_score", "normalized_glue_score", "content_glue_score", "template_glue_score"):
            self.assertIn(key, result)

    def test_layer_glues_differentiate_raw_and_content(self):
        """Raw text with glued words should have higher glue score than content."""
        chunk = SimpleNamespace(
            content="clean text with proper spaces",
            metadata_json={
                "ocr_raw_text": "Raster-onlyOCRtextline002-00invoicenumbervectorindexcheck",
                "ocr_cleaned_text": "clean text with proper spaces",
            },
            image_info={},
        )
        result = layer_glue_summary([chunk])
        self.assertGreater(result["raw_glue_score"], result["content_glue_score"],
                           msg="raw glue should be higher than content glue")

    def test_template_glue_score(self):
        """Template-specific glue score is separate from generic."""
        text = "joined invoicenumber and rasterpage"
        generic = joined_word_risk(text)
        template_score = _template_joined_word_risk(text, "scanned_contract_ocr")
        # Generic should NOT match invoicenumber (template-specific)
        # Template SHOULD match it
        self.assertGreater(template_score, generic,
                           msg="template-specific patterns should not be in generic joined_word_risk")

    def test_chunk_layer_text(self):
        """_chunk_layer_text extracts text from chunk metadata."""
        chunk = SimpleNamespace(
            content="content",
            metadata_json={"ocr_raw_text": "raw value"},
            image_info={},
        )
        raw = _chunk_layer_text(chunk, "ocr_raw_text")
        self.assertEqual(raw, "raw value")

    def test_joined_word_risk_generic_only(self):
        """Generic joined_word_risk should not contain template patterns."""
        text = "invoicenumber is a template pattern"
        # Generic should have 0 matches for invoicenumber (does NOT contain letter runs of 30+)
        self.assertEqual(joined_word_risk(text), 0)


if __name__ == "__main__":
    unittest.main()
