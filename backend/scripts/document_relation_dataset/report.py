from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from .generator import DATASET_TYPES, SCENARIOS


def _coverage(rows: list[dict], field: str) -> dict[str, int | float]:
    total = len(rows)
    present = sum(row.get(field) not in (None, "", []) for row in rows)
    return {
        "present": present,
        "total": total,
        "rate": round(present / total, 4) if total else 0.0,
    }


def _relation_figure_id(relation: dict[str, Any]) -> str:
    for evidence in relation.get("evidence") or []:
        figure_id = str(evidence.get("figure_id") or "").strip().lower()
        if figure_id:
            return figure_id
        cue = str(evidence.get("cue") or "")
        match = re.search(r"figure\s*([0-9]+)", cue, flags=re.IGNORECASE)
        if match:
            return f"figure:{int(match.group(1))}"
    return ""


def build_coverage_report(output_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    layout_blocks: list[dict] = []
    gold_relations: list[dict] = []
    scenario_by_type: dict[str, Counter] = {}
    trace_failures: list[str] = []
    gold_alignment_failures: list[str] = []
    retrieval_replay_failures: list[str] = []
    scope_guard_failures: list[str] = []
    explicit_conformance_failures: list[str] = []
    parsed_blocks: list[dict] = []
    current_links: list[dict] = []
    relation_ids: set[str] = set()
    retrieval_case_count = 0
    scope_guard_case_count = 0
    explicit_relation_count = 0
    phase3_counts_by_type: dict[str, Counter] = {
        document_type: Counter() for document_type in DATASET_TYPES
    }

    for document in manifest["documents"]:
        layout = json.loads((output_dir / document["layout_path"]).read_text(encoding="utf-8"))
        pages = layout["pages"]
        document_gold_relations = [
            relation
            for page in pages
            for relation in page.get("gold_relations", [])
        ]
        gold_path = document.get("gold_path")
        if not gold_path:
            gold_alignment_failures.append(f"{document['document_id']}: gold export path missing")
        else:
            try:
                gold_export = json.loads((output_dir / gold_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                gold_alignment_failures.append(
                    f"{document['document_id']}: gold export unreadable ({exc})"
                )
            else:
                if gold_export.get("document_type") != document["document_type"]:
                    gold_alignment_failures.append(
                        f"{document['document_id']}: gold export document_type mismatch"
                    )
                if gold_export.get("relations") != document_gold_relations:
                    gold_alignment_failures.append(
                        f"{document['document_id']}: gold export differs from layout gold"
                    )
        scenario_by_type[document["document_type"]] = Counter(page["scenario"] for page in pages)
        block_ids = {
            block["block_id"]
            for page in pages
            for block in page.get("blocks", [])
            if block.get("block_id")
        }
        for page in pages:
            layout_blocks.extend(page["blocks"])
            gold_relations.extend(page["gold_relations"])
            for relation in page["gold_relations"]:
                relation_id = relation.get("relation_id")
                if not relation_id or relation_id in relation_ids:
                    gold_alignment_failures.append(
                        f"{document['document_id']}: duplicate or missing relation_id {relation_id!r}"
                    )
                relation_ids.add(relation_id)
                referenced_ids = {
                    *relation.get("source_node_ids", []),
                    *relation.get("target_asset_ids", []),
                    *relation.get("candidate_targets", []),
                }
                missing_ids = sorted(referenced_ids - block_ids)
                if missing_ids:
                    gold_alignment_failures.append(
                        f"{relation_id}: gold IDs missing from layout {missing_ids}"
                    )
                if not relation.get("evidence"):
                    gold_alignment_failures.append(f"{relation_id}: evidence missing")
        snapshot_path = document.get("snapshot_path")
        if not snapshot_path:
            trace_failures.append(f"{document['document_id']}: snapshot missing")
            continue
        snapshot = json.loads((output_dir / snapshot_path).read_text(encoding="utf-8"))
        layers = snapshot.get("layers") or {}
        required = {"raw_parser", "parsed_blocks", "canonical_artifact", "chunk_metadata", "retrieval_output"}
        missing = required - set(layers)
        if missing:
            trace_failures.append(f"{document['document_id']}: missing layers {sorted(missing)}")
        raw_count = (layers.get("raw_parser") or {}).get("content_list_count", 0)
        document_parsed_blocks = layers.get("parsed_blocks") or []
        document_chunks = layers.get("chunk_metadata") or []
        canonical = layers.get("canonical_artifact") or {}
        retrieval = layers.get("retrieval_output") or {}
        if raw_count <= 0:
            trace_failures.append(f"{document['document_id']}: raw parser layer is empty")
        if not document_parsed_blocks:
            trace_failures.append(f"{document['document_id']}: parsed block layer is empty")
        if raw_count != len(document_parsed_blocks):
            trace_failures.append(
                f"{document['document_id']}: raw/parsed count mismatch "
                f"({raw_count} != {len(document_parsed_blocks)})"
            )
        if not (canonical.get("provenance") or []):
            trace_failures.append(f"{document['document_id']}: canonical provenance is empty")
        if not document_chunks:
            trace_failures.append(f"{document['document_id']}: chunk metadata layer is empty")

        expected_direct_ids = {
            chunk["id"]
            for chunk in document_chunks
            if (chunk.get("metadata_json") or {}).get("figure_references")
            or (chunk.get("metadata_json") or {}).get("image_binding")
            or (chunk.get("metadata_json") or {}).get("image_bindings")
        }
        replay_cases = retrieval.get("cases") or []
        retrieval_case_count += len(replay_cases)
        replay_direct_ids = {
            case.get("direct_hit", {}).get("id") for case in replay_cases
        } - {None}
        if replay_direct_ids != expected_direct_ids:
            retrieval_replay_failures.append(
                f"{document['document_id']}: retrieval direct coverage mismatch "
                f"missing={sorted(expected_direct_ids - replay_direct_ids)}, "
                f"extra={sorted(replay_direct_ids - expected_direct_ids)}"
            )
        for case in replay_cases:
            requested = case.get("requested_target_ids") or []
            expanded_ids = [item.get("id") for item in case.get("expanded_hits") or []]
            if expanded_ids != requested:
                retrieval_replay_failures.append(
                    f"{document['document_id']}:{case.get('direct_hit', {}).get('id')}: "
                    f"requested/expanded mismatch ({requested} != {expanded_ids})"
                )
        expected_scope_guard_cases = sum(
            page.get("scenario") == "contaminated_scope" for page in pages
        )
        scope_guard_cases = retrieval.get("scope_guard_cases") or []
        scope_guard_case_count += len(scope_guard_cases)
        if len(scope_guard_cases) != expected_scope_guard_cases:
            scope_guard_failures.append(
                f"{document['document_id']}: scope guard replay count mismatch "
                f"({len(scope_guard_cases)} != {expected_scope_guard_cases})"
            )
        for case in scope_guard_cases:
            if case.get("source_document_id") == case.get("target_document_id"):
                scope_guard_failures.append(
                    f"{document['document_id']}: contaminated target did not cross document scope"
                )
            if case.get("expanded_hits"):
                scope_guard_failures.append(
                    f"{document['document_id']}:{case.get('direct_hit_id')}: "
                    "cross-document target was expanded"
                )

        document_links = (snapshot.get("current_figure_links") or {}).get("links") or []
        relation_graph = snapshot.get("current_relation_graph") or {}
        canonical_assets = canonical.get("assets") or {}
        layout_to_canonical_asset: dict[str, str] = {}
        for page in pages:
            for block in page.get("blocks", []):
                if block.get("block_type") != "image" or block.get("bbox") is None:
                    continue
                expected_key = (block.get("page_no"), tuple(float(v) for v in block["bbox"]))
                for asset_id, asset in canonical_assets.items():
                    actual_bbox = asset.get("bbox")
                    actual_key = (
                        asset.get("page_no"),
                        tuple(float(v) for v in actual_bbox) if actual_bbox is not None else None,
                    )
                    if actual_key == expected_key:
                        layout_to_canonical_asset[block["block_id"]] = asset_id
                        break

        link_buckets: dict[tuple[int, str], list[dict]] = {}
        for link in document_links:
            key = (int(link.get("page_no") or 0), str(link.get("figure_id") or ""))
            link_buckets.setdefault(key, []).append(link)
        for links in link_buckets.values():
            links.sort(key=lambda item: (item.get("start") or 0, item.get("end") or 0))
        bucket_offsets: Counter = Counter()
        for page in pages:
            for relation in page.get("gold_relations", []):
                if relation.get("relation_type") != "explicit_reference":
                    continue
                explicit_relation_count += 1
                key = (int(page["page_no"]), _relation_figure_id(relation))
                offset = bucket_offsets[key]
                bucket_offsets[key] += 1
                links = link_buckets.get(key, [])
                if offset >= len(links):
                    explicit_conformance_failures.append(
                        f"{relation['relation_id']}: no replayed explicit occurrence for {key}"
                    )
                    continue
                observed = links[offset]
                if observed.get("status") != relation.get("status"):
                    explicit_conformance_failures.append(
                        f"{relation['relation_id']}: status mismatch "
                        f"({relation.get('status')} != {observed.get('status')})"
                    )
                    continue
                if relation.get("status") == "bound":
                    expected_assets = [
                        layout_to_canonical_asset.get(target_id)
                        for target_id in relation.get("target_asset_ids", [])
                    ]
                    if None in expected_assets or observed.get("asset_ids", []) != expected_assets:
                        explicit_conformance_failures.append(
                            f"{relation['relation_id']}: target mismatch "
                            f"({expected_assets} != {observed.get('asset_ids', [])})"
                        )

        canonical_to_layout_asset = {
            canonical_id: layout_id
            for layout_id, canonical_id in layout_to_canonical_asset.items()
        }
        graph_groups = {
            group.get("group_id"): group for group in relation_graph.get("groups") or []
        }
        expected_phase3 = {
            tuple(sorted(relation.get("target_asset_ids") or []))
            for relation in document_gold_relations
            if relation.get("relation_type") == "shared_context_of"
            and relation.get("status") == "bound"
            and relation.get("target_asset_ids")
        }
        predicted_phase3: set[tuple[str, ...]] = set()
        for edge in relation_graph.get("edges") or []:
            if (
                edge.get("relation_type") != "shared_context_of"
                or edge.get("status") != "bound"
            ):
                continue
            for group_id in edge.get("target_group_ids") or []:
                group = graph_groups.get(group_id) or {}
                layout_ids = [
                    canonical_to_layout_asset.get(asset_id)
                    for asset_id in group.get("member_asset_ids") or []
                ]
                if layout_ids and None not in layout_ids:
                    predicted_phase3.add(tuple(sorted(layout_ids)))
        counts = phase3_counts_by_type[document["document_type"]]
        counts["gold"] += len(expected_phase3)
        counts["predicted"] += len(predicted_phase3)
        counts["true_positive"] += len(expected_phase3 & predicted_phase3)

        parsed_blocks.extend(document_parsed_blocks)
        current_links.extend(document_links)

    status_counts = Counter(relation["status"] for relation in gold_relations)
    type_counts = Counter(relation["relation_type"] for relation in gold_relations)
    current_status_counts = Counter(link.get("status") for link in current_links)
    pages_by_type = {document["document_type"]: document["page_count"] for document in manifest["documents"]}
    scenario_gaps = {
        document_type: [
            scenario
            for scenario in SCENARIOS
            if scenario_by_type.get(document_type, Counter()).get(scenario, 0) == 0
        ]
        for document_type in DATASET_TYPES
    }
    parser_extent_present = sum(
        bool((block.get("metadata") or {}).get("page_width"))
        and bool((block.get("metadata") or {}).get("page_height"))
        for block in parsed_blocks
    )
    phase3_metrics_by_type = {}
    for document_type, counts in phase3_counts_by_type.items():
        gold = counts["gold"]
        predicted = counts["predicted"]
        true_positive = counts["true_positive"]
        phase3_metrics_by_type[document_type] = {
            "gold_bound_groups": gold,
            "predicted_bound_groups": predicted,
            "true_positive_groups": true_positive,
            "precision": round(true_positive / predicted, 4) if predicted else 0.0,
            "recall": round(true_positive / gold, 4) if gold else 0.0,
        }

    report = {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "source_kind": manifest["provenance"]["source_kind"],
        "page_count": manifest["page_count"],
        "pages_by_type": pages_by_type,
        "minimum_pages_gate": {
            "minimum": 5,
            "passed": all(pages_by_type.get(kind, 0) >= 5 for kind in DATASET_TYPES),
        },
        "scenario_count": len(SCENARIOS),
        "scenario_gaps_by_type": scenario_gaps,
        "scenario_gate_passed": not any(scenario_gaps.values()),
        "gold_relation_count": len(gold_relations),
        "gold_status_counts": dict(sorted(status_counts.items())),
        "gold_relation_type_counts": dict(sorted(type_counts.items())),
        "layout_field_coverage": {
            "bbox": _coverage(layout_blocks, "bbox"),
            "page_no": _coverage(layout_blocks, "page_no"),
            "role": _coverage(layout_blocks, "role"),
            "section_path": _coverage(layout_blocks, "section_path"),
        },
        "parsed_field_coverage": {
            "bbox": _coverage(parsed_blocks, "bbox"),
            "page_no": _coverage(parsed_blocks, "page_no"),
            "context_header": _coverage(parsed_blocks, "context_header"),
            "page_extent": {
                "present": parser_extent_present,
                "total": len(parsed_blocks),
                "rate": round(parser_extent_present / len(parsed_blocks), 4)
                if parsed_blocks
                else 0.0,
            },
        },
        "current_figure_link_status_counts": dict(sorted(current_status_counts.items())),
        "trace_failures": trace_failures,
        "five_layer_trace_gate_passed": not trace_failures,
        "gold_alignment_failures": gold_alignment_failures,
        "gold_alignment_gate_passed": not gold_alignment_failures,
        "retrieval_replay_case_count": retrieval_case_count,
        "retrieval_replay_failures": retrieval_replay_failures,
        "retrieval_replay_gate_passed": not retrieval_replay_failures and retrieval_case_count > 0,
        "scope_guard_replay_case_count": scope_guard_case_count,
        "scope_guard_failures": scope_guard_failures,
        "scope_guard_gate_passed": not scope_guard_failures and scope_guard_case_count > 0,
        "explicit_gold_relation_count": explicit_relation_count,
        "explicit_conformance_failures": explicit_conformance_failures,
        "explicit_conformance_gate_passed": (
            explicit_relation_count > 0 and not explicit_conformance_failures
        ),
        "phase3_group_shared_metrics_by_document_type": phase3_metrics_by_type,
        "phase3_accuracy_gate_applied": False,
        "engineering_baseline_passed": (
            all(pages_by_type.get(kind, 0) >= 5 for kind in DATASET_TYPES)
            and not any(scenario_gaps.values())
            and not trace_failures
            and not gold_alignment_failures
            and not retrieval_replay_failures
            and retrieval_case_count > 0
            and not scope_guard_failures
            and scope_guard_case_count > 0
            and explicit_relation_count > 0
            and not explicit_conformance_failures
        ),
        "accuracy_claim_allowed": False,
        "limitations": [
            "合成数据验证工程规则覆盖，不代表真实 MinerU precision/recall",
            "只有 MinerU raw page_width/page_height 同时有效时才标记 authoritative page extent",
            "Phase 3 图片组与共享上下文只做同页保守规则；方向和通用代词 resolver 尚未实现",
            "Phase 3 合成 precision/recall 仅报告，不设生产准确率门槛",
        ],
    }
    report_path = output_dir / "reports" / "coverage.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report
