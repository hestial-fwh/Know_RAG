"""Tests for document_profiler._page_kind classification logic.

Covers two processing modes:
- visual_art (current full heuristic with pdfplumber signals)
- standard   (Planned — trust MinerU, no dominant_image_ratio)
"""

import pytest

from app.services.document_parser.document_profiler import _page_kind


class TestPageKindClassification:
    """Verify _page_kind correctly classifies pages across boundary scenarios."""

    # ── Baseline cases that must not regress ──

    def test_visual_dominant_calligraphy(self):
        """Calligraphy / pure-art page: no continuous prose, no native text
        layer, large embedded image → visual_dominant."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=1,
            dominant_image_ratio=0.85,
            canonical_text_chars=30,
            heading_count=0,
            non_heading_text_count=0,
            continuous_prose=False,
            native_text_chars=0,
            has_code=False,
        )
        assert kind == "visual_dominant"
        assert "visual_content_dominates_page" in reasons

    def test_scanned_prose_low_text(self):
        """Scanned document with little parser text: continuous prose but
        pdfplumber extracted zero native characters → scanned_prose."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=0.85,
            canonical_text_chars=80,
            heading_count=0,
            non_heading_text_count=3,
            continuous_prose=True,
            native_text_chars=0,
            has_code=False,
        )
        assert kind == "scanned_prose"
        assert "continuous_parser_prose_without_native_text" in reasons

    def test_pure_image_page(self):
        """Pure image page: no text at all → visual_dominant."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=1,
            dominant_image_ratio=0.90,
            canonical_text_chars=0,
            heading_count=0,
            non_heading_text_count=0,
            continuous_prose=False,
            native_text_chars=0,
            has_code=False,
        )
        assert kind == "visual_dominant"

    # ── Large-image pages in visual_art mode: 255ca0d-aligned ──
    #
    # visual_art mode has only ONE escape from visual_dominant:
    #   continuous_prose + native_text_chars == 0 → scanned_prose.
    # There are NO patent/document escapes — those belong in standard mode.
    # Pages with large embedded images are classified as visual_dominant
    # regardless of text volume.  This is the intended behavior for
    # calligraphy, art books, and visual collections.

    def test_patent_page_with_large_figure(self):
        """Page resembling an English patent (large figure + substantial
        continuous prose + native text layer) → visual_dominant in
        visual_art mode.  visual_art does not try to second-guess
        whether text or image is primary — the image wins.  Patent
        users should select standard mode at upload time."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=2,
            primary_visual_count=1,
            dominant_image_ratio=0.75,
            canonical_text_chars=1200,
            heading_count=0,
            non_heading_text_count=8,
            continuous_prose=True,
            native_text_chars=800,
            has_code=False,
        )
        assert kind == "visual_dominant"
        assert "page_primary_visual_present" in reasons
        assert "visual_content_dominates_page" in reasons

    def test_patent_page_no_primary_visual(self):
        """Large embedded image (ratio=0.70) + substantial text but
        no explicit primary_visual marker → visual_dominant in
        visual_art mode.  dominant_image_ratio >= 0.65 alone is
        sufficient to trigger."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=0.70,
            canonical_text_chars=400,
            heading_count=1,
            non_heading_text_count=6,
            continuous_prose=True,
            native_text_chars=500,
            has_code=False,
        )
        assert kind == "visual_dominant"
        assert "dominant_embedded_image" in reasons
        assert "visual_content_dominates_page" in reasons

    # ── Scanned prose in visual_art mode ──

    def test_chinese_patent_born_digital_no_native_text(self):
        """Born-digital PDF where pdfplumber cannot extract text (e.g. CJK
        embedded fonts) + continuous prose + zero native_text_chars →
        scanned_prose in visual_art mode.  This is the sole escape from
        visual_dominant in the 255ca0d-aligned heuristic."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=0.90,
            canonical_text_chars=1200,
            heading_count=1,
            non_heading_text_count=8,
            continuous_prose=True,
            native_text_chars=0,
            has_code=False,
        )
        assert kind == "scanned_prose"
        assert "continuous_parser_prose_without_native_text" in reasons

    def test_chinese_patent_fragmented_text(self):
        """Large image + fragmented parser text (continuous_prose=False) +
        primary_visual marker → visual_dominant in visual_art mode.
        No escape branch for fragmented text exists in visual_art."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=2,
            primary_visual_count=1,
            dominant_image_ratio=0.80,
            canonical_text_chars=350,
            heading_count=0,
            non_heading_text_count=5,
            continuous_prose=False,
            native_text_chars=50,
            has_code=False,
        )
        assert kind == "visual_dominant"
        assert "page_primary_visual_present" in reasons
        assert "visual_content_dominates_page" in reasons

    def test_chinese_patent_no_native_text_fragmented(self):
        """Large image + fragmented text + no native text layer →
        visual_dominant in visual_art mode.  continuous_prose=False
        so the scanned_prose escape does not apply."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=0.75,
            canonical_text_chars=400,
            heading_count=0,
            non_heading_text_count=4,
            continuous_prose=False,
            native_text_chars=0,
            has_code=False,
        )
        assert kind == "visual_dominant"
        assert "dominant_embedded_image" in reasons
        assert "visual_content_dominates_page" in reasons

    def test_calligraphy_page_not_escaped_by_continuous_prose(self):
        """Calligraphy page in visual_art mode: large embedded image,
        short descriptive text (~150 chars), native text layer present,
        continuous prose passes → must stay visual_dominant.

        Escape branch 2 (continuous_prose + native_text_chars > 0)
        requires canonical_text_chars >= 300.  A 150-char calligraphy
        label should NOT trigger the escape — the image IS the content.
        This is the regression test for the 2026-08-10 fix."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=1.0,
            canonical_text_chars=150,
            heading_count=0,
            non_heading_text_count=3,
            continuous_prose=True,
            native_text_chars=145,
            has_code=False,
        )
        assert kind == "visual_dominant"
        assert "dominant_embedded_image" in reasons
        assert "visual_content_dominates_page" in reasons
        assert "continuous_prose_with_visual_elements" not in reasons

    # ── Other page kinds ──

    def test_native_text_page(self):
        """Ordinary text page without visual dominance → native_text."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=0,
            primary_visual_count=0,
            dominant_image_ratio=0.0,
            canonical_text_chars=1200,
            heading_count=2,
            non_heading_text_count=15,
            continuous_prose=True,
            native_text_chars=1200,
            has_code=False,
        )
        assert kind == "native_text"
        assert "canonical_text_present" in reasons

    def test_slide_page(self):
        """Presentation slide: image + heading + short body → slide."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=2,
            primary_visual_count=0,
            dominant_image_ratio=0.30,
            canonical_text_chars=80,
            heading_count=1,
            non_heading_text_count=1,
            continuous_prose=False,
            native_text_chars=80,
            has_code=False,
        )
        assert kind == "slide"

    def test_table_dense_page(self):
        """Table-heavy page → table_dense (checked before visual logic)."""
        kind, reasons, score = _page_kind(
            table_dense=True,
            image_count=0,
            primary_visual_count=0,
            dominant_image_ratio=0.0,
            canonical_text_chars=200,
            heading_count=0,
            non_heading_text_count=3,
            continuous_prose=False,
            native_text_chars=200,
            has_code=False,
        )
        assert kind == "table_dense"
        assert "table_density_threshold_met" in reasons

    def test_unknown_page(self):
        """Page with insufficient evidence → unknown."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=0,
            primary_visual_count=0,
            dominant_image_ratio=0.0,
            canonical_text_chars=0,
            heading_count=0,
            non_heading_text_count=0,
            continuous_prose=False,
            native_text_chars=0,
            has_code=False,
        )
        assert kind == "unknown"
        assert "insufficient_page_evidence" in reasons


# ═══════════════════════════════════════════════════════════════════════
# Standard mode tests (Planned — processing_mode="standard")
#
# In standard mode, _page_kind trusts MinerU's signals exclusively:
#   visual_dominant = bool(primary_visual_count)
#
# dominant_image_ratio and native_text_chars are NOT consulted.
# scanned_prose detection is intentionally absent.
#
# These tests are marked xfail until the processing_mode parameter is
# added to _page_kind().
# ═══════════════════════════════════════════════════════════════════════

STANDARD_MODE_KWARGS = {"processing_mode": "standard"}


class TestStandardModeClassification:
    """_page_kind with processing_mode="standard" — trust MinerU,
    ignore pdfplumber's dominant_image_ratio and native_text_chars."""

    # ── Patent / technical documents: must NOT be misclassified ──

    def test_standard_patent_with_large_figure(self):
        """English patent page: large embedded figure, substantial prose,
        native text layer, NO primary_visual_count → native_text.

        In standard mode dominant_image_ratio=0.75 is irrelevant."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=2,
            primary_visual_count=0,
            dominant_image_ratio=0.75,
            canonical_text_chars=1200,
            heading_count=0,
            non_heading_text_count=8,
            continuous_prose=True,
            native_text_chars=800,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "native_text"
        assert "canonical_text_present" in reasons
        assert "visual_content_dominates_page" not in reasons

    def test_standard_chinese_patent_born_digital(self):
        """Born-digital Chinese patent: large embedded image (ratio=0.90),
        continuous prose, zero native_text_chars (pdfplumber can't read
        CJK embedded fonts), NO primary_visual_count → native_text.

        In the current heuristic this triggers scanned_prose → native_text
        escape.  In standard mode it should never enter the visual_dominant
        block in the first place."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=0.90,
            canonical_text_chars=1200,
            heading_count=1,
            non_heading_text_count=8,
            continuous_prose=True,
            native_text_chars=0,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "native_text"
        assert "canonical_text_present" in reasons
        # Must NOT leak visual_dominant or scanned_prose signals
        assert "dominant_embedded_image" not in reasons
        assert "continuous_parser_prose_without_native_text" not in reasons

    def test_standard_chinese_patent_fragmented_text(self):
        """Chinese patent: fragmented parser text (continuous_prose=False),
        moderate canonical_text_chars (350), native_text_chars=50,
        dominant_image_ratio=0.80, NO primary_visual_count → native_text.

        This is the case that required the third escape branch
        (canonical_text_chars >= 300) in the current heuristic.  In
        standard mode it should never enter visual_dominant at all."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=2,
            primary_visual_count=0,
            dominant_image_ratio=0.80,
            canonical_text_chars=350,
            heading_count=0,
            non_heading_text_count=5,
            continuous_prose=False,
            native_text_chars=50,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "native_text"
        assert "canonical_text_present" in reasons
        assert "visual_content_dominates_page" not in reasons
        assert "dominant_embedded_image" not in reasons

    # ── Primary visual (MinerU explicit marker) ──

    def test_standard_primary_visual_with_substantial_text(self):
        """page_primary_visual marker present BUT canonical_text_chars >= 300
        → native_text, NOT visual_dominant.

        The page_primary_visual tag can come from our own enricher
        (page_visual_enricher.py), not MinerU — the enricher blindly
        tags the largest image on every page where pdfplumber says
        "preserve".  When there is substantial parser text (>= 300
        chars), the page is a text page with a figure, not a truly
        visual-dominant page."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=2,
            primary_visual_count=1,
            dominant_image_ratio=0.95,
            canonical_text_chars=500,
            heading_count=0,
            non_heading_text_count=3,
            continuous_prose=True,
            native_text_chars=0,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "native_text"
        assert "page_primary_visual_present" in reasons
        assert "substantial_text_overrides_primary_visual" in reasons
        assert "visual_content_dominates_page" not in reasons

    def test_standard_primary_visual_no_text(self):
        """MinerU primary_visual with zero canonical text →
        visual_dominant (pure image page)."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=1,
            dominant_image_ratio=0.95,
            canonical_text_chars=0,
            heading_count=0,
            non_heading_text_count=0,
            continuous_prose=False,
            native_text_chars=0,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "visual_dominant"
        assert "page_primary_visual_present" in reasons

    # ── Normal text pages (baseline) ──

    def test_standard_normal_text(self):
        """Ordinary text page without any visual signals → native_text."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=0,
            primary_visual_count=0,
            dominant_image_ratio=0.0,
            canonical_text_chars=1200,
            heading_count=2,
            non_heading_text_count=15,
            continuous_prose=True,
            native_text_chars=1200,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "native_text"
        assert "canonical_text_present" in reasons

    # ── Pure image / empty pages ──

    def test_standard_pure_image_no_text_no_primary_visual(self):
        """Pure image page with zero text and no primary_visual_count →
        unknown.

        Without pdfplumber signals and without MinerU's explicit
        primary_visual marker, there is no evidence to classify this
        page as visual_dominant.  It falls through to unknown, which
        triggers the default structured-text-v1 policy.  The image
        asset is still materialized normally — it just doesn't get
        the visual-collection-v1 special treatment.

        This is an intentional trade-off: standard mode users don't
        upload art books.  If they do, they should use visual_art mode."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=0.90,
            canonical_text_chars=0,
            heading_count=0,
            non_heading_text_count=0,
            continuous_prose=False,
            native_text_chars=0,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "unknown"
        assert "insufficient_page_evidence" in reasons

    # ── Scanned document: intentionally not detected ──

    def test_standard_scanned_page_becomes_native_text(self):
        """Scanned document page (zero native_text_chars, continuous prose,
        no primary_visual_count) → native_text in standard mode.

        This is the primary trade-off of standard mode: without consulting
        native_text_chars, scanned_prose cannot be detected.  The page
        gets structured-text-v1 instead of scanned-prose-v1, losing page
        boundary locking.  For born-digital PDFs (the vast majority of
        uploads), this is correct behavior."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=0.85,
            canonical_text_chars=800,
            heading_count=0,
            non_heading_text_count=6,
            continuous_prose=True,
            native_text_chars=0,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "native_text"
        assert "canonical_text_present" in reasons
        assert "scanned_prose" not in kind

    # ── Table-dense and slide: unchanged in standard mode ──

    def test_standard_table_dense(self):
        """Table-dense pages are unaffected by processing_mode
        (checked before visual logic)."""
        kind, reasons, score = _page_kind(
            table_dense=True,
            image_count=0,
            primary_visual_count=0,
            dominant_image_ratio=0.0,
            canonical_text_chars=200,
            heading_count=0,
            non_heading_text_count=3,
            continuous_prose=False,
            native_text_chars=200,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "table_dense"
        assert "table_density_threshold_met" in reasons

    def test_standard_slide(self):
        """Slides are unaffected by processing_mode."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=2,
            primary_visual_count=0,
            dominant_image_ratio=0.30,
            canonical_text_chars=80,
            heading_count=1,
            non_heading_text_count=1,
            continuous_prose=False,
            native_text_chars=80,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "slide"

    # ── Edge case: low-text calligraphy-like page in standard mode ──

    def test_standard_low_text_calligraphy_like(self):
        """Page resembling calligraphy (30 chars fragmented text,
        dominant_image_ratio=0.85, no primary_visual_count, no headings)
        → native_text in standard mode (falls through slide check).

        This page has a small amount of text and a large image.  In
        visual_art mode it would be visual_dominant.  In standard mode
        it becomes native_text — the 30 characters of text are treated
        as normal page content, and the image is materialized as a
        regular asset chunk.

        This is the correct trade-off: standard mode users with this
        kind of page should switch to visual_art mode."""
        kind, reasons, score = _page_kind(
            table_dense=False,
            image_count=1,
            primary_visual_count=0,
            dominant_image_ratio=0.85,
            canonical_text_chars=30,
            heading_count=0,
            non_heading_text_count=1,
            continuous_prose=False,
            native_text_chars=0,
            has_code=False,
            **STANDARD_MODE_KWARGS,
        )
        assert kind == "native_text"
        assert "canonical_text_present" in reasons
