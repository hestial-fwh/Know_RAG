"""Diagnostic script: analyze multimodal relation coverage for a document.

Usage:
    docker compose exec backend python scripts/analyze_multimodal_relations.py <document_id>
    docker compose exec backend python scripts/analyze_multimodal_relations.py <document_id> --detail
    docker compose exec backend python scripts/analyze_multimodal_relations.py <document_id> --kb <kb_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any

# Allow running from backend/scripts/ directory.
sys.path.insert(0, "/app")

from app.db.session import SessionLocal
from app.db.models import Chunk, Document


def _classify_relation_sources(chunk: Any) -> list[str]:
    """Classify sources of relation_chunks for a given chunk.

    Distinguishes between **strong** relations (bound edges → outgoing/incoming
    → relation_chunks → binding_aware) and **weak** relations (ambiguous edges
    → relation_summaries only, NOT in relation_chunks).
    """
    sources: list[str] = []
    meta = chunk.metadata_json or {}
    if not isinstance(meta, dict):
        return ["unknown"]

    chunk_id = str(chunk.id)

    # 1. Check for explicit FigureLink bindings
    for ref in meta.get("figure_references") or []:
        if isinstance(ref, dict):
            for img_id in (ref.get("image_chunk_ids") or []):
                if img_id and img_id != chunk_id:
                    sources.append("explicit_figure_link")
                    break

    for binding in meta.get("image_bindings") or []:
        if isinstance(binding, dict):
            for ref_id in (binding.get("reference_chunk_ids") or []):
                if ref_id and ref_id != chunk_id:
                    sources.append("explicit_figure_link")
                    break

    # 2. Check for relation_graph strong relations (bound edges)
    #    These write to outgoing_asset_chunk_ids / incoming_reference_chunk_ids
    #    → relation_chunks → binding_related_chunks → binding_aware.
    has_strong_graph = False
    if meta.get("outgoing_asset_chunk_ids"):
        for oid in meta["outgoing_asset_chunk_ids"]:
            if oid and oid != chunk_id:
                sources.append("relation_graph_bound")
                has_strong_graph = True
                break

    if meta.get("incoming_reference_chunk_ids"):
        for iid in meta["incoming_reference_chunk_ids"]:
            if iid and iid != chunk_id:
                sources.append("relation_graph_bound")
                has_strong_graph = True
                break

    # 3. Check for relation_summaries (includes both bound and ambiguous edges)
    #    Ambiguous edges appear ONLY here, not in outgoing/incoming.
    summaries = meta.get("relation_summaries") or []
    if summaries:
        has_bound_summary = any(
            isinstance(s, dict) and s.get("status") == "bound"
            for s in summaries
        )
        has_ambiguous_summary = any(
            isinstance(s, dict) and s.get("status") == "ambiguous"
            for s in summaries
        )
        if has_bound_summary and not has_strong_graph:
            sources.append("relation_graph_bound")
        if has_ambiguous_summary:
            sources.append("relation_graph_ambiguous")

    # 4. Check media_parent_assignment
    ma = meta.get("media_parent_assignment")
    if isinstance(ma, dict) and ma.get("assigned"):
        sources.append("final_media_parent_assignment")

    # 5. If relation_chunks is non-empty but no source identified
    if chunk.relation_chunks and not sources:
        # These likely came from link_same_parent_media
        sources.append("same_parent_media")

    if not sources:
        sources.append("none")

    return list(dict.fromkeys(sources))


def _relation_source_summary(chunk: Any) -> str:
    """Primary relation source for summary display.
    Priority: explicit > graph_bound > graph_ambiguous > media_parent > same_parent_media > none."""
    sources = _classify_relation_sources(chunk)
    if "explicit_figure_link" in sources:
        return "explicit_figure_link"
    if "relation_graph_bound" in sources:
        return "relation_graph_bound"
    if "relation_graph_ambiguous" in sources:
        return "relation_graph_ambiguous"
    if "final_media_parent_assignment" in sources:
        return "media_parent_assignment"
    if "same_parent_media" in sources:
        return "same_parent_media"
    return "none"


def _image_row(chunk: Any) -> dict[str, Any]:
    """Build a diagnostic row for one image chunk."""
    meta = chunk.metadata_json or {}
    row: dict[str, Any] = {
        "image_chunk_id": str(chunk.id),
        "page_no": meta.get("page_no"),
        "caption": str(meta.get("caption", "") or "")[:120],
        "figure_ids": meta.get("figure_ids") or [],
        "parent_chunk_id": str(chunk.parent_chunk_id or ""),
        "parent_materialization_rule": meta.get("parent_materialization_rule", ""),
        "media_parent_assignment": meta.get("media_parent_assignment"),
        "image_bindings_count": len(meta.get("image_bindings") or []),
        "figure_references_count": len(meta.get("figure_references") or []),
        "relation_chunks": chunk.relation_chunks or [],
        "relation_chunks_count": len(chunk.relation_chunks or []),
        "relation_sources": _classify_relation_sources(chunk),
        "primary_source": _relation_source_summary(chunk),
        "outgoing_asset_chunk_ids": meta.get("outgoing_asset_chunk_ids") or [],
        "incoming_reference_chunk_ids": meta.get("incoming_reference_chunk_ids") or [],
        "relation_summaries_count": len(meta.get("relation_summaries") or []),
        "visual_item_id": meta.get("visual_item_id"),
    }
    return row


def _text_row(chunk: Any) -> dict[str, Any]:
    """Build a diagnostic row for one text chunk."""
    meta = chunk.metadata_json or {}
    row: dict[str, Any] = {
        "text_chunk_id": str(chunk.id),
        "content_preview": (chunk.content or "")[:200],
        "figure_references_count": len(meta.get("figure_references") or []),
        "outgoing_asset_chunk_ids": meta.get("outgoing_asset_chunk_ids") or [],
        "outgoing_count": len(meta.get("outgoing_asset_chunk_ids") or []),
        "incoming_reference_chunk_ids": meta.get("incoming_reference_chunk_ids") or [],
        "incoming_count": len(meta.get("incoming_reference_chunk_ids") or []),
        "relation_chunks_count": len(chunk.relation_chunks or []),
    }
    return row


def analyze_document(
    document_id: str,
    *,
    detail: bool = False,
) -> dict[str, Any]:
    """Analyze multimodal relations for a single document."""
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc is None:
            return {"error": f"Document not found: {document_id}"}

        chunks = (
            db.query(Chunk)
            .filter(Chunk.document_id == document_id, Chunk.is_enabled == True)
            .all()
        )

        if not chunks:
            return {"error": f"No chunks found for document: {document_id}"}

        # ── Classify chunks ──
        image_chunks = [c for c in chunks if c.chunk_type == "image"]
        text_chunks = [c for c in chunks if c.chunk_type == "text"]
        table_chunks = [c for c in chunks if c.chunk_type == "table"]

        # ── Parent grouping for singleton detection ──
        parent_image_counts: dict[str, int] = defaultdict(int)
        parent_all_counts: dict[str, int] = defaultdict(int)
        orphan_images: list[Any] = []

        for c in image_chunks:
            pid = str(c.parent_chunk_id or "")
            if pid:
                parent_image_counts[pid] += 1
            else:
                orphan_images.append(c)

        for c in chunks:
            pid = str(c.parent_chunk_id or "")
            if pid:
                parent_all_counts[pid] += 1

        singleton_image_parents = sum(
            1 for cnt in parent_image_counts.values() if cnt == 1
        )

        # ── Image metrics ──
        img_rows = [_image_row(c) for c in image_chunks]

        img_total = len(image_chunks)
        img_with_fig_ids = sum(1 for r in img_rows if r["figure_ids"])
        img_with_bindings = sum(1 for r in img_rows if r["image_bindings_count"] > 0)
        img_with_relation = sum(1 for r in img_rows if r["relation_chunks_count"] > 0)
        img_with_parent_assignment = sum(
            1
            for r in img_rows
            if isinstance(r["media_parent_assignment"], dict)
            and r["media_parent_assignment"].get("assigned")
        )
        img_independent_visual = sum(
            1
            for r in img_rows
            if isinstance(r["media_parent_assignment"], dict)
            and r["media_parent_assignment"].get("strategy") == "independent_visual"
        )

        # ── Relation source breakdown ──
        source_counts: dict[str, int] = defaultdict(int)
        for r in img_rows:
            for s in r["relation_sources"]:
                source_counts[s] += 1

        # ── Text metrics ──
        text_rows = [_text_row(c) for c in text_chunks]
        texts_with_outgoing = sum(1 for r in text_rows if r["outgoing_count"] > 0)
        texts_with_incoming = sum(1 for r in text_rows if r["incoming_count"] > 0)

        # ── Non-explicit coverage ──
        img_no_fig_ids = [r for r in img_rows if not r["figure_ids"]]
        non_explicit_with_relation = sum(
            1 for r in img_no_fig_ids if r["relation_chunks_count"] > 0
        )

        # ── Explicit coverage ──
        explicit_total = sum(1 for r in img_rows if "explicit_figure_link" in r["relation_sources"])
        non_explicit_total = sum(
            1 for r in img_rows if "explicit_figure_link" not in r["relation_sources"]
        )
        non_explicit_covered = sum(
            1
            for r in img_rows
            if "explicit_figure_link" not in r["relation_sources"]
            and r["relation_chunks_count"] > 0
        )

        # ── Graph incremental coverage ──
        # Images that have relation_graph_bound but NOT explicit_figure_link
        # and NOT same_parent_media — these are the graph's net-new relations.
        graph_bound_only = [
            r for r in img_rows
            if "relation_graph_bound" in r["relation_sources"]
            and "explicit_figure_link" not in r["relation_sources"]
            and "same_parent_media" not in r["relation_sources"]
        ]
        # Images with ONLY ambiguous graph relations (no strong at all)
        graph_ambiguous_only = [
            r for r in img_rows
            if "relation_graph_ambiguous" in r["relation_sources"]
            and "relation_graph_bound" not in r["relation_sources"]
            and "explicit_figure_link" not in r["relation_sources"]
            and "same_parent_media" not in r["relation_sources"]
            and r["relation_chunks_count"] == 0
        ]
        # Images where graph-bound provides the ONLY relation (truly incremental)
        img_without_any_strong_source = [
            r for r in img_rows
            if "explicit_figure_link" not in r["relation_sources"]
            and "same_parent_media" not in r["relation_sources"]
            and "final_media_parent_assignment" not in r["relation_sources"]
        ]
        graph_incremental = [
            r for r in img_without_any_strong_source
            if "relation_graph_bound" in r["relation_sources"]
        ]
        # Duplicate relation rate: graph relations that also have
        # explicit_figure_link or same_parent_media for the same image.
        graph_with_duplicate = [
            r for r in img_rows
            if "relation_graph_bound" in r["relation_sources"]
            and (
                "explicit_figure_link" in r["relation_sources"]
                or "same_parent_media" in r["relation_sources"]
            )
        ]

        result: dict[str, Any] = {
            "document_id": document_id,
            "document_title": doc.title,
            "file_type": doc.file_type,
            "knowledge_base_id": doc.knowledge_base_id,
            # ── Aggregate metrics ──
            "aggregate": {
                "total_chunks": len(chunks),
                "image_chunks": img_total,
                "text_chunks": len(text_chunks),
                "table_chunks": len(table_chunks),
                "unique_parents": len(parent_all_counts),
                "singleton_image_parents": singleton_image_parents,
                "orphan_images": len(orphan_images),
                # Coverage
                "images_with_figure_ids": img_with_fig_ids,
                "images_with_bindings": img_with_bindings,
                "images_with_relation_chunks": img_with_relation,
                "images_with_parent_assignment": img_with_parent_assignment,
                "images_independent_visual": img_independent_visual,
                # Relation coverage (P6-corrected)
                "explicit_relation_coverage": (
                    round(explicit_total / img_total * 100, 1) if img_total else 0
                ),
                "non_explicit_relation_coverage": (
                    round(non_explicit_covered / non_explicit_total * 100, 1)
                    if non_explicit_total
                    else 0
                ),
                # Graph incremental metrics (P10)
                "graph_incremental_coverage": (
                    round(len(graph_incremental) / img_total * 100, 1)
                    if img_total else 0
                ),
                "graph_incremental_count": len(graph_incremental),
                "graph_ambiguous_only_count": len(graph_ambiguous_only),
                "graph_duplicate_rate": (
                    round(len(graph_with_duplicate) / max(len([
                        r for r in img_rows
                        if "relation_graph_bound" in r["relation_sources"]
                    ]), 1) * 100, 1)
                ),
                "graph_duplicate_count": len(graph_with_duplicate),
                # Text side
                "text_chunks_with_outgoing_relations": texts_with_outgoing,
                "text_chunks_with_incoming_relations": texts_with_incoming,
                # Source breakdown
                "relation_sources": dict(source_counts),
            },
        }

        if detail:
            result["image_details"] = img_rows
            result["text_details"] = text_rows
            # Also show interesting images (non-explicit with relations)
            result["interesting"] = {
                "non_explicit_with_relations": [
                    r for r in img_no_fig_ids if r["relation_chunks_count"] > 0
                ],
                "explicit_no_relation": [
                    r
                    for r in img_rows
                    if r["figure_ids"]
                    and r["relation_chunks_count"] == 0
                    and "explicit_figure_link" in r["relation_sources"]
                ],
            }

        return result

    finally:
        db.close()


def print_report(result: dict[str, Any], *, detail: bool = False) -> None:
    """Pretty-print the analysis result."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    agg = result["aggregate"]

    print()
    print("=" * 72)
    print(f"  Document: {result['document_title']}")
    print(f"  ID: {result['document_id']}")
    print(f"  Type: {result['file_type']}")
    print(f"  KB: {result['knowledge_base_id']}")
    print("=" * 72)
    print()
    print("── Chunk Composition ──")
    print(f"  Total chunks:        {agg['total_chunks']}")
    print(f"  Image chunks:        {agg['image_chunks']}")
    print(f"  Text chunks:         {agg['text_chunks']}")
    print(f"  Table chunks:        {agg['table_chunks']}")
    print(f"  Unique parents:      {agg['unique_parents']}")
    print(f"  Singleton img parents: {agg['singleton_image_parents']}")
    print(f"  Orphan images:       {agg['orphan_images']}")
    print()
    print("── Image Coverage ──")
    print(f"  With figure_ids:     {agg['images_with_figure_ids']} / {agg['image_chunks']} ({_pct(agg['images_with_figure_ids'], agg['image_chunks'])})")
    print(f"  With bindings:       {agg['images_with_bindings']} / {agg['image_chunks']} ({_pct(agg['images_with_bindings'], agg['image_chunks'])})")
    print(f"  With relation_chunks:{agg['images_with_relation_chunks']} / {agg['image_chunks']} ({_pct(agg['images_with_relation_chunks'], agg['image_chunks'])})")
    print(f"  Parent assignment:   {agg['images_with_parent_assignment']} / {agg['image_chunks']} ({_pct(agg['images_with_parent_assignment'], agg['image_chunks'])})")
    print(f"  Independent visual:  {agg['images_independent_visual']} / {agg['image_chunks']} ({_pct(agg['images_independent_visual'], agg['image_chunks'])})")
    print()
    print("── Relation Coverage ──")
    print(f"  Explicit coverage:          {agg['explicit_relation_coverage']}%")
    print(f"  Non-explicit coverage:      {agg['non_explicit_relation_coverage']}%")
    print()
    print("── Graph Incremental (P10) ──")
    print(f"  Graph incremental coverage: {agg['graph_incremental_coverage']}%  ({agg['graph_incremental_count']} images)")
    print(f"  Graph ambiguous only:       {agg['graph_ambiguous_only_count']} images (weak, NOT in relation_chunks)")
    print(f"  Graph duplicate rate:       {agg['graph_duplicate_rate']}%  ({agg['graph_duplicate_count']} images)")
    print()
    print("── Text Side ──")
    print(f"  Texts with outgoing: {agg['text_chunks_with_outgoing_relations']} / {agg['text_chunks']} ({_pct(agg['text_chunks_with_outgoing_relations'], agg['text_chunks'])})")
    print(f"  Texts with incoming: {agg['text_chunks_with_incoming_relations']} / {agg['text_chunks']} ({_pct(agg['text_chunks_with_incoming_relations'], agg['text_chunks'])})")
    print()
    print("── Relation Sources (image chunks) ──")
    for source, count in sorted(agg["relation_sources"].items(), key=lambda x: -x[1]):
        print(f"  {source:35s} {count:4d} / {agg['image_chunks']} ({_pct(count, agg['image_chunks'])})")

    if detail and "image_details" in result:
        print()
        print("── Image Detail ──")
        for r in result["image_details"]:
            page = str(r.get('page_no', '?'))
            fig = str(r.get('figure_ids', []))
            cap = str(r.get('caption', ''))[:60]
            rel_count = int(r.get('relation_chunks_count', 0))
            primary = str(r.get('primary_source', '?'))
            print(f"  [{primary:25s}] page={page:3s} fig={fig:20s} rel={rel_count:2d} cap={cap}")
            if r["media_parent_assignment"] and isinstance(r["media_parent_assignment"], dict):
                ma = r["media_parent_assignment"]
                print(f"    MA: strategy={ma.get('strategy','')} assigned={ma.get('assigned','')}")

        if result.get("interesting"):
            print()
            print("── Non-explicit Images WITH Relations ──")
            for r in result["interesting"]["non_explicit_with_relations"]:
                print(f"  page={r['page_no']} caption={r['caption'][:80]} sources={r['relation_sources']}")

    print()


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "N/A"
    return f"{numerator / denominator * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze multimodal relations for a document"
    )
    parser.add_argument("document_id", help="Document UUID")
    parser.add_argument("--detail", action="store_true", help="Show per-image details")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = analyze_document(args.document_id, detail=args.detail or args.json)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(result, detail=args.detail)


if __name__ == "__main__":
    main()
