"""Template normaliser registry."""

from __future__ import annotations

from typing import Any

from app.services.ocr.templates.base import TemplateNormalizer
from app.services.ocr.templates.scanned_contract_ocr import ScannedContractOcrNormalizer

_TEMPLATES: dict[str, type[TemplateNormalizer]] = {
    "scanned_contract_ocr": ScannedContractOcrNormalizer,
}


def get_template(template_id: str | None, **kwargs: Any) -> TemplateNormalizer | None:
    """Return a template normaliser instance, or *None*."""
    if not template_id:
        return None
    cls = _TEMPLATES.get(template_id)
    if cls is None:
        return None
    return cls(**kwargs)
