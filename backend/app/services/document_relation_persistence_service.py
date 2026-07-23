from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Document, DocumentCanonicalArtifact
from app.services.document_relation.models import CanonicalRelationGraph
from app.services.document_relation.relation_graph_merger import graph_from_legacy_figure_links
from app.utils.text import sanitize_json_for_postgres


def replace_canonical_artifact(
    db: Session,
    document: Document,
    payload: dict[str, Any],
) -> None:
    """Replace the current artifact in the caller-owned chunk transaction."""
    artifact = db.get(DocumentCanonicalArtifact, document.id)
    if not payload:
        if artifact is not None:
            db.delete(artifact)
        return
    if artifact is None:
        artifact = DocumentCanonicalArtifact(
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            parse_revision=str(payload["parse_revision"]),
            canonical_hash=str(payload["canonical_hash"]),
            canonical_markdown=str(payload["canonical_markdown"]),
        )
    artifact.knowledge_base_id = document.knowledge_base_id
    artifact.parse_revision = str(payload["parse_revision"])
    artifact.schema_version = int(payload["schema_version"])
    artifact.algorithm_version = str(payload["algorithm_version"])
    artifact.source_format = str(payload["source_format"])
    artifact.canonical_hash = str(payload["canonical_hash"])
    artifact.canonical_markdown = str(payload["canonical_markdown"])
    artifact.provenance_json = sanitize_json_for_postgres(payload["provenance_json"])
    artifact.assets_json = sanitize_json_for_postgres(payload["assets_json"])
    artifact.figure_links_json = sanitize_json_for_postgres(payload["figure_links_json"])
    artifact.relation_graph_json = sanitize_json_for_postgres(
        payload.get("relation_graph_json") or {}
    )
    artifact.stats_json = sanitize_json_for_postgres(payload["stats_json"])
    db.add(artifact)


def load_relation_graph(artifact: DocumentCanonicalArtifact) -> CanonicalRelationGraph:
    """Dual-read graph v1, falling back to the legacy explicit projection."""
    payload = getattr(artifact, "relation_graph_json", None) or {}
    if isinstance(payload, dict) and any(
        key in payload for key in ("schema_version", "edges", "groups")
    ):
        return CanonicalRelationGraph.from_dict(payload)
    return graph_from_legacy_figure_links(
        getattr(artifact, "figure_links_json", None) or {}
    )


__all__ = ["load_relation_graph", "replace_canonical_artifact"]
