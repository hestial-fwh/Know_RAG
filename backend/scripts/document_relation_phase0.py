"""Validate and report the frozen document-relation Phase 0 dataset.

This tool intentionally uses only the Python standard library.  It never calls
MinerU, a model, a database, or a running know_rag service, so a checked-in
baseline remains reproducible offline.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


EXPECTED_CASE_IDS = {f"REL-CASE-{index:03d}" for index in range(1, 19)}
REQUIRED_LAYERS = {
    "raw_parser",
    "parsed_blocks",
    "canonical_artifact",
    "chunk_metadata",
    "retrieval_output",
}
REQUIRED_GOLD_FIELDS = {"status", "auto_bind_allowed", "evidence"}
REAL_DOCUMENT_TYPES = {"album", "calligraphy", "paper", "product_manual"}


def default_dataset_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "document_relation_phase0"
        / "dataset.json"
    )


def load_dataset(path: Path | None = None) -> dict[str, Any]:
    dataset_path = path or default_dataset_path()
    with dataset_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_dataset(dataset: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cases = dataset.get("cases")
    if not isinstance(cases, list):
        return ["cases must be a list"]

    case_ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    duplicate_ids = sorted(case_id for case_id, count in Counter(case_ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"duplicate case IDs: {duplicate_ids}")
    actual_ids = {case_id for case_id in case_ids if isinstance(case_id, str)}
    if actual_ids != EXPECTED_CASE_IDS:
        errors.append(
            "case ID set mismatch: "
            f"missing={sorted(EXPECTED_CASE_IDS - actual_ids)}, "
            f"extra={sorted(actual_ids - EXPECTED_CASE_IDS)}"
        )

    for case in cases:
        if not isinstance(case, dict):
            errors.append("case must be an object")
            continue
        case_id = str(case.get("case_id") or "<missing>")
        layers = case.get("layers")
        if not isinstance(layers, dict):
            errors.append(f"{case_id}: layers must be an object")
        else:
            missing_layers = REQUIRED_LAYERS - set(layers)
            if missing_layers:
                errors.append(f"{case_id}: missing layers {sorted(missing_layers)}")

        gold = case.get("gold")
        if not isinstance(gold, list) or not gold:
            errors.append(f"{case_id}: gold must be a non-empty list")
            continue
        for index, relation in enumerate(gold):
            if not isinstance(relation, dict):
                errors.append(f"{case_id}: gold[{index}] must be an object")
                continue
            missing_gold = REQUIRED_GOLD_FIELDS - set(relation)
            if missing_gold:
                errors.append(f"{case_id}: gold[{index}] missing {sorted(missing_gold)}")
            evidence = relation.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{case_id}: gold[{index}].evidence must be non-empty")

    return errors


def _coverage(rows: list[dict[str, Any]], field: str) -> dict[str, int | float]:
    total = len(rows)
    present = sum(row.get(field) not in (None, "", []) for row in rows)
    return {
        "present": present,
        "total": total,
        "rate": round(present / total, 4) if total else 0.0,
    }


def build_report(dataset: dict[str, Any]) -> dict[str, Any]:
    errors = validate_dataset(dataset)
    real_documents = dataset.get("real_documents")
    if not isinstance(real_documents, list):
        real_documents = []

    page_counts = Counter()
    blocks: list[dict[str, Any]] = []
    authoritative_extent_pages = 0
    total_pages = 0
    for document in real_documents:
        if not isinstance(document, dict):
            continue
        document_type = str(document.get("document_type") or "")
        pages = document.get("pages") if isinstance(document.get("pages"), list) else []
        page_counts[document_type] += len(pages)
        total_pages += len(pages)
        for page in pages:
            if not isinstance(page, dict):
                continue
            if page.get("page_width") is not None and page.get("page_height") is not None:
                authoritative_extent_pages += 1
            page_blocks = page.get("blocks")
            if isinstance(page_blocks, list):
                blocks.extend(item for item in page_blocks if isinstance(item, dict))

    real_gate_failures = {
        kind: max(0, 5 - page_counts.get(kind, 0)) for kind in sorted(REAL_DOCUMENT_TYPES)
    }
    real_gate_passed = not any(real_gate_failures.values())
    relations = [
        relation
        for case in dataset.get("cases", [])
        if isinstance(case, dict)
        for relation in case.get("gold", [])
        if isinstance(relation, dict)
    ]

    return {
        "schema_version": 1,
        "dataset_id": dataset.get("dataset_id"),
        "synthetic_case_count": len(dataset.get("cases", [])),
        "synthetic_contract_valid": not errors,
        "validation_errors": errors,
        "gold_status_counts": dict(sorted(Counter(item.get("status") for item in relations).items())),
        "real_documents": {
            "document_count": len(real_documents),
            "page_count": total_pages,
            "pages_by_type": {
                kind: page_counts.get(kind, 0) for kind in sorted(REAL_DOCUMENT_TYPES)
            },
            "minimum_pages_by_type": 5,
            "missing_pages_by_type": real_gate_failures,
            "gate_passed": real_gate_passed,
        },
        "field_coverage": {
            "bbox": _coverage(blocks, "bbox"),
            "page_no": _coverage(blocks, "page_no"),
            "role": _coverage(blocks, "role"),
            "section_path": _coverage(blocks, "section_path"),
            "vlm_description": _coverage(blocks, "vlm_description"),
            "authoritative_page_extent": {
                "present": authoritative_extent_pages,
                "total": total_pages,
                "rate": round(authoritative_extent_pages / total_pages, 4)
                if total_pages
                else 0.0,
            },
        },
        "phase0_passed": not errors,
        "real_validation_passed": real_gate_passed,
        "real_validation_blockers": (
            []
            if real_gate_passed
            else [
                "四类真实 MinerU 页面未达到每类 5 页；不能计算或宣称真实准确率",
                "尚无可复查的真实 MinerU 原始响应用于判断 page width/height",
            ]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=default_dataset_path())
    parser.add_argument("--strict-real", action="store_true")
    args = parser.parse_args()
    report = build_report(load_dataset(args.dataset))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["validation_errors"]:
        return 1
    if args.strict_real and not report["real_documents"]["gate_passed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
