from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from scripts.document_relation_dataset.pdf_writer import (
    PdfImage,
    PdfPage,
    PdfText,
    write_pdf,
)
from scripts.document_relation_dataset.raster import make_asset, write_png

from .models import BlockSpec, DocumentSpec, PageSpec
from .templates import (
    DOCUMENT_TYPES,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    SCENARIOS,
    build_document_specs,
)


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _raw_item(block: BlockSpec, page: PageSpec) -> dict[str, Any]:
    raw_type = block.block_type
    if block.role == "heading":
        raw_type = "title"
    item: dict[str, Any] = {
        "type": raw_type,
        "page_idx": page.page_no - 1,
        "bbox": list(block.bbox),
        "page_width": page.page_width,
        "page_height": page.page_height,
        "source_block_id": block.block_id,
        "role": block.role,
        "section_path": page.case_id,
    }
    if block.block_type == "image":
        item["img_path"] = block.asset_path
        item["image_caption"] = [block.caption] if block.caption else []
        item["text"] = block.caption
    elif block.block_type == "table":
        item["table_body"] = block.text
        item["table_caption"] = ["Quarterly Metrics"]
    else:
        item["text"] = block.text
        if block.role == "heading":
            item["text_level"] = 1
    return item


def _asset_seed(seed: int, document_index: int, page_no: int, block_index: int) -> int:
    return seed + document_index * 100_000 + page_no * 1_003 + block_index * 37


def _write_document(
    output_dir: Path,
    spec: DocumentSpec,
    *,
    seed: int,
    document_index: int,
) -> dict[str, Any]:
    pdf_pages: list[PdfPage] = []
    raw_items: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []

    for page in spec.pages:
        texts: list[PdfText] = []
        images: list[PdfImage] = []
        for block_index, block in enumerate(page.blocks):
            if block.block_type == "image":
                raster = make_asset(
                    block.asset_kind or "album",
                    _asset_seed(seed, document_index, page.page_no, block_index),
                )
                asset_path = output_dir / block.asset_path
                write_png(asset_path, raster)
                images.append(PdfImage(block.block_id, raster, block.bbox))
                assets.append(
                    {
                        "asset_id": block.block_id,
                        "path": block.asset_path,
                        "sha256": sha256(asset_path.read_bytes()).hexdigest(),
                        "page_no": page.page_no,
                        "bbox": list(block.bbox),
                    }
                )
            if block.native_pdf_text and block.text:
                texts.append(
                    PdfText(
                        block.text,
                        block.bbox,
                        16 if block.role == "heading" else 11,
                    )
                )
            if block.parser_output:
                raw_items.append(_raw_item(block, page))
        pdf_pages.append(
            PdfPage(
                width=PAGE_WIDTH,
                height=PAGE_HEIGHT,
                texts=tuple(texts),
                images=tuple(images),
            )
        )

    document_name = f"{spec.document_id}.pdf"
    pdf_path = output_dir / "documents" / document_name
    write_pdf(pdf_path, pdf_pages)
    layout_path = output_dir / "layouts" / f"{spec.document_id}.json"
    layout = {
        "document_id": spec.document_id,
        "document_type": spec.document_type,
        "primary_kind": spec.primary_kind,
        "pages": [page.to_dict() for page in spec.pages],
    }
    _json(layout_path, layout)

    raw_path = output_dir / "raw" / f"{spec.document_id}.json"
    _json(
        raw_path,
        {
            "results": {
                document_name: {
                    "md_content": "\n\n".join(
                        block.text or block.caption
                        for page in spec.pages
                        for block in page.blocks
                        if block.parser_output and (block.text or block.caption)
                    ),
                    "content_list": json.dumps(raw_items, ensure_ascii=False),
                }
            },
            "dcp0_fixture": {
                "source": "deterministic_synthetic_mineru_shape",
                "parser_version": "dcp0-synthetic-v1",
            },
        },
    )

    gold_path = output_dir / "gold" / f"{spec.document_id}.json"
    _json(
        gold_path,
        {
            "schema_version": 1,
            "document_id": spec.document_id,
            "document_type": spec.document_type,
            "primary_kind": spec.primary_kind,
            "pages": [
                {
                    "case_id": page.case_id,
                    "page_no": page.page_no,
                    "page_kind": page.page_kind,
                    "preserve_page_visual": page.preserve_page_visual,
                    "chunk_policy_id": page.chunk_policy_id,
                    "expected_known_gaps": list(page.expected_known_gaps),
                    "block_policies": [
                        {
                            "block_id": block.block_id,
                            "semantic_role": block.expected_semantic_role,
                            "index_policy": block.expected_index_policy,
                            "parser_output_expected": block.parser_output,
                        }
                        for block in page.blocks
                    ],
                }
                for page in spec.pages
            ],
        },
    )
    return {
        "document_id": spec.document_id,
        "document_type": spec.document_type,
        "primary_kind": spec.primary_kind,
        "page_count": len(spec.pages),
        "scenarios": [page.case_id for page in spec.pages],
        "pdf_path": pdf_path.relative_to(output_dir).as_posix(),
        "pdf_sha256": sha256(pdf_path.read_bytes()).hexdigest(),
        "layout_path": layout_path.relative_to(output_dir).as_posix(),
        "raw_response_path": raw_path.relative_to(output_dir).as_posix(),
        "gold_path": gold_path.relative_to(output_dir).as_posix(),
        "assets": assets,
    }


def generate_dataset(output_dir: Path, *, seed: int = 20260723) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    specs = build_document_specs()
    documents = [
        _write_document(output_dir, spec, seed=seed, document_index=index)
        for index, spec in enumerate(specs)
    ]
    manifest = {
        "schema_version": 1,
        "dataset_id": "document-profile-dcp0-v1",
        "generator_seed": seed,
        "document_types": list(DOCUMENT_TYPES),
        "scenarios": list(SCENARIOS),
        "document_count": len(documents),
        "page_count": sum(document["page_count"] for document in documents),
        "documents": documents,
        "provenance": {
            "source_kind": "deterministic_synthetic",
            "external_assets": False,
            "external_model_calls": False,
            "accuracy_claim_allowed": False,
        },
    }
    _json(output_dir / "corpus_manifest.json", manifest)
    return manifest


__all__ = ["DOCUMENT_TYPES", "SCENARIOS", "generate_dataset"]
