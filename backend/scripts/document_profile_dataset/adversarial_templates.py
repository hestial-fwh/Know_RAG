from __future__ import annotations

from .models import BlockSpec, DocumentSpec, PageSpec


W = 612
H = 792


def _block(
    case_id: str,
    suffix: str,
    block_type: str,
    bbox: tuple[int, int, int, int],
    **kwargs,
) -> BlockSpec:
    return BlockSpec(
        block_id=f"{case_id}-{suffix}",
        block_type=block_type,
        bbox=bbox,
        **kwargs,
    )


def _image(
    case_id: str,
    suffix: str,
    bbox: tuple[int, int, int, int],
    *,
    kind: str = "calligraphy",
    caption: str = "",
    item: str = "",
    parser_output: bool = True,
    metadata: dict | None = None,
) -> BlockSpec:
    return _block(
        case_id,
        suffix,
        "image",
        bbox,
        parser_output=parser_output,
        asset_kind=kind,
        asset_path=f"assets/adversarial/{case_id}-{suffix}.png",
        caption=caption,
        expected_semantic_role="figure",
        expected_visual_item_id=item,
        metadata=dict(metadata or {}),
    )


def _text(
    case_id: str,
    suffix: str,
    bbox: tuple[int, int, int, int],
    text: str,
    *,
    role: str = "body_text",
    native: bool = False,
    item: str = "",
    relation: str = "",
    ocr_quality: str = "",
) -> BlockSpec:
    effective_role = "caption" if relation == "caption" and role == "body_text" else role
    return _block(
        case_id,
        suffix,
        "text",
        bbox,
        text=text,
        role=effective_role,
        native_pdf_text=native,
        expected_semantic_role=effective_role,
        expected_visual_item_id=item,
        expected_relation=relation,
        ocr_quality=ocr_quality,
    )


def _page(
    case_id: str,
    page_no: int,
    blocks: tuple[BlockSpec, ...],
    *,
    page_kind: str = "visual_dominant",
    policy: str = "visual-collection-v1",
    preserve: bool = True,
    capabilities: tuple[str, ...] = (),
    group: str = "",
    variant: str = "",
    items: tuple[dict, ...] = (),
    tags: tuple[str, ...] = (),
) -> PageSpec:
    return PageSpec(
        case_id=case_id,
        page_no=page_no,
        page_kind=page_kind,
        chunk_policy_id=policy,
        preserve_page_visual=preserve,
        blocks=blocks,
        tags=("dcp01_adversarial", *tags),
        capabilities=capabilities,
        confusion_group=group,
        variant=variant,
        expected_visual_items=items,
        baseline_assertion=False,
    )


def _threshold_pages() -> tuple[PageSpec, ...]:
    pages: list[PageSpec] = []
    case = "publisher_album_native_text"
    pages.append(
        _page(
            case,
            1,
            (
                _image(case, "art", (20, 20, 592, 600), kind="album", item="item-1"),
                _text(
                    case,
                    "native",
                    (55, 625, 557, 755),
                    "Publisher supplied native artist biography and catalogue notes. " * 8,
                    native=True,
                    item="item-1",
                    relation="caption",
                ),
            ),
            capabilities=("native_text_visual_page", "negative_visual_isolation"),
            group="native-text-preservation",
            variant="native-text-positive",
            items=({"id": "item-1", "asset_blocks": [f"{case}-art"]},),
        )
    )
    for page_no, count in enumerate((29, 30, 400, 401), start=2):
        case = f"poster_chars_{count}"
        pages.append(
            _page(
                case,
                page_no,
                (
                    _image(case, "poster", (18, 18, 594, 774), kind="album"),
                    _text(case, "ocr", (90, 120, 522, 360), "A" * count),
                ),
                capabilities=("poster_boundary", "character_threshold_perturbation"),
                group="poster-char-count",
                variant=str(count),
                tags=(f"canonical_text_chars={count}",),
            )
        )
    ratio_boxes = (
        ("dominant_ratio_below", (20, 20, 592, 570), "below"),
        ("dominant_ratio_above", (20, 20, 592, 574), "above"),
    )
    for offset, (case, bbox, variant) in enumerate(ratio_boxes, start=6):
        pages.append(
            _page(
                case,
                offset,
                (
                    _image(case, "visual", bbox, kind="album"),
                    _text(case, "body", (55, 610, 557, 748), "Reliable native notes. " * 10, native=True),
                ),
                capabilities=("dominant_ratio_boundary", "bbox_perturbation"),
                group="dominant-image-ratio",
                variant=variant,
            )
        )
    for offset, (case, bbox, variant) in enumerate(
        (
            ("text_area_below", (50, 610, 557, 742), "below"),
            ("text_area_above", (48, 590, 560, 750), "above"),
        ),
        start=8,
    ):
        pages.append(
            _page(
                case,
                offset,
                (
                    _image(case, "visual", (18, 18, 594, 774), kind="album"),
                    _text(case, "text", bbox, "Poster programme and venue details. " * 7),
                ),
                capabilities=("text_area_boundary", "bbox_perturbation"),
                group="parser-text-area",
                variant=variant,
            )
        )
    return tuple(pages)


def _ocr_pages() -> tuple[PageSpec, ...]:
    definitions = (
        (
            "academic_full_page_figure",
            "primary_visual_negative",
            "normal",
            "Full page scientific figure with substantial native discussion. " * 10,
            True,
        ),
        (
            "regular_script_short_ocr",
            "ocr_quality",
            "regular_script",
            "歲在癸丑暮春之初",
            False,
        ),
        (
            "cursive_low_quality_ocr",
            "ocr_quality",
            "cursive",
            "遠上寒山石徑斜誤辨亂碼",
            False,
        ),
        (
            "meaningful_signature_ocr",
            "ocr_quality",
            "signature",
            "八大山人",
            False,
        ),
        (
            "cjk_semantic_gibberish_ocr",
            "ocr_quality",
            "cjk_gibberish",
            (
                "皇清洞真道经紫素骑修空本华京板五吉横反胎七元庭玄解重结景祖绝锦青能四秀"
                "大虚空元景超室九素合重虚帝君静幽谷八真合景君玄太雄雌运玉房九变逍得小形散"
                "泥丸易分别碑神善光府神梁南事五符引刻金生华日发夜月沈板尸太极元景王三天称神"
            ),
            False,
        ),
        (
            "mixed_inscription_printed",
            "mixed_text_sources",
            "mixed",
            "Printed curator note beside a handwritten inscription.",
            True,
        ),
        (
            "ancient_book_negative",
            "negative_document_type",
            "scanned_prose",
            "Chapter text is continuous prose and must remain searchable evidence. " * 6,
            False,
        ),
        (
            "scanned_contract_negative",
            "negative_document_type",
            "scanned_contract",
            "The supplier shall deliver within thirty days after execution. " * 7,
            False,
        ),
        (
            "ppt_visual_negative",
            "negative_document_type",
            "presentation",
            "Roadmap milestone architecture delivery metrics",
            True,
        ),
        (
            "vlm_missing",
            "vlm_resilience",
            "missing",
            "Catalogue caption remains available without VLM.",
            True,
        ),
        (
            "vlm_failure",
            "vlm_resilience",
            "failed",
            "Parser caption survives an explicit VLM failure.",
            True,
        ),
        (
            "cross_page_figure_context",
            "cross_page_relation",
            "continued",
            "Figure discussion continues on the following page without forced binding.",
            True,
        ),
    )
    pages: list[PageSpec] = []
    for page_no, (case, capability, variant, text, native) in enumerate(
        definitions,
        start=1,
    ):
        role = "visual_ocr_candidate" if capability == "ocr_quality" else "body_text"
        quality = variant if capability == "ocr_quality" else ""
        metadata = {"vlm_status": variant} if capability == "vlm_resilience" else {}
        pages.append(
            _page(
                case,
                page_no,
                (
                    _image(
                        case,
                        "visual",
                        (18, 18, 594, 620),
                        kind="paper" if case == "academic_full_page_figure" else "calligraphy",
                        caption="" if variant == "missing" else f"{case} caption",
                        item="item-1",
                        metadata=metadata,
                    ),
                    _text(
                        case,
                        "text",
                        (55, 640, 557, 755),
                        text,
                        role=role,
                        native=native,
                        item="item-1" if capability != "cross_page_relation" else "",
                        relation="caption" if native else "inscription",
                        ocr_quality=quality,
                    ),
                ),
                page_kind="native_text" if native and capability != "vlm_resilience" else "visual_dominant",
                policy="structured-text-v1" if native and capability != "vlm_resilience" else "visual-collection-v1",
                preserve=True,
                capabilities=(capability,),
                group=capability,
                variant=variant,
                items=({"id": "item-1", "asset_blocks": [f"{case}-visual"]},),
            )
        )
    return tuple(pages)


def _layout_pages() -> tuple[PageSpec, ...]:
    pages: list[PageSpec] = []

    def visual(
        case: str,
        suffix: str,
        bbox,
        item: str,
        *,
        kind: str = "calligraphy",
        caption: str = "",
    ):
        return _image(
            case,
            suffix,
            bbox,
            item=item,
            kind=kind,
            caption=caption,
        )

    layouts: tuple[tuple[str, tuple[BlockSpec, ...], tuple[dict, ...]], ...] = (
        (
            "VI-01",
            (
                visual("VI-01", "top", (45, 35, 567, 320), "item-1"),
                _text("VI-01", "top-cap", (80, 325, 530, 355), "Top work", item="item-1", relation="caption"),
                visual("VI-01", "bottom", (45, 390, 567, 680), "item-2"),
                _text("VI-01", "bottom-cap", (80, 685, 530, 720), "Bottom work", item="item-2", relation="caption"),
            ),
            ({"id": "item-1"}, {"id": "item-2"}),
        ),
        (
            "VI-02",
            (
                visual("VI-02", "left", (30, 60, 270, 700), "item-1"),
                visual("VI-02", "right", (342, 60, 582, 700), "item-2"),
                _text("VI-02", "shared", (275, 280, 337, 470), "Shared exhibition note", relation="ambiguous"),
            ),
            ({"id": "item-1"}, {"id": "item-2"}),
        ),
        (
            "VI-03",
            tuple(
                block
                for row in range(2)
                for col in range(2)
                for block in (
                    visual(
                        "VI-03",
                        f"img-{row}-{col}",
                        (35 + col * 285, 35 + row * 365, 292 + col * 285, 300 + row * 365),
                        f"item-{row * 2 + col + 1}",
                    ),
                    _text(
                        "VI-03",
                        f"cap-{row}-{col}",
                        (45 + col * 285, 305 + row * 365, 282 + col * 285, 335 + row * 365),
                        f"Grid work {row * 2 + col + 1}",
                        item=f"item-{row * 2 + col + 1}",
                        relation="caption",
                    ),
                )
            ),
            tuple({"id": f"item-{index}"} for index in range(1, 5)),
        ),
        (
            "VI-04",
            (
                visual("VI-04", "large", (30, 35, 390, 735), "item-1"),
                visual("VI-04", "small-1", (420, 55, 580, 245), "item-2"),
                visual("VI-04", "small-2", (420, 295, 580, 485), "item-3"),
                visual("VI-04", "small-3", (420, 535, 580, 725), "item-4"),
            ),
            tuple({"id": f"item-{index}"} for index in range(1, 5)),
        ),
        (
            "VI-05",
            (
                visual("VI-05", "center", (150, 180, 462, 610), "item-1"),
                _text("VI-05", "top", (170, 125, 445, 165), "Unique title", item="item-1", relation="caption"),
                _text("VI-05", "left", (35, 300, 135, 450), "Left page note", relation="unbound"),
                _text("VI-05", "right", (477, 300, 577, 450), "Right page note", relation="unbound"),
            ),
            ({"id": "item-1"},),
        ),
        (
            "VI-06",
            (
                visual(
                    "VI-06",
                    "fallback",
                    (10, 10, 602, 782),
                    "fallback",
                    caption="Full page fallback",
                ),
                visual("VI-06", "local-1", (45, 60, 285, 700), "item-1"),
                visual("VI-06", "local-2", (327, 60, 567, 700), "item-2"),
            ),
            ({"id": "item-1"}, {"id": "item-2"}),
        ),
        (
            "VI-07",
            (
                visual(
                    "VI-07",
                    "page",
                    (10, 10, 602, 782),
                    "fallback",
                    kind="split_album",
                    caption="Two-panel raster page",
                ),
            ),
            ({"id": "candidate-left"}, {"id": "candidate-right"}),
        ),
        (
            "VI-08",
            (
                visual(
                    "VI-08",
                    "page",
                    (10, 10, 602, 782),
                    "item-1",
                    caption="Unsegmented full page artwork",
                ),
            ),
            ({"id": "item-1", "status": "unsegmented_page_visual"},),
        ),
        (
            "VI-09",
            (
                visual("VI-09", "figure", (55, 210, 557, 610), "item-1"),
                _text(
                    "VI-09",
                    "body-1",
                    (45, 35, 567, 190),
                    "Academic body paragraph. " * 14,
                    native=True,
                    relation="unbound",
                ),
                _text(
                    "VI-09",
                    "body-2",
                    (45, 625, 567, 755),
                    "Discussion continues after the figure. " * 10,
                    native=True,
                    relation="unbound",
                ),
            ),
            ({"id": "item-1"},),
        ),
        (
            "VI-10",
            (
                visual("VI-10", "left", (33, 58, 291, 702), "item-1"),
                visual("VI-10", "right", (321, 62, 579, 698), "item-2"),
                _text("VI-10", "cap-left", (45, 710, 280, 745), "Left work", item="item-1", relation="caption"),
                _text("VI-10", "cap-right", (332, 708, 567, 743), "Right work", item="item-2", relation="caption"),
            ),
            ({"id": "item-1"}, {"id": "item-2"}),
        ),
    )
    for page_no, (case, blocks, items) in enumerate(layouts, start=1):
        capabilities = ["visual_item_layout"]
        if case == "VI-06":
            capabilities.append("duplicate_fallback")
        if case == "VI-10":
            capabilities.append("bbox_perturbation")
        pages.append(
            _page(
                case,
                page_no,
                blocks,
                capabilities=tuple(capabilities),
                group="visual-item-layout",
                variant=case,
                items=items,
            )
        )
    return tuple(pages)


def build_adversarial_document_specs() -> tuple[DocumentSpec, ...]:
    return (
        DocumentSpec(
            "dcp-adversarial-thresholds",
            "adversarial",
            "mixed",
            _threshold_pages(),
            evaluation_scope="adversarial",
        ),
        DocumentSpec(
            "dcp-adversarial-ocr",
            "adversarial",
            "mixed",
            _ocr_pages(),
            evaluation_scope="adversarial",
        ),
        DocumentSpec(
            "dcp-adversarial-layout",
            "adversarial",
            "visual_collection",
            _layout_pages(),
            evaluation_scope="adversarial",
        ),
    )


ADVERSARIAL_SCENARIOS = tuple(
    page.case_id
    for document in build_adversarial_document_specs()
    for page in document.pages
)


__all__ = ["ADVERSARIAL_SCENARIOS", "build_adversarial_document_specs"]
