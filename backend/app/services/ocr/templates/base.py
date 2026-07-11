"""Abstract base for template-specific OCR normalisers."""

from __future__ import annotations

from typing import Any


class TemplateNormalizer:
    """Template normaliser protocol.

    Subclasses override one or more methods to supply dataset-specific
    OCR repairs, identifier normalisation, and glue-risk patterns that
    should NOT be applied to general documents.
    """

    template_id: str = ""

    def repair_ocr(self, text: str, **kwargs: Any) -> str:
        """Template-specific text repair (called AFTER generic repair)."""
        return text

    def normalize_identifiers(self, text: str, **kwargs: Any) -> str:
        """Template-specific identifier normalisation."""
        return text

    def glue_risk_patterns(self) -> list[str]:
        """Template-specific glue-word patterns for diagnostics."""
        return []

    def looks_like_ocr_patterns(self) -> list[str]:
        """Regex patterns that, when matched, suggest the text is OCR."""
        return []
