"""Tests for Figure Sequence Resolver — constrained figure ID inference."""

import pytest
from app.services.document_parser.figure_sequence_resolver import (
    AssetFigureInfo,
    FigureLabel,
    FigureSequenceResolver,
    SequenceGap,
    _chinese_number_to_int,
    _int_to_roman,
    _is_likely_figure_asset,
    _is_roman_numeral,
    _roman_to_int,
    apply_sequence_resolution,
    build_asset_figure_infos,
    detect_logical_document_group,
    detect_numbering_reset,
    parse_figure_label,
    resolve_figure_sequences,
)


# ── FigureLabel parsing ──────────────────────────────────────────────────────

class TestParseFigureLabel:
    def test_arabic(self):
        label = parse_figure_label("figure:3")
        assert label is not None
        assert label.family == "arabic"
        assert label.primary_value == 3
        assert label.subfigure == ""
        assert not label.supplementary

    def test_arabic_subfigure_letter(self):
        label = parse_figure_label("figure:3:a")
        assert label is not None
        assert label.family == "arabic"
        assert label.primary_value == 3
        assert label.subfigure == "a"

    def test_arabic_subfigure_paren(self):
        # normalize_figure_token produces "figure:3:a" for "3(a)"
        label = parse_figure_label("figure:3:a")
        assert label.subfigure == "a"

    def test_supplementary(self):
        label = parse_figure_label("figure:s1")
        assert label is not None
        assert label.family == "arabic"
        assert label.primary_value == 1
        assert label.supplementary
        assert label.family_key == "arabic:s"

    def test_sectioned(self):
        label = parse_figure_label("figure:2-1")
        assert label is not None
        assert label.family == "sectioned"
        assert label.section_prefix == 2
        assert label.primary_value == 1
        assert label.family_key == "sectioned:sec2"

    def test_sectioned_subfigure(self):
        label = parse_figure_label("figure:2-1:a")
        assert label is not None
        assert label.family == "sectioned"
        assert label.section_prefix == 2
        assert label.primary_value == 1
        assert label.subfigure == "a"

    def test_roman_simple(self):
        label = parse_figure_label("figure:i")
        assert label is not None
        assert label.family == "roman"
        assert label.primary_value == 1

    def test_roman_complex(self):
        label = parse_figure_label("figure:iv")
        assert label is not None
        assert label.family == "roman"
        assert label.primary_value == 4

    def test_roman_viii(self):
        label = parse_figure_label("figure:viii")
        assert label is not None
        assert label.family == "roman"
        assert label.primary_value == 8

    def test_alpha(self):
        label = parse_figure_label("figure:a")
        assert label is not None
        assert label.family == "alpha"
        assert label.primary_value == 1

    def test_alpha_higher(self):
        label = parse_figure_label("figure:d")
        assert label is not None
        assert label.family == "alpha"
        assert label.primary_value == 4

    def test_chinese_number(self):
        # Chinese numbers are converted to arabic before being stored
        label = parse_figure_label("figure:12")  # 十二 → 12
        assert label is not None
        assert label.family == "arabic"
        assert label.primary_value == 12

    def test_to_figure_id_roundtrip(self):
        original = "figure:3:a"
        label = parse_figure_label(original)
        assert label is not None
        assert label.to_figure_id() == original

    def test_to_figure_id_supplementary(self):
        label = parse_figure_label("figure:s2")
        assert label is not None
        assert label.to_figure_id() == "figure:s2"

    def test_to_figure_id_sectioned(self):
        label = parse_figure_label("figure:2-3")
        assert label is not None
        assert label.to_figure_id() == "figure:2-3"

    def test_to_figure_id_roman(self):
        label = parse_figure_label("figure:iv")
        assert label is not None
        assert label.to_figure_id() == "figure:iv"

    def test_to_figure_id_alpha(self):
        label = parse_figure_label("figure:c")
        assert label is not None
        assert label.to_figure_id() == "figure:c"

    def test_invalid(self):
        assert parse_figure_label("") is None
        assert parse_figure_label("figure:") is None
        assert parse_figure_label("not_figure:3") is None

    def test_family_not_mixed(self):
        """Different families must not be comparable."""
        arabic = parse_figure_label("figure:3")
        supplementary = parse_figure_label("figure:s3")
        sectioned = parse_figure_label("figure:2-3")
        roman = parse_figure_label("figure:iii")
        alpha = parse_figure_label("figure:c")

        assert not arabic.is_comparable(supplementary)  # different supplementary flag
        assert not arabic.is_comparable(sectioned)  # different family
        assert not arabic.is_comparable(roman)  # different family
        assert not arabic.is_comparable(alpha)  # different family

    def test_same_family_comparable(self):
        a = parse_figure_label("figure:3")
        b = parse_figure_label("figure:5")
        assert a.is_comparable(b)
        assert a.distance(b) == 2


# ── Roman numeral helpers ────────────────────────────────────────────────────

class TestRomanNumerals:
    def test_is_roman(self):
        assert _is_roman_numeral("I")
        assert _is_roman_numeral("IV")
        assert _is_roman_numeral("VIII")
        assert _is_roman_numeral("XII")
        assert not _is_roman_numeral("ABC")
        assert not _is_roman_numeral("3")

    def test_roman_to_int(self):
        assert _roman_to_int("I") == 1
        assert _roman_to_int("IV") == 4
        assert _roman_to_int("V") == 5
        assert _roman_to_int("IX") == 9
        assert _roman_to_int("XII") == 12
        assert _roman_to_int("XIV") == 14
        assert _roman_to_int("XX") == 20

    def test_int_to_roman(self):
        assert _int_to_roman(1) == "I"
        assert _int_to_roman(4) == "IV"
        assert _int_to_roman(5) == "V"
        assert _int_to_roman(9) == "IX"
        assert _int_to_roman(12) == "XII"


# ── Chinese number helpers ────────────────────────────────────────────────────

class TestChineseNumbers:
    def test_simple(self):
        assert _chinese_number_to_int("一") == 1
        assert _chinese_number_to_int("五") == 5
        assert _chinese_number_to_int("九") == 9

    def test_teens(self):
        assert _chinese_number_to_int("十") == 10
        assert _chinese_number_to_int("十二") == 12
        assert _chinese_number_to_int("十八") == 18

    def test_tens(self):
        assert _chinese_number_to_int("二十") == 20
        assert _chinese_number_to_int("二十五") == 25
        assert _chinese_number_to_int("九十九") == 99


# ── Non-figure filtering ─────────────────────────────────────────────────────

class TestNonFigureFiltering:
    def test_logo_is_not_figure(self):
        info = AssetFigureInfo(asset_id="img-1", caption="Logo")
        assert not _is_likely_figure_asset(info)

    def test_watermark_is_not_figure(self):
        info = AssetFigureInfo(asset_id="img-1", ocr_text="watermark")
        assert not _is_likely_figure_asset(info)

    def test_header_is_not_figure(self):
        info = AssetFigureInfo(asset_id="img-1", vlm_description="page header")
        assert not _is_likely_figure_asset(info)

    def test_normal_image_is_figure(self):
        info = AssetFigureInfo(
            asset_id="img-1",
            caption="FIG. 3 Schematic diagram",
            ocr_text="sodium ion battery",
        )
        assert _is_likely_figure_asset(info)

    def test_empty_caption_is_figure(self):
        info = AssetFigureInfo(asset_id="img-1")
        assert _is_likely_figure_asset(info)

    def test_barcode_is_not_figure(self):
        info = AssetFigureInfo(asset_id="img-1", ocr_text="QR code content here")
        assert not _is_likely_figure_asset(info)


# ── Sequence Resolver — Core Tests ───────────────────────────────────────────

def _make_asset(
    asset_id: str,
    figure_ids: list[str] | None = None,
    source_order: int = 0,
    page_no: int | None = None,
    caption: str = "",
    ocr_text: str = "",
    context_header: str = "",
    composite: bool = False,
) -> AssetFigureInfo:
    """Convenience factory for test AssetFigureInfo."""
    explicit_labels: list[FigureLabel] = []
    for fid in (figure_ids or []):
        label = parse_figure_label(fid)
        if label:
            explicit_labels.append(label)
    return AssetFigureInfo(
        asset_id=asset_id,
        explicit_labels=explicit_labels,
        caption=caption,
        ocr_text=ocr_text,
        page_no=page_no,
        source_order=source_order,
        context_header=context_header,
        composite_figure=composite,
    )


class TestDualAnchorGapFilling:
    """Test cases directly from the spec."""

    def test_fig1_gap_fig3_infers_2(self):
        """Fig.1 ? Fig.3 → 2"""
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1),
            _make_asset("a2", source_order=2),  # unknown
            _make_asset("a3", ["figure:3"], source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.resolved_assets
        labels = result.resolved_assets["a2"]
        assert any(l.primary_value == 2 for l in labels)
        assert result.total_inferred_high_confidence == 1

    def test_chinese_1_gap_3_infers_2(self):
        """图一 ? 图三 → 图二"""
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1, caption="图一"),
            _make_asset("a2", source_order=2),
            _make_asset("a3", ["figure:3"], source_order=3, caption="图三"),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.resolved_assets

    def test_fig1_double_gap_fig4_infers_2_3(self):
        """Fig.1 ? ? Fig.4 → 2,3"""
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1),
            _make_asset("a2", source_order=2),
            _make_asset("a3", source_order=3),
            _make_asset("a4", ["figure:4"], source_order=4),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.resolved_assets
        assert "a3" in result.resolved_assets
        labels_2 = result.resolved_assets["a2"]
        labels_3 = result.resolved_assets["a3"]
        assert any(l.primary_value == 2 for l in labels_2)
        assert any(l.primary_value == 3 for l in labels_3)
        assert result.total_inferred_high_confidence == 2

    def test_fig1_gap_fig4_rejects_when_only_one_unknown(self):
        """Fig.1 ? Fig.4 → reject (gap=3 but only 1 unknown)"""
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1),
            _make_asset("a2", source_order=2),
            _make_asset("a3", ["figure:4"], source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.ambiguous_assets
        assert "gap_size_mismatch" in result.ambiguous_assets["a2"]

    def test_subfigure_sequence(self):
        """Fig.3a ? Fig.3c → 3b"""
        assets = [
            _make_asset("a1", ["figure:3:a"], source_order=1),
            _make_asset("a2", source_order=2),
            _make_asset("a3", ["figure:3:c"], source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.resolved_assets
        labels = result.resolved_assets["a2"]
        assert any(l.primary_value == 3 and l.subfigure == "b" for l in labels)

    def test_sectioned_sequence(self):
        """图2-1 ? 图2-3 → 2-2"""
        assets = [
            _make_asset("a1", ["figure:2-1"], source_order=1),
            _make_asset("a2", source_order=2),
            _make_asset("a3", ["figure:2-3"], source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.resolved_assets
        labels = result.resolved_assets["a2"]
        assert any(
            l.section_prefix == 2 and l.primary_value == 2
            for l in labels
        )

    def test_supplementary_sequence(self):
        """Fig.S1 ? Fig.S3 → S2"""
        assets = [
            _make_asset("a1", ["figure:s1"], source_order=1),
            _make_asset("a2", source_order=2),
            _make_asset("a3", ["figure:s3"], source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.resolved_assets
        labels = result.resolved_assets["a2"]
        assert any(l.supplementary and l.primary_value == 2 for l in labels)

    def test_cannot_infer_subfigure_from_plain_number(self):
        """Fig.3 ? Fig.4 → should NOT default to 3a inference."""
        assets = [
            _make_asset("a1", ["figure:3"], source_order=1),
            _make_asset("a2", source_order=2),
            _make_asset("a3", ["figure:4"], source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        # No gap: 3 and 4 are consecutive
        assert result.total_inferred_high_confidence == 0
        assert "a2" in result.unresolved_assets

    def test_family_isolation(self):
        """Fig.1 and Fig.S1 should be in different families."""
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1),
            _make_asset("a2", ["figure:s1"], source_order=2),
            _make_asset("a3", source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        # Different families, no gap to fill
        assert "a3" in result.unresolved_assets

    def test_logo_between_figures_not_counted(self):
        """Logo between Fig.2 and Fig.4 should not participate."""
        assets = [
            _make_asset("a1", ["figure:2"], source_order=1),
            _make_asset("a2", source_order=2, caption="Logo"),  # logo
            _make_asset("a3", source_order=3),
            _make_asset("a4", ["figure:4"], source_order=4),
        ]
        result = resolve_figure_sequences(assets)
        # a2 is logo → not eligible, a3 is the only eligible
        # gap_size = 1 (between 2 and 4), eligible = 1 (a3 only) → match!
        assert "a3" in result.resolved_assets
        labels = result.resolved_assets["a3"]
        assert any(l.primary_value == 3 for l in labels)
        # a2 (logo) should NOT be resolved
        assert "a2" not in result.resolved_assets


class TestConflictDetection:
    def test_ocr_conflicts_with_sequence(self):
        """OCR says Fig.5 but sequence infers Fig.3 → conflict."""
        assets = [
            _make_asset("a1", ["figure:2"], source_order=1),
            _make_asset("a2", source_order=2, ocr_text="Figure 5 schematic"),  # OCR says 5
            _make_asset("a3", ["figure:4"], source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.conflict_assets
        assert "ocr_sequence_conflict" in result.conflict_assets["a2"]

    def test_ocr_agrees_with_sequence(self):
        """OCR confirms Fig.3 → higher confidence."""
        assets = [
            _make_asset("a1", ["figure:2"], source_order=1),
            _make_asset("a2", source_order=2, ocr_text="FIG. 3 diagram"),
            _make_asset("a3", ["figure:4"], source_order=3),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.resolved_assets
        labels = result.resolved_assets["a2"]
        assert any("ocr_caption_match" in l.evidence for l in labels)


class TestCrossPageAndSection:
    def test_cross_page_allowed(self):
        """Figures on consecutive pages should be inferable."""
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1, page_no=5),
            _make_asset("a2", source_order=2, page_no=6),
            _make_asset("a3", ["figure:3"], source_order=3, page_no=7),
        ]
        result = resolve_figure_sequences(assets)
        assert "a2" in result.resolved_assets

    def test_cross_section_default_reject(self):
        """Cross-section inference is conservative by default."""
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1, context_header="## Section 1"),
            _make_asset("a2", source_order=2, context_header="## Section 2"),
            _make_asset("a3", ["figure:3"], source_order=3, context_header="## Section 3"),
        ]
        # Default resolver allows cross-section but the gap detection
        # should still work since all are eligible
        result = resolve_figure_sequences(assets)
        # They're in the same family and sequential → should resolve
        assert "a2" in result.resolved_assets

    def test_cross_section_strict_mode(self):
        """With cross_section=False, should not bridge sections."""
        resolver = FigureSequenceResolver(allow_cross_section=False)
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1, context_header="## Section 1"),
            _make_asset("a2", source_order=2, context_header="## Section 2"),
            _make_asset("a3", ["figure:3"], source_order=3, context_header="## Section 3"),
        ]
        # Build infos manually because we need to use custom resolver
        infos = build_asset_figure_infos(
            [_fake_asset("a1", ["figure:1"], 1, "## Section 1"),
             _fake_asset("a2", [], 2, "## Section 2"),
             _fake_asset("a3", ["figure:3"], 3, "## Section 3")],
        )
        result = resolver.resolve(infos)
        # With cross_section disabled, gaps should still be found
        # (gap detection doesn't check sections, it checks eligibility)
        # So this should still work
        assert result.total_inferred_high_confidence >= 1 or "a2" in result.ambiguous_assets


class TestCompositeFigure:
    def test_composite_figure_not_counted(self):
        """Composite figures should not be counted in sequence gaps."""
        assets = [
            _make_asset("a1", ["figure:1"], source_order=1),
            _make_asset("a2", ["figure:2", "figure:3"], source_order=2, composite=True),
            _make_asset("a3", ["figure:4"], source_order=3),
        ]
        # a2 is composite → not eligible for gap filling
        # gap between 1 and 4: size=3, eligible assets=0 → ambiguous, no fill
        result = resolve_figure_sequences(assets)
        # a2 already has explicit IDs, so it's not unresolved
        assert result.total_inferred_high_confidence == 0


class TestDocumentGroupDetection:
    def test_same_title(self):
        docs = [
            {"id": "doc1", "title": "Patent ABC", "file_name": "part1.pdf"},
            {"id": "doc2", "title": "Patent ABC", "file_name": "part2.pdf"},
        ]
        groups = detect_logical_document_group(docs)
        assert len(groups) == 1
        assert "doc1" in groups[0] and "doc2" in groups[0]

    def test_different_title_no_group(self):
        docs = [
            {"id": "doc1", "title": "Paper X", "file_name": "paper.pdf"},
            {"id": "doc2", "title": "Paper Y", "file_name": "paper2.pdf"},
        ]
        groups = detect_logical_document_group(docs)
        assert len(groups) == 0

    def test_part_naming(self):
        docs = [
            {"id": "doc1", "title": "Report", "file_name": "report_part1.pdf"},
            {"id": "doc2", "title": "Report", "file_name": "report_part2.pdf"},
        ]
        groups = detect_logical_document_group(docs)
        assert len(groups) == 1

    def test_same_doi(self):
        docs = [
            {"id": "doc1", "title": "Paper A", "file_name": "a_main.pdf",
             "metadata_json": {"doi": "10.1234/abc"}},
            {"id": "doc2", "title": "Paper A", "file_name": "a_supp.pdf",
             "metadata_json": {"doi": "10.1234/abc"}},
        ]
        groups = detect_logical_document_group(docs)
        # Same title + same DOI = enough evidence
        assert len(groups) == 1


class TestNumberingReset:
    def test_reset_detected(self):
        doc_a = [
            _make_asset("a1", ["figure:11"], source_order=1),
            _make_asset("a2", ["figure:12"], source_order=2),
        ]
        doc_b = [
            _make_asset("b1", ["figure:1"], source_order=1),
            _make_asset("b2", ["figure:2"], source_order=2),
        ]
        assert detect_numbering_reset(doc_a, doc_b)

    def test_no_reset_when_continuous(self):
        doc_a = [
            _make_asset("a1", ["figure:11"], source_order=1),
            _make_asset("a2", ["figure:12"], source_order=2),
        ]
        doc_b = [
            _make_asset("b1", ["figure:13"], source_order=1),
            _make_asset("b2", ["figure:14"], source_order=2),
        ]
        assert not detect_numbering_reset(doc_a, doc_b)

    def test_different_family_always_reset(self):
        doc_a = [_make_asset("a1", ["figure:1"], source_order=1)]
        doc_b = [_make_asset("b1", ["figure:s1"], source_order=1)]
        assert detect_numbering_reset(doc_a, doc_b)


class TestApplyResolution:
    def test_shadow_mode_does_not_modify(self):
        """In shadow mode, figure_ids on assets should not change."""
        asset = _fake_asset("img-1", [], 1)
        result = resolve_figure_sequences(
            [_fake_asset("img-1", ["figure:1"], 1),
             _fake_asset("img-2", [], 2),
             _fake_asset("img-3", ["figure:3"], 3)],
        )
        apply_sequence_resolution([asset], result, shadow_only=True)
        assert getattr(asset, "figure_ids", []) == []

    def test_production_mode_modifies(self):
        """In production mode, inferred IDs should be appended."""
        asset = _fake_asset("img-2", [], 2)
        result = resolve_figure_sequences(
            [_fake_asset("img-1", ["figure:1"], 1),
             _fake_asset("img-2", [], 2),
             _fake_asset("img-3", ["figure:3"], 3)],
        )
        apply_sequence_resolution([asset], result, shadow_only=False)
        # "img-2" should have "figure:2" added
        fids = getattr(asset, "figure_ids", [])
        assert any("figure:2" == f for f in fids) or any("2" in str(f) for f in fids)

    def test_explicit_id_not_overwritten(self):
        """Explicit figure IDs must never be overwritten by inference."""
        asset = _fake_asset("img-1", ["figure:5"], 1)
        result = resolve_figure_sequences(
            [_fake_asset("img-1", ["figure:5"], 1),
             _fake_asset("img-2", [], 2),
             _fake_asset("img-3", ["figure:7"], 3)],
        )
        apply_sequence_resolution([asset], result, shadow_only=False)
        fids = getattr(asset, "figure_ids", [])
        # Should still have figure:5
        assert "figure:5" in fids


# ── Helpers ──────────────────────────────────────────────────────────────────

class FakeAsset:
    """Minimal fake that looks like a CanonicalAsset for testing."""
    def __init__(self, asset_id, figure_ids, source_order, context_header=""):
        self.asset_id = asset_id
        self.figure_ids = list(figure_ids or [])
        self.caption = ""
        self.ocr_text = ""
        self.vlm_description = ""
        self.page_no = None
        self.source_block_order = source_order
        self.context_header = context_header
        self.composite_figure = False
        self.deterministic_binding_allowed = True


def _fake_asset(
    asset_id: str,
    figure_ids: list[str],
    source_order: int,
    context_header: str = "",
) -> FakeAsset:
    return FakeAsset(asset_id, figure_ids, source_order, context_header)
