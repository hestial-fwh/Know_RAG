"""Parser-independent image caption quality and ambiguity rules."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
import unicodedata


_FIGURE_LABEL_PATTERN = r"(?:图|fig(?:s|ure|ures)?\.?)"
_CHINESE_FIGURE_NUMBER_PATTERN = r"[一二三四五六七八九十百]+"
_FIGURE_TOKEN_PATTERN = (
    rf"(?:[Ss]\s*[-.]?\s*)?(?:[0-9]+|{_CHINESE_FIGURE_NUMBER_PATTERN})"
    r"(?:\s*[-.]\s*[0-9]+)?"
    r"(?:\s*[（(]\s*[A-Za-z0-9]\s*[）)]|[A-Za-z])?"
)
_FIGURE_GROUP_RE = re.compile(
    rf"{_FIGURE_LABEL_PATTERN}\s*(?P<body>{_FIGURE_TOKEN_PATTERN}"
    rf"(?:\s*(?:、|,|，|和|及|与|&|and)\s*(?:{_FIGURE_LABEL_PATTERN}\s*)?"
    rf"{_FIGURE_TOKEN_PATTERN})*)",
    re.IGNORECASE,
)
_FIGURE_TOKEN_RE = re.compile(_FIGURE_TOKEN_PATTERN, re.IGNORECASE)
_IDENTIFIER_RE = re.compile(
    r"^[A-Za-z0-9]+(?:[\s,._:/-][A-Za-z0-9]+)*(?:\s*[x×])?$",
    re.IGNORECASE,
)
_MIN_HORIZONTAL_OVERLAP = 0.35
_MAX_VERTICAL_GAP_RATIO = 1.5
_MIN_SPATIAL_SCORE = 0.62
SPATIAL_AMBIGUITY_MARGIN = 0.08


@dataclass(frozen=True)
class SpatialRelationCandidate:
    text: str
    kind: str
    page_no: int
    bbox: tuple[float, float, float, float] | None
    source_order: int
    raw_bbox: tuple[float, float, float, float] | None = None
    ownership_ambiguous: bool = False


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def relation_text_key(value: str) -> str:
    return _normalize_text(value).casefold()


def _chinese_number_to_int(value: str) -> int | None:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
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


def normalize_figure_token(token: str) -> str:
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", token)).lower()
    subfigure = ""
    match = re.search(r"\(([a-z0-9])\)$", value)
    if match:
        subfigure = f":{match.group(1)}"
        value = value[: match.start()]
    else:
        trailing = re.search(r"(?<=\d)([a-z])$", value)
        if trailing:
            subfigure = f":{trailing.group(1)}"
            value = value[: trailing.start()]
    supplementary = value.startswith("s")
    if supplementary:
        value = value[1:].lstrip("-.")
    chinese_match = re.match(rf"^({_CHINESE_FIGURE_NUMBER_PATTERN})", value)
    if chinese_match:
        number = _chinese_number_to_int(chinese_match.group(1))
        if number is not None:
            value = str(number) + value[chinese_match.end() :]
    value = value.replace(".", "-")
    prefix = "s" if supplementary else ""
    return f"figure:{prefix}{value}{subfigure}"


def extract_figure_ids(caption: str) -> list[str]:
    figure_ids: list[str] = []
    for group in _FIGURE_GROUP_RE.finditer(_normalize_text(caption)):
        for token in _FIGURE_TOKEN_RE.finditer(group.group("body")):
            figure_id = normalize_figure_token(token.group(0))
            if figure_id not in figure_ids:
                figure_ids.append(figure_id)
    return figure_ids


def _is_pure_numeric_or_component_identifier(value: str) -> bool:
    text = _normalize_text(value)
    if not text or not _IDENTIFIER_RE.fullmatch(text):
        return False
    return any(character.isdigit() for character in text)


def _suppress_duplicate_identifier(relation: "ImageRelation", ocr_text: str) -> bool:
    caption_key = relation_text_key(relation.caption)
    ocr_key = relation_text_key(ocr_text)
    if not (
        caption_key
        and caption_key == ocr_key
        and not relation.figure_ids
        and _is_pure_numeric_or_component_identifier(relation.caption)
    ):
        return False
    relation.caption = ""
    relation.caption_method = "suppressed_duplicate_identifier"
    relation.binding_score = 0.05
    relation.caption_suppressed = True
    return True


def valid_bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(item) for item in value[:4])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in (x0, y0, x1, y1)):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def normalize_bbox(
    bbox: object,
    *,
    page_extent: tuple[float, float, float, float],
    v2_enabled: bool = False,
    text_page_extent: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float] | None:
    box = valid_bbox(bbox)
    extent = valid_bbox(page_extent)
    if box is None or extent is None:
        return None
    ex0, ey0, ex1, ey1 = extent
    width = ex1 - ex0
    height = ey1 - ey0
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = box

    # v2: detect when the extent is likely wrong (bbox corners extend
    # beyond the extent by more than 5 %).  Fall back to text-only
    # extent or return None to signal explicit degradation.
    if v2_enabled:
        out_of_extent = (
            x0 < ex0 - 0.05 * width
            or y0 < ey0 - 0.05 * height
            or x1 > ex1 + 0.05 * width
            or y1 > ey1 + 0.05 * height
        )
        if out_of_extent:
            if text_page_extent is not None:
                text_extent = valid_bbox(text_page_extent)
                if text_extent is not None:
                    tex0, tey0, tex1, tey1 = text_extent
                    tw = tex1 - tex0
                    th = tey1 - tey0
                    if tw > 0 and th > 0:
                        normalized = (
                            max(0.0, min(1.0, (x0 - tex0) / tw)),
                            max(0.0, min(1.0, (y0 - tey0) / th)),
                            max(0.0, min(1.0, (x1 - tex0) / tw)),
                            max(0.0, min(1.0, (y1 - tey0) / th)),
                        )
                        return valid_bbox(normalized)
            # extent is suspect and no fallback available
            return None

        # bbox is wholly within extent — exact normalization (no clamping)
        normalized = (
            (x0 - ex0) / width,
            (y0 - ey0) / height,
            (x1 - ex0) / width,
            (y1 - ey0) / height,
        )
        return valid_bbox(normalized)

    # v1: clamp to [0,1] (existing behaviour)
    normalized = (
        max(0.0, min(1.0, (x0 - ex0) / width)),
        max(0.0, min(1.0, (y0 - ey0) / height)),
        max(0.0, min(1.0, (x1 - ex0) / width)),
        max(0.0, min(1.0, (y1 - ey0) / height)),
    )
    return valid_bbox(normalized)


def score_spatial_candidate(
    image_bbox: tuple[float, float, float, float],
    image_order: int,
    candidate: SpatialRelationCandidate,
) -> dict:
    if candidate.ownership_ambiguous:
        return {"accepted": False, "reason": "shared_between_images"}
    candidate_bbox = valid_bbox(candidate.bbox)
    if candidate_bbox is None:
        return {"accepted": False, "reason": "invalid_bbox"}

    ix0, iy0, ix1, iy1 = image_bbox
    cx0, cy0, cx1, cy1 = candidate_bbox
    overlap = max(0.0, min(ix1, cx1) - max(ix0, cx0))
    overlap_ratio = overlap / max(min(ix1 - ix0, cx1 - cx0), 1e-9)
    if overlap_ratio < _MIN_HORIZONTAL_OVERLAP:
        return {
            "accepted": False,
            "reason": "insufficient_horizontal_overlap",
            "horizontal_overlap": round(overlap_ratio, 4),
        }

    if cy0 >= iy1:
        direction = "below"
        vertical_gap = cy0 - iy1
    elif cy1 <= iy0:
        direction = "above"
        vertical_gap = iy0 - cy1
    else:
        direction = "overlap"
        vertical_gap = 0.0
    reference_height = max(iy1 - iy0, cy1 - cy0, 1e-9)
    gap_ratio = vertical_gap / reference_height
    if gap_ratio > _MAX_VERTICAL_GAP_RATIO:
        return {
            "accepted": False,
            "reason": "vertical_gap_too_large",
            "horizontal_overlap": round(overlap_ratio, 4),
            "vertical_gap_ratio": round(gap_ratio, 4),
            "direction": direction,
        }

    proximity = max(0.0, 1.0 - gap_ratio / _MAX_VERTICAL_GAP_RATIO)
    direction_score = {"below": 0.12, "above": 0.07, "overlap": 0.02}[direction]
    expected_order = (
        (direction == "below" and candidate.source_order > image_order)
        or (direction == "above" and candidate.source_order < image_order)
    )
    score = 0.42 + 0.25 * overlap_ratio + 0.16 * proximity + direction_score
    if expected_order:
        score += 0.05
    score = min(score, 0.94)
    return {
        "accepted": score >= _MIN_SPATIAL_SCORE,
        "reason": "accepted" if score >= _MIN_SPATIAL_SCORE else "score_below_threshold",
        "score": round(score, 4),
        "horizontal_overlap": round(overlap_ratio, 4),
        "vertical_gap_ratio": round(gap_ratio, 4),
        "direction": direction,
        "order_consistent": expected_order,
    }


@dataclass
class ImageRelation:
    caption: str = ""
    original_caption: str = ""
    footnote: str = ""
    caption_method: str = "missing"
    binding_score: float = 0.0
    ambiguous: bool = False
    caption_ambiguous: bool = False
    footnote_ambiguous: bool = False
    ambiguity_reasons: list[str] = field(default_factory=list)
    figure_ids: list[str] = field(default_factory=list)
    caption_suppressed: bool = False
    deterministic_binding_allowed: bool = False
    spatial_status: str = "not_evaluated"
    normalized_bbox: tuple[float, float, float, float] | None = None
    raw_bbox: tuple[float, float, float, float] | None = None
    normalization_page_extent: tuple[float, float, float, float] | None = None
    bbox_coordinate_space: str = ""
    selected_candidate_orders: list[int] = field(default_factory=list)
    spatial_candidates: list[dict] = field(default_factory=list)

    def to_metadata(self) -> dict:
        return {
            "version": 1,
            "caption": self.caption,
            "original_caption": self.original_caption,
            "caption_method": self.caption_method,
            "binding_score": self.binding_score,
            "ambiguous": self.ambiguous,
            "caption_ambiguous": self.caption_ambiguous,
            "footnote_ambiguous": self.footnote_ambiguous,
            "ambiguity_reasons": list(self.ambiguity_reasons),
            "figure_ids": list(self.figure_ids),
            "caption_suppressed": self.caption_suppressed,
            "deterministic_binding_allowed": self.deterministic_binding_allowed,
            "spatial_status": self.spatial_status,
            "normalized_bbox": list(self.normalized_bbox) if self.normalized_bbox else None,
            "raw_bbox": list(self.raw_bbox) if self.raw_bbox else None,
            "normalization_page_extent": (
                list(self.normalization_page_extent)
                if self.normalization_page_extent
                else None
            ),
            "bbox_coordinate_space": self.bbox_coordinate_space,
            "selected_candidate_orders": list(self.selected_candidate_orders),
            "spatial_candidates": list(self.spatial_candidates),
        }


def resolve_image_relation(
    *,
    caption: str,
    ocr_text: str = "",
    footnote: str = "",
    page_no: int | None = None,
    image_bbox: tuple[float, float, float, float] | None = None,
    raw_image_bbox: tuple[float, float, float, float] | None = None,
    normalization_page_extent: tuple[float, float, float, float] | None = None,
    image_order: int = 0,
    spatial_candidates: list[SpatialRelationCandidate] | None = None,
    bbox_coordinate_space: str = "",
    enable_spatial: bool = False,
) -> ImageRelation:
    original_caption = str(caption or "").strip()
    clean_caption = original_caption
    figure_ids = extract_figure_ids(original_caption)
    relation = ImageRelation(
        caption=clean_caption,
        original_caption=original_caption,
        footnote=str(footnote or "").strip(),
        caption_method="parser_caption" if clean_caption else "missing",
        binding_score=0.95 if clean_caption else 0.0,
        figure_ids=figure_ids,
        normalized_bbox=valid_bbox(image_bbox),
        raw_bbox=valid_bbox(raw_image_bbox),
        normalization_page_extent=valid_bbox(normalization_page_extent),
        bbox_coordinate_space=bbox_coordinate_space,
    )

    _suppress_duplicate_identifier(relation, ocr_text)

    candidates = list(spatial_candidates or [])
    if enable_spatial:
        has_parser_relation = bool(relation.caption or relation.footnote)
        if relation.normalized_bbox is None or page_no is None:
            relation.spatial_status = (
                "parser_attached" if has_parser_relation else "missing_image_geometry"
            )
        else:
            missing_kinds = set()
            if not relation.caption:
                missing_kinds.add("caption")
            if not relation.footnote:
                missing_kinds.add("footnote")
            if not missing_kinds:
                relation.spatial_status = "parser_attached"
            else:
                accepted_any = False
                ambiguous_kinds: set[str] = set()
                geometry_missing = False
                for kind in sorted(missing_kinds):
                    scored: list[tuple[float, SpatialRelationCandidate]] = []
                    shared_between_images = False
                    for candidate in candidates:
                        if candidate.kind != kind or candidate.page_no != page_no:
                            continue
                        metrics = score_spatial_candidate(
                            relation.normalized_bbox,
                            image_order,
                            candidate,
                        )
                        audit = {
                            "kind": candidate.kind,
                            "source_order": candidate.source_order,
                            "raw_bbox": list(candidate.raw_bbox) if candidate.raw_bbox else None,
                            "normalized_bbox": list(candidate.bbox) if candidate.bbox else None,
                            **metrics,
                        }
                        relation.spatial_candidates.append(audit)
                        if metrics.get("reason") == "invalid_bbox":
                            geometry_missing = True
                        if metrics.get("reason") == "shared_between_images":
                            shared_between_images = True
                        if metrics.get("accepted"):
                            scored.append((float(metrics["score"]), candidate))
                    scored.sort(key=lambda item: (-item[0], item[1].source_order))
                    if shared_between_images:
                        ambiguous_kinds.add(kind)
                        relation.ambiguity_reasons.append(
                            f"spatial_{kind}_candidate_shared_between_images"
                        )
                        continue
                    if not scored:
                        continue
                    if (
                        len(scored) > 1
                        and scored[0][0] - scored[1][0] < SPATIAL_AMBIGUITY_MARGIN
                    ):
                        ambiguous_kinds.add(kind)
                        relation.ambiguity_reasons.append(f"multiple_spatial_{kind}_candidates")
                        continue
                    score, selected = scored[0]
                    accepted_any = True
                    relation.selected_candidate_orders.append(selected.source_order)
                    if kind == "caption":
                        relation.caption = selected.text.strip()
                        relation.original_caption = relation.caption
                        relation.figure_ids = extract_figure_ids(relation.caption)
                        relation.caption_method = "spatial_bbox"
                        relation.binding_score = score
                        relation.caption_suppressed = False
                    else:
                        relation.footnote = selected.text.strip()
                if ambiguous_kinds:
                    relation.caption_ambiguous = "caption" in ambiguous_kinds
                    relation.footnote_ambiguous = "footnote" in ambiguous_kinds
                    relation.ambiguous = True
                    relation.spatial_status = "ambiguous_candidates"
                elif accepted_any:
                    relation.spatial_status = "spatial_bound"
                elif has_parser_relation:
                    relation.spatial_status = "parser_attached"
                elif geometry_missing:
                    relation.spatial_status = "missing_candidate_geometry"
                else:
                    relation.spatial_status = "no_candidate"
    else:
        relation.spatial_status = "disabled_unreliable_parser"

    if not relation.caption_suppressed and _suppress_duplicate_identifier(relation, ocr_text):
        if relation.spatial_status == "spatial_bound":
            relation.spatial_status = "spatial_candidate_suppressed"

    if len(relation.figure_ids) > 1:
        relation.caption_ambiguous = True
        if "multiple_figure_ids_in_single_caption" not in relation.ambiguity_reasons:
            relation.ambiguity_reasons.append("multiple_figure_ids_in_single_caption")
        relation.caption_method = f"{relation.caption_method}_ambiguous"
        relation.binding_score = min(relation.binding_score, 0.25)

    relation.ambiguous = relation.caption_ambiguous or relation.footnote_ambiguous

    relation.deterministic_binding_allowed = bool(
        len(relation.figure_ids) == 1
        and relation.caption
        and not relation.caption_suppressed
        and not relation.caption_ambiguous
    )
    return relation


__all__ = [
    "ImageRelation",
    "SPATIAL_AMBIGUITY_MARGIN",
    "SpatialRelationCandidate",
    "extract_figure_ids",
    "normalize_bbox",
    "normalize_figure_token",
    "relation_text_key",
    "resolve_image_relation",
    "score_spatial_candidate",
    "valid_bbox",
]
