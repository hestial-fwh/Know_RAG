from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from .templates import DOCUMENT_TYPES, SCENARIOS


EXPECTED_LAYERS = {
    "source_pdf_facts",
    "raw_parser",
    "parsed_blocks",
    "page_visual_enrichment",
    "canonical_artifact",
    "chunk_metadata",
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _media_crop_closure() -> dict[str, Any]:
    backend_root = Path(__file__).resolve().parents[2]
    service_root = backend_root / "app" / "services"
    document_service = service_root / "document_service.py"
    source = document_service.read_text(encoding="utf-8")
    consumer_pattern = re.compile(r"\.get\(\s*['\"]enable_media_crop['\"]")
    consumers = []
    for path in service_root.rglob("*.py"):
        if consumer_pattern.search(path.read_text(encoding="utf-8")):
            consumers.append(path.relative_to(backend_root).as_posix())
    return {
        "option_recorded": '"enable_media_crop"' in source,
        "runtime_consumer_detected": bool(consumers),
        "consumer_files": consumers,
        "known_gap_confirmed": '"enable_media_crop"' in source and not consumers,
    }


def build_coverage_report(output_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    scenario_counts: Counter[str] = Counter()
    page_kind_counts: Counter[str] = Counter()
    policy_counts: Counter[str] = Counter()
    expected_visual_pages = 0
    parser_visual_pages = 0
    canonical_visual_pages = 0
    enriched_visual_pages = 0
    enriched_duplicate_visual_pages = 0
    injected_visual_assets = 0
    expected_gap_pages = 0
    observed_gap_pages = 0
    native_text_expected_pages = 0
    native_text_observed_pages = 0
    expected_block_roles = 0
    preserved_block_roles = 0
    expected_non_normal_index_policies = 0
    observed_non_normal_index_policies = 0
    observed_adaptive_chunk_policies = 0
    traced_pages = 0

    for document in manifest.get("documents") or []:
        layout = _read(output_dir / document["layout_path"])
        gold = _read(output_dir / document["gold_path"])
        snapshot_path = output_dir / document.get("snapshot_path", "")
        if not snapshot_path.is_file():
            failures.append(f"{document['document_id']}: missing replay snapshot")
            continue
        snapshot = _read(snapshot_path)
        layers = snapshot.get("layers") or {}
        if set(layers) != EXPECTED_LAYERS:
            failures.append(
                f"{document['document_id']}: layer mismatch {sorted(layers)}"
            )
            continue
        facts_by_page = {
            int(item["page_no"]): item for item in layers["source_pdf_facts"]
        }
        parsed_by_page: dict[int, list[dict[str, Any]]] = {}
        for block in layers["parsed_blocks"]:
            parsed_by_page.setdefault(int(block.get("page_no") or 0), []).append(block)
        asset_pages = {
            int(item.get("page_no") or 0)
            for item in (
                (layers["canonical_artifact"].get("assets_json") or {}).get("items")
                or []
            )
            if item.get("page_no") is not None
        }
        enrichment = layers["page_visual_enrichment"]
        injected_visual_assets += int(
            (enrichment.get("metadata") or {}).get("page_visual_asset_count") or 0
        )
        enriched_by_page: dict[int, list[dict[str, Any]]] = {}
        for block in enrichment.get("blocks") or []:
            enriched_by_page.setdefault(int(block.get("page_no") or 0), []).append(
                block
            )
        gold_pages = {int(page["page_no"]): page for page in gold.get("pages") or []}
        if len(gold_pages) != len(layout.get("pages") or []):
            failures.append(
                f"{document['document_id']}: layout/gold page count mismatch"
            )

        for page in layout.get("pages") or []:
            page_no = int(page["page_no"])
            traced_pages += 1
            scenario_counts[page["case_id"]] += 1
            page_kind_counts[page["page_kind"]] += 1
            policy_counts[page["chunk_policy_id"]] += 1
            fact = facts_by_page.get(page_no)
            gold_page = gold_pages.get(page_no)
            if fact is None or gold_page is None:
                failures.append(
                    f"{document['document_id']}: page {page_no} missing fact/gold"
                )
                continue
            expected_images = sum(
                block["block_type"] == "image" for block in page.get("blocks") or []
            )
            if fact["embedded_image_count"] != expected_images:
                failures.append(
                    f"{document['document_id']}: page {page_no} embedded image mismatch"
                )
            native_expected = any(
                block.get("native_pdf_text") and block.get("text")
                for block in page.get("blocks") or []
            )
            native_text_expected_pages += int(native_expected)
            native_text_observed_pages += int(fact["native_text_chars"] > 0)
            if native_expected != (fact["native_text_chars"] > 0):
                failures.append(
                    f"{document['document_id']}: page {page_no} native text mismatch"
                )

            parsed_blocks = parsed_by_page.get(page_no, [])
            parser_has_image = any(
                block.get("block_type") == "image" for block in parsed_blocks
            )
            preserve = bool(gold_page["preserve_page_visual"])
            expected_visual_pages += int(preserve)
            parser_visual_pages += int(preserve and parser_has_image)
            canonical_visual_pages += int(preserve and page_no in asset_pages)
            full_page_blocks = []
            for block in enriched_by_page.get(page_no, []):
                if block.get("block_type") != "image":
                    continue
                metadata = block.get("metadata") or {}
                bbox = block.get("bbox")
                area_ratio = 0.0
                if isinstance(bbox, list) and len(bbox) >= 4:
                    area_ratio = (
                        max(0.0, float(bbox[2]) - float(bbox[0]))
                        * max(0.0, float(bbox[3]) - float(bbox[1]))
                        / max(
                            float(fact["page_width"]) * float(fact["page_height"]),
                            1.0,
                        )
                    )
                if (
                    metadata.get("visual_role") == "page_primary_visual"
                    or area_ratio >= 0.65
                ):
                    full_page_blocks.append(block)
            enriched_visual_pages += int(preserve and bool(full_page_blocks))
            enriched_duplicate_visual_pages += int(
                preserve and len(full_page_blocks) > 1
            )
            expected_gap = "missing_page_primary_visual" in set(
                gold_page.get("expected_known_gaps") or []
            )
            expected_gap_pages += int(expected_gap)
            observed_gap = preserve and not parser_has_image
            observed_gap_pages += int(observed_gap)
            if expected_gap != observed_gap:
                failures.append(
                    f"{document['document_id']}: page {page_no} known visual gap drift"
                )

            observed_roles = Counter(
                str((block.get("metadata") or {}).get("role") or "")
                for block in parsed_blocks
            )
            expected_roles: Counter[str] = Counter()
            for policy in gold_page.get("block_policies") or []:
                if not policy.get("parser_output_expected"):
                    continue
                expected_role = str(policy.get("semantic_role") or "")
                if expected_role:
                    expected_block_roles += 1
                    expected_roles[expected_role] += 1
                if policy.get("index_policy") != "normal":
                    expected_non_normal_index_policies += 1
                    observed_non_normal_index_policies += int(
                        any(
                            (block.get("metadata") or {}).get("role") == expected_role
                            and (block.get("metadata") or {}).get(
                                "index_policy", "normal"
                            )
                            != "normal"
                            for block in parsed_blocks
                        )
                    )
            preserved_block_roles += sum(
                min(count, observed_roles.get(role, 0))
                for role, count in expected_roles.items()
            )

        for chunk in layers["chunk_metadata"]:
            metadata = chunk.get("metadata_json") or {}
            observed_adaptive_chunk_policies += int(
                bool(metadata.get("chunk_policy_id"))
            )

    media_crop = _media_crop_closure()
    scenario_gate = set(scenario_counts) == set(SCENARIOS) and all(
        scenario_counts[scenario] == 1 for scenario in SCENARIOS
    )
    document_type_gate = set(manifest.get("document_types") or []) == set(
        DOCUMENT_TYPES
    )
    five_layer_gate = traced_pages == manifest.get("page_count") and not any(
        "layer mismatch" in failure or "missing replay" in failure
        for failure in failures
    )
    known_gap_gate = expected_gap_pages > 0 and expected_gap_pages == observed_gap_pages
    page_visual_closure_gate = (
        enriched_visual_pages == expected_visual_pages
        and enriched_duplicate_visual_pages == 0
        and injected_visual_assets == expected_gap_pages
    )
    report = {
        "schema_version": 1,
        "dataset_id": manifest.get("dataset_id"),
        "document_count": manifest.get("document_count"),
        "page_count": manifest.get("page_count"),
        "traced_page_count": traced_pages,
        "document_type_gate_passed": document_type_gate,
        "scenario_gate_passed": scenario_gate,
        "five_layer_trace_gate_passed": five_layer_gate,
        "page_kind_counts": dict(sorted(page_kind_counts.items())),
        "recommended_chunk_policy_counts": dict(sorted(policy_counts.items())),
        "source_pdf_facts": {
            "native_text_expected_pages": native_text_expected_pages,
            "native_text_observed_pages": native_text_observed_pages,
        },
        "page_visual_baseline": {
            "expected_preserve_pages": expected_visual_pages,
            "parser_image_pages": parser_visual_pages,
            "canonical_asset_pages": canonical_visual_pages,
            "expected_missing_visual_pages": expected_gap_pages,
            "observed_missing_visual_pages": observed_gap_pages,
            "known_gap_gate_passed": known_gap_gate,
        },
        "page_visual_dcp1": {
            "expected_preserve_pages": expected_visual_pages,
            "enriched_visual_pages": enriched_visual_pages,
            "injected_visual_assets": injected_visual_assets,
            "duplicate_visual_pages": enriched_duplicate_visual_pages,
            "closure_gate_passed": page_visual_closure_gate,
        },
        "semantic_role_baseline": {
            "expected_parser_roles": expected_block_roles,
            "preserved_parser_roles": preserved_block_roles,
            "preservation_rate": round(preserved_block_roles / expected_block_roles, 4)
            if expected_block_roles
            else 0.0,
        },
        "index_policy_baseline": {
            "expected_non_normal_blocks": expected_non_normal_index_policies,
            "observed_non_normal_blocks": observed_non_normal_index_policies,
            "implemented": observed_non_normal_index_policies > 0,
        },
        "adaptive_chunk_policy_baseline": {
            "chunks_with_policy_id": observed_adaptive_chunk_policies,
            "implemented": observed_adaptive_chunk_policies > 0,
        },
        "configuration_closure": {"enable_media_crop": media_crop},
        "failures": failures,
        "accuracy_claim_allowed": False,
    }
    report["engineering_baseline_passed"] = all(
        (
            document_type_gate,
            scenario_gate,
            five_layer_gate,
            known_gap_gate,
            page_visual_closure_gate,
            not failures,
            media_crop["option_recorded"],
            media_crop["runtime_consumer_detected"],
        )
    )
    report_path = output_dir / "reports" / "coverage.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


__all__ = ["EXPECTED_LAYERS", "build_coverage_report"]
