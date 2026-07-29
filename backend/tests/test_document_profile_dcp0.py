import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pypdf import PdfReader

from app.services.chunk_service import rebuild_document_chunks
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.mineru_adapter import (
    _content_list_to_blocks,
    _find_content_list,
)
from app.services.document_parser.types import ParsedDocument
from app.plugins.parse_eval.service import document_profile_dataset_report
from scripts.document_profile_dataset.generator import (
    DOCUMENT_TYPES,
    SCENARIOS,
    generate_dataset,
)
from scripts.document_profile_dataset.replay import replay_manifest
from scripts.document_profile_dataset.report import (
    EXPECTED_LAYERS,
    build_coverage_report,
)


SPEC_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "document_profile_dcp0"
    / "generated_corpus_spec.json"
)


def test_dcp0_tracked_spec_matches_generator_contract():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    assert spec["document_types"] == list(DOCUMENT_TYPES)
    assert spec["scenarios"] == list(SCENARIOS)
    assert spec["document_count"] == 11
    assert spec["page_count"] == 42
    assert spec["baseline_page_count"] == 11
    assert spec["adversarial_page_count"] == 31


def test_dcp0_generator_creates_pdf_assets_raw_layout_and_gold(tmp_path):
    manifest = generate_dataset(tmp_path, seed=17)

    assert manifest["document_count"] == 11
    assert manifest["page_count"] == 42
    assert manifest["baseline_document_count"] == len(DOCUMENT_TYPES)
    assert manifest["baseline_page_count"] == 11
    assert manifest["adversarial_page_count"] == 31
    assert manifest["provenance"]["external_assets"] is False
    assert manifest["provenance"]["external_model_calls"] is False
    observed_scenarios = set()
    for document in manifest["documents"]:
        assert (
            len(PdfReader(tmp_path / document["pdf_path"]).pages)
            == document["page_count"]
        )
        assert (tmp_path / document["layout_path"]).is_file()
        assert (tmp_path / document["raw_response_path"]).is_file()
        assert (tmp_path / document["gold_path"]).is_file()
        assert all((tmp_path / asset["path"]).is_file() for asset in document["assets"])
        observed_scenarios.update(document["scenarios"])
    assert observed_scenarios == set(SCENARIOS)


def test_dcp0_generator_is_deterministic(tmp_path):
    first = generate_dataset(tmp_path / "first", seed=19)
    second = generate_dataset(tmp_path / "second", seed=19)

    assert [document["pdf_sha256"] for document in first["documents"]] == [
        document["pdf_sha256"] for document in second["documents"]
    ]
    assert [
        [asset["sha256"] for asset in document["assets"]]
        for document in first["documents"]
    ] == [
        [asset["sha256"] for asset in document["assets"]]
        for document in second["documents"]
    ]
    for hash_key in (
        "layout_sha256",
        "raw_response_sha256",
        "gold_sha256",
    ):
        assert [document[hash_key] for document in first["documents"]] == [
            document[hash_key] for document in second["documents"]
        ]


def test_dcp0_replay_and_report_close_five_layer_baseline(tmp_path):
    manifest = generate_dataset(tmp_path, seed=23)
    snapshots = replay_manifest(tmp_path, manifest)
    report = build_coverage_report(tmp_path, manifest)

    assert len(snapshots) == 11
    assert all(set(snapshot["layers"]) == EXPECTED_LAYERS for snapshot in snapshots)
    assert report["engineering_baseline_passed"] is True
    assert report["document_type_gate_passed"] is True
    assert report["scenario_gate_passed"] is True
    assert report["five_layer_trace_gate_passed"] is True
    assert report["traced_page_count"] == 11
    assert report["replayed_page_count"] == 42
    assert report["dcp01_dataset_gate_passed"] is True
    adversarial = report["adversarial_dataset"]
    assert adversarial["case_count"] == 31
    assert adversarial["gate_passed"] is True
    assert adversarial["artifact_hashes_complete"] is True
    assert adversarial["visual_item_cases"] == [
        f"VI-{index:02d}" for index in range(1, 11)
    ]
    assert (tmp_path / "reports" / "coverage.html").is_file()
    visual = report["page_visual_baseline"]
    assert visual == {
        "expected_preserve_pages": 6,
        "parser_image_pages": 2,
        "canonical_asset_pages": 6,
        "expected_missing_visual_pages": 4,
        "observed_missing_visual_pages": 4,
        "known_gap_gate_passed": True,
    }
    assert report["page_visual_dcp1"] == {
        "expected_preserve_pages": 6,
        "enriched_visual_pages": 6,
        "injected_visual_assets": 4,
        "duplicate_visual_pages": 0,
        "closure_gate_passed": True,
    }
    assert report["index_policy_baseline"]["implemented"] is False
    assert report["adaptive_chunk_policy_baseline"]["implemented"] is True
    assert report["document_profile_dcp2"]["gate_passed"] is True
    assert report["resolved_chunk_policy_dcp2"]["gate_passed"] is True
    assert report["adaptive_chunk_policy_dcp3"]["gate_passed"] is True
    closure = report["configuration_closure"]["enable_media_crop"]
    assert closure["option_recorded"] is True
    assert closure["runtime_consumer_detected"] is True
    assert closure["known_gap_confirmed"] is False
    assert report["accuracy_claim_allowed"] is False


def test_dcp0_report_fails_when_expected_visual_gap_drifts(tmp_path):
    manifest = generate_dataset(tmp_path, seed=29)
    replay_manifest(tmp_path, manifest)
    visual_document = manifest["documents"][0]
    snapshot_path = tmp_path / visual_document["snapshot_path"]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["layers"]["parsed_blocks"][0]["block_type"] = "image"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    report = build_coverage_report(tmp_path, manifest)

    assert report["engineering_baseline_passed"] is False
    assert report["page_visual_baseline"]["known_gap_gate_passed"] is False
    assert any("known visual gap drift" in failure for failure in report["failures"])


def test_dcp01_report_is_available_to_parse_eval_frontend(tmp_path):
    manifest = generate_dataset(tmp_path, seed=24)
    replay_manifest(tmp_path, manifest)
    build_coverage_report(tmp_path, manifest)

    response = document_profile_dataset_report(tmp_path)

    assert response["success"] is True
    assert response["data"]["available"] is True
    assert response["data"]["manifest"]["page_count"] == 42
    assert response["data"]["report"]["dcp01_dataset_gate_passed"] is True


def test_dcp0_report_fails_when_media_option_has_no_runtime_consumer(
    monkeypatch,
    tmp_path,
):
    manifest = generate_dataset(tmp_path, seed=30)
    replay_manifest(tmp_path, manifest)
    monkeypatch.setattr(
        "scripts.document_profile_dataset.report._media_crop_closure",
        lambda: {
            "option_recorded": True,
            "runtime_consumer_detected": False,
            "consumer_files": [],
            "known_gap_confirmed": True,
        },
    )

    report = build_coverage_report(tmp_path, manifest)

    assert report["engineering_baseline_passed"] is False


def test_dcp0_mixed_document_reaches_production_rebuild_entrypoint(tmp_path):
    manifest = generate_dataset(tmp_path, seed=31)
    mixed = next(
        document
        for document in manifest["documents"]
        if document["document_type"] == "mixed"
    )
    payload = json.loads(
        (tmp_path / mixed["raw_response_path"]).read_text(encoding="utf-8")
    )
    content_list = _find_content_list(payload)
    for item in content_list:
        item.pop("img_path", None)
    blocks = _content_list_to_blocks(
        content_list,
        {},
        knowledge_base_id="dcp0-kb",
        document_id="dcp0-mixed",
    )
    parsed = ParsedDocument(
        content="\n\n".join(block.index_text() for block in blocks),
        blocks=blocks,
        metadata={"parser": "mineru", "source": "dcp0_entrypoint"},
    )
    document = SimpleNamespace(
        id="dcp0-mixed",
        knowledge_base_id="dcp0-kb",
        title="DCP-0 mixed",
        file_name="dcp0-mixed.pdf",
        file_type="pdf",
    )
    db = MagicMock()
    config = ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
        separators=["\n\n", "\n", ". "],
    )

    with (
        patch(
            "app.services.chunk_service._replace_canonical_artifact"
        ) as replace_artifact,
        patch("app.services.chunk_service.update_knowledge_base_chunk_count"),
    ):
        inserted = rebuild_document_chunks(db, document, parsed, cfg_override=config)

    assert inserted > 0
    assert db.add.call_count == inserted
    db.flush.assert_called_once()
    replace_artifact.assert_called_once()
