from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.chunk_service import _rebuild_canonical_weknora_parent_child
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.mineru_adapter import (
    _content_list_to_blocks,
    _find_content_list,
)
from app.services.document_parser.page_visual_enricher import enrich_pdf_page_visuals
from app.services.document_parser.types import ParsedDocument


_GOLD_ONLY_ROLES = {
    "poster_core_text",
    "scanned_body_ocr",
    "visual_ocr_candidate",
}


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _config() -> ChunkerConfig:
    return ChunkerConfig(
        enable_parent_child=True,
        parent_chunk_size=4096,
        child_chunk_size=384,
        child_chunk_overlap=64,
        separators=["\n\n", "\n", "。", ". "],
    )


def _source_pdf_facts(pdf_path: Path) -> list[dict[str, Any]]:
    import pdfplumber

    facts: list[dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            page_area = max(float(page.width) * float(page.height), 1.0)
            image_areas: list[float] = []
            for image in page.images:
                try:
                    width = max(0.0, float(image["x1"]) - float(image["x0"]))
                    height = max(0.0, float(image["bottom"]) - float(image["top"]))
                except (KeyError, TypeError, ValueError):
                    continue
                image_areas.append(width * height)
            native_text = page.extract_text() or ""
            facts.append(
                {
                    "page_no": page_no,
                    "page_width": float(page.width),
                    "page_height": float(page.height),
                    "native_text_chars": len("".join(native_text.split())),
                    "embedded_image_count": len(page.images),
                    "dominant_image_ratio": round(
                        max(image_areas, default=0.0) / page_area, 6
                    ),
                }
            )
    return facts


def _replay_renderer(path, page_numbers, **kwargs) -> dict[int, dict]:
    return {
        page_no: {
            "path": f"replay://page-primary-{page_no}.jpg",
            "url": f"/files/replay/page-primary-{page_no}.jpg",
            "relative_path": f"replay/page-primary-{page_no}.jpg",
            "filename": f"page-primary-{page_no}.jpg",
            "sha256": f"replay-page-{page_no}",
            "size": 1,
            "base64": "",
        }
        for page_no in page_numbers
    }


def _block_snapshot(blocks) -> list[dict[str, Any]]:
    return [
        {
            **asdict(block),
            "bbox": list(block.bbox) if block.bbox else None,
            "image_base64": "",
        }
        for block in blocks
    ]


def replay_document(
    raw_path: Path,
    pdf_path: Path,
    destination: Path,
    *,
    document_id: str,
    knowledge_base_id: str = "dcp0-kb",
) -> dict[str, Any]:
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_items = _find_content_list(payload)
    replay_items = deepcopy(raw_items)
    for item in replay_items:
        if not isinstance(item, dict):
            continue
        if item.get("role") in _GOLD_ONLY_ROLES:
            # These are gold targets for later OCR-role work, not facts that
            # current MinerU production output can be assumed to provide.
            item.pop("role", None)
        if item.get("type") == "image":
            for key in ("img_path", "image_path", "path", "url"):
                item.pop(key, None)
    blocks = _content_list_to_blocks(
        replay_items,
        {},
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )
    parsed = ParsedDocument(
        content="\n\n".join(
            block.index_text() for block in blocks if block.index_text()
        ),
        blocks=blocks,
        metadata={"parser": "mineru", "source": "dcp0_replay"},
    )
    raw_parsed_blocks = _block_snapshot(blocks)
    parsed = enrich_pdf_page_visuals(
        pdf_path,
        parsed,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        renderer=_replay_renderer,
    )
    document = SimpleNamespace(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        title=document_id,
        file_type="pdf",
    )
    artifact: dict[str, Any] = {}
    chunks, _ = _rebuild_canonical_weknora_parent_child(
        document,
        parsed,
        "pdf",
        _config(),
        artifact_out=artifact,
    )
    snapshot = {
        "schema_version": 1,
        "document_id": document_id,
        "layers": {
            "source_pdf_facts": _source_pdf_facts(pdf_path),
            "raw_parser": {
                "path": raw_path.as_posix(),
                "content_list_count": len(raw_items),
            },
            "parsed_blocks": raw_parsed_blocks,
            "page_visual_enrichment": {
                "metadata": parsed.metadata,
                "blocks": _block_snapshot(parsed.blocks),
            },
            "canonical_artifact": artifact,
            "chunk_metadata": [
                {
                    "id": chunk.id,
                    "chunk_type": chunk.chunk_type,
                    "content": chunk.content,
                    "start_at": chunk.start_at,
                    "end_at": chunk.end_at,
                    "metadata_json": chunk.metadata_json or {},
                    "image_info": chunk.image_info or {},
                }
                for chunk in chunks
            ],
        },
    }
    _json(destination, snapshot)
    return snapshot


def replay_manifest(output_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for document in manifest["documents"]:
        destination = output_dir / "snapshots" / f"{document['document_id']}.json"
        snapshot = replay_document(
            output_dir / document["raw_response_path"],
            output_dir / document["pdf_path"],
            destination,
            document_id=document["document_id"],
        )
        document["snapshot_path"] = destination.relative_to(output_dir).as_posix()
        snapshots.append(snapshot)
    _json(output_dir / "corpus_manifest.json", manifest)
    return snapshots


__all__ = ["replay_document", "replay_manifest"]
