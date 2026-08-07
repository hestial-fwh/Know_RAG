from __future__ import annotations

from typing import Any


_FORBIDDEN_CONTENT_KINDS = {
    "equation",
    "formula",
    "image",
    "ocr",
    "ocr_text",
    "table",
}


def _is_allowed_text_child(chunk: Any) -> bool:
    if getattr(chunk, "chunk_type", "") != "text":
        return False
    metadata = getattr(chunk, "metadata_json", None) or {}
    if not isinstance(metadata, dict):
        return False
    if metadata.get("role") in {"parent", "parent_text"}:
        return False
    content_kind = str(metadata.get("content_kind") or "").strip().lower()
    return content_kind not in _FORBIDDEN_CONTENT_KINDS


def _occurrence_id(reference: Any) -> str:
    return (
        f"{reference.figure_id}@{int(reference.start)}:{int(reference.end)}"
        f":{getattr(reference, 'source_node_id', '') or ''}"
    )


def _reference_key(reference: dict[str, Any]) -> tuple[Any, ...]:
    occurrence_id = reference.get("occurrence_id")
    if occurrence_id:
        return (occurrence_id, tuple(reference.get("image_chunk_ids") or []))
    return (
        reference.get("figure_id"),
        reference.get("start"),
        reference.get("end"),
        tuple(reference.get("image_chunk_ids") or []),
    )


def _binding_key(binding: dict[str, Any]) -> tuple[Any, ...]:
    occurrence_id = binding.get("occurrence_id")
    if occurrence_id:
        return (occurrence_id, tuple(binding.get("reference_chunk_ids") or []))
    return (
        binding.get("figure_id"),
        binding.get("start"),
        binding.get("end"),
        tuple(binding.get("reference_chunk_ids") or []),
    )


def _explicit_edge_lookup(graph: Any | None) -> dict[tuple[Any, ...], Any]:
    lookup: dict[tuple[Any, ...], Any] = {}
    for edge in getattr(graph, "edges", ()) or ():
        if getattr(edge, "relation_type", "") != "explicit_reference":
            continue
        occurrence = (getattr(edge, "evidence", ()) or ({},))[0]
        lookup[
            (
                occurrence.get("figure_id"),
                occurrence.get("start"),
                occurrence.get("end"),
                tuple(getattr(edge, "target_asset_ids", ()) or ()),
            )
        ] = edge
    return lookup


def _select_carrier(chunks: list[Any], ref_start: int, ref_end: int) -> Any | None:
    candidates = [
        (input_order, chunk)
        for input_order, chunk in enumerate(chunks)
        if _is_allowed_text_child(chunk)
        and getattr(chunk, "start_at", None) is not None
        and getattr(chunk, "end_at", None) is not None
        and int(chunk.start_at) <= ref_start
        and int(chunk.end_at) >= ref_end
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: (
            int(candidate[1].end_at) - int(candidate[1].start_at),
            int(candidate[1].start_at),
            int(candidate[1].end_at),
            candidate[0],
        ),
    )[1]


def materialize_figure_bindings(
    chunks: list[Any],
    plan: Any,
    *,
    relation_graph: Any | None = None,
) -> list[Any]:
    """Write one occurrence to one smallest full-range prose child and its images."""
    links = getattr(getattr(plan, "figure_links", None), "links", None) or []
    if not links:
        return chunks

    asset_to_image: dict[str, Any] = {}
    for chunk in chunks:
        if getattr(chunk, "chunk_type", "") != "image":
            continue
        metadata = getattr(chunk, "metadata_json", None) or {}
        if isinstance(metadata, dict) and metadata.get("asset_id"):
            asset_to_image[str(metadata["asset_id"])] = chunk
    if not asset_to_image:
        return chunks
    edge_lookup = _explicit_edge_lookup(relation_graph)

    for link in links:
        if getattr(link, "status", "") != "bound" or not getattr(link, "asset_ids", ()):
            continue
        reference = link.reference
        ref_start = int(reference.start)
        ref_end = int(reference.end)
        if ref_end <= ref_start:
            continue
        carrier = _select_carrier(chunks, ref_start, ref_end)
        if carrier is None:
            continue
        image_chunks = [asset_to_image[asset_id] for asset_id in link.asset_ids if asset_id in asset_to_image]
        if not image_chunks:
            continue

        occurrence_id = _occurrence_id(reference)
        relation_edge = edge_lookup.get(
            (
                reference.figure_id,
                ref_start,
                ref_end,
                tuple(link.asset_ids),
            )
        )
        image_chunk_ids = [str(chunk.id) for chunk in image_chunks]
        reference_summary = {
            "version": 2,
            "occurrence_id": occurrence_id,
            "figure_id": reference.figure_id,
            "reference_type": reference.reference_type,
            "image_chunk_ids": image_chunk_ids,
            "binding_method": link.binding_method,
            "binding_score": link.binding_score,
            "score_kind": link.score_kind,
            "start": ref_start,
            "end": ref_end,
        }
        if relation_edge is not None:
            reference_summary["relation_id"] = relation_edge.relation_id
            reference_summary["relation_type"] = relation_edge.relation_type
            reference_summary["status"] = relation_edge.status
        carrier_meta = dict(carrier.metadata_json or {})
        references = [item for item in carrier_meta.get("figure_references", []) if isinstance(item, dict)]
        if _reference_key(reference_summary) not in {_reference_key(item) for item in references}:
            references.append(reference_summary)
        carrier_meta["figure_references"] = references
        carrier.metadata_json = carrier_meta

        for image_chunk in image_chunks:
            binding_summary = {
                "version": 2,
                "occurrence_id": occurrence_id,
                "figure_id": reference.figure_id,
                "reference_type": reference.reference_type,
                "binding_method": link.binding_method,
                "binding_score": link.binding_score,
                "score_kind": link.score_kind,
                "reference_chunk_ids": [str(carrier.id)],
                "start": ref_start,
                "end": ref_end,
            }
            if relation_edge is not None:
                binding_summary["relation_id"] = relation_edge.relation_id
                binding_summary["relation_type"] = relation_edge.relation_type
                binding_summary["status"] = relation_edge.status
            image_meta = dict(image_chunk.metadata_json or {})
            bindings = [item for item in image_meta.get("image_bindings", []) if isinstance(item, dict)]
            if _binding_key(binding_summary) not in {_binding_key(item) for item in bindings}:
                bindings.append(binding_summary)
            image_meta["image_bindings"] = bindings
            # Stable single-relation projection for readers created before Phase 1.
            first_binding = bindings[0]
            image_meta["image_binding"] = {
                "version": 1,
                "figure_id": first_binding.get("figure_id"),
                "binding_method": first_binding.get("binding_method"),
                "binding_score": first_binding.get("binding_score"),
                "score_kind": first_binding.get("score_kind"),
                "reference_chunk_ids": list(first_binding.get("reference_chunk_ids") or []),
            }
            image_chunk.metadata_json = image_meta

    return chunks


def _compact_relation_summary(edge: Any) -> dict[str, Any]:
    return {
        "relation_id": edge.relation_id,
        "relation_type": edge.relation_type,
        "status": edge.status,
        "binding_method": edge.binding_method,
        "binding_score": edge.binding_score,
        "score_kind": edge.score_kind,
    }


def _append_ordered(metadata: dict[str, Any], key: str, values: list[str]) -> None:
    current = [str(item) for item in metadata.get(key, []) if item]
    metadata[key] = list(dict.fromkeys([*current, *values]))


def _unique_chunks(chunks: list[Any]) -> list[Any]:
    output: list[Any] = []
    seen: set[str] = set()
    for chunk in chunks:
        chunk_id = str(getattr(chunk, "id", ""))
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        output.append(chunk)
    return output


def materialize_relation_summaries(chunks: list[Any], graph: Any) -> list[Any]:
    """Materialize compact graph summaries without copying candidates/evidence."""
    asset_chunks: dict[str, Any] = {}
    chunks_by_source_node: dict[str, list[Any]] = {}
    for chunk in chunks:
        metadata = getattr(chunk, "metadata_json", None) or {}
        if not isinstance(metadata, dict):
            continue
        asset_id = metadata.get("asset_id")
        if asset_id and getattr(chunk, "chunk_type", "") == "image":
            asset_chunks[str(asset_id)] = chunk
        for node_id in metadata.get("source_node_ids") or ():
            chunks_by_source_node.setdefault(str(node_id), []).append(chunk)

    for edge in getattr(graph, "edges", ()) or ():
        source_chunks: list[Any] = []
        if edge.relation_type == "explicit_reference":
            for chunk in chunks:
                metadata = getattr(chunk, "metadata_json", None) or {}
                if any(
                    item.get("relation_id") == edge.relation_id
                    for item in metadata.get("figure_references", [])
                    if isinstance(item, dict)
                ):
                    source_chunks.append(chunk)
        if not source_chunks:
            for node_id in edge.source_node_ids:
                source_chunks.extend(chunks_by_source_node.get(node_id, ()))
        source_chunks = _unique_chunks(source_chunks)
        target_chunks = [
            asset_chunks[asset_id]
            for asset_id in edge.target_asset_ids
            if asset_id in asset_chunks
        ]
        participants = _unique_chunks([*source_chunks, *target_chunks])
        summary = _compact_relation_summary(edge)
        for chunk in participants:
            metadata = dict(chunk.metadata_json or {})
            summaries = [
                item for item in metadata.get("relation_summaries", []) if isinstance(item, dict)
            ]
            if edge.relation_id not in {item.get("relation_id") for item in summaries}:
                summaries.append(summary)
            metadata["relation_summary_version"] = 1
            metadata["relation_summaries"] = summaries
            _append_ordered(metadata, "relation_ids", [edge.relation_id])
            _append_ordered(metadata, "group_ids", list(edge.target_group_ids))
            chunk.metadata_json = metadata

        if edge.status != "bound":
            continue
        target_chunk_ids = [str(chunk.id) for chunk in target_chunks]
        source_chunk_ids = [str(chunk.id) for chunk in source_chunks]
        for chunk in source_chunks:
            metadata = dict(chunk.metadata_json or {})
            _append_ordered(metadata, "outgoing_asset_chunk_ids", target_chunk_ids)
            chunk.metadata_json = metadata
        for chunk in target_chunks:
            metadata = dict(chunk.metadata_json or {})
            _append_ordered(metadata, "incoming_reference_chunk_ids", source_chunk_ids)
            chunk.metadata_json = metadata
    return chunks


def populate_relation_chunks(chunks: list[Any]) -> list[Any]:
    """Compute and write `relation_chunks` from fully materialized metadata_json.

    Reads the deterministic binding fields written by materialize_figure_bindings
    and materialize_relation_summaries, then produces a stable, deduplicated list
    of directly related chunk IDs for each chunk.

    Indirect relations (same-page, same-section) are NOT included here;
    they will be written to indirect_relation_chunks separately.
    """
    for chunk in chunks:
        metadata = getattr(chunk, "metadata_json", None) or {}
        if not isinstance(metadata, dict):
            chunk.relation_chunks = []
            continue

        related: list[str] = []
        chunk_id = str(getattr(chunk, "id", ""))

        # figure_references → image chunks this text references
        for ref in metadata.get("figure_references") or []:
            if isinstance(ref, dict):
                for img_id in (ref.get("image_chunk_ids") or []):
                    if isinstance(img_id, str) and img_id:
                        related.append(img_id)

        # outgoing_asset_chunk_ids → image/asset chunks linked via relation graph
        for asset_id in metadata.get("outgoing_asset_chunk_ids") or []:
            if isinstance(asset_id, str) and asset_id:
                related.append(asset_id)

        # image_bindings → text chunks that reference this image
        for binding in metadata.get("image_bindings") or []:
            if isinstance(binding, dict):
                for ref_id in (binding.get("reference_chunk_ids") or []):
                    if isinstance(ref_id, str) and ref_id:
                        related.append(ref_id)

        # incoming_reference_chunk_ids → text chunks referencing this chunk
        for ref_id in metadata.get("incoming_reference_chunk_ids") or []:
            if isinstance(ref_id, str) and ref_id:
                related.append(ref_id)

        # Deduplicate preserving declaration order, exclude self-reference.
        chunk.relation_chunks = [
            cid for cid in list(dict.fromkeys(related)) if cid != chunk_id
        ]

    return chunks


__all__ = [
    "materialize_figure_bindings",
    "materialize_relation_summaries",
    "populate_relation_chunks",
]
