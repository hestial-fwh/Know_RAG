from __future__ import annotations

from .models import BlockSpec, DocumentSpec, PageSpec


PAGE_WIDTH = 612
PAGE_HEIGHT = 792

DOCUMENT_TYPES = (
    "visual_collection",
    "scanned_contract",
    "poster",
    "academic",
    "table_document",
    "presentation",
    "code_manual",
    "mixed",
)

SCENARIOS = (
    "calligraphy_visual_ocr",
    "existing_full_page_image",
    "scanned_contract_body_ocr",
    "poster_core_text",
    "academic_small_figure",
    "table_dense",
    "presentation_slide",
    "code_technical",
    "mixed_native_text",
    "mixed_scanned_page",
    "mixed_visual_page",
)


def _block(prefix: str, suffix: str, block_type: str, bbox, **kwargs) -> BlockSpec:
    return BlockSpec(
        block_id=f"{prefix}-{suffix}", block_type=block_type, bbox=bbox, **kwargs
    )


def build_document_specs() -> tuple[DocumentSpec, ...]:
    specs: list[DocumentSpec] = []

    prefix = "DCP-VISUAL"
    visual_pages = (
        PageSpec(
            case_id="calligraphy_visual_ocr",
            page_no=1,
            page_kind="visual_dominant",
            chunk_policy_id="visual-collection-v1",
            preserve_page_visual=True,
            blocks=(
                _block(
                    prefix,
                    "art",
                    "image",
                    (18, 18, 594, 774),
                    parser_output=False,
                    asset_kind="calligraphy",
                    asset_path="assets/visual_collection/calligraphy.png",
                    expected_semantic_role="page_primary_visual",
                ),
                _block(
                    prefix,
                    "ocr",
                    "text",
                    (120, 100, 500, 700),
                    text="mountain river spring dream ink",
                    role="visual_ocr_candidate",
                    expected_semantic_role="visual_ocr_candidate",
                    expected_index_policy="shadow_auxiliary",
                ),
            ),
            tags=("full_page_image", "ocr_noise", "no_native_text"),
            expected_known_gaps=("missing_page_primary_visual",),
        ),
        PageSpec(
            case_id="existing_full_page_image",
            page_no=2,
            page_kind="visual_dominant",
            chunk_policy_id="visual-collection-v1",
            preserve_page_visual=True,
            blocks=(
                _block(
                    prefix,
                    "existing",
                    "image",
                    (18, 18, 594, 774),
                    parser_output=True,
                    asset_kind="album",
                    asset_path="assets/visual_collection/existing.png",
                    caption="Plate 2 Existing parser image",
                    expected_semantic_role="page_primary_visual",
                ),
                _block(
                    prefix,
                    "label",
                    "text",
                    (60, 720, 550, 752),
                    text="Plate 2, landscape study",
                    role="caption",
                    native_pdf_text=True,
                    expected_semantic_role="caption",
                ),
            ),
            tags=("full_page_image", "parser_image_present", "dedup_required"),
        ),
    )
    specs.append(
        DocumentSpec(
            "dcp-visual-collection",
            "visual_collection",
            "visual_collection",
            visual_pages,
        )
    )

    prefix = "DCP-CONTRACT"
    specs.append(
        DocumentSpec(
            "dcp-scanned-contract",
            "scanned_contract",
            "scanned_prose",
            (
                PageSpec(
                    case_id="scanned_contract_body_ocr",
                    page_no=1,
                    page_kind="scanned_prose",
                    chunk_policy_id="scanned-prose-v1",
                    preserve_page_visual=True,
                    blocks=(
                        _block(
                            prefix,
                            "scan",
                            "image",
                            (16, 16, 596, 776),
                            parser_output=False,
                            asset_kind="product_manual",
                            asset_path="assets/scanned_contract/page.png",
                            expected_semantic_role="page_primary_visual",
                        ),
                        _block(
                            prefix,
                            "ocr",
                            "text",
                            (55, 70, 557, 710),
                            text="Section 1. The supplier shall deliver the equipment within thirty days. Section 2. Payment is due after acceptance.",
                            role="scanned_body_ocr",
                            expected_semantic_role="scanned_body_ocr",
                            expected_index_policy="normal",
                        ),
                    ),
                    tags=("full_page_image", "continuous_ocr", "no_native_text"),
                    expected_known_gaps=("missing_page_primary_visual",),
                ),
            ),
        )
    )

    prefix = "DCP-POSTER"
    specs.append(
        DocumentSpec(
            "dcp-poster",
            "poster",
            "visual_collection",
            (
                PageSpec(
                    case_id="poster_core_text",
                    page_no=1,
                    page_kind="visual_dominant",
                    chunk_policy_id="poster-page-v1",
                    preserve_page_visual=True,
                    blocks=(
                        _block(
                            prefix,
                            "poster",
                            "image",
                            (20, 20, 592, 772),
                            parser_output=True,
                            asset_kind="album",
                            asset_path="assets/poster/poster.png",
                            caption="Community Science Day",
                            expected_semantic_role="page_primary_visual",
                        ),
                        _block(
                            prefix,
                            "ocr",
                            "text",
                            (90, 120, 525, 320),
                            text="COMMUNITY SCIENCE DAY SATURDAY 10 AM FREE ENTRY",
                            role="poster_core_text",
                            expected_semantic_role="body_text",
                            expected_index_policy="normal",
                        ),
                    ),
                    tags=(
                        "full_page_image",
                        "core_visual_text",
                        "parser_image_present",
                    ),
                ),
            ),
        )
    )

    prefix = "DCP-PAPER"
    specs.append(
        DocumentSpec(
            "dcp-academic",
            "academic",
            "structured_text",
            (
                PageSpec(
                    case_id="academic_small_figure",
                    page_no=1,
                    page_kind="native_text",
                    chunk_policy_id="structured-text-v1",
                    preserve_page_visual=False,
                    blocks=(
                        _block(
                            prefix,
                            "heading",
                            "text",
                            (50, 45, 560, 80),
                            text="1 Experimental Results",
                            role="heading",
                            native_pdf_text=True,
                            expected_semantic_role="heading",
                        ),
                        _block(
                            prefix,
                            "body",
                            "text",
                            (50, 100, 560, 300),
                            text="The experiment compares three stable baselines. Figure 1 summarizes the measured trend and variance.",
                            role="body_text",
                            native_pdf_text=True,
                            expected_semantic_role="body_text",
                        ),
                        _block(
                            prefix,
                            "figure",
                            "image",
                            (150, 360, 462, 590),
                            parser_output=True,
                            asset_kind="paper",
                            asset_path="assets/academic/figure.png",
                            caption="Figure 1 Experimental trend",
                            expected_semantic_role="figure",
                        ),
                    ),
                    tags=("native_text", "small_figure", "must_not_render_page"),
                ),
            ),
        )
    )

    prefix = "DCP-TABLE"
    specs.append(
        DocumentSpec(
            "dcp-table-document",
            "table_document",
            "table_dense",
            (
                PageSpec(
                    case_id="table_dense",
                    page_no=1,
                    page_kind="table_dense",
                    chunk_policy_id="table-row-group-v1",
                    preserve_page_visual=False,
                    blocks=(
                        _block(
                            prefix,
                            "heading",
                            "text",
                            (50, 45, 560, 80),
                            text="Quarterly Metrics",
                            role="heading",
                            native_pdf_text=True,
                            expected_semantic_role="heading",
                        ),
                        _block(
                            prefix,
                            "table",
                            "table",
                            (45, 120, 567, 650),
                            text="| Region | Q1 | Q2 |\n|---|---:|---:|\n| North | 10 | 12 |\n| South | 8 | 11 |",
                            role="table",
                            native_pdf_text=True,
                            expected_semantic_role="table",
                        ),
                    ),
                    tags=("native_text", "table", "repeat_header_on_split"),
                ),
            ),
        )
    )

    prefix = "DCP-SLIDE"
    specs.append(
        DocumentSpec(
            "dcp-presentation",
            "presentation",
            "presentation",
            (
                PageSpec(
                    case_id="presentation_slide",
                    page_no=1,
                    page_kind="slide",
                    chunk_policy_id="slide-page-v1",
                    preserve_page_visual=False,
                    blocks=(
                        _block(
                            prefix,
                            "title",
                            "text",
                            (45, 40, 567, 90),
                            text="System Roadmap",
                            role="heading",
                            native_pdf_text=True,
                            expected_semantic_role="heading",
                        ),
                        _block(
                            prefix,
                            "bullets",
                            "text",
                            (55, 125, 300, 430),
                            text="Phase one: ingest. Phase two: retrieve. Phase three: evaluate.",
                            role="body_text",
                            native_pdf_text=True,
                            expected_semantic_role="body_text",
                        ),
                        _block(
                            prefix,
                            "diagram",
                            "image",
                            (330, 140, 570, 440),
                            parser_output=True,
                            asset_kind="product_manual",
                            asset_path="assets/presentation/diagram.png",
                            caption="Roadmap diagram",
                            expected_semantic_role="figure",
                        ),
                    ),
                    tags=("slide", "one_page_parent", "mixed_media"),
                ),
            ),
        )
    )

    prefix = "DCP-CODE"
    specs.append(
        DocumentSpec(
            "dcp-code-manual",
            "code_manual",
            "code_or_technical",
            (
                PageSpec(
                    case_id="code_technical",
                    page_no=1,
                    page_kind="native_text",
                    chunk_policy_id="code-technical-v1",
                    preserve_page_visual=False,
                    blocks=(
                        _block(
                            prefix,
                            "heading",
                            "text",
                            (45, 40, 567, 80),
                            text="Configuration Example",
                            role="heading",
                            native_pdf_text=True,
                            expected_semantic_role="heading",
                        ),
                        _block(
                            prefix,
                            "code",
                            "text",
                            (55, 115, 557, 430),
                            text='```xml\n<service name="local">\n  <port>8000</port>\n</service>\n```',
                            role="code_block",
                            native_pdf_text=True,
                            expected_semantic_role="code_block",
                        ),
                    ),
                    tags=("native_text", "code_fence", "preserve_xml"),
                ),
            ),
        )
    )

    prefix = "DCP-MIXED"
    mixed_pages = (
        PageSpec(
            case_id="mixed_native_text",
            page_no=1,
            page_kind="native_text",
            chunk_policy_id="structured-text-v1",
            preserve_page_visual=False,
            blocks=(
                _block(
                    prefix,
                    "native",
                    "text",
                    (50, 60, 560, 500),
                    text="Chapter One contains selectable native text with stable paragraph boundaries.",
                    role="body_text",
                    native_pdf_text=True,
                    expected_semantic_role="body_text",
                ),
            ),
            tags=("mixed_document", "native_text"),
        ),
        PageSpec(
            case_id="mixed_scanned_page",
            page_no=2,
            page_kind="scanned_prose",
            chunk_policy_id="scanned-prose-v1",
            preserve_page_visual=True,
            blocks=(
                _block(
                    prefix,
                    "scan",
                    "image",
                    (16, 16, 596, 776),
                    parser_output=False,
                    asset_kind="product_manual",
                    asset_path="assets/mixed/scan.png",
                    expected_semantic_role="page_primary_visual",
                ),
                _block(
                    prefix,
                    "scan-ocr",
                    "text",
                    (55, 70, 557, 700),
                    text="Appendix A contains a scanned paragraph that remains useful searchable evidence.",
                    role="scanned_body_ocr",
                    expected_semantic_role="scanned_body_ocr",
                ),
            ),
            tags=("mixed_document", "no_native_text"),
            expected_known_gaps=("missing_page_primary_visual",),
        ),
        PageSpec(
            case_id="mixed_visual_page",
            page_no=3,
            page_kind="visual_dominant",
            chunk_policy_id="visual-collection-v1",
            preserve_page_visual=True,
            blocks=(
                _block(
                    prefix,
                    "visual",
                    "image",
                    (18, 18, 594, 774),
                    parser_output=False,
                    asset_kind="calligraphy",
                    asset_path="assets/mixed/visual.png",
                    expected_semantic_role="page_primary_visual",
                ),
                _block(
                    prefix,
                    "visual-ocr",
                    "text",
                    (130, 110, 490, 680),
                    text="wind cloud ancient ink",
                    role="visual_ocr_candidate",
                    expected_semantic_role="visual_ocr_candidate",
                    expected_index_policy="shadow_auxiliary",
                ),
            ),
            tags=("mixed_document", "full_page_image", "ocr_noise"),
            expected_known_gaps=("missing_page_primary_visual",),
        ),
    )
    specs.append(DocumentSpec("dcp-mixed", "mixed", "mixed", mixed_pages))
    return tuple(specs)


__all__ = [
    "DOCUMENT_TYPES",
    "PAGE_HEIGHT",
    "PAGE_WIDTH",
    "SCENARIOS",
    "build_document_specs",
]
