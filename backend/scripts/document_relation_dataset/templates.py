from __future__ import annotations

from .models import GoldRelation, LayoutBlock, PageSpec


PAGE_WIDTH = 612
PAGE_HEIGHT = 792


SCENARIOS = (
    "explicit_reference",
    "figure_range_reference",
    "repeated_reference",
    "overlap_child",
    "shared_context",
    "independent_captions",
    "vertical_direction",
    "horizontal_direction",
    "following_count",
    "generic_pronoun",
    "cross_section",
    "cross_page",
    "missing_bbox",
    "repeated_figure_id",
    "flag_off",
    "retrieval_text_to_image",
    "retrieval_image_to_text",
    "contaminated_scope",
    "no_relation",
)


def _relation(
    case_id: str,
    suffix: str,
    relation_type: str,
    status: str,
    method: str,
    *,
    sources: tuple[str, ...] = (),
    targets: tuple[str, ...] = (),
    groups: tuple[str, ...] = (),
    candidates: tuple[str, ...] = (),
    auto: bool = False,
    evidence: tuple[dict, ...],
    reasons: tuple[str, ...] = (),
) -> GoldRelation:
    return GoldRelation(
        relation_id=f"{case_id}-{suffix}",
        relation_type=relation_type,
        status=status,
        auto_bind_allowed=auto,
        binding_method=method,
        binding_score=1.0 if status == "bound" else 0.0,
        source_node_ids=sources,
        target_asset_ids=targets,
        target_group_ids=groups,
        candidate_targets=candidates,
        evidence=evidence,
        ambiguity_reasons=reasons,
    )


def build_page_spec(
    document_type: str,
    scenario: str,
    page_no: int,
    variant: int,
) -> PageSpec:
    case_id = f"SYN-{document_type.upper()}-{page_no:03d}"
    section = f"Section {1 + page_no // 10}"
    order = 0
    blocks: list[LayoutBlock] = []

    def text(
        suffix: str,
        value: str,
        bbox: tuple[int, int, int, int],
        *,
        role: str = "prose",
        section_path: str = section,
    ) -> str:
        nonlocal order
        block_id = f"{case_id}-text-{suffix}"
        blocks.append(
            LayoutBlock(
                block_id=block_id,
                block_type="text",
                bbox=bbox,
                text=value,
                section_path=section_path,
                role=role,
                source_order=order,
                page_no=page_no,
            )
        )
        order += 1
        return block_id

    def image(
        suffix: str,
        bbox: tuple[int, int, int, int],
        caption: str = "",
        *,
        section_path: str = section,
        missing_bbox: bool = False,
        target_page_no: int | None = None,
    ) -> str:
        nonlocal order
        block_id = f"{case_id}-image-{suffix}"
        blocks.append(
            LayoutBlock(
                block_id=block_id,
                block_type="image",
                bbox=None if missing_bbox else bbox,
                caption=caption,
                section_path=section_path,
                role="figure",
                asset_path=f"assets/{document_type}/{case_id.lower()}-{suffix}.png",
                source_order=order,
                page_no=target_page_no or page_no,
                metadata={"render_bbox": list(bbox)},
            )
        )
        order += 1
        return block_id

    text("heading", section, (45, 35, 560, 65), role="heading")
    relations: list[GoldRelation] = []
    tags = {document_type, scenario, "synthetic", f"variant-{variant}"}

    if scenario == "explicit_reference":
        source = text("a", "As shown in Figure 1, the system starts safely.", (55, 90, 555, 125))
        target = image("a", (85, 170, 525, 470), "Figure 1 System overview")
        relations.append(_relation(case_id, "r1", "explicit_reference", "bound", "figure_id_page_exact", sources=(source,), targets=(target,), auto=True, evidence=({"cue": "Figure 1", "page_no": page_no, "figure_id": "figure:1"},)))
    elif scenario == "figure_range_reference":
        source = text(
            "a",
            "如图1‑图2所示，自动清洁设备包含移动平台和清洁模组。",
            (55, 90, 555, 135),
        )
        first = image("first", (45, 185, 292, 500), "图1 移动平台")
        second = image("second", (320, 185, 567, 500), "图2 清洁模组")
        relations.extend(
            [
                _relation(
                    case_id,
                    "r1",
                    "explicit_reference",
                    "bound",
                    "figure_id_page_exact",
                    sources=(source,),
                    targets=(first,),
                    auto=True,
                    evidence=(
                        {
                            "cue": "图1‑图2",
                            "figure_id": "figure:1",
                            "range_index": 0,
                            "page_no": page_no,
                        },
                    ),
                ),
                _relation(
                    case_id,
                    "r2",
                    "explicit_reference",
                    "bound",
                    "figure_id_page_exact",
                    sources=(source,),
                    targets=(second,),
                    auto=True,
                    evidence=(
                        {
                            "cue": "图1‑图2",
                            "figure_id": "figure:2",
                            "range_index": 1,
                            "page_no": page_no,
                        },
                    ),
                ),
            ]
        )
        tags.update({"figure-range", "chinese-reference"})
    elif scenario == "repeated_reference":
        source_a = text("a", "See Figure 1 for the input stage.", (55, 90, 555, 125))
        source_b = text("b", "Refer to Figure 1 again for the output stage.", (55, 145, 555, 180))
        target = image("a", (85, 220, 525, 520), "Figure 1 Processing stages")
        for suffix, source, occurrence in (("r1", source_a, 1), ("r2", source_b, 2)):
            relations.append(_relation(case_id, suffix, "explicit_reference", "bound", "figure_id_exact", sources=(source,), targets=(target,), auto=True, evidence=({"cue": "Figure 1", "occurrence": occurrence, "page_no": page_no},)))
    elif scenario == "overlap_child":
        # Put the cue in the last complete sentence of the first child.  The
        # sentence-level overlap then reuses that unit in the next child, so
        # both children genuinely cover the same reference occurrence.
        value = (
            "Alpha context " + "x" * 100 + ". "
            "Beta context " + "y" * 100 + ". "
            "Boundary context " + "z" * 70 + " Figure 10 shows the result. "
            "Following context " + "w" * 180 + "."
        )
        source = text("a", value, (55, 90, 555, 250))
        target = image("a", (100, 310, 510, 650), "Figure 10 Result")
        relations.append(_relation(case_id, "r1", "explicit_reference", "bound", "smallest_full_range_child", sources=(source,), targets=(target,), auto=True, evidence=({"cue": "Figure 10", "requires_full_range": True, "overlap_children": 2},)))
        tags.add("chunk-overlap")
    elif scenario == "shared_context":
        source = text("a", "The following works share one visual theme.", (45, 85, 567, 125), role="shared_context")
        left = image("left", (45, 165, 292, 500))
        right = image("right", (320, 165, 567, 500))
        group = f"{case_id}-group-1"
        relations.append(_relation(case_id, "r1", "shared_context_of", "bound", "same_page_horizontal_group", sources=(source,), groups=(group,), targets=(left, right), auto=True, evidence=({"page_no": page_no, "page_extent_source": "authoritative", "members": [left, right]},)))
    elif scenario == "independent_captions":
        left = image("left", (45, 165, 292, 500), "Figure 1 Input")
        right = image("right", (320, 165, 567, 500), "Figure 2 Output")
        relations.append(_relation(case_id, "r1", "shared_context_of", "rejected", "independent_caption_guard", targets=(left, right), candidates=(left, right), evidence=({"captions": ["Figure 1 Input", "Figure 2 Output"], "reason": "independent_captions_present"},), reasons=("independent_captions_present",)))
    elif scenario == "vertical_direction":
        upper = image("upper", (100, 105, 512, 335))
        source = text("a", "The upper image is the original; the lower image is optimized.", (70, 370, 542, 410))
        lower = image("lower", (100, 450, 512, 680))
        relations.extend([
            _relation(case_id, "up", "relative_reference", "bound", "directional_bbox_rule", sources=(source,), targets=(upper,), auto=True, evidence=({"cue": "upper image", "direction": "up", "page_no": page_no},)),
            _relation(case_id, "down", "relative_reference", "bound", "directional_bbox_rule", sources=(source,), targets=(lower,), auto=True, evidence=({"cue": "lower image", "direction": "down", "page_no": page_no},)),
        ])
    elif scenario == "horizontal_direction":
        left = image("left", (45, 145, 292, 480))
        right = image("right", (320, 145, 567, 480))
        source = text("a", "The left image is input; the right image is output.", (70, 520, 542, 560))
        relations.extend([
            _relation(case_id, "left", "relative_reference", "bound", "directional_bbox_rule", sources=(source,), targets=(left,), auto=True, evidence=({"cue": "left image", "direction": "left", "x_order": 0},)),
            _relation(case_id, "right", "relative_reference", "bound", "directional_bbox_rule", sources=(source,), targets=(right,), auto=True, evidence=({"cue": "right image", "direction": "right", "x_order": 1},)),
        ])
    elif scenario == "following_count":
        source = text("a", "The following two images show before and after results.", (55, 85, 557, 125))
        first = image("first", (45, 165, 292, 500))
        second = image("second", (320, 165, 567, 500))
        group = f"{case_id}-group-1"
        relations.append(_relation(case_id, "r1", "shared_context_of", "bound", "explicit_count_following_assets", sources=(source,), targets=(first, second), groups=(group,), auto=True, evidence=({"cue": "following two images", "count": 2, "ordered_assets": [first, second]},)))
    elif scenario == "generic_pronoun":
        candidates = (image("a", (35, 145, 202, 430)), image("b", (222, 145, 389, 430)), image("c", (409, 145, 576, 430)))
        source = text("a", "This image presents the result.", (80, 485, 532, 525))
        relations.append(_relation(case_id, "r1", "relative_reference", "ambiguous", "generic_pronoun_candidates", sources=(source,), candidates=candidates, evidence=({"cue": "This image", "candidate_count": 3},), reasons=("no_unique_antecedent",)))
    elif scenario == "cross_section":
        source = text("a", "The image below presents the result.", (55, 90, 555, 125), section_path="Section A")
        text("heading-b", "Section B", (45, 180, 560, 210), role="heading", section_path="Section B")
        target = image("a", (90, 245, 522, 560), section_path="Section B")
        relations.append(_relation(case_id, "r1", "relative_reference", "rejected", "section_boundary_guard", sources=(source,), candidates=(target,), evidence=({"source_section": "Section A", "target_section": "Section B"},), reasons=("section_mismatch",)))
    elif scenario == "cross_page":
        source = text("a", "The next page contains the related image.", (55, 90, 555, 125))
        target = image(
            "candidate",
            (382, 600, 567, 755),
            target_page_no=page_no + 1,
        )
        relations.append(_relation(case_id, "r1", "shared_context_of", "ambiguous", "cross_page_candidate", sources=(source,), candidates=(target,), evidence=({"declared_page_delta": 1, "fixture_page": page_no},), reasons=("missing_cross_page_evidence",)))
        tags.add("cross-page-simulation")
    elif scenario == "missing_bbox":
        source = text("a", "The left image is the input.", (55, 90, 555, 125))
        target = image("a", (90, 180, 522, 510), missing_bbox=True)
        relations.append(_relation(case_id, "r1", "relative_reference", "ambiguous", "missing_geometry_guard", sources=(source,), candidates=(target,), evidence=({"page_no": page_no, "target_bbox": None},), reasons=("missing_target_geometry",)))
        tags.add("missing-bbox")
    elif scenario == "repeated_figure_id":
        source = text("a", "As shown in Figure 1, both candidates are plausible.", (55, 90, 555, 125))
        first = image("a", (45, 165, 292, 500), "Figure 1 Candidate A")
        second = image("b", (320, 165, 567, 500), "Figure 1 Candidate B")
        relations.append(_relation(case_id, "r1", "explicit_reference", "ambiguous", "repeated_figure_id_candidates", sources=(source,), candidates=(first, second), evidence=({"figure_id": "figure:1", "candidate_count": 2},), reasons=("repeated_figure_id_not_uniquely_resolved",)))
    elif scenario == "flag_off":
        source = text("a", "As shown in Figure 1, the system starts.", (55, 90, 555, 125))
        target = image("a", (90, 180, 522, 510), "Figure 1 System")
        relations.append(_relation(case_id, "r1", "flag_off_compatibility", "unmatched", "feature_disabled", sources=(source,), targets=(target,), evidence=({"flags": ["graph", "materialization", "expansion"], "direct_hits_unchanged": True},)))
    elif scenario == "retrieval_text_to_image":
        source = text("a", "See Figure 1 for retrieval evidence.", (55, 90, 555, 125))
        target = image("a", (90, 180, 522, 510), "Figure 1 Evidence")
        relations.append(_relation(case_id, "r1", "retrieval_expansion", "bound", "scoped_text_to_image_expansion", sources=(source,), targets=(target,), auto=True, evidence=({"same_document": True, "same_knowledge_base": True, "image_budget": 3},)))
    elif scenario == "retrieval_image_to_text":
        source_a = text("a", "See Figure 1 for the first explanation.", (55, 90, 555, 125))
        source_b = text("b", "Refer to Figure 1 for the second explanation.", (55, 145, 555, 180))
        target = image("a", (90, 230, 522, 540), "Figure 1 Evidence")
        relations.append(_relation(case_id, "r1", "retrieval_expansion", "bound", "ordered_image_to_context_expansion", sources=(source_a, source_b), targets=(target,), auto=True, evidence=({"ordered_sources": [source_a, source_b], "text_budget": 5},)))
    elif scenario == "contaminated_scope":
        source = text("a", "See Figure 1 in this document only.", (55, 90, 555, 125))
        target = image("foreign", (90, 180, 522, 510), "Foreign scoped asset")
        relations.append(_relation(case_id, "r1", "retrieval_expansion", "rejected", "scope_guard", sources=(source,), targets=(target,), evidence=({"source_document": case_id, "target_document": "FOREIGN-DOC", "reason": "scope_mismatch"},), reasons=("scope_mismatch",)))
        tags.add("metadata-contamination")
    else:
        text("a", "This page contains ordinary prose without any figure reference.", (55, 90, 555, 125))
        image("a", (90, 180, 522, 510), "Decorative plate")
        relations.append(_relation(case_id, "r1", "no_relation", "unmatched", "no_relation_expected", evidence=({"reason": "no_reference_cue"},)))

    return PageSpec(
        case_id=case_id,
        document_type=document_type,
        scenario=scenario,
        page_no=page_no,
        page_width=PAGE_WIDTH,
        page_height=PAGE_HEIGHT,
        blocks=tuple(blocks),
        gold_relations=tuple(relations),
        tags=tuple(sorted(tags)),
        parser_noise={"omit_page_extent": scenario in {"missing_bbox", "cross_page"}},
    )
