from __future__ import annotations

from collections import Counter
from html import escape
import json
from pathlib import Path
import re
from typing import Any

from app.services.chunker.chunk_policy_resolver import DCP3_ACTIVE_POLICY_IDS

from .templates import BASE_SCENARIOS, DOCUMENT_TYPES, SCENARIOS


EXPECTED_LAYERS = {
    "source_pdf_facts",
    "raw_parser",
    "parsed_blocks",
    "page_visual_enrichment",
    "canonical_artifact",
    "chunk_metadata",
}
OCR_ADMISSION_STABILITY_PAIRS = {
    "poster-char-count": (("29", "30"), ("400", "401")),
    "dominant-image-ratio": (("below", "above"),),
    "parser-text-area": (("below", "above"),),
    "vlm_resilience": (("missing", "failed"),),
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


def _visual_relation_checks(
    gold_page: dict[str, Any],
    actual_layout: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    policies = gold_page.get("block_policies") or []
    item_asset_blocks: dict[str, set[str]] = {}
    for policy in policies:
        item_id = str(policy.get("expected_visual_item_id") or "")
        relation = str(policy.get("expected_relation") or "")
        block_id = str(policy.get("block_id") or "")
        if item_id and block_id and not relation:
            item_asset_blocks.setdefault(item_id, set()).add(block_id)
    actual_items = {
        str(item.get("visual_item_id") or ""): set(
            str(value)
            for value in item.get("source_block_ids") or []
            if str(value)
        )
        for item in actual_layout.get("visual_items") or []
        if item.get("visual_item_id")
    }
    relations_by_block = {
        str(relation.get("source_block_id") or ""): relation
        for relation in actual_layout.get("text_relations") or []
        if relation.get("source_block_id")
    }
    checks: list[dict[str, Any]] = []
    diffs: list[str] = []
    for policy in policies:
        expected_relation = str(policy.get("expected_relation") or "")
        if not expected_relation:
            continue
        block_id = str(policy.get("block_id") or "")
        expected_item_id = str(policy.get("expected_visual_item_id") or "")
        relation = relations_by_block.get(block_id)
        passed = relation is not None
        reason = ""
        if relation is None:
            reason = "missing_relation"
        elif expected_relation == "unbound":
            passed = (
                relation.get("status") == "unbound"
                and relation.get("relation_type") == "unbound"
                and not (relation.get("visual_item_ids") or [])
            )
            reason = "" if passed else "expected_unbound"
        elif expected_relation == "ambiguous":
            passed = (
                relation.get("status") == "ambiguous"
                and relation.get("relation_type")
                in {"ambiguous", "shared_context"}
            )
            reason = "" if passed else "expected_ambiguous"
        else:
            target_ids = [
                str(value) for value in relation.get("visual_item_ids") or []
            ]
            actual_target_blocks = {
                block
                for target_id in target_ids
                for block in actual_items.get(target_id, set())
            }
            expected_target_blocks = item_asset_blocks.get(
                expected_item_id,
                set(),
            )
            passed = (
                relation.get("status") == "bound"
                and relation.get("relation_type") == expected_relation
                and bool(expected_target_blocks)
                and actual_target_blocks == expected_target_blocks
            )
            reason = "" if passed else "relation_or_target_mismatch"
        check = {
            "block_id": block_id,
            "expected_relation": expected_relation,
            "expected_visual_item_id": expected_item_id,
            "actual_relation": relation,
            "passed": passed,
            "reason": reason,
        }
        checks.append(check)
        if not passed:
            diffs.append(
                f"visual_relation[{block_id}]: {reason or 'mismatch'}"
            )
    return checks, diffs


def _adversarial_cases(
    *,
    document: dict[str, Any],
    layout: dict[str, Any],
    gold: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    layers = snapshot.get("layers") or {}
    stats = (layers.get("canonical_artifact") or {}).get("stats_json") or {}
    profiles = {
        int(item["page_no"]): item
        for item in (stats.get("document_profile") or {}).get("pages") or []
        if item.get("page_no") is not None
    }
    profiles_v2 = {
        int(item["page_no"]): item
        for item in (stats.get("document_profile_v2") or {}).get("pages") or []
        if item.get("page_no") is not None
    }
    policies = {
        int(item["page_no"]): item
        for item in stats.get("resolved_chunk_policies") or []
        if item.get("page_no") is not None
    }
    visual_layout_pages = {
        int(item["page_no"]): item
        for item in (stats.get("document_visual_layout") or {}).get("pages") or []
        if item.get("page_no") is not None
    }
    chunks_by_page: Counter[int] = Counter()
    for chunk in layers.get("chunk_metadata") or []:
        metadata = chunk.get("metadata_json") or {}
        page_no = metadata.get("page_no") or metadata.get("chunk_policy_page_no")
        try:
            chunks_by_page[int(page_no)] += 1
        except (TypeError, ValueError):
            continue
    gold_pages = {
        int(page["page_no"]): page for page in gold.get("pages") or []
    }
    cases: list[dict[str, Any]] = []
    for page in layout.get("pages") or []:
        page_no = int(page["page_no"])
        gold_page = gold_pages.get(page_no) or {}
        profile = profiles.get(page_no) or {}
        profile_v2 = profiles_v2.get(page_no) or {}
        policy = policies.get(page_no) or {}
        diffs: list[str] = []
        if profile.get("page_kind") != gold_page.get("page_kind"):
            diffs.append(
                "page_kind: "
                f"{profile.get('page_kind') or 'missing'} != "
                f"{gold_page.get('page_kind') or 'missing'}"
            )
        if policy.get("policy_id") != gold_page.get("chunk_policy_id"):
            diffs.append(
                "policy: "
                f"{policy.get('policy_id') or 'missing'} != "
                f"{gold_page.get('chunk_policy_id') or 'missing'}"
            )
        expected_items = gold_page.get("expected_visual_items") or []
        actual_visual_layout = visual_layout_pages.get(page_no) or {}
        actual_items = actual_visual_layout.get("visual_items")
        if expected_items and not isinstance(actual_items, list):
            diffs.append("visual_items: missing")
        elif expected_items and len(actual_items) != len(expected_items):
            diffs.append(
                f"visual_items: {len(actual_items)} != {len(expected_items)}"
            )
        relation_checks, relation_diffs = _visual_relation_checks(
            gold_page,
            actual_visual_layout,
        )
        diffs.extend(relation_diffs)
        cases.append(
            {
                "case_id": page["case_id"],
                "document_id": document["document_id"],
                "page_no": page_no,
                "artifacts": {
                    "pdf_path": document.get("pdf_path"),
                    "raw_response_path": document.get("raw_response_path"),
                    "gold_path": document.get("gold_path"),
                    "snapshot_path": document.get("snapshot_path"),
                    "replay_command": (
                        "python -m scripts.generate_document_profile_dataset "
                        f"--seed {document.get('generator_seed') or 20260723}"
                    ),
                },
                "capabilities": gold_page.get("capabilities") or [],
                "confusion_group": gold_page.get("confusion_group") or "",
                "variant": gold_page.get("variant") or "",
                "gold": {
                    "page_kind": gold_page.get("page_kind"),
                    "chunk_policy_id": gold_page.get("chunk_policy_id"),
                    "visual_items": expected_items,
                    "block_policies": gold_page.get("block_policies") or [],
                },
                "actual": {
                    "page_kind": profile.get("page_kind"),
                    "profile_evidence": profile.get("evidence") or {},
                    "page_profile_v2": profile_v2,
                    "chunk_policy_id": policy.get("policy_id"),
                    "policy_evidence": policy.get("evidence") or {},
                    "chunk_count": chunks_by_page.get(page_no, 0),
                    "visual_items": actual_items,
                    "text_relations": actual_visual_layout.get("text_relations"),
                    "layout_summary": actual_visual_layout.get("summary"),
                    "fallback_reason": actual_visual_layout.get("fallback_reason"),
                    "relation_checks": relation_checks,
                },
                "diffs": diffs,
                "status": "observed_diff" if diffs else "matched",
                "accuracy_claim_allowed": False,
            }
        )
    return cases


def _write_html_report(path: Path, report: dict[str, Any]) -> None:
    rows = []
    for case in report.get("adversarial_dataset", {}).get("cases", []):
        capabilities = ", ".join(case.get("capabilities") or [])
        diffs = "; ".join(case.get("diffs") or []) or "无差异"
        profile_v2 = case.get("actual", {}).get("page_profile_v2") or {}
        rows.append(
            "<tr>"
            f"<td>{escape(str(case.get('case_id') or ''))}</td>"
            f"<td>{escape(str(case.get('confusion_group') or '-'))}</td>"
            f"<td>{escape(str(case.get('variant') or '-'))}</td>"
            f"<td>{escape(capabilities)}</td>"
            f"<td>{escape(str(case.get('actual', {}).get('page_kind') or '-'))}</td>"
            f"<td>{escape(str(profile_v2.get('layout_kind') or '-'))}</td>"
            f"<td>{escape(str(profile_v2.get('ocr_trust_band') or '-'))}</td>"
            f"<td>{escape(diffs)}</td>"
            "</tr>"
        )
    payload = escape(json.dumps(report, ensure_ascii=False, sort_keys=True))
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>DCP-0.1 对抗数据集覆盖率</title>
  <style>
    body {{ font: 14px/1.5 system-ui, sans-serif; margin: 24px; color: #172033; }}
    .summary {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
    .metric {{ border: 1px solid #d8dfeb; border-radius: 8px; padding: 10px 14px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d8dfeb; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f7fb; text-align: left; }}
    tr:nth-child(even) {{ background: #fafbfd; }}
    details {{ margin-top: 20px; }}
  </style>
</head>
<body>
  <h1>DCP-0.1 对抗数据集覆盖率</h1>
  <p>合成数据仅用于工程覆盖，不代表真实 MinerU 准确率。</p>
  <div class="summary">
    <div class="metric">总场景：{report.get("page_count", 0)}</div>
    <div class="metric">对抗场景：{report.get("adversarial_dataset", {}).get("case_count", 0)}</div>
    <div class="metric">混淆组：{report.get("adversarial_dataset", {}).get("confusion_group_count", 0)}</div>
    <div class="metric">数据门禁：{"PASS" if report.get("dcp01_dataset_gate_passed") else "FAIL"}</div>
    <div class="metric">DCP-1.1 视觉项：{"PASS" if report.get("document_visual_items_dcp11", {}).get("gate_passed") else "FAIL"}</div>
    <div class="metric">DCP-2.1 多轴画像：{"PASS" if report.get("document_profile_dcp21", {}).get("gate_passed") else "FAIL"}</div>
  </div>
  <table>
    <thead><tr><th>Case</th><th>混淆组</th><th>变体</th><th>能力</th><th>v1 类型</th><th>v2 布局</th><th>OCR 可信度</th><th>差异</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <details><summary>完整 JSON</summary><pre>{payload}</pre></details>
</body>
</html>"""
    path.write_text(html, encoding="utf-8")


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
    profiled_pages = 0
    matched_page_profiles = 0
    resolved_policy_pages = 0
    matched_resolved_policies = 0
    complete_profile_evidence_pages = 0
    matched_document_profiles = 0
    shadow_only_policy_pages = 0
    active_resolved_policy_pages = 0
    expected_active_policy_pages = 0
    matched_active_policy_pages = 0
    active_policy_chunks = 0
    chunks_without_policy = 0
    active_cross_page_chunks = 0
    adaptive_enabled_documents = 0
    traced_pages = 0
    adversarial_cases: list[dict[str, Any]] = []
    replayed_page_count = 0

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
        replayed_page_count += int(document.get("page_count") or 0)
        if document.get("evaluation_scope") == "adversarial":
            adversarial_cases.extend(
                _adversarial_cases(
                    document=document,
                    layout=layout,
                    gold=gold,
                    snapshot=snapshot,
                )
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
        artifact_stats = (
            layers["canonical_artifact"].get("stats_json") or {}
        )
        adaptive_enabled_documents += int(
            artifact_stats.get("adaptive_chunk_policy_enabled") is True
        )
        document_profile = artifact_stats.get("document_profile") or {}
        profiles_by_page = {
            int(item["page_no"]): item
            for item in document_profile.get("pages") or []
            if item.get("page_no") is not None
        }
        policies_by_page = {
            int(item["page_no"]): item
            for item in artifact_stats.get("resolved_chunk_policies") or []
            if item.get("page_no") is not None
        }
        if document_profile.get("primary_kind") == gold.get("primary_kind"):
            matched_document_profiles += 1
        else:
            failures.append(
                f"{document['document_id']}: document profile mismatch "
                f"{document_profile.get('primary_kind')!r} != "
                f"{gold.get('primary_kind')!r}"
            )
        injected_visual_assets += int(
            (enrichment.get("metadata") or {}).get("page_visual_asset_count") or 0
        )
        enriched_by_page: dict[int, list[dict[str, Any]]] = {}
        for block in enrichment.get("blocks") or []:
            enriched_by_page.setdefault(int(block.get("page_no") or 0), []).append(
                block
            )
        gold_pages = {int(page["page_no"]): page for page in gold.get("pages") or []}
        expected_active_pairs = {
            (int(page["page_no"]), str(page["chunk_policy_id"]))
            for page in gold.get("pages") or []
            if page.get("chunk_policy_id") in DCP3_ACTIVE_POLICY_IDS
        }
        expected_active_policy_pages += len(expected_active_pairs)
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
            page_profile = profiles_by_page.get(page_no)
            if page_profile is not None:
                profiled_pages += 1
                complete_profile_evidence_pages += int(
                    bool(page_profile.get("evidence"))
                )
                if page_profile.get("page_kind") == gold_page.get("page_kind"):
                    matched_page_profiles += 1
                else:
                    failures.append(
                        f"{document['document_id']}: page {page_no} profile "
                        f"mismatch {page_profile.get('page_kind')!r} != "
                        f"{gold_page.get('page_kind')!r}"
                    )
            else:
                failures.append(
                    f"{document['document_id']}: page {page_no} profile missing"
                )
            resolved_policy = policies_by_page.get(page_no)
            if resolved_policy is not None:
                resolved_policy_pages += 1
                shadow_only_policy_pages += int(
                    resolved_policy.get("shadow_only") is True
                )
                active_resolved_policy_pages += int(
                    resolved_policy.get("shadow_only") is False
                )
                if resolved_policy.get("policy_id") == gold_page.get(
                    "chunk_policy_id"
                ):
                    matched_resolved_policies += 1
                else:
                    failures.append(
                        f"{document['document_id']}: page {page_no} resolved "
                        f"policy mismatch {resolved_policy.get('policy_id')!r} != "
                        f"{gold_page.get('chunk_policy_id')!r}"
                    )
            else:
                failures.append(
                    f"{document['document_id']}: page {page_no} resolved policy missing"
                )
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

        observed_active_pairs: set[tuple[int, str]] = set()
        for chunk in layers["chunk_metadata"]:
            metadata = chunk.get("metadata_json") or {}
            policy_id = str(metadata.get("chunk_policy_id") or "")
            observed_adaptive_chunk_policies += int(bool(policy_id))
            chunks_without_policy += int(not policy_id)
            if metadata.get("chunk_policy_applied") is True:
                active_policy_chunks += 1
                try:
                    policy_page_no = int(metadata.get("chunk_policy_page_no"))
                except (TypeError, ValueError):
                    policy_page_no = 0
                if policy_page_no > 0 and policy_id:
                    observed_active_pairs.add((policy_page_no, policy_id))
                candidate_pages = metadata.get("chunk_policy_candidate_page_nos") or []
                active_cross_page_chunks += int(len(set(candidate_pages)) > 1)
        matched_active_policy_pages += len(
            expected_active_pairs.intersection(observed_active_pairs)
        )
        missing_active_pairs = expected_active_pairs - observed_active_pairs
        if missing_active_pairs:
            failures.append(
                f"{document['document_id']}: missing active DCP-3 policies "
                f"{sorted(missing_active_pairs)}"
            )

    media_crop = _media_crop_closure()
    scenario_gate = set(scenario_counts) == set(BASE_SCENARIOS) and all(
        scenario_counts[scenario] == 1 for scenario in BASE_SCENARIOS
    )
    document_type_gate = set(manifest.get("document_types") or []) == set(
        DOCUMENT_TYPES
    )
    five_layer_gate = traced_pages == manifest.get("baseline_page_count") and not any(
        "layer mismatch" in failure or "missing replay" in failure
        for failure in failures
    )
    known_gap_gate = expected_gap_pages > 0 and expected_gap_pages == observed_gap_pages
    page_visual_closure_gate = (
        enriched_visual_pages == expected_visual_pages
        and enriched_duplicate_visual_pages == 0
        and injected_visual_assets == expected_gap_pages
    )
    expected_page_count = int(manifest.get("baseline_page_count") or 0)
    expected_document_count = int(manifest.get("baseline_document_count") or 0)
    document_profile_gate = (
        profiled_pages == expected_page_count
        and matched_page_profiles == expected_page_count
        and complete_profile_evidence_pages == expected_page_count
        and matched_document_profiles == expected_document_count
    )
    resolved_policy_gate = (
        resolved_policy_pages == expected_page_count
        and matched_resolved_policies == expected_page_count
    )
    adaptive_policy_gate = (
        expected_active_policy_pages > 0
        and matched_active_policy_pages == expected_active_policy_pages
        and active_policy_chunks > 0
        and chunks_without_policy == 0
        and active_cross_page_chunks == 0
        and adaptive_enabled_documents == expected_document_count
    )
    capabilities = {
        capability
        for case in adversarial_cases
        for capability in case.get("capabilities") or []
    }
    required_capabilities = {
        "native_text_visual_page",
        "poster_boundary",
        "character_threshold_perturbation",
        "dominant_ratio_boundary",
        "text_area_boundary",
        "bbox_perturbation",
        "primary_visual_negative",
        "ocr_quality",
        "mixed_text_sources",
        "negative_document_type",
        "vlm_resilience",
        "cross_page_relation",
        "visual_item_layout",
        "duplicate_fallback",
    }
    confusion_groups: dict[str, set[str]] = {}
    for case in adversarial_cases:
        group = str(case.get("confusion_group") or "")
        if group:
            confusion_groups.setdefault(group, set()).add(
                str(case.get("variant") or "")
            )
    required_pairs = {
        "poster-char-count",
        "dominant-image-ratio",
        "parser-text-area",
        "ocr_quality",
        "vlm_resilience",
    }
    paired_groups = {
        group for group, variants in confusion_groups.items() if len(variants) >= 2
    }
    visual_item_cases = {
        case["case_id"]
        for case in adversarial_cases
        if "visual_item_layout" in set(case.get("capabilities") or [])
    }
    expected_visual_item_cases = {f"VI-{index:02d}" for index in range(1, 11)}
    visual_item_case_map = {
        case["case_id"]: case
        for case in adversarial_cases
        if case["case_id"] in expected_visual_item_cases
    }
    visual_item_count_gate = (
        set(visual_item_case_map) == expected_visual_item_cases
        and all(
            len(case["actual"].get("visual_items") or [])
            == len(case["gold"].get("visual_items") or [])
            for case in visual_item_case_map.values()
        )
    )
    visual_relation_checks = [
        check
        for case in visual_item_case_map.values()
        for check in case["actual"].get("relation_checks") or []
    ]
    visual_relation_gate = (
        bool(visual_relation_checks)
        and all(check.get("passed") for check in visual_relation_checks)
    )
    vi02_relations = visual_item_case_map.get("VI-02", {}).get("actual", {}).get(
        "text_relations"
    ) or []
    vi06_summary = visual_item_case_map.get("VI-06", {}).get("actual", {}).get(
        "layout_summary"
    ) or {}
    vi07_actual = visual_item_case_map.get("VI-07", {}).get("actual", {})
    vi08_actual = visual_item_case_map.get("VI-08", {}).get("actual", {})
    vi09_relations = visual_item_case_map.get("VI-09", {}).get("actual", {}).get(
        "text_relations"
    ) or []
    visual_item_guard_gate = all(
        (
            any(relation.get("status") == "ambiguous" for relation in vi02_relations),
            vi06_summary.get("duplicate_fallback_count") == 1,
            vi07_actual.get("fallback_reason")
            == "full_page_raster_split_candidates",
            {
                item.get("status")
                for item in vi07_actual.get("visual_items") or []
            }
            == {"candidate"},
            vi08_actual.get("fallback_reason")
            == "no_reliable_internal_partition",
            {
                item.get("status")
                for item in vi08_actual.get("visual_items") or []
            }
            == {"unsegmented_page_visual"},
            bool(vi09_relations)
            and all(
                relation.get("status") == "unbound"
                for relation in vi09_relations
            ),
        )
    )
    visual_item_gate = (
        visual_item_count_gate
        and visual_item_guard_gate
        and visual_relation_gate
    )
    profile_v2_cases = {
        case["case_id"]: case["actual"].get("page_profile_v2") or {}
        for case in adversarial_cases
    }
    profile_v2_complete_gate = bool(profile_v2_cases) and all(
        bool(profile.get("layout_kind")) and bool(profile.get("evidence"))
        for profile in profile_v2_cases.values()
    )
    reliable_origins = {"native_text", "caption", "vlm_description"}
    reliable_origin_gate = all(
        all(
            admission.get(origin) == "normal"
            for origin in reliable_origins.intersection(
                set(profile.get("text_origins") or [])
            )
        )
        for profile in profile_v2_cases.values()
        for admission in [profile.get("origin_admission") or {}]
    )
    native_text_gate = all(
        (profile.get("origin_admission") or {}).get("native_text") == "normal"
        and "visual_ocr" not in set(profile.get("text_origins") or [])
        for profile in profile_v2_cases.values()
        if int(profile.get("native_text_chars") or 0) > 0
    )
    visual_ocr_candidate_gate = all(
        "visual_ocr" in set(profile.get("text_origins") or [])
        and profile.get("ocr_trust_band") != "not_applicable"
        for profile in profile_v2_cases.values()
        if int(profile.get("native_text_chars") or 0) == 0
        and int(profile.get("canonical_text_chars") or 0) > 0
        and int(profile.get("visual_item_count") or 0) > 0
    )
    adversarial_case_map = {
        case["case_id"]: case for case in adversarial_cases
    }
    gibberish_profile = (
        adversarial_case_map.get("cjk_semantic_gibberish_ocr", {})
        .get("actual", {})
        .get("page_profile_v2", {})
    )
    cjk_gibberish_gate = (
        gibberish_profile.get("ocr_trust_band") in {"uncertain", "shadow"}
        and (gibberish_profile.get("origin_admission") or {}).get("visual_ocr")
        == "shadow_auxiliary"
        and any(
            item.get("signal")
            == "unpunctuated_cjk_fragmentation_penalty"
            and float(item.get("contribution") or 0) < 0
            for item in gibberish_profile.get("evidence") or []
        )
    )
    profile_visual_count_gate = all(
        int(profile.get("visual_item_count") or 0)
        == len(
            adversarial_case_map[case_id]["actual"].get("visual_items") or []
        )
        for case_id, profile in profile_v2_cases.items()
    )
    profile_band_groups: dict[str, set[str]] = {}
    profile_variants: dict[str, dict[str, dict[str, Any]]] = {}
    for case in adversarial_cases:
        group = str(case.get("confusion_group") or "")
        variant = str(case.get("variant") or "")
        profile = case["actual"].get("page_profile_v2") or {}
        band = str(profile.get("ocr_trust_band") or "")
        if group and band:
            profile_band_groups.setdefault(group, set()).add(band)
        if group and variant:
            profile_variants.setdefault(group, {})[variant] = profile
    perturbation_checks: list[dict[str, Any]] = []
    for group, pairs in OCR_ADMISSION_STABILITY_PAIRS.items():
        variants = profile_variants.get(group) or {}
        for left_variant, right_variant in pairs:
            left_profile = variants.get(left_variant)
            right_profile = variants.get(right_variant)
            left_admission = (
                (left_profile or {}).get("origin_admission") or {}
            ).get("visual_ocr", "not_applicable")
            right_admission = (
                (right_profile or {}).get("origin_admission") or {}
            ).get("visual_ocr", "not_applicable")
            present = left_profile is not None and right_profile is not None
            perturbation_checks.append(
                {
                    "confusion_group": group,
                    "variants": [left_variant, right_variant],
                    "available_variants": sorted(variants),
                    "admission": [left_admission, right_admission],
                    "passed": present and left_admission == right_admission,
                    "reason": (
                        ""
                        if present and left_admission == right_admission
                        else "missing_pair_or_admission_flip"
                    ),
                }
            )
    perturbation_stability_gate = bool(perturbation_checks) and all(
        check["passed"] for check in perturbation_checks
    )
    document_profile_v2_gate = all(
        (
            profile_v2_complete_gate,
            reliable_origin_gate,
            native_text_gate,
            visual_ocr_candidate_gate,
            cjk_gibberish_gate,
            profile_visual_count_gate,
            perturbation_stability_gate,
        )
    )
    adversarial_gate = all(
        (
            int(manifest.get("page_count") or 0) >= 32,
            len(adversarial_cases)
            == int(manifest.get("adversarial_page_count") or 0),
            required_capabilities <= capabilities,
            required_pairs <= paired_groups,
            expected_visual_item_cases == visual_item_cases,
            replayed_page_count == int(manifest.get("page_count") or 0),
            set(manifest.get("scenarios") or []) == set(SCENARIOS),
            all(
                document.get(hash_key)
                for document in manifest.get("documents") or []
                for hash_key in (
                    "pdf_sha256",
                    "layout_sha256",
                    "raw_response_sha256",
                    "gold_sha256",
                    "snapshot_sha256",
                )
            ),
        )
    )
    report = {
        "schema_version": 2,
        "dataset_id": manifest.get("dataset_id"),
        "document_count": manifest.get("document_count"),
        "page_count": manifest.get("page_count"),
        "traced_page_count": traced_pages,
        "replayed_page_count": replayed_page_count,
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
        "document_profile_dcp2": {
            "profiled_pages": profiled_pages,
            "matched_page_profiles": matched_page_profiles,
            "complete_evidence_pages": complete_profile_evidence_pages,
            "matched_document_profiles": matched_document_profiles,
            "gate_passed": document_profile_gate,
        },
        "resolved_chunk_policy_dcp2": {
            "resolved_pages": resolved_policy_pages,
            "matched_policies": matched_resolved_policies,
            "shadow_only_pages": shadow_only_policy_pages,
            "active_pages": active_resolved_policy_pages,
            "gate_passed": resolved_policy_gate,
        },
        "adaptive_chunk_policy_dcp3": {
            "expected_active_pages": expected_active_policy_pages,
            "matched_active_pages": matched_active_policy_pages,
            "chunks_with_policy_id": observed_adaptive_chunk_policies,
            "chunks_without_policy_id": chunks_without_policy,
            "active_policy_chunks": active_policy_chunks,
            "active_cross_page_chunks": active_cross_page_chunks,
            "adaptive_enabled_documents": adaptive_enabled_documents,
            "gate_passed": adaptive_policy_gate,
        },
        "configuration_closure": {"enable_media_crop": media_crop},
        "document_visual_items_dcp11": {
            "case_count": len(visual_item_case_map),
            "expected_cases": sorted(expected_visual_item_cases),
            "item_count_gate_passed": visual_item_count_gate,
            "guard_gate_passed": visual_item_guard_gate,
            "relation_check_count": len(visual_relation_checks),
            "relation_gate_passed": visual_relation_gate,
            "gate_passed": visual_item_gate,
        },
        "document_profile_dcp21": {
            "profiled_adversarial_pages": len(profile_v2_cases),
            "complete_evidence_gate_passed": profile_v2_complete_gate,
            "reliable_origin_gate_passed": reliable_origin_gate,
            "native_text_gate_passed": native_text_gate,
            "visual_ocr_candidate_gate_passed": visual_ocr_candidate_gate,
            "cjk_gibberish_gate_passed": cjk_gibberish_gate,
            "visual_item_count_gate_passed": profile_visual_count_gate,
            "perturbation_stability_gate_passed": perturbation_stability_gate,
            "perturbation_band_groups": {
                key: sorted(value)
                for key, value in sorted(profile_band_groups.items())
            },
            "perturbation_checks": perturbation_checks,
            "shadow_only": True,
            "gate_passed": document_profile_v2_gate,
        },
        "adversarial_dataset": {
            "case_count": len(adversarial_cases),
            "capabilities": sorted(capabilities),
            "required_capabilities": sorted(required_capabilities),
            "confusion_group_count": len(confusion_groups),
            "paired_confusion_groups": sorted(paired_groups),
            "required_paired_groups": sorted(required_pairs),
            "visual_item_cases": sorted(visual_item_cases),
            "cases": adversarial_cases,
            "gate_passed": adversarial_gate,
            "artifact_hashes_complete": all(
                document.get(hash_key)
                for document in manifest.get("documents") or []
                for hash_key in (
                    "pdf_sha256",
                    "layout_sha256",
                    "raw_response_sha256",
                    "gold_sha256",
                    "snapshot_sha256",
                )
            ),
        },
        "dcp01_dataset_gate_passed": adversarial_gate,
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
            document_profile_gate,
            resolved_policy_gate,
            adaptive_policy_gate,
            adversarial_gate,
            visual_item_gate,
            document_profile_v2_gate,
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
    _write_html_report(output_dir / "reports" / "coverage.html", report)
    return report


__all__ = ["EXPECTED_LAYERS", "build_coverage_report"]
