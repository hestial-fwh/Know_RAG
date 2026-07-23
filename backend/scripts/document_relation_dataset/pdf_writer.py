from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zlib

from .raster import Raster


def _pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrapped_lines(text: PdfText) -> list[str]:
    """Return deterministic approximate lines that stay inside the declared bbox."""
    x0, y0, x1, y1 = text.bbox
    max_chars = max(1, int((x1 - x0) / max(text.size * 0.55, 1)))
    max_lines = max(1, int((y1 - y0) / max(text.size * 1.25, 1)))
    return [text.text[index : index + max_chars] for index in range(0, len(text.text), max_chars)][
        :max_lines
    ] or [""]


@dataclass(frozen=True)
class PdfImage:
    name: str
    raster: Raster
    bbox: tuple[int, int, int, int]


@dataclass(frozen=True)
class PdfText:
    text: str
    bbox: tuple[int, int, int, int]
    size: int = 12


@dataclass(frozen=True)
class PdfPage:
    width: int
    height: int
    texts: tuple[PdfText, ...]
    images: tuple[PdfImage, ...]


def write_pdf(path: Path, pages: list[PdfPage]) -> None:
    """Write a deterministic PDF using only built-in Type1 fonts and RGB images."""
    objects: list[bytes] = []

    def reserve() -> int:
        objects.append(b"")
        return len(objects)

    def put(number: int, payload: bytes) -> None:
        objects[number - 1] = payload

    catalog_id = reserve()
    pages_id = reserve()
    font_id = reserve()
    put(font_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []

    for page in pages:
        image_ids: list[tuple[PdfImage, int]] = []
        for image in page.images:
            image_id = reserve()
            compressed = zlib.compress(bytes(image.raster.pixels), level=9)
            header = (
                f"<< /Type /XObject /Subtype /Image /Width {image.raster.width} "
                f"/Height {image.raster.height} /ColorSpace /DeviceRGB "
                f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(compressed)} >>\n"
            ).encode()
            put(image_id, header + b"stream\n" + compressed + b"\nendstream")
            image_ids.append((image, image_id))

        commands: list[str] = []
        for text in page.texts:
            x0, y0, _, _ = text.bbox
            for line_no, line in enumerate(_wrapped_lines(text)):
                line_y = y0 + line_no * text.size * 1.25
                pdf_y = page.height - line_y - text.size
                commands.append(
                    f"BT /F1 {text.size} Tf {x0} {pdf_y:g} Td ({_pdf_text(line)}) Tj ET"
                )
        for index, (image, _) in enumerate(image_ids, start=1):
            x0, y0, x1, y1 = image.bbox
            width = x1 - x0
            height = y1 - y0
            pdf_y = page.height - y1
            commands.append(f"q {width} 0 0 {height} {x0} {pdf_y} cm /Im{index} Do Q")
        stream = "\n".join(commands).encode("latin-1", errors="replace")
        content_id = reserve()
        put(content_id, f"<< /Length {len(stream)} >>\n".encode() + b"stream\n" + stream + b"\nendstream")

        resources = f"/Font << /F1 {font_id} 0 R >>"
        if image_ids:
            xobjects = " ".join(
                f"/Im{index} {image_id} 0 R"
                for index, (_, image_id) in enumerate(image_ids, start=1)
            )
            resources += f" /XObject << {xobjects} >>"
        page_id = reserve()
        put(
            page_id,
            (
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page.width} {page.height}] "
                f"/Resources << {resources} >> /Contents {content_id} 0 R >>"
            ).encode(),
        )
        page_ids.append(page_id)

    put(
        pages_id,
        f"<< /Type /Pages /Count {len(page_ids)} /Kids [{' '.join(f'{item} 0 R' for item in page_ids)}] >>".encode(),
    )
    put(catalog_id, f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    path.parent.mkdir(parents=True, exist_ok=True)
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(payload)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(output)
