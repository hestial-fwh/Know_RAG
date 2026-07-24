from app.services.chunker.canonical_parent_child import (
    build_canonical_parent_child_plan,
)
from app.services.chunker.chunk_policy_resolver import (
    DCP3_ACTIVE_POLICY_IDS,
    resolve_chunk_policies,
)
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import (
    CanonicalDocument,
    CanonicalSourceNode,
)
from app.services.document_parser.document_profiler import (
    profile_canonical_document,
)


def _cfg(*, child_size: int = 180) -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=800,
        child_chunk_size=child_size,
        child_chunk_overlap=24,
        separators=["\n\n", "\n", "。", ". "],
    )


def _page_facts(*pages):
    return {
        "page_visual_profile": {
            "decisions": [
                {
                    "page_no": page_no,
                    "page_width": 612,
                    "page_height": 792,
                    "native_text_chars": native_text_chars,
                    "dominant_image_ratio": dominant_image_ratio,
                    "decision": decision,
                }
                for (
                    page_no,
                    native_text_chars,
                    dominant_image_ratio,
                    decision,
                ) in pages
            ]
        }
    }


def test_dcp3_scanned_pages_split_inside_authoritative_page_boundaries():
    page_one = "Section ALPHA. " + ("Supplier delivery obligation. " * 35)
    page_two = "Section BETA. " + ("Payment acceptance condition. " * 35)
    markdown = page_one + "\n\n" + page_two
    canonical = CanonicalDocument(
        markdown_content=markdown,
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="page-1",
                node_type="text",
                source_order=0,
                canonical_text=page_one,
                text_hash="page-1",
                page_no=1,
                bbox=(55, 70, 557, 700),
            ),
            CanonicalSourceNode(
                node_id="page-2",
                node_type="text",
                source_order=1,
                canonical_text=page_two,
                text_hash="page-2",
                page_no=2,
                bbox=(55, 70, 557, 700),
            ),
        ],
        metadata=_page_facts(
            (1, 0, 0.93, "preserve"),
            (2, 0, 0.93, "preserve"),
        ),
        source_format="pdf",
    )
    profile = profile_canonical_document(canonical)
    policies = resolve_chunk_policies(
        profile,
        _cfg(),
        active_policy_ids=DCP3_ACTIVE_POLICY_IDS,
    )

    plan = build_canonical_parent_child_plan(
        canonical,
        _cfg(),
        resolved_policies=policies,
    )

    active_children = [
        child
        for child in plan.children
        if child.metadata.get("chunk_policy_applied") is True
    ]
    assert active_children
    assert {
        child.metadata["chunk_policy_id"] for child in active_children
    } == {"scanned-prose-v1"}
    assert all(
        len(child.metadata["chunk_policy_candidate_page_nos"]) == 1
        for child in active_children
    )
    assert not any(
        "ALPHA" in child.content and "BETA" in child.content
        for child in active_children
    )


def test_dcp3_table_policy_repeats_header_for_row_groups():
    header = "| Region | Q1 | Q2 |\n|---|---:|---:|\n"
    rows = "".join(f"| Region {index} | {index} | {index + 1} |\n" for index in range(30))
    table = header + rows
    canonical = CanonicalDocument(
        markdown_content=table,
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="table",
                node_type="table",
                source_order=0,
                canonical_text=table,
                text_hash="table",
                page_no=1,
                bbox=(45, 120, 567, 650),
                semantic_role="table",
            )
        ],
        metadata=_page_facts((1, len(table), 0.0, "normal")),
        source_format="pdf",
    )
    config = _cfg(child_size=180)
    profile = profile_canonical_document(canonical)
    shadow_policies = resolve_chunk_policies(profile, config)
    policies = resolve_chunk_policies(
        profile,
        config,
        active_policy_ids=DCP3_ACTIVE_POLICY_IDS,
    )

    assert shadow_policies[0].shadow_only is True
    assert policies[0].shadow_only is False
    plan = build_canonical_parent_child_plan(
        canonical,
        config,
        resolved_policies=policies,
    )

    table_children = [child for child in plan.children if child.chunk_type == "table"]
    assert len(table_children) > 1
    assert all("| Region | Q1 | Q2 |" in child.content for child in table_children)
    assert all(
        child.metadata["chunk_policy_id"] == "table-row-group-v1"
        and child.metadata["chunk_policy_applied"] is True
        and child.metadata["chunk_policy_table_mode"] == "repeat_header_row_group"
        for child in table_children
    )


def test_dcp3_structured_text_shadow_keeps_existing_boundaries():
    text = (
        "# Introduction\n\n"
        + ("The ordinary text baseline remains stable. " * 30)
        + "\n\n# Conclusion\n\n"
        + ("The fallback splitter keeps its prior behavior. " * 30)
    )
    canonical = CanonicalDocument(
        markdown_content=text,
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="text",
                node_type="text",
                source_order=0,
                canonical_text=text,
                text_hash="text",
                page_no=1,
                semantic_role="body_text",
            )
        ],
        metadata=_page_facts((1, len(text), 0.0, "normal")),
        source_format="pdf",
    )
    config = _cfg()
    profile = profile_canonical_document(canonical)
    policies = resolve_chunk_policies(
        profile,
        config,
        active_policy_ids=DCP3_ACTIVE_POLICY_IDS,
    )

    baseline = build_canonical_parent_child_plan(canonical, config)
    dcp3 = build_canonical_parent_child_plan(
        canonical,
        config,
        resolved_policies=policies,
    )

    assert policies[0].shadow_only is True
    assert [
        (child.content, child.start, child.end, child.parent_index)
        for child in dcp3.children
    ] == [
        (child.content, child.start, child.end, child.parent_index)
        for child in baseline.children
    ]
    assert [
        (parent.content, parent.start, parent.end)
        for parent in dcp3.parents
    ] == [
        (parent.content, parent.start, parent.end)
        for parent in baseline.parents
    ]


def test_dcp3_recovers_mixed_page_boundaries_after_long_table_normalization():
    page_one = "# Before\n\nIntroductory prose."
    header = "| Region | Q1 | Q2 |\n|---|---:|---:|\n"
    page_two = header + "".join(
        f"| Region {index} | {index} | {index + 1} |\n"
        for index in range(30)
    )
    page_three = "# After\n\nConcluding prose."
    markdown = "\n\n".join((page_one, page_two, page_three))
    canonical = CanonicalDocument(
        markdown_content=markdown,
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="page-1",
                node_type="text",
                source_order=0,
                canonical_text=page_one,
                text_hash="page-1",
                page_no=1,
                semantic_role="body_text",
            ),
            CanonicalSourceNode(
                node_id="page-2",
                node_type="table",
                source_order=1,
                canonical_text=page_two,
                text_hash="page-2",
                page_no=2,
                semantic_role="table",
            ),
            CanonicalSourceNode(
                node_id="page-3",
                node_type="text",
                source_order=2,
                canonical_text=page_three,
                text_hash="page-3",
                page_no=3,
                semantic_role="body_text",
            ),
        ],
        metadata=_page_facts(
            (1, len(page_one), 0.0, "normal"),
            (2, len(page_two), 0.0, "normal"),
            (3, len(page_three), 0.0, "normal"),
        ),
        source_format="pdf",
    )
    config = _cfg(child_size=180)
    policies = resolve_chunk_policies(
        profile_canonical_document(canonical),
        config,
        active_policy_ids=DCP3_ACTIVE_POLICY_IDS,
    )

    plan = build_canonical_parent_child_plan(
        canonical,
        config,
        resolved_policies=policies,
    )

    table_children = [child for child in plan.children if child.chunk_type == "table"]
    assert len(table_children) > 1
    assert all(
        child.metadata["chunk_policy_id"] == "table-row-group-v1"
        and child.metadata["chunk_policy_page_no"] == 2
        and child.metadata["chunk_policy_applied"] is True
        and child.metadata["chunk_policy_candidate_page_nos"] == [2]
        for child in table_children
    )
    assert not any(
        "Before" in child.content or "After" in child.content
        for child in table_children
    )


def test_dcp3_does_not_claim_application_without_an_executed_page_segment():
    markdown = "Unrelated fallback content."
    canonical = CanonicalDocument(
        markdown_content=markdown,
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="missing-table",
                node_type="table",
                source_order=0,
                canonical_text="| Missing |\n|---|\n| value |",
                text_hash="missing-table",
                page_no=1,
                semantic_role="table",
            )
        ],
        metadata=_page_facts((1, 25, 0.0, "normal")),
        source_format="pdf",
    )
    config = _cfg()
    policies = resolve_chunk_policies(
        profile_canonical_document(canonical),
        config,
        active_policy_ids=DCP3_ACTIVE_POLICY_IDS,
    )

    plan = build_canonical_parent_child_plan(
        canonical,
        config,
        resolved_policies=policies,
    )

    assert policies[0].shadow_only is False
    assert plan.children
    assert all(
        child.metadata["chunk_policy_id"] == "table-row-group-v1"
        and child.metadata["chunk_policy_applied"] is False
        and child.metadata["chunk_policy_mode"] == "shadow_fallback"
        for child in plan.children
    )
