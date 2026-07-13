"""Additional lightweight document parsers for broad upload support.

These parsers intentionally avoid pulling in a large converter stack.  They
follow the project rule: preserve the original file, extract the best available
text/tables/images, and fall back to an attachment chunk instead of rejecting an
otherwise valid upload.
"""

from __future__ import annotations

import csv
import html
import json
import re
import subprocess
import zipfile
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image

from app.services.document_parser.media_storage import save_media_bytes
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.utils.text import sanitize_text_for_postgres

IMAGE_SUFFIXES = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "svg", "heic", "heif"}
TABLE_SUFFIXES = {"csv", "tsv", "xlsx", "xls"}
PRESENTATION_SUFFIXES = {"pptx", "ppt"}
HTML_SUFFIXES = {"html", "htm"}
STRUCTURED_TEXT_SUFFIXES = {"json", "jsonl", "xml", "yaml", "yml"}
TEXT_SUFFIXES = {"txt", "md", "markdown", "log", "rst", "tex"}
EPUB_SUFFIXES = {"epub"}
ARCHIVE_SUFFIXES = {"zip"}
LEGACY_DOC_SUFFIXES = {"doc", "rtf", "odt"}


def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|section|article|h[1-6]|tr|li)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    return sanitize_text_for_postgres(re.sub(r"[ \t]+", " ", text).strip())


def _table_html(rows: list[list[Any]]) -> tuple[str, str]:
    if not rows:
        return "", ""
    html_rows = ["<table>"]
    text_rows: list[str] = []
    for row in rows:
        cells = [sanitize_text_for_postgres(str(c if c is not None else "")).strip() for c in row]
        html_rows.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
        text_rows.append(" | ".join(cells))
    html_rows.append("</table>")
    return "\n".join(html_rows), "\n".join(text_rows)


def parse_image_document(path: Path, *, knowledge_base_id: str, document_id: str) -> ParsedDocument:
    data = path.read_bytes()
    suffix = path.suffix.lower() or ".png"
    media = save_media_bytes(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        filename_hint=path.name,
        data=data,
        default_ext=suffix if suffix else ".png",
    )
    width = height = None
    fmt = suffix.lstrip(".").upper()
    if suffix.lower() != ".svg":
        try:
            with Image.open(BytesIO(data)) as img:
                width, height = img.size
                fmt = img.format or fmt
        except Exception:
            pass
    text = f"图片文件：{path.name}"
    block = ParsedBlock(
        block_type="image",
        text=text,
        image_path=media.get("path", ""),
        image_url=media.get("relative_path", ""),
        image_base64=media.get("base64", ""),
        order_index=0,
        metadata={
            "parser": "uploaded_image",
            "source": "uploaded_image",
            "ocr_candidate": True,
            "image_width": width,
            "image_height": height,
            "image_format": fmt,
            "file_name": path.name,
        },
    )
    return ParsedDocument(content=text, blocks=[block], metadata={"parser": "image", "image_block_count": 1})


def parse_csv_document(path: Path, *, delimiter: str | None = None) -> ParsedDocument:
    raw = _decode_bytes(path.read_bytes())
    if delimiter is None:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        try:
            dialect = csv.Sniffer().sniff(raw[:4096])
            delimiter = dialect.delimiter
        except Exception:
            pass
    rows: list[list[str]] = []
    for idx, row in enumerate(csv.reader(StringIO(raw), delimiter=delimiter)):
        rows.append(row)
        if idx >= 5000:
            break
    table_html, text = _table_html(rows)
    block = ParsedBlock(block_type="table", text=text, table_html=table_html, order_index=0, metadata={"parser": "csv", "row_count": len(rows)})
    return ParsedDocument(content=text, blocks=[block] if text else [], metadata={"parser": "csv", "table_block_count": 1 if text else 0})


def parse_excel_document(path: Path) -> ParsedDocument:
    # Prefer pandas if available, but keep a fallback for xlsx zip internals.
    try:
        import pandas as pd  # type: ignore

        sheets = pd.read_excel(path, sheet_name=None, dtype=str)
        blocks: list[ParsedBlock] = []
        content_parts: list[str] = []
        order = 0
        for name, df in sheets.items():
            df = df.fillna("")
            rows = [list(map(str, df.columns.tolist()))] + df.astype(str).values.tolist()
            table_html, text = _table_html(rows)
            header = f"Sheet: {name}"
            block_text = f"{header}\n{text}" if text else header
            blocks.append(
                ParsedBlock(
                    block_type="table",
                    text=block_text,
                    table_html=table_html,
                    order_index=order,
                    context_header=header,
                    metadata={"parser": "excel", "sheet_name": str(name), "row_count": len(rows)},
                )
            )
            content_parts.append(block_text)
            order += 1
        return ParsedDocument(content="\n\n".join(content_parts), blocks=blocks, metadata={"parser": "excel", "table_block_count": len(blocks)})
    except Exception:
        if path.suffix.lower() == ".xlsx":
            return _parse_xlsx_zip_fallback(path)
        return parse_binary_fallback(path, parser="excel_fallback", reason="Excel parser dependency unavailable")


def _parse_xlsx_zip_fallback(path: Path) -> ParsedDocument:
    texts: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root.iter():
                    if si.tag.endswith("}t") and si.text:
                        shared.append(si.text)
            for name in sorted(n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")):
                root = ET.fromstring(zf.read(name))
                rows: list[str] = []
                for c in root.iter():
                    if not c.tag.endswith("}c"):
                        continue
                    cell_type = c.attrib.get("t")
                    val = ""
                    for child in c:
                        if child.tag.endswith("}v") and child.text:
                            val = child.text
                            break
                    if cell_type == "s" and val.isdigit() and int(val) < len(shared):
                        val = shared[int(val)]
                    if val:
                        rows.append(val)
                if rows:
                    texts.append(f"{Path(name).stem}: " + " | ".join(rows))
    except Exception:
        pass
    text = sanitize_text_for_postgres("\n\n".join(texts))
    return ParsedDocument.from_text(text, metadata={"parser": "xlsx_zip_fallback"}) if text else parse_binary_fallback(path, parser="xlsx_zip_fallback")


def parse_html_document(path: Path) -> ParsedDocument:
    text = _strip_html(_decode_bytes(path.read_bytes()))
    return ParsedDocument.from_text(text, metadata={"parser": "html"})


def parse_json_document(path: Path) -> ParsedDocument:
    raw = _decode_bytes(path.read_bytes())
    try:
        if path.suffix.lower() == ".jsonl":
            lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
            text = json.dumps(lines, ensure_ascii=False, indent=2)
        else:
            text = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except Exception:
        text = raw
    text = sanitize_text_for_postgres(text)
    return ParsedDocument.from_text(text, metadata={"parser": "json"})


def parse_xml_document(path: Path) -> ParsedDocument:
    raw = _decode_bytes(path.read_bytes())
    try:
        root = ET.fromstring(raw)
        texts = [t.strip() for t in root.itertext() if t and t.strip()]
        text = "\n".join(texts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = sanitize_text_for_postgres(text)
    return ParsedDocument.from_text(text, metadata={"parser": "xml"})


def parse_rtf_document(path: Path) -> ParsedDocument:
    raw = _decode_bytes(path.read_bytes())
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = sanitize_text_for_postgres(re.sub(r"\s+", " ", text))
    return ParsedDocument.from_text(text, metadata={"parser": "rtf"})


def _xml_text_from_zip(zf: zipfile.ZipFile, members: list[str]) -> str:
    parts: list[str] = []
    for name in members:
        try:
            root = ET.fromstring(zf.read(name))
            vals = [t.strip() for t in root.itertext() if t and t.strip()]
            if vals:
                parts.append("\n".join(vals))
        except Exception:
            continue
    return sanitize_text_for_postgres("\n\n".join(parts))


def parse_pptx_document(path: Path, *, knowledge_base_id: str, document_id: str) -> ParsedDocument:
    if path.suffix.lower() == ".ppt":
        return parse_binary_fallback(path, parser="ppt_legacy", reason="Legacy .ppt needs LibreOffice/antiword style converter")
    blocks: list[ParsedBlock] = []
    content_parts: list[str] = []
    order = 0
    try:
        with zipfile.ZipFile(path) as zf:
            slide_names = sorted(n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n))
            for slide_idx, slide in enumerate(slide_names, start=1):
                text = _xml_text_from_zip(zf, [slide])
                if text:
                    header = f"Slide {slide_idx}"
                    blocks.append(ParsedBlock(block_type="text", text=f"{header}\n{text}", order_index=order, context_header=header, metadata={"parser": "pptx", "slide": slide_idx}))
                    content_parts.append(f"{header}\n{text}")
                    order += 1
            for media in sorted(n for n in zf.namelist() if n.startswith("ppt/media/")):
                data = zf.read(media)
                saved = save_media_bytes(knowledge_base_id=knowledge_base_id, document_id=document_id, filename_hint=Path(media).name, data=data)
                blocks.append(ParsedBlock(block_type="image", text=f"PPT 图片：{Path(media).name}", image_path=saved["path"], image_url=saved["relative_path"], image_base64=saved["base64"], order_index=order, metadata={"parser": "pptx", "source": "pptx_media", "ocr_candidate": True}))
                order += 1
    except Exception as exc:
        return parse_binary_fallback(path, parser="pptx", reason=str(exc))
    return ParsedDocument(content="\n\n".join(content_parts), blocks=blocks, metadata={"parser": "pptx", "image_block_count": len([b for b in blocks if b.block_type == "image"])})


def parse_epub_document(path: Path, *, knowledge_base_id: str, document_id: str) -> ParsedDocument:
    blocks: list[ParsedBlock] = []
    parts: list[str] = []
    order = 0
    try:
        with zipfile.ZipFile(path) as zf:
            html_members = sorted(n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
            for name in html_members[:200]:
                text = _strip_html(_decode_bytes(zf.read(name)))
                if text:
                    blocks.append(ParsedBlock(block_type="text", text=text, order_index=order, context_header=name, metadata={"parser": "epub", "member": name}))
                    parts.append(text)
                    order += 1
            for name in sorted(n for n in zf.namelist() if Path(n).suffix.lower().lstrip(".") in IMAGE_SUFFIXES):
                data = zf.read(name)
                saved = save_media_bytes(knowledge_base_id=knowledge_base_id, document_id=document_id, filename_hint=Path(name).name, data=data)
                blocks.append(ParsedBlock(block_type="image", text=f"EPUB 图片：{Path(name).name}", image_path=saved["path"], image_url=saved["relative_path"], image_base64=saved["base64"], order_index=order, metadata={"parser": "epub", "source": "epub_media", "ocr_candidate": True}))
                order += 1
    except Exception as exc:
        return parse_binary_fallback(path, parser="epub", reason=str(exc))
    return ParsedDocument(content="\n\n".join(parts), blocks=blocks, metadata={"parser": "epub", "image_block_count": len([b for b in blocks if b.block_type == "image"])})


def parse_legacy_doc_document(path: Path) -> ParsedDocument:
    if path.suffix.lower() == ".rtf":
        return parse_rtf_document(path)
    for cmd in (["antiword", str(path)], ["catdoc", str(path)]):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout.strip():
                return ParsedDocument.from_text(sanitize_text_for_postgres(result.stdout), metadata={"parser": Path(cmd[0]).name})
        except Exception:
            continue
    return parse_binary_fallback(path, parser="legacy_doc_fallback", reason="未检测到 .doc 转换器，已作为附件入库")


def parse_archive_document(path: Path) -> ParsedDocument:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        preview = "\n".join(names[:500])
        text = f"压缩包文件：{path.name}\n文件列表：\n{preview}"
        return ParsedDocument.from_text(sanitize_text_for_postgres(text), metadata={"parser": "archive", "member_count": len(names)})
    except Exception:
        return parse_binary_fallback(path, parser="archive_fallback")


def parse_binary_fallback(path: Path, *, parser: str = "binary_fallback", reason: str = "当前格式仅保存原文件，未抽取结构化内容") -> ParsedDocument:
    text = sanitize_text_for_postgres(f"附件文件：{path.name}\n文件类型：{path.suffix.lower().lstrip('.') or 'unknown'}\n说明：{reason}")
    fallback_meta = {
        "parser": parser,
        "fallback_used": True,
        "fallback_kind": "attachment_placeholder",
        "fallback_reason": reason,
        "content_available": False,
        "indexable": False,
    }
    block = ParsedBlock(
        block_type="text",
        text=text,
        order_index=0,
        metadata={
            **fallback_meta,
            "file_name": path.name,
            "skip_embedding_recommended": True,
            "skip_reason": "attachment_placeholder",
        },
    )
    return ParsedDocument(content=text, blocks=[block], metadata=fallback_meta)
