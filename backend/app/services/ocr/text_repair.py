"""Generic conservative OCR text repair.

This module contains ONLY universal rules that apply to any document type.
No dataset-specific or template-specific patterns.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.text import sanitize_text_for_postgres

# ── Character category helpers ──────────────────────────────────────────
_CJK_RE = re.compile(r"[一-鿿]")
_LETTER_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"[0-9]")

# ── Boundary repair patterns ────────────────────────────────────────────

# Insert space between CJK char and adjacent Latin letter
_CJK_LETTER_BOUNDARY = re.compile(r"([一-鿿])([A-Za-z])")
_LETTER_CJK_BOUNDARY = re.compile(r"([A-Za-z])([一-鿿])")

# Insert space between CJK char and adjacent digit
_CJK_DIGIT_BOUNDARY = re.compile(r"([一-鿿])([0-9])")
_DIGIT_CJK_BOUNDARY = re.compile(r"([0-9])([一-鿿])")

# Collapse runs of whitespace (keep single newline boundaries)
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_LINE_WS = re.compile(r" *\n *")


def repair_text(text: str, **kwargs: Any) -> str:
    """Apply universal conservative OCR repairs.

    - CTL/null-char removal (via sanitize_text_for_postgres)
    - CJK / Latin letter boundary spacing
    - CJK / digit boundary spacing
    - Whitespace normalisation

    This function NEVER applies O/0, I/1, 5/S substitutions.
    It NEVER uses template-specific patterns.
    """
    value = sanitize_text_for_postgres(text or "")
    if not value:
        return ""

    # ── Boundary repairs ────────────────────────────────────────────
    value = _CJK_LETTER_BOUNDARY.sub(r"\1 \2", value)
    value = _LETTER_CJK_BOUNDARY.sub(r"\1 \2", value)
    value = _CJK_DIGIT_BOUNDARY.sub(r"\1 \2", value)
    value = _DIGIT_CJK_BOUNDARY.sub(r"\1 \2", value)

    # ── Whitespace normalisation ────────────────────────────────────
    value = _MULTI_SPACE.sub(" ", value)
    value = _LINE_WS.sub("\n", value)

    return sanitize_text_for_postgres(value.strip())
