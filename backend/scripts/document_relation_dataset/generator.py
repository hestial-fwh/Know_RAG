from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .models import PageSpec
from .pdf_writer import PdfImage, PdfPage, PdfText, write_pdf
from .raster import make_asset, write_png
from .templates import PAGE_HEIGHT, PAGE_WIDTH, SCENARIOS, build_page_spec


DATASET_TYPES = ("album", "calligraphy", "paper", "product_manual")


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _content_item(block, page: PageSpec) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "image" if block.block_type == "image" else "text",
        "page_idx": block.page_no - 1,
        "source_block_id": block.block_id,
        "role": block.role,
        "section_path": block.section_path,
        "source_order": block.source_order,
    }
    if block.bbox is not None:
        item["bbox"] = list(block.bbox)
    if not page.parser_noise.get("omit_page_extent"):
        item["page_width"] = page.page_width
        item["page_height"] = page.page_height
    if block.block_type == "image":
        item["img_path"] = block.asset_path
        item["image_caption"] = [block.caption] if block.caption else []
        item["text"] = block.caption
    else:
        item["text"] = block.text
        if block.role == "heading":
            item["text_level"] = 1
    return item


def _markdown(pages: list[PageSpec]) -> str:
    parts: list[str] = []
    for page in pages:
        for block in page.blocks:
            if block.role == "heading":
                parts.append(f"# {block.text}")
            elif block.block_type == "image":
                parts.append(f"![{block.caption}]({block.asset_path})")
            else:
                parts.append(block.text)
    return "\n\n".join(parts)


def _make_document(
    output_dir: Path,
    document_type: str,
    pages_per_type: int,
    seed: int,
) -> dict[str, Any]:
    pages = [
        build_page_spec(
            document_type,
            SCENARIOS[(page_no - 1) % len(SCENARIOS)],
            page_no,
            (page_no - 1) // len(SCENARIOS),
        )
        for page_no in range(1, pages_per_type + 1)
    ]
    pdf_texts: dict[int, list[PdfText]] = {
        page_no: [] for page_no in range(1, pages_per_type + 1)
    }
    pdf_images: dict[int, list[PdfImage]] = {
        page_no: [] for page_no in range(1, pages_per_type + 1)
    }
    content_list: list[dict[str, Any]] = []
    asset_records: list[dict[str, Any]] = []

    for page in pages:
        for block in page.blocks:
            content_list.append(_content_item(block, page))
            render_bbox = block.bbox or tuple(block.metadata["render_bbox"])
            if block.block_type == "image":
                asset_seed = seed + page.page_no * 101 + block.source_order * 17
                raster = make_asset(document_type, asset_seed)
                asset_path = output_dir / block.asset_path
                write_png(asset_path, raster)
                pdf_images[block.page_no].append(PdfImage(block.block_id, raster, render_bbox))
                asset_records.append(
                    {
                        "asset_id": block.block_id,
                        "path": block.asset_path,
                        "sha256": sha256(asset_path.read_bytes()).hexdigest(),
                        "page_no": page.page_no,
                        "bbox": list(render_bbox),
                    }
                )
                if block.caption:
                    x0, _, _, y1 = render_bbox
                    pdf_texts[block.page_no].append(
                        PdfText(block.caption, (x0, y1 + 8, 560, y1 + 28), 10)
                    )
            else:
                pdf_texts[block.page_no].append(
                    PdfText(block.text, render_bbox, 12 if block.role != "heading" else 16)
                )

    # A scenario may span physical pages (for example cross_page).  Render by
    # each block's declared page rather than by the PageSpec that owns its gold
    # relation, and keep the MinerU-shaped content list in physical page order.
    content_list.sort(
        key=lambda item: (
            item["page_idx"],
            (item.get("bbox") or [0, 0])[1],
            item.get("source_order", 0),
        )
    )
    pdf_pages = [
        PdfPage(
            PAGE_WIDTH,
            PAGE_HEIGHT,
            tuple(pdf_texts[page_no]),
            tuple(pdf_images[page_no]),
        )
        for page_no in range(1, pages_per_type + 1)
    ]

    document_name = f"synthetic_{document_type}.pdf"
    pdf_path = output_dir / "documents" / document_name
    write_pdf(pdf_path, pdf_pages)
    layout_path = output_dir / "layouts" / f"synthetic_{document_type}.json"
    _json(layout_path, {"document_type": document_type, "pages": [page.to_dict() for page in pages]})
    raw_response = {
        "results": {
            document_name: {
                "md_content": _markdown(pages),
                "content_list": json.dumps(content_list, ensure_ascii=False),
            }
        },
        "phase0_fixture": {
            "source": "deterministic_synthetic_mineru_shape",
            "parser_version": "synthetic-v1",
        },
    }
    raw_path = output_dir / "raw" / f"synthetic_{document_type}.json"
    _json(raw_path, raw_response)
    gold_path = output_dir / "gold" / f"synthetic_{document_type}.json"
    _json(
        gold_path,
        {
            "document_type": document_type,
            "relations": [
                relation.to_dict()
                for page in pages
                for relation in page.gold_relations
            ],
        },
    )
    return {
        "document_id": f"synthetic-{document_type}",
        "document_type": document_type,
        "page_count": len(pages),
        "scenario_counts": {
            scenario: sum(page.scenario == scenario for page in pages) for scenario in SCENARIOS
        },
        "pdf_path": pdf_path.relative_to(output_dir).as_posix(),
        "pdf_sha256": sha256(pdf_path.read_bytes()).hexdigest(),
        "layout_path": layout_path.relative_to(output_dir).as_posix(),
        "raw_response_path": raw_path.relative_to(output_dir).as_posix(),
        "gold_path": gold_path.relative_to(output_dir).as_posix(),
        "assets": asset_records,
    }


def generate_dataset(
    output_dir: Path,
    *,
    pages_per_type: int = 40,
    seed: int = 20260722,
) -> dict[str, Any]:
    if pages_per_type < len(SCENARIOS):
        raise ValueError(f"pages_per_type must be at least {len(SCENARIOS)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = [
        _make_document(output_dir, document_type, pages_per_type, seed + index * 100_000)
        for index, document_type in enumerate(DATASET_TYPES)
    ]
    manifest = {
        "schema_version": 1,
        "dataset_id": "document-relation-generated-v1",
        "generator_seed": seed,
        "pages_per_type": pages_per_type,
        "page_count": pages_per_type * len(DATASET_TYPES),
        "document_types": list(DATASET_TYPES),
        "scenarios": list(SCENARIOS),
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


__all__ = ["DATASET_TYPES", "SCENARIOS", "generate_dataset"]
