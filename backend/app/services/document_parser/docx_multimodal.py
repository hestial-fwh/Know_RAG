import html
import logging
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph

from app.services.document_parser.media_storage import save_media_bytes
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.utils.text import sanitize_text_for_postgres

logger = logging.getLogger(__name__)


def _iter_body_blocks(doc: DocxDocument):
    for child in doc._element.body:
        if child.tag.endswith("}p") or child.tag.endswith("p"):
            yield "paragraph", Paragraph(child, doc)
        elif child.tag.endswith("}tbl") or child.tag.endswith("tbl"):
            yield "table", DocxTable(child, doc)


def _extract_images_from_paragraph(doc: DocxDocument, paragraph: Paragraph) -> list[bytes]:
    image_blobs: list[bytes] = []
    pics = paragraph._element.xpath(".//pic:pic")
    for pic in pics:
        embeds = pic.xpath(".//a:blip/@r:embed")
        if not embeds:
            continue
        embed = embeds[0]
        related_part = doc.part.related_parts.get(embed)
        if related_part is None:
            continue
        blob = None
        try:
            image = getattr(related_part, "image", None)
            blob = getattr(image, "blob", None) if image is not None else None
        except Exception as exc:
            logger.info("docx image normal extraction failed; falling back to raw blob: %s", exc)
        if blob is None:
            blob = getattr(related_part, "blob", None)
        if blob:
            image_blobs.append(blob)
    return image_blobs


def _table_to_html(table: DocxTable) -> tuple[str, str]:
    rows_text: list[str] = []
    parts = ["<table>"]
    for row in table.rows:
        parts.append("<tr>")
        row_cells: list[str] = []
        for cell in row.cells:
            text = sanitize_text_for_postgres(cell.text.strip())
            row_cells.append(text)
            parts.append(f"<td>{html.escape(text)}</td>")
        parts.append("</tr>")
        if any(row_cells):
            rows_text.append(" | ".join(row_cells))
    parts.append("</table>")
    return "".join(parts), "\n".join(rows_text)


def parse_docx_multimodal(
    path: Path,
    *,
    knowledge_base_id: str,
    document_id: str,
) -> ParsedDocument:
    """Parse DOCX in body order and preserve text/table/image blocks.

    This follows the WeKnora/RAGFlow shared idea that DOCX is structured enough
    to parse by its XML body order.  It avoids importing RAGFlow code and keeps
    images as independent local files plus image chunks.
    """

    doc = DocxDocument(str(path))
    blocks: list[ParsedBlock] = []
    text_parts: list[str] = []
    order = 0
    pending_images: list[bytes] = []

    def flush_pending_images(caption: str = "") -> None:
        nonlocal order, pending_images
        for idx, blob in enumerate(pending_images):
            media = save_media_bytes(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                filename_hint=f"docx_image_{order}_{idx}.png",
                data=blob,
                default_ext=".png",
            )
            caption_text = sanitize_text_for_postgres(caption)
            blocks.append(
                ParsedBlock(
                    block_type="image",
                    text=caption_text or f"图片：{media['filename']}",
                    order_index=order,
                    image_path=media["path"],
                    image_url=media["relative_path"],
                    image_base64=media["base64"],
                    metadata={
                        "parser": "docx_multimodal",
                        "source": "docx_embedded_image",
                        "filename": media["filename"],
                        "relative_path": media["relative_path"],
                        "sha256": media["sha256"],
                        "size": media["size"],
                        "caption": caption_text,
                    },
                )
            )
            text_parts.append(f"![{caption_text or media['filename']}]({media['relative_path']})")
            order += 1
        pending_images = []

    for kind, block in _iter_body_blocks(doc):
        if kind == "paragraph":
            paragraph: Paragraph = block
            text = sanitize_text_for_postgres(paragraph.text.strip())
            style_name = paragraph.style.name if paragraph.style else ""
            images = _extract_images_from_paragraph(doc, paragraph)

            if text and style_name.lower() == "caption" and pending_images:
                flush_pending_images(text)
                blocks.append(
                    ParsedBlock(
                        block_type="text",
                        text=text,
                        order_index=order,
                        metadata={"parser": "docx_multimodal", "style": style_name},
                    )
                )
                text_parts.append(text)
                order += 1
                continue

            if pending_images:
                flush_pending_images()

            if text:
                blocks.append(
                    ParsedBlock(
                        block_type="text",
                        text=text,
                        order_index=order,
                        metadata={"parser": "docx_multimodal", "style": style_name},
                    )
                )
                text_parts.append(text)
                order += 1

            if images:
                pending_images.extend(images)
                if text:
                    flush_pending_images(text if style_name.lower() == "caption" else "")
            continue

        if kind == "table":
            if pending_images:
                flush_pending_images()
            table: DocxTable = block
            html_text, plain_text = _table_to_html(table)
            if not plain_text.strip() and not html_text.strip():
                continue
            blocks.append(
                ParsedBlock(
                    block_type="table",
                    text=plain_text,
                    table_html=html_text,
                    order_index=order,
                    metadata={"parser": "docx_multimodal", "source": "docx_table"},
                )
            )
            text_parts.append(plain_text or html_text)
            order += 1

    if pending_images:
        flush_pending_images()

    content = sanitize_text_for_postgres("\n\n".join(part for part in text_parts if part))
    return ParsedDocument(content=content, blocks=blocks, metadata={"parser": "docx_multimodal"})
