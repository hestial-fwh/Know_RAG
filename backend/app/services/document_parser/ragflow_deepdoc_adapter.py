"""Adapter that uses the original RAGFlow DeepDoc parser, but keeps know_rag ingestion unchanged.

The vendored code under backend/ragflow_vendor/deepdoc is copied from the
RAGFlow DeepDoc reference package.  This adapter is the boundary: RAGFlow is
used for document recognition/cleaning, while know_rag still owns document,
chunk, embedding and retrieval storage.
"""
from __future__ import annotations

import base64
import html
import logging
import os
import re
import shutil
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from app.services.document_parser.media_storage import save_media_bytes
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.utils.text import sanitize_text_for_postgres

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"@@([0-9\-]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)##")
_HTML_RE = re.compile(r"<[^>]+>")


def _vendor_root() -> Path:
    return Path(__file__).resolve().parents[4] / "ragflow_vendor"


def _ensure_vendor_path() -> Path:
    root = _vendor_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("RAGFLOW_VENDOR_BASE", str(root))
    os.environ.setdefault("RAG_PROJECT_BASE", str(root))
    _ensure_model_dir(root)
    return root


def _ensure_model_dir(root: Path) -> None:
    """Make RAGFlow's expected rag/res/deepdoc point at Docker model cache when possible."""
    expected = root / "rag" / "res" / "deepdoc"
    expected.parent.mkdir(parents=True, exist_ok=True)
    external = Path(os.getenv("DEEPDOC_MODEL_DIR", "/data/models/deepdoc"))
    if external.exists() and any(external.iterdir()):
        if expected.exists() and not expected.is_symlink() and any(expected.iterdir()):
            return
        try:
            if expected.exists() or expected.is_symlink():
                if expected.is_symlink() or expected.is_file():
                    expected.unlink()
                else:
                    shutil.rmtree(expected)
            expected.symlink_to(external, target_is_directory=True)
            return
        except Exception:
            expected.mkdir(parents=True, exist_ok=True)
            for child in external.iterdir():
                if child.is_file():
                    dst = expected / child.name
                    if not dst.exists():
                        try:
                            shutil.copy2(child, dst)
                        except Exception:
                            pass
    else:
        expected.mkdir(parents=True, exist_ok=True)


def _clean_text(text: Any) -> str:
    return sanitize_text_for_postgres(str(text or "").strip())


def _strip_position_tags(text: str) -> str:
    return _clean_text(_TAG_RE.sub("", text or ""))


def _positions_from_text(text: str) -> tuple[int | None, tuple[float, float, float, float] | None]:
    m = _TAG_RE.search(text or "")
    if not m:
        return None, None
    try:
        page_token = m.group(1).split("-")[0]
        page_no = int(page_token)
        x0, x1, top, bottom = map(float, m.groups()[1:])
        return page_no, (x0, top, x1, bottom)
    except Exception:
        return None, None


def _pil_to_bytes(img: Any) -> bytes | None:
    if img is None:
        return None
    try:
        if hasattr(img, "to_pil"):
            img = img.to_pil()
        if isinstance(img, bytes):
            return img
        if isinstance(img, str):
            try:
                return base64.b64decode(img)
            except Exception:
                return None
        if isinstance(img, Image.Image):
            buf = BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()
    except Exception as exc:
        logger.info("convert ragflow image failed: %s", exc)
    return None


def _table_text(rows_or_html: Any) -> tuple[str, str]:
    if rows_or_html is None:
        return "", ""
    if isinstance(rows_or_html, str):
        raw = rows_or_html
        text = html.unescape(_HTML_RE.sub(" ", raw))
        text = re.sub(r"\s+", " ", text).strip()
        return _clean_text(text), raw if "<table" in raw.lower() else ""
    if isinstance(rows_or_html, (list, tuple)):
        lines = []
        for row in rows_or_html:
            if isinstance(row, (list, tuple)):
                lines.append(" | ".join(str(c) for c in row))
            else:
                lines.append(str(row))
        return _clean_text("\n".join(lines)), ""
    return _clean_text(str(rows_or_html)), ""


def _classify_ragflow_pdf_media(rows_or_html: Any, text: str, table_html: str) -> str:
    """Classify RAGFlow PDF media output as table or image/figure.

    RAGFlowPdfParser returns the second value as a list of ``(PIL.Image, rows)``.
    In practice ``rows`` may be:
    - real table rows / table HTML;
    - figure OCR/caption text;
    - equation OCR text.

    The previous adapter treated any list as a table, so figures and equations were
    incorrectly ingested as table chunks and no image chunks were created.  Prefer
    image unless the media explicitly looks like a table.
    """
    raw_text = (text or "").strip()
    lower = raw_text.lower()
    if table_html and "<table" in table_html.lower():
        return "table"
    if re.search(r"(^|\n)\s*(table|表)\s*[0-9ivx一二三四五六七八九十]+\b", raw_text, re.I):
        return "table"
    if isinstance(rows_or_html, (list, tuple)) and rows_or_html:
        structured_rows = [r for r in rows_or_html if isinstance(r, (list, tuple))]
        if structured_rows and len(structured_rows) >= max(2, len(rows_or_html) // 2):
            return "table"
    if re.search(r"(^|\n)\s*(fig\.?|figure|图)\s*[0-9ivx一二三四五六七八九十]+\b", raw_text, re.I):
        return "image"
    if re.search(r"^\s*\(?[0-9]+\)?\s*$", raw_text) or any(sym in raw_text for sym in ["=", "∑", "∫", "√", "≤", "≥"]):
        return "image"
    if "figure" in lower or "fig." in lower:
        return "image"
    # RAGFlow media items are already cropped images; default to image to avoid
    # losing figures.  True tables are caught above by explicit table signals.
    return "image"


def parse_pdf_with_ragflow_deepdoc(path: Path, *, knowledge_base_id: str, document_id: str) -> ParsedDocument:
    _ensure_vendor_path()
    from deepdoc.parser.pdf_parser import RAGFlowPdfParser  # type: ignore

    parser = RAGFlowPdfParser()
    sections, media_items = parser(str(path), need_image=True, zoomin=3, return_html=True)
    content = _strip_position_tags(sections or "")
    blocks: list[ParsedBlock] = []

    for idx, part in enumerate([p for p in re.split(r"\n\s*\n", sections or "") if _strip_position_tags(p)]):
        page_no, bbox = _positions_from_text(part)
        text = _strip_position_tags(part)
        if not text:
            continue
        blocks.append(
            ParsedBlock(
                block_type="text",
                text=text,
                order_index=len(blocks),
                page_no=page_no,
                bbox=bbox,
                metadata={"parser": "ragflow_deepdoc", "source": "section"},
            )
        )

    for item in media_items or []:
        # RAGFlowPdfParser.__call__ normally returns media items as (image, rows)
        # because it calls _extract_table_figure(..., need_position=False).
        # Some patched versions may return ((image, rows), positions). Support both
        # shapes so figure/table crops are not silently dropped.
        positions = []
        try:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[0], (list, tuple))
                and len(item[0]) == 2
            ):
                (img, rows), positions = item
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                img, rows = item
            else:
                logger.debug("unexpected RAGFlow pdf media item shape: %r", type(item))
                continue
        except Exception as exc:
            logger.info("skip malformed RAGFlow pdf media item: %s", exc)
            continue
        text, table_html = _table_text(rows)
        media_kind = _classify_ragflow_pdf_media(rows, text, table_html)
        is_table = media_kind == "table"
        first_pos = None
        page_no = None
        bbox = None
        if positions:
            first_pos = positions[0]
            try:
                page_no = int(first_pos[0]) + 1 if int(first_pos[0]) == 0 else int(first_pos[0])
                bbox = (float(first_pos[1]), float(first_pos[3]), float(first_pos[2]), float(first_pos[4]))
            except Exception:
                pass
        image_info = {}
        img_bytes = _pil_to_bytes(img)
        if img_bytes:
            image_info = save_media_bytes(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                filename_hint=f"pdf_{'table' if is_table else 'figure'}_{len(blocks)}.png",
                data=img_bytes,
                default_ext=".png",
            )
        meta = {
            "parser": "ragflow_deepdoc",
            "source": "table_figure_extraction",
            "media_kind": media_kind,
            "positions": positions or [],
            "caption_binding": "ragflow_native",
        }
        if image_info:
            meta["image_info"] = {k: v for k, v in image_info.items() if k != "base64"}
        blocks.append(
            ParsedBlock(
                block_type="table" if is_table else "image",
                text=text or ("PDF 表格" if is_table else "PDF 图片"),
                table_html=table_html,
                image_path=image_info.get("relative_path", ""),
                image_url=image_info.get("url", ""),
                image_base64=image_info.get("base64", ""),
                order_index=len(blocks),
                page_no=page_no,
                bbox=bbox,
                metadata=meta,
            )
        )

    if not blocks and content:
        blocks.append(ParsedBlock(block_type="text", text=content, order_index=0, metadata={"parser": "ragflow_deepdoc"}))
    table_crop_count = sum(1 for b in blocks if b.block_type == "table" and b.image_path)
    figure_crop_count = sum(1 for b in blocks if b.block_type == "image" and b.image_path)
    caption_bound_count = sum(1 for b in blocks if b.block_type in {"table", "image"} and b.text)
    return ParsedDocument(
        content=content or "\n\n".join(b.index_text() for b in blocks if b.index_text()),
        blocks=blocks,
        metadata={
            "parser": "ragflow_deepdoc_original_adapter",
            "pdf_engine": "ragflow_deepdoc_original",
            "recognition_alignment": "ragflow_deepdoc_source_adapter",
            "block_count": len(blocks),
            "text_blocks": sum(1 for b in blocks if b.block_type == "text"),
            "table_blocks": sum(1 for b in blocks if b.block_type == "table"),
            "image_blocks": sum(1 for b in blocks if b.block_type == "image"),
            "table_crop_count": table_crop_count,
            "figure_crop_count": figure_crop_count,
            "caption_bound_count": caption_bound_count,
            "pdf_media_item_count": table_crop_count + figure_crop_count,
        },
    )
