import json

from app.services.chunker.chunk_policy_resolver import resolve_chunk_policies
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import (
    CanonicalDocument,
    CanonicalSourceNode,
)
from app.services.document_parser.document_profiler import (
    profile_canonical_document,
)
from scripts.document_profile_dataset.generator import generate_dataset
from scripts.document_profile_dataset.replay import replay_document, replay_manifest
from scripts.document_profile_dataset.report import build_coverage_report


def _stats(snapshot):
    return snapshot["layers"]["canonical_artifact"]["stats_json"]


def test_dcp2_profiles_page_less_canonical_as_one_logical_scope():
    canonical = CanonicalDocument(
        markdown_content="# Configuration\n\n```xml\n<port>8000</port>\n```",
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="node-code",
                node_type="text",
                source_order=0,
                canonical_text="```xml\n<port>8000</port>\n```",
                text_hash="code-hash",
                semantic_role="code_block",
            )
        ],
        source_format="markdown",
    )

    profile = profile_canonical_document(canonical)
    policies = resolve_chunk_policies(
        profile,
        ChunkerConfig(enable_parent_child=True),
        materialize_parent=False,
    )

    assert profile.primary_kind == "code_or_technical"
    assert profile.page_count == 1
    assert profile.pages[0].page_no == 1
    assert any(
        item["signal"] == "logical_page_fallback"
        for item in profile.pages[0].evidence
    )
    assert policies[0].policy_id == "code-technical-v1"
    assert policies[0].materialize_parent is False


def test_dcp2_scanned_and_visual_pages_do_not_require_fixture_roles():
    page_facts = {
        "page_visual_profile": {
            "decisions": [
                {
                    "page_no": 1,
                    "page_width": 612,
                    "page_height": 792,
                    "native_text_chars": 0,
                    "dominant_image_ratio": 0.92,
                    "decision": "preserve",
                },
                {
                    "page_no": 2,
                    "page_width": 612,
                    "page_height": 792,
                    "native_text_chars": 0,
                    "dominant_image_ratio": 0.92,
                    "decision": "preserve",
                },
            ]
        }
    }
    canonical = CanonicalDocument(
        markdown_content="contract text\n\nink marks",
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="scan",
                node_type="text",
                source_order=0,
                canonical_text=(
                    "The supplier shall deliver the equipment within thirty days. "
                    "Payment is due after acceptance."
                ),
                text_hash="scan",
                page_no=1,
                bbox=(55, 70, 557, 700),
            ),
            CanonicalSourceNode(
                node_id="visual",
                node_type="text",
                source_order=1,
                canonical_text="wind cloud ancient ink",
                text_hash="visual",
                page_no=2,
                bbox=(130, 110, 490, 680),
            ),
        ],
        metadata=page_facts,
        source_format="pdf",
    )

    profile = profile_canonical_document(canonical)

    assert [page.page_kind for page in profile.pages] == [
        "scanned_prose",
        "visual_dominant",
    ]
    assert all(not page.semantic_roles for page in profile.pages)


def test_dcp2_small_table_does_not_make_the_whole_page_table_dense():
    canonical = CanonicalDocument(
        markdown_content="Report\n\n" + ("body " * 150) + "\n\n| A | B |",
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="heading",
                node_type="text",
                source_order=0,
                canonical_text="Report",
                text_hash="heading",
                page_no=1,
                bbox=(50, 40, 560, 75),
                semantic_role="heading",
            ),
            CanonicalSourceNode(
                node_id="body",
                node_type="text",
                source_order=1,
                canonical_text="body " * 150,
                text_hash="body",
                page_no=1,
                bbox=(50, 90, 560, 650),
            ),
            CanonicalSourceNode(
                node_id="table",
                node_type="table",
                source_order=2,
                canonical_text="| A | B |\n|---|---|\n| 1 | 2 |",
                text_hash="table",
                page_no=1,
                bbox=(180, 670, 430, 735),
                semantic_role="table",
            ),
        ],
        metadata={
            "page_visual_profile": {
                "decisions": [
                    {
                        "page_no": 1,
                        "page_width": 612,
                        "page_height": 792,
                        "native_text_chars": 700,
                        "dominant_image_ratio": 0,
                        "decision": "normal",
                    }
                ]
            }
        },
        source_format="pdf",
    )

    profile = profile_canonical_document(canonical)
    policy = resolve_chunk_policies(profile, ChunkerConfig())[0]

    assert profile.pages[0].table_node_count == 1
    assert profile.pages[0].table_area_ratio < 0.35
    assert profile.pages[0].table_text_ratio < 0.60
    assert profile.pages[0].page_kind == "native_text"
    assert policy.policy_id == "structured-text-v1"


def test_dcp2_heading_density_counts_every_heading_node():
    nodes = [
        CanonicalSourceNode(
            node_id=f"heading-{index}",
            node_type="text",
            source_order=index,
            canonical_text=f"Heading {index}",
            text_hash=f"heading-{index}",
            page_no=1,
            semantic_role="heading",
        )
        for index in range(3)
    ]
    canonical = CanonicalDocument(
        markdown_content="\n\n".join(node.canonical_text for node in nodes),
        provenance_nodes=nodes,
        source_format="pdf",
    )

    profile = profile_canonical_document(canonical)

    assert profile.pages[0].heading_count == 3
    assert profile.heading_density == 3.0


def test_dcp2_profiles_and_resolves_mixed_document_per_page(tmp_path):
    manifest = generate_dataset(tmp_path, seed=41)
    snapshots = replay_manifest(tmp_path, manifest)
    mixed = next(
        snapshot
        for snapshot in snapshots
        if snapshot["document_id"] == "dcp-mixed"
    )
    stats = _stats(mixed)

    assert stats["document_profile_version"] == "document-profile-v1"
    assert stats["document_primary_kind"] == "mixed"
    assert [
        (page["page_no"], page["page_kind"])
        for page in stats["document_profile"]["pages"]
    ] == [
        (1, "native_text"),
        (2, "scanned_prose"),
        (3, "visual_dominant"),
    ]
    assert all(page["evidence"] for page in stats["document_profile"]["pages"])
    assert [
        (policy["page_no"], policy["policy_id"])
        for policy in stats["resolved_chunk_policies"]
    ] == [
        (1, "structured-text-v1"),
        (2, "scanned-prose-v1"),
        (3, "visual-collection-v1"),
    ]
    assert all(
        policy["shadow_only"] is True
        for policy in stats["resolved_chunk_policies"]
    )
    assert all(
        not (chunk["metadata_json"] or {}).get("chunk_policy_id")
        for chunk in mixed["layers"]["chunk_metadata"]
    )


def test_dcp2_profile_and_policy_shadow_decisions_are_deterministic(tmp_path):
    manifest = generate_dataset(tmp_path, seed=43)
    mixed = next(
        document
        for document in manifest["documents"]
        if document["document_id"] == "dcp-mixed"
    )
    first = replay_document(
        tmp_path / mixed["raw_response_path"],
        tmp_path / mixed["pdf_path"],
        tmp_path / "snapshots" / "first.json",
        document_id=mixed["document_id"],
    )
    second = replay_document(
        tmp_path / mixed["raw_response_path"],
        tmp_path / mixed["pdf_path"],
        tmp_path / "snapshots" / "second.json",
        document_id=mixed["document_id"],
    )

    first_stats = _stats(first)
    second_stats = _stats(second)
    assert first_stats["document_profile"] == second_stats["document_profile"]
    assert (
        first_stats["resolved_chunk_policies"]
        == second_stats["resolved_chunk_policies"]
    )


def test_dcp2_coverage_report_matches_all_gold_profiles_and_policies(tmp_path):
    manifest = generate_dataset(tmp_path, seed=47)
    replay_manifest(tmp_path, manifest)
    report = build_coverage_report(tmp_path, manifest)

    assert report["document_profile_dcp2"] == {
        "profiled_pages": 11,
        "matched_page_profiles": 11,
        "complete_evidence_pages": 11,
        "matched_document_profiles": 8,
        "gate_passed": True,
    }
    assert report["resolved_chunk_policy_dcp2"] == {
        "resolved_pages": 11,
        "matched_policies": 11,
        "shadow_only": True,
        "gate_passed": True,
    }
    assert report["adaptive_chunk_policy_baseline"]["implemented"] is False
    assert report["engineering_baseline_passed"] is True
    assert not json.loads(
        (tmp_path / "reports" / "coverage.json").read_text(encoding="utf-8")
    )["failures"]
