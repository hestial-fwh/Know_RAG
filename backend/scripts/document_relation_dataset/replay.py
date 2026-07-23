from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.chunk_service import _apply_figure_bindings
from app.services.chunker.canonical_parent_child import build_canonical_parent_child_plan
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import build_canonical_document
from app.services.document_parser.mineru_adapter import (
    MinerUPdfConfig,
    _content_list_to_blocks,
    _find_content_list,
    _post_file_parse,
)
from app.services.document_parser.types import ParsedDocument
from app.services.retrieval_service import _expand_figure_bindings


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _chunker_config() -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
        separators=["\n\n", "\n", "。", ". "],
    )


def capture_live_mineru_response(pdf_path: Path, mineru_url: str, destination: Path) -> dict:
    """Call MinerU only when the caller explicitly supplies a URL."""
    config = MinerUPdfConfig(
        base_url=mineru_url.rstrip("/"),
        source="phase0-explicit-cli",
        runtime_profile="cpu",
        backend="pipeline",
        parse_method="auto",
    )
    payload = _post_file_parse(pdf_path, config, output_dir=None)
    _json(destination, payload)
    return payload


def _parsed_snapshot(blocks) -> list[dict[str, Any]]:
    output = []
    for block in blocks:
        value = asdict(block)
        value["bbox"] = list(block.bbox) if block.bbox else None
        value["image_base64"] = "" if block.image_base64 else ""
        output.append(value)
    return output


def _canonical_snapshot(canonical) -> dict[str, Any]:
    return {
        "markdown": canonical.markdown_content,
        "source_format": canonical.source_format,
        "metadata": canonical.metadata,
        "assets": {
            asset_id: asset.to_metadata() for asset_id, asset in canonical.assets.items()
        },
        "provenance": [node.to_metadata() for node in canonical.provenance_nodes],
    }


def _draft_chunks(plan, document_id: str, knowledge_base_id: str):
    chunks = []
    for index, child in enumerate(plan.children):
        chunks.append(
            SimpleNamespace(
                id=f"{document_id}-chunk-{index:05d}",
                content=child.content,
                chunk_type=child.chunk_type,
                document_id=document_id,
                knowledge_base_id=knowledge_base_id,
                metadata_json=dict(child.metadata),
                image_info=dict(child.image_info),
                parent_chunk_id=(
                    f"{document_id}-parent-{child.parent_index:04d}"
                    if child.parent_index is not None
                    else None
                ),
                context_header=child.context_header,
                start_at=child.start,
                end_at=child.end,
                is_enabled=True,
            )
        )
    return _apply_figure_bindings(chunks, plan)


class _RowsQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def all(self):
        return self.rows


class _RowsDB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _RowsQuery(self.rows)


def _retrieval_snapshot(chunks) -> dict[str, Any]:
    direct_chunks = [
        chunk
        for chunk in chunks
        if (chunk.metadata_json or {}).get("figure_references")
        or (chunk.metadata_json or {}).get("image_binding")
        or (chunk.metadata_json or {}).get("image_bindings")
    ]
    if not direct_chunks:
        return {"direct_hits": [], "expanded_hits": [], "cases": [], "reason": "no_current_p4_binding"}

    cases: list[dict[str, Any]] = []
    all_direct: list[dict[str, Any]] = []
    all_expanded: list[dict[str, Any]] = []
    for direct in direct_chunks:
        direct_result = {
            "id": direct.id,
            "content": direct.content,
            "chunk_type": direct.chunk_type,
            "document_id": direct.document_id,
            "knowledge_base_id": direct.knowledge_base_id,
            "metadata_json": direct.metadata_json,
            "image_info": direct.image_info,
            "parent_chunk_id": direct.parent_chunk_id or "",
            "context_header": direct.context_header,
            "score": 1.0,
            "start_at": direct.start_at,
            "end_at": direct.end_at,
        }
        metadata = direct.metadata_json or {}
        target_ids: list[str] = []
        for reference in metadata.get("figure_references") or []:
            target_ids.extend(reference.get("image_chunk_ids") or [])
        bindings = metadata.get("image_bindings") or []
        legacy_binding = metadata.get("image_binding")
        if legacy_binding:
            bindings = [*bindings, legacy_binding]
        for binding in bindings:
            target_ids.extend(binding.get("reference_chunk_ids") or [])
        target_ids = list(dict.fromkeys(target_ids))
        targets_by_id = {chunk.id: chunk for chunk in chunks if chunk.id in target_ids}
        targets = [targets_by_id[target_id] for target_id in target_ids if target_id in targets_by_id]
        expanded = _expand_figure_bindings(_RowsDB(targets), [direct_result])
        expanded_hits = expanded[1:]
        cases.append(
            {
                "direct_hit": direct_result,
                "expanded_hits": expanded_hits,
                "requested_target_ids": target_ids,
            }
        )
        all_direct.append(direct_result)
        all_expanded.extend(expanded_hits)
    scope_guard_cases: list[dict[str, Any]] = []
    scope_sources = [
        chunk
        for chunk in chunks
        if "this document only" in str(getattr(chunk, "content", ""))
    ]
    scope_targets = [
        chunk
        for chunk in chunks
        if getattr(chunk, "chunk_type", "") == "image"
        and "Foreign scoped asset" in str(getattr(chunk, "content", ""))
    ]
    for occurrence, (scope_source, scope_target) in enumerate(
        zip(scope_sources, scope_targets),
        start=1,
    ):
        foreign_target_id = f"foreign-scope-{occurrence}-{scope_target.id}"
        foreign_target = SimpleNamespace(
            **{
                **vars(scope_target),
                "id": foreign_target_id,
                "document_id": "FOREIGN-DOC",
            }
        )
        contaminated_direct = {
            "id": scope_source.id,
            "content": scope_source.content,
            "chunk_type": scope_source.chunk_type,
            "document_id": scope_source.document_id,
            "knowledge_base_id": scope_source.knowledge_base_id,
            "metadata_json": {
                **(scope_source.metadata_json or {}),
                "figure_references": [
                    {
                        "figure_id": "figure:1",
                        "image_chunk_ids": [foreign_target_id],
                    }
                ],
            },
            "image_info": scope_source.image_info,
            "parent_chunk_id": scope_source.parent_chunk_id or "",
            "context_header": scope_source.context_header,
            "score": 1.0,
            "start_at": scope_source.start_at,
            "end_at": scope_source.end_at,
        }
        guarded = _expand_figure_bindings(
            _RowsDB([foreign_target]),
            [contaminated_direct],
        )
        scope_guard_cases.append(
            {
                "direct_hit_id": scope_source.id,
                "requested_target_id": foreign_target_id,
                "source_document_id": scope_source.document_id,
                "target_document_id": foreign_target.document_id,
                "expanded_hits": guarded[1:],
            }
        )
    return {
        "direct_hits": all_direct,
        "expanded_hits": all_expanded,
        "cases": cases,
        "scope_guard_cases": scope_guard_cases,
    }


def replay_raw_response(
    raw_response_path: Path,
    destination: Path,
    *,
    document_id: str,
    knowledge_base_id: str = "phase0-generated-kb",
) -> dict[str, Any]:
    payload = json.loads(raw_response_path.read_text(encoding="utf-8"))
    content_list = _find_content_list(payload)
    replay_content_list = deepcopy(content_list)
    # Raw paths stay in the raw layer. Avoid uploading generated media through
    # production MinIO while replaying parser semantics offline.
    for item in replay_content_list:
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        for key in ("img_path", "image_path", "chart_img_path", "chart_path", "path", "url"):
            item.pop(key, None)
    blocks = _content_list_to_blocks(
        replay_content_list,
        {},
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )
    parsed = ParsedDocument(
        content="\n\n".join(block.index_text() for block in blocks if block.index_text()),
        blocks=blocks,
        metadata={
            "parser": "mineru",
            "source": "phase0_replay",
            "content_list_count": len(content_list),
        },
    )
    canonical = build_canonical_document(parsed, file_type="pdf", document_title=document_id)
    plan = build_canonical_parent_child_plan(canonical, _chunker_config())
    chunks = _draft_chunks(plan, document_id, knowledge_base_id)
    snapshot = {
        "schema_version": 1,
        "document_id": document_id,
        "layers": {
            "raw_parser": {
                "path": raw_response_path.as_posix(),
                "content_list_count": len(content_list),
            },
            "parsed_blocks": _parsed_snapshot(blocks),
            "canonical_artifact": _canonical_snapshot(canonical),
            "chunk_metadata": [
                {
                    "id": chunk.id,
                    "chunk_type": chunk.chunk_type,
                    "start_at": chunk.start_at,
                    "end_at": chunk.end_at,
                    "metadata_json": chunk.metadata_json,
                    "image_info": chunk.image_info,
                }
                for chunk in chunks
            ],
            "retrieval_output": _retrieval_snapshot(chunks),
        },
        "current_figure_links": plan.figure_links.to_metadata(),
    }
    _json(destination, snapshot)
    return snapshot


def replay_manifest(output_dir: Path, manifest: dict, *, mineru_url: str = "") -> list[dict]:
    snapshots = []
    for document in manifest["documents"]:
        raw_path = output_dir / document["raw_response_path"]
        if mineru_url:
            live_path = output_dir / "raw_live" / f"{document['document_id']}.json"
            capture_live_mineru_response(output_dir / document["pdf_path"], mineru_url, live_path)
            raw_path = live_path
            document["live_raw_response_path"] = live_path.relative_to(output_dir).as_posix()
        snapshot_path = output_dir / "snapshots" / f"{document['document_id']}.json"
        snapshot = replay_raw_response(
            raw_path,
            snapshot_path,
            document_id=document["document_id"],
        )
        document["snapshot_path"] = snapshot_path.relative_to(output_dir).as_posix()
        snapshots.append(snapshot)
    _json(output_dir / "corpus_manifest.json", manifest)
    return snapshots
