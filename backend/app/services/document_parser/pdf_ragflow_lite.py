import logging
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from app.services.document_parser.media_storage import save_media_bytes
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.utils.text import sanitize_text_for_postgres

logger = logging.getLogger(__name__)


def _table_to_html(rows: list[list[str]]) -> tuple[str, str]:
    import html

    if not rows:
        return "", ""
    html_parts = ["<table>"]
    plain_rows: list[str] = []
    for row in rows:
        clean_row = [sanitize_text_for_postgres(str(cell or "").strip()) for cell in row]
        if any(clean_row):
            plain_rows.append(" | ".join(clean_row))
        html_parts.append("<tr>")
        for cell in clean_row:
            html_parts.append(f"<td>{html.escape(cell)}</td>")
        html_parts.append("</tr>")
    html_parts.append("</table>")
    return "".join(html_parts), "\n".join(plain_rows)


def _valid_bbox(bbox: tuple[float, float, float, float], *, width: float, height: float) -> tuple[float, float, float, float] | None:
    x0, top, x1, bottom = [float(v) for v in bbox]
    x0 = max(0.0, min(x0, width))
    x1 = max(0.0, min(x1, width))
    top = max(0.0, min(top, height))
    bottom = max(0.0, min(bottom, height))
    if x1 - x0 < 8 or bottom - top < 8:
        return None
    return (x0, top, x1, bottom)


def _save_pdfplumber_crop(
    page: Any,
    bbox: tuple[float, float, float, float],
    *,
    knowledge_base_id: str,
    document_id: str,
    filename_hint: str,
    resolution: int = 144,
) -> dict | None:
    try:
        cropped = page.crop(bbox)
        page_image = cropped.to_image(resolution=resolution)
        pil = page_image.original.convert("RGB")
        buf = BytesIO()
        pil.save(buf, format="PNG")
        return save_media_bytes(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            filename_hint=filename_hint,
            data=buf.getvalue(),
            default_ext=".png",
        )
    except Exception as exc:
        logger.info("PDF crop failed page=%s bbox=%s: %s", getattr(page, "page_number", "?"), bbox, exc)
        return None


def _parse_with_pdfplumber(
    path: Path,
    *,
    knowledge_base_id: str,
    document_id: str,
    crop_tables: bool = True,
    crop_figures: bool = True,
) -> tuple[list[ParsedBlock], dict]:
    import pdfplumber

    blocks: list[ParsedBlock] = []
    order = 0
    metadata = {
        "pdf_engine": "pdfplumber",
        "pages": 0,
        "table_block_count": 0,
        "table_crop_count": 0,
        "figure_crop_count": 0,
    }
    with pdfplumber.open(str(path)) as pdf:
        metadata["pages"] = len(pdf.pages)
        for page_index, page in enumerate(pdf.pages, start=1):
            page_text = sanitize_text_for_postgres(page.extract_text() or "")
            if page_text:
                blocks.append(
                    ParsedBlock(
                        block_type="text",
                        text=page_text,
                        order_index=order,
                        page_no=page_index,
                        metadata={"parser": "ragflow_lite_pdf", "source": "pdf_text", "page_no": page_index},
                    )
                )
                order += 1

            table_bboxes: list[tuple[float, float, float, float]] = []
            try:
                tables = page.find_tables() or []
            except Exception as exc:
                logger.info("pdfplumber find_tables failed on page %s: %s", page_index, exc)
                tables = []
            for table_index, table in enumerate(tables):
                try:
                    rows = table.extract() or []
                except Exception:
                    rows = []
                html_text, plain_text = _table_to_html(rows)
                if not plain_text.strip():
                    continue
                bbox = _valid_bbox(tuple(table.bbox), width=float(page.width), height=float(page.height)) if getattr(table, "bbox", None) else None
                if bbox:
                    table_bboxes.append(bbox)
                media = None
                if crop_tables and bbox:
                    media = _save_pdfplumber_crop(
                        page,
                        bbox,
                        knowledge_base_id=knowledge_base_id,
                        document_id=document_id,
                        filename_hint=f"pdf_page_{page_index}_table_{table_index}.png",
                    )
                    if media:
                        metadata["table_crop_count"] += 1
                block_metadata = {
                    "parser": "ragflow_lite_pdf",
                    "source": "pdfplumber_table",
                    "page_no": page_index,
                    "table_index": table_index,
                }
                if media:
                    block_metadata.update(
                        {
                            "table_image_relative_path": media["relative_path"],
                            "table_image_sha256": media["sha256"],
                            "table_image_size": media["size"],
                        }
                    )
                blocks.append(
                    ParsedBlock(
                        block_type="table",
                        text=plain_text,
                        table_html=html_text,
                        order_index=order,
                        page_no=page_index,
                        bbox=bbox,
                        image_path=media["path"] if media else "",
                        image_url=media["relative_path"] if media else "",
                        image_base64=media["base64"] if media else "",
                        metadata=block_metadata,
                    )
                )
                metadata["table_block_count"] += 1
                order += 1

            if crop_figures:
                page_images = getattr(page, "images", []) or []
                for image_index, image in enumerate(page_images):
                    raw_bbox = (
                        image.get("x0", 0),
                        image.get("top", image.get("y0", 0)),
                        image.get("x1", 0),
                        image.get("bottom", image.get("y1", 0)),
                    )
                    bbox = _valid_bbox(raw_bbox, width=float(page.width), height=float(page.height))
                    if not bbox:
                        continue
                    # Skip very small decorative elements and table-overlapping crops.
                    x0, top, x1, bottom = bbox
                    if (x1 - x0) * (bottom - top) < 2000:
                        continue
                    def overlaps(a, b):
                        ax0, at, ax1, ab = a
                        bx0, bt, bx1, bb = b
                        return max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ab, bb) - max(at, bt))
                    if any(overlaps(bbox, tb) / max((x1 - x0) * (bottom - top), 1) > 0.5 for tb in table_bboxes):
                        continue
                    media = _save_pdfplumber_crop(
                        page,
                        bbox,
                        knowledge_base_id=knowledge_base_id,
                        document_id=document_id,
                        filename_hint=f"pdf_page_{page_index}_figure_{image_index}.png",
                    )
                    if not media:
                        continue
                    blocks.append(
                        ParsedBlock(
                            block_type="image",
                            text=f"PDF 第 {page_index} 页图像区域：{media['filename']}",
                            order_index=order,
                            page_no=page_index,
                            bbox=bbox,
                            image_path=media["path"],
                            image_url=media["relative_path"],
                            image_base64=media["base64"],
                            metadata={
                                "parser": "ragflow_lite_pdf",
                                "source": "pdfplumber_figure_crop",
                                "page_no": page_index,
                                "figure_index": image_index,
                                "filename": media["filename"],
                                "relative_path": media["relative_path"],
                                "sha256": media["sha256"],
                                "size": media["size"],
                            },
                        )
                    )
                    metadata["figure_crop_count"] += 1
                    order += 1
    return blocks, metadata


def _parse_with_pypdf(path: Path) -> tuple[list[ParsedBlock], dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[ParsedBlock] = []
    for index, page in enumerate(reader.pages, start=1):
        text = sanitize_text_for_postgres(page.extract_text() or "")
        if text:
            blocks.append(
                ParsedBlock(
                    block_type="text",
                    text=text,
                    order_index=len(blocks),
                    page_no=index,
                    metadata={"parser": "ragflow_lite_pdf", "source": "pypdf_text", "page_no": index},
                )
            )
    return blocks, {"pdf_engine": "pypdf", "pages": len(reader.pages), "table_block_count": 0, "table_crop_count": 0, "figure_crop_count": 0}


def _extract_pypdf_images(
    path: Path,
    *,
    knowledge_base_id: str,
    document_id: str,
    start_order: int,
) -> list[ParsedBlock]:
    try:
        from pypdf import PdfReader
    except Exception:
        return []

    blocks: list[ParsedBlock] = []
    try:
        reader = PdfReader(str(path))
    except Exception:
        return []

    order = start_order
    for page_index, page in enumerate(reader.pages, start=1):
        images = getattr(page, "images", []) or []
        for image_index, image in enumerate(images):
            try:
                data = image.data
            except Exception:
                continue
            if not data:
                continue
            name = getattr(image, "name", "") or f"pdf_page_{page_index}_image_{image_index}.png"
            media = save_media_bytes(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                filename_hint=name,
                data=data,
                default_ext=".png",
            )
            blocks.append(
                ParsedBlock(
                    block_type="image",
                    text=f"PDF 第 {page_index} 页内嵌图片：{media['filename']}",
                    order_index=order,
                    page_no=page_index,
                    image_path=media["path"],
                    image_url=media["relative_path"],
                    image_base64=media["base64"],
                    metadata={
                        "parser": "ragflow_lite_pdf",
                        "source": "pdf_embedded_image",
                        "page_no": page_index,
                        "image_index": image_index,
                        "filename": media["filename"],
                        "relative_path": media["relative_path"],
                        "sha256": media["sha256"],
                        "size": media["size"],
                    },
                )
            )
            order += 1
    return blocks


def _render_pages_as_images(
    path: Path,
    *,
    knowledge_base_id: str,
    document_id: str,
    start_order: int,
    dpi: int = 144,
) -> list[ParsedBlock]:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        logger.info("pypdfium2 not available; skip scanned PDF rendering: %s", exc)
        return []

    blocks: list[ParsedBlock] = []
    pdf = pdfium.PdfDocument(str(path))
    scale = max(dpi, 72) / 72
    order = start_order
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            bitmap = None
            try:
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil().convert("RGB")
                buf = BytesIO()
                pil.save(buf, format="JPEG", quality=85, optimize=True)
                data = buf.getvalue()
            finally:
                close = getattr(bitmap, "close", None)
                if close:
                    close()
                close = getattr(page, "close", None)
                if close:
                    close()
            media = save_media_bytes(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                filename_hint=f"pdf_page_{page_index + 1}.jpg",
                data=data,
                default_ext=".jpg",
            )
            blocks.append(
                ParsedBlock(
                    block_type="image",
                    text=f"扫描型 PDF 第 {page_index + 1} 页图片：{media['filename']}",
                    order_index=order,
                    page_no=page_index + 1,
                    image_path=media["path"],
                    image_url=media["relative_path"],
                    image_base64=media["base64"],
                    metadata={
                        "parser": "ragflow_lite_pdf",
                        "source": "pdf_page_render",
                        "page_no": page_index + 1,
                        "relative_path": media["relative_path"],
                        "sha256": media["sha256"],
                        "size": media["size"],
                    },
                )
            )
            order += 1
    finally:
        close = getattr(pdf, "close", None)
        if close:
            close()
    return blocks


def parse_pdf_ragflow_lite(
    path: Path,
    *,
    knowledge_base_id: str,
    document_id: str,
    extract_images: bool = True,
    render_scanned_pages: bool = True,
    crop_tables: bool = True,
    crop_figures: bool = True,
) -> ParsedDocument:
    """Compatibility entrypoint.

    Phase G uses the advanced RAGFlow-style layout parser by default.  The old
    lightweight implementation is kept below as a fallback so deployments are
    still robust when pdfplumber layout extraction fails on a broken PDF.
    """
    try:
        from app.services.document_parser.pdf_ragflow_advanced import parse_pdf_ragflow_advanced

        return parse_pdf_ragflow_advanced(
            path,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            render_scanned_pages=render_scanned_pages,
        )
    except Exception as advanced_exc:
        logger.warning("advanced PDF parser failed; fallback to lite parser: %s", advanced_exc)

    try:
        blocks, metadata = _parse_with_pdfplumber(
            path,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            crop_tables=crop_tables,
            crop_figures=crop_figures,
        )
    except Exception as exc:
        logger.info("pdfplumber parser unavailable or failed; fallback to pypdf: %s", exc)
        blocks, metadata = _parse_with_pypdf(path)

    if extract_images and int(metadata.get("figure_crop_count") or 0) == 0:
        blocks.extend(
            _extract_pypdf_images(
                path,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                start_order=len(blocks),
            )
        )

    has_text = any(block.block_type in {"text", "table"} and block.index_text().strip() for block in blocks)
    if not has_text and render_scanned_pages:
        rendered = _render_pages_as_images(
            path,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            start_order=len(blocks),
        )
        blocks.extend(rendered)
        metadata["scanned_pdf_rendered"] = bool(rendered)
        metadata["scanned_page_image_count"] = len(rendered)

    blocks.sort(key=lambda item: (item.page_no or 0, item.order_index))
    content_parts: list[str] = []
    for block in blocks:
        if block.block_type == "image":
            content_parts.append(f"![{block.text}]({block.image_url})")
        elif block.block_type == "table" and block.image_url:
            content_parts.append(f"{block.index_text()}\n\n![table]({block.image_url})")
        else:
            content_parts.append(block.index_text())
    content = sanitize_text_for_postgres("\n\n".join(part for part in content_parts if part))
    if not content:
        raise ValueError("未能从 PDF 中解析出文本或图片内容，可能需要安装 pypdfium2/pdfplumber 或接入 OCR 服务")
    return ParsedDocument(content=content, blocks=blocks, metadata={"parser": "ragflow_lite_pdf", "advanced_fallback": True, **metadata})
