"""Figure Sequence Resolver — recovers missing figure IDs via constrained inference.

Pipelines:
  CanonicalAsset
  → Explicit Figure ID Extraction (caption/OCR/VLM)
  → Figure Sequence Resolver (THIS MODULE)
  → FigureLinker
  → Parent Assignment
  → Relation Graph

Design principles:
  - Structured :class:`FigureLabel` is the canonical representation.
  - Different numbering families (arabic, roman, alpha, sectioned, supplementary)
    are NEVER mixed during inference.
  - Dual-anchor gap filling is the highest-confidence rule.
  - Inferred IDs are tagged with ``source="sequence_inference"`` and never
    override explicit IDs.
  - Shadow Mode runs inference + stats without modifying production bindings.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings

# ── Roman numeral helpers ────────────────────────────────────────────────────

_ROMAN_PATTERN = re.compile(
    r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)

_ROMAN_TO_INT: dict[str, int] = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
    "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20,
    "xxi": 21, "xxii": 22, "xxiii": 23, "xxiv": 24, "xxv": 25,
    "l": 50, "c": 100, "d": 500, "m": 1000,
}



def _int_to_roman(n: int) -> str:
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    sym = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    roman = ""
    for i in range(len(val)):
        while n >= val[i]:
            roman += sym[i]
            n -= val[i]
    return roman


def _is_roman_numeral(value: str) -> bool:
    return bool(_ROMAN_PATTERN.match(value.strip()))


def _roman_to_int(value: str) -> int | None:
    v = value.strip().lower()
    if v in _ROMAN_TO_INT:
        return _ROMAN_TO_INT[v]
    # Try algorithmic conversion
    if not _ROMAN_PATTERN.match(v):
        return None
    roman_map = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    prev = 0
    for ch in reversed(v):
        cur = roman_map.get(ch, 0)
        if cur >= prev:
            total += cur
        else:
            total -= cur
        prev = cur
    return total


# ── Chinese number helpers ───────────────────────────────────────────────────

_CHINESE_DIGITS: dict[str, int] = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}
_CHINESE_TENS: dict[str, int] = {"十": 10, "百": 100}


def _chinese_number_to_int(value: str) -> int | None:
    digits = _CHINESE_DIGITS
    if value in digits:
        return digits[value]
    if "百" in value:
        left, right = value.split("百", 1)
        hundreds = digits.get(left, 1) * 100
        if not right:
            return hundreds
        tail = _chinese_number_to_int(right)
        return hundreds + tail if tail is not None else None
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) * 10
        return tens + (digits.get(right, 0) if right else 0)
    return None


def _int_to_chinese(n: int) -> str | None:
    if n < 1 or n > 99:
        return None
    digits_cn = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if n < 10:
        return digits_cn[n]
    if n < 20:
        return "十" + (digits_cn[n % 10] if n % 10 else "")
    tens = n // 10
    ones = n % 10
    return digits_cn[tens] + "十" + (digits_cn[ones] if ones else "")


# ── FigureLabel ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FigureLabel:
    """Structured, comparable figure identifier.

    Normalized from the string ``figure:...`` format produced by
    :func:`~app.services.document_parser.image_relation_resolver.normalize_figure_token`.
    """

    family: str  # "arabic", "roman", "alpha", "sectioned", "supplementary"
    primary_value: int  # Numeric value for ordering (arabic int, roman→int, alpha→1-26)
    section_prefix: int | None = None  # Present for "figure:2-1" style IDs
    subfigure: str = ""  # e.g. "a", "b", "c"
    supplementary: bool = False
    raw_token: str = ""  # Original normalized string e.g. "figure:3:a"
    original_text: str = ""  # Human-readable, e.g. "Fig. 3a"
    roman_original: str = ""  # Original roman numeral string, e.g. "IV"

    # Source tracking
    source: str = "explicit"  # "explicit" | "sequence_inference"
    confidence: float = 1.0
    left_anchor: str = ""  # asset_id of left anchor
    right_anchor: str = ""  # asset_id of right anchor
    evidence: list[str] = field(default_factory=list)  # Supporting evidence strings

    def is_comparable(self, other: FigureLabel) -> bool:
        """Two labels can be compared in a sequence if they share the same family."""
        if self.family != other.family:
            return False
        if self.supplementary != other.supplementary:
            return False
        if self.section_prefix != other.section_prefix:
            return False
        # Subfigures: must share the same parent figure
        if bool(self.subfigure) != bool(other.subfigure):
            return False
        return True

    def distance(self, other: FigureLabel) -> int | None:
        """Return the integer gap between two comparable labels (other - self)."""
        if not self.is_comparable(other):
            return None
        # Subfigure ordering: when primary values are the same, compare subfigure letters
        if self.subfigure and other.subfigure and self.primary_value == other.primary_value:
            return ord(other.subfigure) - ord(self.subfigure)
        return other.primary_value - self.primary_value

    def to_metadata(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "primary_value": self.primary_value,
            "section_prefix": self.section_prefix,
            "subfigure": self.subfigure,
            "supplementary": self.supplementary,
            "raw_token": self.raw_token,
            "source": self.source,
            "confidence": self.confidence,
            "left_anchor": self.left_anchor,
            "right_anchor": self.right_anchor,
            "evidence": list(self.evidence),
        }

    def to_figure_id(self) -> str:
        """Convert back to normalized figure ID string."""
        prefix = "s" if self.supplementary else ""
        if self.family == "roman":
            num = _int_to_roman(self.primary_value).lower()
        elif self.family == "alpha":
            num = chr(ord("a") + self.primary_value - 1)
        elif self.section_prefix is not None:
            return f"figure:{self.section_prefix}-{self.primary_value}{':' + self.subfigure if self.subfigure else ''}"
        else:
            num = str(self.primary_value)
        sub = f":{self.subfigure}" if self.subfigure else ""
        return f"figure:{prefix}{num}{sub}"

    @property
    def family_key(self) -> str:
        """Stable key for grouping labels into the same sequence family."""
        parts = [self.family]
        if self.supplementary:
            parts.append("s")
        if self.section_prefix is not None:
            parts.append(f"sec{self.section_prefix}")
        if self.subfigure:
            parts.append("sub")
        return ":".join(parts)


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_figure_label(raw: str, *, original_text: str = "") -> FigureLabel | None:
    """Parse a ``figure:...`` normalized ID into a structured :class:`FigureLabel`.

    Returns ``None`` for unparseable or composite IDs.
    """
    if not raw or not raw.startswith("figure:"):
        return None

    body = raw[len("figure:"):]
    if not body:
        return None

    supplementary = False
    subfigure = ""
    section_prefix: int | None = None
    roman_original = ""

    # Check supplementary prefix
    if body.startswith("s") and len(body) > 1 and body[1:2].isdigit():
        supplementary = True
        body = body[1:].lstrip("-.")

    # Extract subfigure suffix
    if ":" in body:
        body_part, subfigure = body.rsplit(":", 1)
        body = body_part

    # Check for sectioned format (e.g. "2-1")
    section_match = re.match(r"^(\d+)-(\d+)$", body)
    if section_match:
        section_prefix = int(section_match.group(1))
        primary = int(section_match.group(2))
        return FigureLabel(
            family="sectioned",
            primary_value=primary,
            section_prefix=section_prefix,
            subfigure=subfigure,
            supplementary=supplementary,
            raw_token=raw,
            original_text=original_text or raw,
        )

    # Check roman numeral.
    # Multi-character: unambiguous roman (ii, iii, iv, viii, etc.)
    # Single-letter: only treat as roman if it's a roman numeral character
    # (I, V, X, L, C, D, M); other single letters (A, B, C, D, ...) are alpha.
    # Only I, V, X are common single-char roman numeral figure labels.
    # L, C, D, M are almost always alpha (Figure D = Appendix D, etc.)
    _ROMAN_SINGLE_CHARS = set("ivx")
    is_roman = _is_roman_numeral(body)
    is_single_roman_char = len(body) == 1 and body in _ROMAN_SINGLE_CHARS
    if is_roman and (len(body) >= 2 or is_single_roman_char):
        primary = _roman_to_int(body)
        if primary is not None:
            return FigureLabel(
                family="roman",
                primary_value=primary,
                subfigure=subfigure,
                supplementary=supplementary,
                raw_token=raw,
                original_text=original_text or raw,
                roman_original=body.upper(),
            )

    # Check pure alpha (single letter)
    if re.match(r"^[a-z]$", body):
        return FigureLabel(
            family="alpha",
            primary_value=ord(body) - ord("a") + 1,
            subfigure=subfigure,
            supplementary=supplementary,
            raw_token=raw,
            original_text=original_text or raw,
        )

    # Check chinese number
    chinese_match = re.match(r"^([一二三四五六七八九十百]+)$", body)
    if chinese_match:
        primary = _chinese_number_to_int(chinese_match.group(1))
        if primary is not None:
            return FigureLabel(
                family="arabic",
                primary_value=primary,
                subfigure=subfigure,
                supplementary=supplementary,
                raw_token=raw,
                original_text=original_text or raw,
            )

    # Default: arabic number
    try:
        primary = int(body)
    except (ValueError, TypeError):
        return None

    return FigureLabel(
        family="arabic",
        primary_value=primary,
        subfigure=subfigure,
        supplementary=supplementary,
        raw_token=raw,
        original_text=original_text or raw,
    )


# ── Sequence Resolution Data Types ────────────────────────────────────────────

@dataclass
class AssetFigureInfo:
    """Figure information for a single CanonicalAsset, used by the resolver."""

    asset_id: str
    explicit_labels: list[FigureLabel] = field(default_factory=list)
    inferred_labels: list[FigureLabel] = field(default_factory=list)
    caption: str = ""
    ocr_text: str = ""
    vlm_description: str = ""
    page_no: int | None = None
    source_order: int = 0
    context_header: str = ""
    composite_figure: bool = False
    deterministic_binding_allowed: bool = False
    # Cross-document metadata
    document_id: str = ""
    document_title: str = ""
    document_file_name: str = ""

    @property
    def has_explicit_id(self) -> bool:
        return len(self.explicit_labels) > 0

    @property
    def primary_label(self) -> FigureLabel | None:
        if self.explicit_labels:
            return self.explicit_labels[0]
        if self.inferred_labels:
            return self.inferred_labels[0]
        return None

    @property
    def family_key(self) -> str | None:
        label = self.primary_label
        return label.family_key if label else None


@dataclass
class SequenceGap:
    """A gap in a figure sequence bounded by two known anchors."""

    family_key: str
    left_anchor: AssetFigureInfo
    right_anchor: AssetFigureInfo
    gap_size: int  # Expected number of figures between anchors
    eligible_assets: list[AssetFigureInfo] = field(default_factory=list)
    resolved: bool = False
    conflict: bool = False
    conflict_reason: str = ""


@dataclass
class FigureSequenceResult:
    """Aggregate result of sequence resolution."""

    resolved_assets: dict[str, list[FigureLabel]] = field(default_factory=dict)
    ambiguous_assets: dict[str, str] = field(default_factory=dict)  # asset_id → reason
    conflict_assets: dict[str, str] = field(default_factory=dict)  # asset_id → reason
    unresolved_assets: dict[str, str] = field(default_factory=dict)  # asset_id → reason
    cross_document_groups: list[list[str]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def total_explicit(self) -> int:
        return self.stats.get("explicit_ids", 0)

    @property
    def total_inferred_high_confidence(self) -> int:
        return self.stats.get("inferred_high_confidence", 0)

    @property
    def total_ambiguous(self) -> int:
        return self.stats.get("ambiguous", 0)

    @property
    def total_conflict(self) -> int:
        return self.stats.get("conflict", 0)

    @property
    def total_unresolved(self) -> int:
        return self.stats.get("unresolved", 0)


# ── Non-Figure Filtering ────────────────────────────────────────────────────

# Patterns / roles that indicate an image is NOT a figure (logo, watermark, etc.)
_NON_FIGURE_CAPTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^\s*logo\s*$",
        r"^\s*watermark\s*$",
        r"^\s*page\s*scan\s*$",
        r"^\s*封面\s*$",
        r"^\s*封底\s*$",
        r"^\s*目录\s*$",
        r"^\s*background\s*$",
        r"^\s*header\s*$",
        r"^\s*footer\s*$",
        r"^\s*页眉\s*$",
        r"^\s*页脚\s*$",
        r"^\s*banner\s*$",
        r"^\s*icon\s*$",
        r"^\s*二维码\s*$",
        r"^\s*qr\s*code\s*$",
        r"^\s*barcode\s*$",
    ]
]

_NON_FIGURE_VLM_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\blogo\b",
        r"\bwatermark\b",
        r"\bheader\b",
        r"\bfooter\b",
        r"\bpage\s*number\b",
        r"\bbarcode\b",
        r"\bqr\s*code\b",
        r"\bicon\b",
        r"\bhorizontal\s*line\b",
        r"\bseparator\b",
        r"\bbackground\b",
    ]
]


def _is_likely_figure_asset(info: AssetFigureInfo) -> bool:
    """Return False for likely non-figure images (logos, watermarks, headers, etc.)."""
    # Empty everything → likely a figure
    if not info.caption and not info.ocr_text and not info.vlm_description:
        return True

    combined = f"{info.caption} {info.ocr_text} {info.vlm_description} {info.context_header}"

    for pattern in _NON_FIGURE_CAPTION_PATTERNS:
        if pattern.search(combined):
            return False

    # Also check VLM-specific patterns
    for pattern in _NON_FIGURE_VLM_PATTERNS:
        if pattern.search(combined):
            return False

    return True


def _is_low_information_page_noise(info: AssetFigureInfo) -> bool:
    """Detect page-number-like images that aren't real figures."""
    combined = (info.caption or "").strip()
    if not combined:
        return False
    # Pure number captions like "20", "21" are page numbers, not figure labels
    if re.match(r"^\d{1,4}$", combined) and len(combined) <= 3:
        return True
    return False


# ── Prose Reference Extraction ──────────────────────────────────────────────

def _extract_prose_figure_refs(
    markdown: str,
) -> dict[str, list[FigureLabel]]:
    """Extract figure references from prose text for cross-referencing.

    Returns a mapping of normalized figure_id to list of FigureLabels found.
    """
    from app.services.document_parser.figure_linker import (
        extract_explicit_figure_references,
    )

    refs = extract_explicit_figure_references(markdown)
    result: dict[str, list[FigureLabel]] = {}
    for ref in refs:
        label = parse_figure_label(ref.figure_id, original_text=ref.raw_text)
        if label is not None:
            result.setdefault(ref.figure_id, []).append(label)
    return result


# ── Sequence Family Grouping ─────────────────────────────────────────────────

def _group_by_family(
    assets: list[AssetFigureInfo],
) -> dict[str, list[AssetFigureInfo]]:
    """Group assets by their figure label family key.

    Only assets with explicit figure IDs contribute to family determination.
    Assets without explicit IDs are eligible for inference in any family
    that has positional proximity.
    """
    # First pass: group explicit-ID assets by family
    families: dict[str, list[AssetFigureInfo]] = {}
    for info in assets:
        if not info.has_explicit_id:
            continue
        fk = info.family_key
        if fk is None:
            continue
        families.setdefault(fk, []).append(info)

    # Sort each family by primary_value
    for fk in families:
        families[fk].sort(key=lambda a: a.primary_label.primary_value if a.primary_label else 0)

    return families


def _find_gaps(
    family_assets: list[AssetFigureInfo],
    all_assets: list[AssetFigureInfo],
) -> list[SequenceGap]:
    """Find gaps between consecutive explicit-ID assets in a family."""
    gaps: list[SequenceGap] = []
    if len(family_assets) < 2:
        return gaps

    family_key = family_assets[0].family_key
    if family_key is None:
        return gaps

    for i in range(len(family_assets) - 1):
        left = family_assets[i]
        right = family_assets[i + 1]

        left_label = left.primary_label
        right_label = right.primary_label
        if left_label is None or right_label is None:
            continue

        gap_size = left_label.distance(right_label)
        if gap_size is None:
            continue

        # If gap_size == 1, they are consecutive → no gap
        if gap_size <= 1:
            continue

        expected_missing = gap_size - 1

        # Find eligible assets between left and right in source order
        eligible: list[AssetFigureInfo] = []
        for asset in all_assets:
            if asset.has_explicit_id:
                continue
            if not _is_likely_figure_asset(asset):
                continue
            if _is_low_information_page_noise(asset):
                continue
            if asset.composite_figure:
                continue
            # Must fall between left and right in source order
            if left.source_order < asset.source_order < right.source_order:
                eligible.append(asset)

        gaps.append(SequenceGap(
            family_key=family_key,
            left_anchor=left,
            right_anchor=right,
            gap_size=expected_missing,
            eligible_assets=eligible,
        ))

    return gaps


# ── Evidence Gathering ───────────────────────────────────────────────────────

def _find_in_ocr_or_caption(info: AssetFigureInfo, search_label: FigureLabel) -> bool:
    """Check if the OCR or caption text contains the expected figure label."""
    combined = f"{info.caption} {info.ocr_text}".lower()
    if not combined.strip():
        return False
    # Look for the figure label in various formats
    primary = search_label.primary_value
    patterns = [
        rf"\bfig\.?\s*{primary}\b",
        rf"\bfigure\s*{primary}\b",
        rf"图\s*{primary}\b",
    ]
    if search_label.subfigure:
        patterns.append(rf"\bfig\.?\s*{primary}\s*{search_label.subfigure}\b")
    if search_label.supplementary:
        patterns.append(rf"\bsupp?(?:lementary)?\s*(?:figure|fig)\.?\s*{primary}\b")
    for p in patterns:
        if re.search(p, combined, re.IGNORECASE):
            return True
    return False


def _find_in_prose_refs(
    prose_refs: dict[str, list[FigureLabel]],
    search_label: FigureLabel,
) -> bool:
    """Check if the prose text references this figure ID."""
    fid = search_label.to_figure_id()
    return fid in prose_refs


# ── Main Resolver ────────────────────────────────────────────────────────────

@dataclass
class FigureSequenceResolver:
    """Resolves missing figure IDs via constrained sequence inference.

    Usage::

        resolver = FigureSequenceResolver()
        result = resolver.resolve(assets, prose_refs=prose_refs, cross_document_docs=[])
    """

    # Configuration
    max_gap_size: int = 10
    min_confidence_threshold: float = 0.9
    allow_cross_section: bool = False
    allow_cross_page: bool = True
    allowed_page_gap: int = 5  # Max page gap for cross-page inference

    def resolve(
        self,
        assets: list[AssetFigureInfo],
        *,
        prose_refs: dict[str, list[FigureLabel]] | None = None,
        cross_document_docs: list[list[AssetFigureInfo]] | None = None,
    ) -> FigureSequenceResult:
        """Run sequence resolution on a list of asset figure infos.

        Args:
            assets: Asset figure infos from one or more documents.
            prose_refs: Figure references extracted from prose text.
            cross_document_docs: Assets grouped by logical document for cross-PDF.

        Returns:
            FigureSequenceResult with resolved, ambiguous, conflict, unresolved.
        """
        prose_refs = prose_refs or {}
        result = FigureSequenceResult()

        # ── Step 1: Filter eligible assets ──
        eligible = [
            a for a in assets
            if _is_likely_figure_asset(a) and not _is_low_information_page_noise(a)
        ]
        result.stats["total_assets"] = len(assets)
        result.stats["eligible_assets"] = len(eligible)
        result.stats["explicit_ids"] = sum(1 for a in eligible if a.has_explicit_id)
        result.stats["unresolved_before"] = sum(
            1 for a in eligible if not a.has_explicit_id and not a.composite_figure
        )

        # ── Step 2: Group by family ──
        families = _group_by_family(eligible)

        # ── Step 3: Dual-anchor gap filling ──
        all_resolved: dict[str, list[FigureLabel]] = {}
        all_ambiguous: dict[str, str] = {}
        all_conflict: dict[str, str] = {}

        for fk, family_assets in families.items():
            gaps = _find_gaps(family_assets, eligible)
            for gap in gaps:
                self._resolve_gap(
                    gap,
                    prose_refs,
                    all_resolved,
                    all_ambiguous,
                    all_conflict,
                )

        # Cross-document resolution
        if cross_document_docs:
            for doc_group in cross_document_docs:
                if len(doc_group) < 2:
                    continue
                cross_result = self._resolve_cross_document(doc_group)
                all_resolved.update(cross_result)

        result.resolved_assets = all_resolved
        result.ambiguous_assets = all_ambiguous
        result.conflict_assets = all_conflict

        # Track unresolved
        for a in eligible:
            if a.has_explicit_id:
                continue
            if a.composite_figure:
                continue
            if a.asset_id in all_resolved:
                continue
            if a.asset_id in all_ambiguous:
                continue
            if a.asset_id in all_conflict:
                continue
            result.unresolved_assets[a.asset_id] = "no_suitable_gap"

        # ── Step 4: Compute stats ──
        result.stats["inferred_high_confidence"] = sum(
            1 for labels in all_resolved.values()
            if any(l.confidence >= self.min_confidence_threshold for l in labels)
        )
        result.stats["inferred_low_confidence"] = sum(
            1 for labels in all_resolved.values()
            if all(l.confidence < self.min_confidence_threshold for l in labels)
        )
        result.stats["ambiguous"] = len(all_ambiguous)
        result.stats["conflict"] = len(all_conflict)
        result.stats["unresolved"] = len(result.unresolved_assets)

        return result

    def _resolve_gap(
        self,
        gap: SequenceGap,
        prose_refs: dict[str, list[FigureLabel]],
        resolved: dict[str, list[FigureLabel]],
        ambiguous: dict[str, str],
        conflict: dict[str, str],
    ) -> None:
        """Attempt to resolve a single gap between two anchor assets."""
        eligible_count = len(gap.eligible_assets)

        if eligible_count == 0:
            return  # No candidates to fill the gap

        # Reject if eligible count doesn't match gap size exactly
        if eligible_count != gap.gap_size:
            for asset in gap.eligible_assets:
                ambiguous[asset.asset_id] = (
                    f"gap_size_mismatch: expected {gap.gap_size}, "
                    f"found {eligible_count} eligible between "
                    f"{gap.left_anchor.asset_id} and {gap.right_anchor.asset_id}"
                )
            return

        # ── Exact match: fill the gap ──
        left_label = gap.left_anchor.primary_label
        if left_label is None:
            return

        gap.conflict = False

        # Determine if this is a subfigure gap (same primary, different subfigures)
        is_subfigure_gap = (
            bool(left_label.subfigure)
            and gap.left_anchor.primary_label is not None
            and gap.right_anchor.primary_label is not None
            and left_label.primary_value == gap.right_anchor.primary_label.primary_value
        )

        for idx, asset in enumerate(gap.eligible_assets):
            if is_subfigure_gap:
                # Subfigure gap: primary stays, subfigure increments
                subfigure_ord = ord(left_label.subfigure) + idx + 1
                subfigure_char = chr(subfigure_ord)
                sec_prefix = f"{left_label.section_prefix}-" if left_label.section_prefix else ""
                s_prefix = "s" if left_label.supplementary else ""
                raw_token = f"figure:{s_prefix}{sec_prefix}{left_label.primary_value}:{subfigure_char}"
                inferred_label = FigureLabel(
                    family=left_label.family,
                    primary_value=left_label.primary_value,
                    section_prefix=left_label.section_prefix,
                    subfigure=subfigure_char,
                    supplementary=left_label.supplementary,
                    raw_token=raw_token,
                    original_text=raw_token,
                )
            else:
                inferred_value = left_label.primary_value + idx + 1
                inferred_label = self._build_inferred_label(
                    left_label, inferred_value, gap, idx
                )

            # Gather evidence
            evidence: list[str] = []
            confidence = 0.90  # Base confidence for exact gap match

            # Prose reference evidence
            if _find_in_prose_refs(prose_refs, inferred_label):
                evidence.append("prose_reference_match")
                confidence += 0.10

            # OCR/caption evidence
            if _find_in_ocr_or_caption(asset, inferred_label):
                evidence.append("ocr_caption_match")
                confidence += 0.08

            # Page continuity
            if _is_page_continuous(
                gap.left_anchor, asset, gap.right_anchor
            ):
                evidence.append("page_continuity")
                confidence += 0.03

            # Section continuity
            if _is_same_section(gap.left_anchor, asset, gap.right_anchor):
                evidence.append("same_section")
                confidence += 0.02

            # Check for conflicting evidence
            conflict_detected = self._check_conflict(
                asset, inferred_label, gap
            )
            if conflict_detected:
                conflict[asset.asset_id] = conflict_detected
                gap.conflict = True
                continue

            inferred_label = FigureLabel(
                family=inferred_label.family,
                primary_value=inferred_label.primary_value,
                section_prefix=inferred_label.section_prefix,
                subfigure=inferred_label.subfigure,
                supplementary=inferred_label.supplementary,
                raw_token=inferred_label.raw_token,
                original_text=inferred_label.original_text,
                source="sequence_inference",
                confidence=min(confidence, 1.0),
                left_anchor=gap.left_anchor.asset_id,
                right_anchor=gap.right_anchor.asset_id,
                evidence=evidence,
            )

            resolved.setdefault(asset.asset_id, []).append(inferred_label)

        gap.resolved = True

    def _build_inferred_label(
        self,
        template: FigureLabel,
        value: int,
        gap: SequenceGap,
        idx: int,
    ) -> FigureLabel:
        """Create an inferred label based on the anchor template."""
        if template.family == "roman":
            roman_str = _int_to_roman(value).lower()
            raw = f"figure:{'s' if template.supplementary else ''}{roman_str}"
            sub = f":{template.subfigure}" if template.subfigure else ""
            raw += sub
            return FigureLabel(
                family="roman",
                primary_value=value,
                supplementary=template.supplementary,
                subfigure=template.subfigure,
                raw_token=raw,
                original_text=raw,
                roman_original=_int_to_roman(value),
                section_prefix=template.section_prefix,
            )

        if template.family == "alpha":
            letter = chr(ord("a") + value - 1)
            raw = f"figure:{letter}"
            return FigureLabel(
                family="alpha",
                primary_value=value,
                raw_token=raw,
                original_text=raw,
            )

        # arabic, sectioned, supplementary all use numeric
        prefix = "s" if template.supplementary else ""
        sec = f"{template.section_prefix}-" if template.section_prefix else ""
        sub = f":{template.subfigure}" if template.subfigure else ""
        raw = f"figure:{prefix}{sec}{value}{sub}"

        return FigureLabel(
            family=template.family,
            primary_value=value,
            section_prefix=template.section_prefix,
            subfigure=template.subfigure,
            supplementary=template.supplementary,
            raw_token=raw,
            original_text=raw,
        )

    def _check_conflict(
        self,
        asset: AssetFigureInfo,
        inferred_label: FigureLabel,
        gap: SequenceGap,
    ) -> str | None:
        """Check if the OCR/caption of the asset contradicts the inferred label.

        Returns a conflict reason string, or None if no conflict.
        """
        combined = f"{asset.caption} {asset.ocr_text}".lower()
        if not combined.strip():
            return None

        # Extract any figure-like labels from OCR/caption
        from app.services.document_parser.image_relation_resolver import (
            extract_figure_ids,
        )

        found_ids = extract_figure_ids(combined)
        for fid in found_ids:
            found_label = parse_figure_label(fid)
            if found_label is None:
                continue
            # If OCR contains a different figure number in the same family
            if found_label.is_comparable(inferred_label):
                if found_label.primary_value != inferred_label.primary_value:
                    return (
                        f"ocr_sequence_conflict: OCR suggests "
                        f"'{fid}' but sequence infers '{inferred_label.to_figure_id()}'"
                    )

        return None

    def _resolve_cross_document(
        self,
        doc_groups: list[list[AssetFigureInfo]],
    ) -> dict[str, list[FigureLabel]]:
        """Attempt cross-document sequence resolution.

        Currently a stub — requires logical document group detection first.
        """
        # Cross-document resolution will be implemented in a follow-up phase
        # after logical document grouping infrastructure is in place.
        return {}


# ── Evidence Helpers ─────────────────────────────────────────────────────────

def _is_page_continuous(
    left: AssetFigureInfo,
    mid: AssetFigureInfo,
    right: AssetFigureInfo,
) -> bool:
    """Check if the three assets are on continuous or nearby pages."""
    pages = []
    for a in [left, mid, right]:
        if a.page_no is not None:
            pages.append(a.page_no)
    if len(pages) < 2:
        return False
    return max(pages) - min(pages) <= 5


def _is_same_section(
    left: AssetFigureInfo,
    mid: AssetFigureInfo,
    right: AssetFigureInfo,
) -> bool:
    """Check if all three assets share the same section context."""
    sections = set()
    for a in [left, mid, right]:
        if a.context_header:
            sections.add(a.context_header)
    return len(sections) == 1 and bool(sections)


# ── Document Group Detection ─────────────────────────────────────────────────

def detect_logical_document_group(
    documents: list[dict[str, Any]],
) -> list[list[str]]:
    """Group documents that appear to be parts of the same logical document.

    Uses:
      - File name patterns (part1/part2, page ranges)
      - Same title
      - DOI / patent number / report number
      - Same header/footer fingerprint
      - Continuous page numbering

    Returns list of lists of document IDs.
    """
    if len(documents) <= 1:
        return []

    groups: list[list[str]] = []
    used: set[int] = set()

    for i, doc_a in enumerate(documents):
        if i in used:
            continue
        group = [doc_a.get("id", "")]
        used.add(i)

        for j, doc_b in enumerate(documents):
            if j in used:
                continue
            if _same_logical_document(doc_a, doc_b):
                group.append(doc_b.get("id", ""))
                used.add(j)

        if len(group) > 1:
            groups.append(group)

    return groups


def _same_logical_document(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Check if two documents appear to be parts of the same logical document."""
    evidence = 0
    total = 0

    # Same title
    title_a = str(a.get("title", "")).strip().lower()
    title_b = str(b.get("title", "")).strip().lower()
    if title_a and title_b:
        total += 1
        if title_a == title_b:
            evidence += 1

    # Part number pattern in file names
    fn_a = str(a.get("file_name", "")).strip().lower()
    fn_b = str(b.get("file_name", "")).strip().lower()
    if fn_a and fn_b:
        total += 1
        base_a, base_b = _strip_part_suffix(fn_a), _strip_part_suffix(fn_b)
        if base_a == base_b:
            evidence += 1

    # Same DOI/patent/report number in metadata
    meta_a = a.get("metadata_json") or {}
    meta_b = b.get("metadata_json") or {}
    for key in ("doi", "patent_number", "report_number", "arxiv_id"):
        val_a = str(meta_a.get(key, "")).strip()
        val_b = str(meta_b.get(key, "")).strip()
        if val_a and val_b:
            total += 1
            if val_a == val_b:
                evidence += 1

    if total == 0:
        return False
    return evidence >= total * 0.5  # At least half the evidence must match


_PART_SUFFIX_RE = re.compile(
    r"[-_\s]*(?:part|pt|section|sec|vol|volume|ch|chapter)[-_\s]*\d+",
    re.IGNORECASE,
)
_PAGE_RANGE_RE = re.compile(
    r"[-_\s]*p(?:ages?)?[-_\s]*\d+[-_]\d+",
    re.IGNORECASE,
)


def _strip_part_suffix(filename: str) -> str:
    """Remove part/page-range suffixes for comparison."""
    name = _PART_SUFFIX_RE.sub("", filename)
    name = _PAGE_RANGE_RE.sub("", name)
    return name.strip()


# ── Detection of Numbering Reset ────────────────────────────────────────────

def detect_numbering_reset(
    doc_a_assets: list[AssetFigureInfo],
    doc_b_assets: list[AssetFigureInfo],
) -> bool:
    """Detect if document B resets figure numbering compared to document A.

    Returns True if numbering appears to reset (doc B starts from low numbers).
    """
    a_explicit = [a for a in doc_a_assets if a.has_explicit_id and a.primary_label]
    b_explicit = [b for b in doc_b_assets if b.has_explicit_id and b.primary_label]

    if not a_explicit or not b_explicit:
        return False

    # Get the last figure ID from doc A
    a_last = max(
        a_explicit,
        key=lambda x: x.primary_label.primary_value if x.primary_label else 0,
    )
    # Get the first figure ID from doc B
    b_first = min(
        b_explicit,
        key=lambda x: x.primary_label.primary_value if x.primary_label else float("inf"),
    )

    a_label = a_last.primary_label
    b_label = b_first.primary_label
    if a_label is None or b_label is None:
        return False

    if not a_label.is_comparable(b_label):
        return True  # Different families → reset

    # If B starts with a number <= 5 and A ends with a much larger number
    if b_label.primary_value <= 5 and a_label.primary_value > b_label.primary_value:
        return True

    return False


# ── Shadow Mode Integration ──────────────────────────────────────────────────

def is_figure_sequence_resolution_enabled() -> bool:
    return getattr(settings, "document_figure_sequence_resolution_enabled", False)


def is_figure_sequence_shadow_only() -> bool:
    return getattr(settings, "document_figure_sequence_shadow_only", True)


# ── Top-Level API ────────────────────────────────────────────────────────────

def build_asset_figure_infos(
    assets: list[Any],  # list[CanonicalAsset]
    *,
    document_id: str = "",
    document_title: str = "",
    document_file_name: str = "",
) -> list[AssetFigureInfo]:
    """Convert CanonicalAsset objects into AssetFigureInfo for the resolver."""
    infos: list[AssetFigureInfo] = []
    for asset in assets:
        explicit_labels: list[FigureLabel] = []
        for fid in (getattr(asset, "figure_ids", None) or []):
            label = parse_figure_label(fid)
            if label is not None:
                explicit_labels.append(label)

        info = AssetFigureInfo(
            asset_id=getattr(asset, "asset_id", ""),
            explicit_labels=explicit_labels,
            caption=getattr(asset, "caption", "") or "",
            ocr_text=getattr(asset, "ocr_text", "") or "",
            vlm_description=getattr(asset, "vlm_description", "") or "",
            page_no=getattr(asset, "page_no", None),
            source_order=getattr(asset, "source_block_order", 0),
            context_header=getattr(asset, "context_header", "") or "",
            composite_figure=getattr(asset, "composite_figure", False),
            deterministic_binding_allowed=getattr(asset, "deterministic_binding_allowed", False),
            document_id=document_id,
            document_title=document_title,
            document_file_name=document_file_name,
        )
        infos.append(info)
    return infos


def resolve_figure_sequences(
    assets: list[Any],
    *,
    markdown: str = "",
    cross_document_asset_groups: list[list[Any]] | None = None,
) -> FigureSequenceResult:
    """Run figure sequence resolution and return structured results.

    Args:
        assets: List of CanonicalAsset objects.
        markdown: Normalized markdown for prose reference extraction.
        cross_document_asset_groups: Assets from related documents for cross-PDF.

    Returns:
        FigureSequenceResult with resolved/ambiguous/conflict/unresolved.
    """
    infos: list[AssetFigureInfo] = []
    if assets and isinstance(assets[0], AssetFigureInfo):
        infos = list(assets)
    else:
        infos = build_asset_figure_infos(assets)
    prose_refs = _extract_prose_figure_refs(markdown) if markdown else {}

    cross_groups: list[list[AssetFigureInfo]] | None = None
    if cross_document_asset_groups:
        cross_groups = []
        for group in cross_document_asset_groups:
            cross_groups.append(build_asset_figure_infos(group))

    resolver = FigureSequenceResolver()
    return resolver.resolve(infos, prose_refs=prose_refs, cross_document_docs=cross_groups)


def apply_sequence_resolution(
    assets: list[Any],
    result: FigureSequenceResult,
    *,
    shadow_only: bool = True,
) -> list[Any]:
    """Apply resolved figure IDs to assets.

    In shadow mode, this does NOT modify assets — it only logs.
    In production mode (shadow_only=False), inferred high-confidence IDs
    are appended to each asset's ``figure_ids`` list.

    Returns the (possibly modified) list of assets.
    """
    import logging
    logger = logging.getLogger("know_rag.figure_sequence")

    for asset in assets:
        asset_id = getattr(asset, "asset_id", "")
        if asset_id in result.resolved_assets:
            labels = result.resolved_assets[asset_id]
            high_conf = [l for l in labels if l.confidence >= 0.85]
            low_conf = [l for l in labels if l.confidence < 0.85]

            for label in high_conf:
                fid = label.to_figure_id()
                if not shadow_only:
                    existing = list(getattr(asset, "figure_ids", []) or [])
                    if fid not in existing:
                        existing.append(fid)
                        object.__setattr__(asset, "figure_ids", existing)
                logger.info(
                    "sequence_inference asset=%s inferred=%s confidence=%.2f source=%s shadow=%s",
                    asset_id, fid, label.confidence, label.source, shadow_only,
                )

            for label in low_conf:
                logger.info(
                    "sequence_inference asset=%s low_confidence=%s (not applied) shadow=%s",
                    asset_id, label.to_figure_id(), shadow_only,
                )

        if asset_id in result.conflict_assets:
            logger.warning(
                "sequence_inference asset=%s conflict=%s",
                asset_id, result.conflict_assets[asset_id],
            )

    return assets


__all__ = [
    "AssetFigureInfo",
    "FigureLabel",
    "FigureSequenceGap",  # re-exported as SequenceGap
    "FigureSequenceResolver",
    "FigureSequenceResult",
    "SequenceGap",
    "apply_sequence_resolution",
    "build_asset_figure_infos",
    "detect_logical_document_group",
    "detect_numbering_reset",
    "is_figure_sequence_resolution_enabled",
    "is_figure_sequence_shadow_only",
    "parse_figure_label",
    "resolve_figure_sequences",
]
