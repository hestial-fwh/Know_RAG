import json
from pathlib import Path

from pypdf import PdfReader

from scripts.document_relation_dataset.generator import (
    DATASET_TYPES,
    SCENARIOS,
    generate_dataset,
)
from scripts.document_relation_dataset.replay import replay_manifest
from scripts.document_relation_dataset.report import build_coverage_report


SPEC_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "document_relation_phase0"
    / "generated_corpus_spec.json"
)


def test_generated_corpus_spec_matches_code_defaults():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    assert spec["document_types"] == list(DATASET_TYPES)
    assert spec["scenarios"] == list(SCENARIOS)
    assert spec["pages_per_type"] == 40


def test_generator_creates_valid_pdf_assets_layout_raw_and_gold(tmp_path):
    manifest = generate_dataset(tmp_path, pages_per_type=len(SCENARIOS), seed=7)

    assert manifest["page_count"] == len(DATASET_TYPES) * len(SCENARIOS)
    assert manifest["provenance"]["external_assets"] is False
    assert manifest["provenance"]["external_model_calls"] is False
    for document in manifest["documents"]:
        assert len(PdfReader(tmp_path / document["pdf_path"]).pages) == len(SCENARIOS)
        assert (tmp_path / document["layout_path"]).is_file()
        assert (tmp_path / document["raw_response_path"]).is_file()
        assert (tmp_path / document["gold_path"]).is_file()
        assert document["assets"]
        assert all((tmp_path / asset["path"]).is_file() for asset in document["assets"])
        assert all(count == 1 for count in document["scenario_counts"].values())


def test_generator_is_deterministic_for_same_seed(tmp_path):
    first = generate_dataset(tmp_path / "first", pages_per_type=len(SCENARIOS), seed=19)
    second = generate_dataset(tmp_path / "second", pages_per_type=len(SCENARIOS), seed=19)

    assert [item["pdf_sha256"] for item in first["documents"]] == [
        item["pdf_sha256"] for item in second["documents"]
    ]
    assert [
        [asset["sha256"] for asset in item["assets"]] for item in first["documents"]
    ] == [[asset["sha256"] for asset in item["assets"]] for item in second["documents"]]


def test_replay_builds_five_layers_and_engineering_coverage_report(tmp_path):
    manifest = generate_dataset(tmp_path, pages_per_type=len(SCENARIOS), seed=23)

    snapshots = replay_manifest(tmp_path, manifest)
    report = build_coverage_report(tmp_path, manifest)

    assert len(snapshots) == len(DATASET_TYPES)
    assert all(
        set(snapshot["layers"])
        == {
            "raw_parser",
            "parsed_blocks",
            "canonical_artifact",
            "chunk_metadata",
            "retrieval_output",
        }
        for snapshot in snapshots
    )
    assert report["engineering_baseline_passed"] is True
    assert report["minimum_pages_gate"]["passed"] is True
    assert report["scenario_gate_passed"] is True
    assert report["five_layer_trace_gate_passed"] is True
    assert report["gold_alignment_gate_passed"] is True
    assert report["retrieval_replay_gate_passed"] is True
    assert report["scope_guard_gate_passed"] is True
    assert report["scope_guard_replay_case_count"] == len(DATASET_TYPES)
    assert report["explicit_conformance_gate_passed"] is True
    assert report["retrieval_replay_case_count"] > len(DATASET_TYPES)
    assert report["parsed_field_coverage"]["page_extent"]["rate"] == 0.0
    assert report["accuracy_claim_allowed"] is False


def test_generated_range_and_cross_scope_cases_reach_real_replay(tmp_path):
    manifest = generate_dataset(tmp_path, pages_per_type=len(SCENARIOS), seed=27)
    snapshots = replay_manifest(tmp_path, manifest)

    for document, snapshot in zip(manifest["documents"], snapshots, strict=True):
        layout = json.loads(
            (tmp_path / document["layout_path"]).read_text(encoding="utf-8")
        )
        range_page = next(
            page for page in layout["pages"] if page["scenario"] == "figure_range_reference"
        )
        range_links = [
            link
            for link in snapshot["current_figure_links"]["links"]
            if link.get("page_no") == range_page["page_no"]
        ]
        assert [link["figure_id"] for link in range_links] == ["figure:1", "figure:2"]
        assert [link["reference_type"] for link in range_links] == [
            "explicit_range",
            "explicit_range",
        ]
        assert all(link["status"] == "bound" for link in range_links)

        scope_cases = snapshot["layers"]["retrieval_output"]["scope_guard_cases"]
        assert len(scope_cases) == 1
        assert scope_cases[0]["source_document_id"] != scope_cases[0]["target_document_id"]
        assert scope_cases[0]["expanded_hits"] == []


def test_cross_page_and_overlap_scenarios_exist_in_replayed_geometry(tmp_path):
    manifest = generate_dataset(tmp_path, pages_per_type=len(SCENARIOS), seed=29)
    snapshots = replay_manifest(tmp_path, manifest)
    document = manifest["documents"][0]
    layout = json.loads((tmp_path / document["layout_path"]).read_text(encoding="utf-8"))
    raw = json.loads((tmp_path / document["raw_response_path"]).read_text(encoding="utf-8"))

    cross_page = next(page for page in layout["pages"] if page["scenario"] == "cross_page")
    source = next(block for block in cross_page["blocks"] if block["block_type"] == "text" and block["role"] == "prose")
    target = next(block for block in cross_page["blocks"] if block["block_type"] == "image")
    assert target["page_no"] == source["page_no"] + 1
    content_list = json.loads(next(iter(raw["results"].values()))["content_list"])
    raw_target = next(item for item in content_list if item["source_block_id"] == target["block_id"])
    assert raw_target["page_idx"] == target["page_no"] - 1

    snapshot = snapshots[0]
    overlap_page = next(page for page in layout["pages"] if page["scenario"] == "overlap_child")
    link = next(
        item
        for item in snapshot["current_figure_links"]["links"]
        if item.get("page_no") == overlap_page["page_no"] and item.get("status") == "bound"
    )
    text_chunks = [
        chunk
        for chunk in snapshot["layers"]["chunk_metadata"]
        if chunk["chunk_type"] == "text"
        and chunk["start_at"] is not None
        and chunk["end_at"] is not None
    ]
    covering = [
        chunk
        for chunk in text_chunks
        if chunk["start_at"] <= link["start"] and chunk["end_at"] >= link["end"]
    ]
    assert len(covering) >= 2
    assert max(chunk["start_at"] for chunk in covering) < min(
        chunk["end_at"] for chunk in covering
    )


def test_coverage_gate_rejects_empty_replay_or_broken_gold_ids(tmp_path):
    manifest = generate_dataset(tmp_path, pages_per_type=len(SCENARIOS), seed=37)
    replay_manifest(tmp_path, manifest)
    document = manifest["documents"][0]
    snapshot_path = tmp_path / document["snapshot_path"]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["layers"]["parsed_blocks"] = []
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    report = build_coverage_report(tmp_path, manifest)
    assert report["five_layer_trace_gate_passed"] is False
    assert report["engineering_baseline_passed"] is False

    replay_manifest(tmp_path, manifest)
    layout_path = tmp_path / document["layout_path"]
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    layout["pages"][0]["gold_relations"][0]["target_asset_ids"] = ["missing-asset"]
    layout_path.write_text(json.dumps(layout), encoding="utf-8")

    report = build_coverage_report(tmp_path, manifest)
    assert report["gold_alignment_gate_passed"] is False
    assert report["engineering_baseline_passed"] is False

    valid_target = layout["pages"][0]["blocks"][-1]["block_id"]
    layout["pages"][0]["gold_relations"][0]["target_asset_ids"] = [valid_target]
    layout["pages"][0]["gold_relations"][0]["status"] = "rejected"
    layout_path.write_text(json.dumps(layout), encoding="utf-8")
    gold_path = tmp_path / document["gold_path"]
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    gold["relations"] = [
        relation
        for page in layout["pages"]
        for relation in page["gold_relations"]
    ]
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    report = build_coverage_report(tmp_path, manifest)
    assert report["gold_alignment_gate_passed"] is True
    assert report["explicit_conformance_gate_passed"] is False
    assert report["engineering_baseline_passed"] is False


def test_coverage_gate_rejects_stale_exported_gold(tmp_path):
    manifest = generate_dataset(tmp_path, pages_per_type=len(SCENARIOS), seed=41)
    replay_manifest(tmp_path, manifest)
    document = manifest["documents"][0]
    gold_path = tmp_path / document["gold_path"]
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    gold["relations"][0]["status"] = "stale-status"
    gold_path.write_text(json.dumps(gold), encoding="utf-8")

    report = build_coverage_report(tmp_path, manifest)

    assert report["gold_alignment_gate_passed"] is False
    assert report["engineering_baseline_passed"] is False
    assert any("gold export differs" in failure for failure in report["gold_alignment_failures"])
