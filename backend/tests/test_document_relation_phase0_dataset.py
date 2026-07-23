from pathlib import Path

from scripts.document_relation_phase0 import (
    EXPECTED_CASE_IDS,
    build_report,
    load_dataset,
    validate_dataset,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "document_relation_phase0"
    / "dataset.json"
)


def test_phase0_has_exactly_eighteen_unique_fixed_cases():
    dataset = load_dataset(FIXTURE)

    assert validate_dataset(dataset) == []
    assert {case["case_id"] for case in dataset["cases"]} == EXPECTED_CASE_IDS


def test_phase0_every_case_has_five_traceable_layers_and_gold_evidence():
    dataset = load_dataset(FIXTURE)

    for case in dataset["cases"]:
        assert set(case["layers"]) == {
            "raw_parser",
            "parsed_blocks",
            "canonical_artifact",
            "chunk_metadata",
            "retrieval_output",
        }
        assert case["gold"]
        assert all(relation["evidence"] for relation in case["gold"])


def test_phase0_report_never_counts_synthetic_cases_as_real_pages():
    report = build_report(load_dataset(FIXTURE))

    assert report["synthetic_contract_valid"] is True
    assert report["synthetic_case_count"] == 18
    assert report["real_documents"]["page_count"] == 0
    assert report["real_documents"]["gate_passed"] is False
    assert report["phase0_passed"] is True
    assert report["real_validation_passed"] is False


def test_real_dataset_gate_remains_optional_and_separate_from_synthetic_contract():
    report = build_report(load_dataset(FIXTURE))

    assert report["real_documents"]["gate_passed"] is False
    assert report["synthetic_contract_valid"] is True
