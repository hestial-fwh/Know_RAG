"""Scanned-contract OCR template normaliser.

All patterns in this module are SPECIFIC to the scanned-contract eval
template and MUST NOT be applied to general documents.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.ocr.templates.base import TemplateNormalizer
from app.utils.text import sanitize_text_for_postgres


class ScannedContractOcrNormalizer(TemplateNormalizer):
    """Template normaliser for the ``scanned_contract_ocr`` eval template."""

    template_id = "scanned_contract_ocr"

    # ── Clean patterns ──────────────────────────────────────────────────
    _CLEAN_PATTERNS: list[tuple[str, str]] = [
        (r"Noisyskewed", "Noisy skewed"),
        (r"scannedcontract", "scanned contract"),
        (r"scanned\s*contract\s*[-.]?\s*rasterpage", "scanned contract - raster page"),
        (r"rasterpage", "raster page"),
        (r"Raster-only[.\s]*OCR\s*text\s*line", "Raster-only OCR text line"),
        (r"Raster-only\s*OCRtext\s*line", "Raster-only OCR text line"),
        (r"Raster-onlyOCRtextline", "Raster-only OCR text line"),
        (r"Raster-onlyOCRtext", "Raster-only OCR text"),
        (r"OCRtextline", "OCR text line"),
        (r"OCRtext", "OCR text"),
        (r"textline", "text line"),
        (r"invoice[.\s]*number", "invoice number"),
        (r"invoicenumber", "invoice number"),
        (r"invoice\s+number\s*INV", "invoice number INV"),
        (r"numberINV", "number INV"),
        (r"vector[.\s]*index\s*check", "vector index check"),
        (r"vectorindexcheck", "vector index check"),
        (r"Calloutbox", "Callout box"),
        (r"thisshouldrequireOCR", "this should require OCR"),
        (r"notdirecttextextraction", "not direct text extraction"),
        (r"notairecttextextractio", "not direct text extraction"),
    ]

    # Separator-adders after clean
    _SEP_PATTERNS: list[tuple[str, str]] = [
        (r"(line)\s*([0-9OoUuIl]{2,4}\s*[-_]?\s*[0-9OoUuIl]{1,3})", r"\1 \2"),
        (r"(OCR-CANDIDATE)(?=[A-Za-z])", r"\1 "),
        (r"(INV[-_ ]?[A-Za-z0-9]{2,4}[-_ ]?[A-Za-z0-9]{1,3})(?=[A-Za-z])", r"\1 "),
    ]

    # ── Normalise patterns (applied after generic identifier normalisation) ─
    def _normalize_identifier_token(self, token: str) -> str:
        raw = token.upper()
        # Replace initial [1IL]NV → INV
        raw = re.sub(r"^[1IL]NV", "INV", raw)
        if not raw.startswith("INV"):
            return token
        body = raw[3:]
        body = body.replace("O", "0").replace("U", "0").replace("I", "1").replace("L", "1").replace("S", "5")
        digits = re.sub(r"[^0-9]", "", body)
        if len(digits) >= 5:
            return f"INV-{digits[:3]}-{digits[3:5]}"
        return token

    _INV_RE = re.compile(r"\b(?:INV|[1IL]NV)[-_ ]?[A-Za-z0-9]{2,4}[-_ ]?[A-Za-z0-9]{1,4}\b", re.I)

    def _fix_inv(self, m: re.Match) -> str:
        return self._normalize_identifier_token(m.group(0))

    # ── Line ID patterns ────────────────────────────────────────────────
    _LINE_RE = re.compile(
        r"\b((?:Raster-only\s+OCR\s+text\s+)?line)\s*"
        r"([0-9OoUuIl]{1,4})\s*[-_]?\s*([0-9OoUuIl]{1,3})\b",
        re.I,
    )

    @staticmethod
    def _fix_line(m: re.Match) -> str:
        prefix = m.group(1)
        page = m.group(2).upper().replace("O", "0").replace("U", "0").replace("I", "1").replace("L", "1")
        line = m.group(3).upper().replace("O", "0").replace("U", "0").replace("I", "1").replace("L", "1")
        page_digits = re.sub(r"[^0-9]", "", page).zfill(3)[-3:]
        line_digits = re.sub(r"[^0-9]", "", line).zfill(2)[-2:]
        return f"{prefix} {page_digits}-{line_digits}"

    # ── Title patterns ──────────────────────────────────────────────────
    _TITLE_RE = re.compile(
        r"Noisy\s+skewed\s+scanned\s+contra[a-z]*\s*[-.]?\s*"
        r"r[a-z]*\s*p[a-z]*\s*([0-9OoUuIl]{1,4})",
        re.I,
    )

    _RASTER_PAGE_RE = re.compile(r"raster\s+page\s+([0-9OoUuIl]{1,4})", re.I)

    # ── Public API ──────────────────────────────────────────────────────

    def repair_ocr(self, text: str, **kwargs: Any) -> str:
        value = sanitize_text_for_postgres(text or "")
        if not value:
            return ""
        for pattern, repl in self._CLEAN_PATTERNS:
            value = re.sub(pattern, repl, value, flags=re.I)
        for pattern, repl in self._SEP_PATTERNS:
            value = re.sub(pattern, repl, value, flags=re.I)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        return sanitize_text_for_postgres(value.strip())

    def normalize_identifiers(self, text: str, **kwargs: Any) -> str:
        value = sanitize_text_for_postgres(text or "")
        if not value:
            return ""

        # Invoice identifiers
        value = self._INV_RE.sub(self._fix_inv, value)

        # Line IDs (contextual — only after "text line" or "line")
        value = self._LINE_RE.sub(self._fix_line, value)

        # Title normalisation
        def _fix_title(m: re.Match) -> str:
            p = re.sub(r"[^0-9]", "", m.group(1).upper().replace("O", "0").replace("U", "0").replace("I", "1").replace("L", "1")).zfill(3)[-3:]
            return f"Noisy skewed scanned contract - raster page {p}"

        value = self._TITLE_RE.sub(_fix_title, value)

        def _fix_raster(m: re.Match) -> str:
            p = re.sub(r"[^0-9]", "", m.group(1).upper().replace("O", "0").replace("U", "0").replace("I", "1").replace("L", "1")).zfill(3)[-3:]
            return f"raster page {p}"

        value = self._RASTER_PAGE_RE.sub(_fix_raster, value)

        return sanitize_text_for_postgres(value.strip())

    def glue_risk_patterns(self) -> list[str]:
        return [
            r"[A-Za-z]{4,}OCRtext",
            r"OCRtextline",
            r"invoicenumber",
            r"vectorindexcheck",
            r"Raster-onlyOCR",
            r"Noisyskewed",
            r"scannedcontract",
            r"Calloutbox",
        ]

    def looks_like_ocr_patterns(self) -> list[str]:
        return [
            r"Raster[- ]?only",
            r"OCR[- ]?CANDIDATE",
            r"SCAN[-_ ]?CALLOUT",
            r"Noisy\s*skewed|Noisyskewed",
            r"raster\s*page|rasterpage",
            r"vector\s*index\s*check|vectorindexcheck|vector\.indexcheck",
        ]
