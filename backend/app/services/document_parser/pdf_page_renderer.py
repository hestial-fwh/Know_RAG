from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path

from app.services.document_parser.media_storage import save_media_bytes


logger = logging.getLogger(__name__)


def render_pdf_pages(
    path: Path,
    page_numbers: list[int],
    *,
    knowledge_base_id: str,
    document_id: str,
    dpi: int = 144,
) -> dict[int, dict]:
    """Render only selected one-based PDF pages and persist them as JPEG media."""
    selected = sorted({int(page_no) for page_no in page_numbers if int(page_no) > 0})
    if not selected:
        return {}
    import pypdfium2 as pdfium

    rendered: dict[int, dict] = {}
    pdf = pdfium.PdfDocument(str(path))
    scale = max(int(dpi), 72) / 72
    try:
        for page_no in selected:
            if page_no > len(pdf):
                logger.warning(
                    "Skip PDF page render outside document: page=%s", page_no
                )
                continue
            page = None
            bitmap = None
            try:
                page = pdf[page_no - 1]
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil().convert("RGB")
                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=88, optimize=True)
                media = save_media_bytes(
                    knowledge_base_id=knowledge_base_id,
                    document_id=document_id,
                    filename_hint=f"pdf_page_primary_visual_{page_no}.jpg",
                    data=buffer.getvalue(),
                    default_ext=".jpg",
                )
                # The page is already durable in MinIO. Keeping the encoded
                # payload would duplicate every full-page JPEG in memory and
                # later in Chunk.image_info.
                media.pop("base64", None)
                rendered[page_no] = media
            except Exception:
                logger.warning(
                    "PDF page visual render failed: document_id=%s page=%s",
                    document_id,
                    page_no,
                    exc_info=True,
                )
            finally:
                close = getattr(bitmap, "close", None)
                if close:
                    close()
                close = getattr(page, "close", None)
                if close:
                    close()
    finally:
        close = getattr(pdf, "close", None)
        if close:
            close()
    return rendered


__all__ = ["render_pdf_pages"]
