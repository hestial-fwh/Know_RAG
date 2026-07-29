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
from scripts.document_relation_dataset.raster import Raster, make_asset, write_png

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


def _raw_item(
    block: BlockSpec,
    page: PageSpec,
    *,
    derived_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    for key, value in {**block.metadata, **(derived_metadata or {})}.items():
        item.setdefault(str(key), value)
    return item


def _asset_seed(seed: int, document_index: int, page_no: int, block_index: int) -> int:
    return seed + document_index * 100_000 + page_no * 1_003 + block_index * 37


def _vertical_whitespace_candidates(raster: Raster) -> list[list[float]]:
    """Detect a strong internal vertical whitespace separator without OpenCV."""

    white_columns: list[bool] = []
    for x in range(raster.width):
        white = 0
        for y in range(raster.height):
            offset = (y * raster.width + x) * 3
            if min(raster.pixels[offset : offset + 3]) >= 248:
                white += 1
        white_columns.append(white / max(raster.height, 1) >= 0.96)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, is_white in enumerate([*white_columns, False]):
        if is_white and start is None:
            start = index
        elif not is_white and start is not None:
            runs.append((start, index))
            start = None
    internal = [
        run
        for run in runs
        if run[1] - run[0] >= raster.width * 0.04
        and run[0] >= raster.width * 0.2
        and run[1] <= raster.width * 0.8
    ]
    if not internal:
        return []
    left, right = max(internal, key=lambda run: run[1] - run[0])
    return [
        [0.0, 0.0, round(left / raster.width, 6), 1.0],
        [round(right / raster.width, 6), 0.0, 1.0, 1.0],
    ]


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
            derived_metadata: dict[str, Any] = {}
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
                whitespace_candidates = (
                    _vertical_whitespace_candidates(raster)
                    if block.asset_kind == "split_album"
                    else []
                )
                if whitespace_candidates:
                    derived_metadata = {
                        "layout_region_candidates": whitespace_candidates,
                        "layout_candidate_method": (
                            "raster_vertical_whitespace_projection"
                        ),
                    }
            if block.native_pdf_text and block.text:
                texts.append(
                    PdfText(
                        block.text,
                        block.bbox,
                        16 if block.role == "heading" else 11,
                    )
                )
            if block.parser_output:
                raw_items.append(
                    _raw_item(
                        block,
                        page,
                        derived_metadata=derived_metadata,
                    )
                )
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
        "evaluation_scope": spec.evaluation_scope,
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
            "evaluation_scope": spec.evaluation_scope,
            "pages": [
                {
                    "case_id": page.case_id,
                    "page_no": page.page_no,
                    "page_kind": page.page_kind,
                    "preserve_page_visual": page.preserve_page_visual,
                    "chunk_policy_id": page.chunk_policy_id,
                    "expected_known_gaps": list(page.expected_known_gaps),
                    "capabilities": list(page.capabilities),
                    "confusion_group": page.confusion_group,
                    "variant": page.variant,
                    "expected_visual_items": list(page.expected_visual_items),
                    "baseline_assertion": page.baseline_assertion,
                    "block_policies": [
                        {
                            "block_id": block.block_id,
                            "semantic_role": block.expected_semantic_role,
                            "index_policy": block.expected_index_policy,
                            "parser_output_expected": block.parser_output,
                            "expected_visual_item_id": block.expected_visual_item_id,
                            "expected_relation": block.expected_relation,
                            "ocr_quality": block.ocr_quality,
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
        "evaluation_scope": spec.evaluation_scope,
        "page_count": len(spec.pages),
        "scenarios": [page.case_id for page in spec.pages],
        "pdf_path": pdf_path.relative_to(output_dir).as_posix(),
        "pdf_sha256": sha256(pdf_path.read_bytes()).hexdigest(),
        "layout_path": layout_path.relative_to(output_dir).as_posix(),
        "layout_sha256": sha256(layout_path.read_bytes()).hexdigest(),
        "raw_response_path": raw_path.relative_to(output_dir).as_posix(),
        "raw_response_sha256": sha256(raw_path.read_bytes()).hexdigest(),
        "gold_path": gold_path.relative_to(output_dir).as_posix(),
        "gold_sha256": sha256(gold_path.read_bytes()).hexdigest(),
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
        "schema_version": 2,
        "dataset_id": "document-profile-dcp01-v2",
        "generator_seed": seed,
        "document_types": list(DOCUMENT_TYPES),
        "scenarios": list(SCENARIOS),
        "document_count": len(documents),
        "page_count": sum(document["page_count"] for document in documents),
        "baseline_document_count": sum(
            document["evaluation_scope"] == "baseline" for document in documents
        ),
        "baseline_page_count": sum(
            document["page_count"]
            for document in documents
            if document["evaluation_scope"] == "baseline"
        ),
        "adversarial_page_count": sum(
            document["page_count"]
            for document in documents
            if document["evaluation_scope"] == "adversarial"
        ),
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
