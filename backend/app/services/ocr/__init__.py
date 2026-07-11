"""OCR text processing module.

Usage::

    from app.services.ocr import repair_text, normalize_identifiers, layered_ocr_metadata

    # 1. Generic repair (safe for any document)
    cleaned = repair_text(raw_text)

    # 2. Generic identifier normalisation (only touches ID-like tokens)
    normalized = normalize_identifiers(cleaned)

    # 3. Template-specific (only when eval template is known)
    from app.services.ocr.templates import get_template
    tmpl = get_template("scanned_contract_ocr")
    if tmpl:
        normalized = tmpl.normalize_identifiers(normalized)
"""

from __future__ import annotations

from typing import Any

from app.services.ocr.identifier import normalize_identifiers
from app.services.ocr.templates import get_template
from app.services.ocr.text_repair import repair_text


def layered_ocr_metadata(
    raw_text: str,
    *,
    enabled: bool = False,
    template_id: str | None = None,
) -> dict[str, str]:
    """Build OCR layer metadata dict.

    Layers
        ocr_raw_text       — unmodified input
        ocr_cleaned_text   — generic conservative repair
        ocr_normalized_text — generic identifier normalisation + optional template
        ocr_text           — alias for cleaned
        ocr_layered        — True
    """
    raw = raw_text.strip()
    if not raw or not enabled:
        return {}

    cleaned = repair_text(raw)
    normalized = normalize_identifiers(cleaned or raw)

    # Template-specific normalisation (repair + identifier)
    tmpl = get_template(template_id)
    if tmpl is not None:
        normalized = tmpl.repair_ocr(normalized or cleaned or raw)
        normalized = tmpl.normalize_identifiers(normalized)

    return {
        "ocr_raw_text": raw,
        "ocr_cleaned_text": cleaned,
        "ocr_normalized_text": normalized,
        "ocr_text": cleaned or raw,
        "ocr_layered": True,
    }
