from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import re
from typing import Any

from app.services.document_parser.canonical_document import CanonicalDocument
from app.services.document_parser.document_profiler import DocumentProfile, PageProfile


PROFILE_VERSION = "document-profile-v2"
_SPACE_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_VALID_TEXT_RE = re.compile(r"[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff]")
_PUNCTUATION_RE = re.compile(r"[，。！？；：,.!?;:]")
_CJK_FUNCTION_CHARS = frozenset("的了是在有和与为及其于中对将由等这那者之而以")
_CALLIGRAPHY_TERMS = ("书法", "行书", "草书", "楷书", "隶书", "篆书", "calligraphy")
_PAINTING_TERMS = ("画作", "绘画", "水墨", "油画", "painting", "artwork")


@dataclass(frozen=True)
class PageProfileV2:
    page_no: int
    layout_kind: str
    semantic_hints: tuple[str, ...]
    text_origins: tuple[str, ...]
    origin_admission: dict[str, str]
    ocr_trust_score: float | None
    ocr_trust_band: str
    uncertain: bool
    uncertainty_reasons: tuple[str, ...]
    visual_item_count: int
    unsegmented_visual: bool
    ambiguous_text_count: int
    native_text_chars: int
    canonical_text_chars: int
    parser_text_area_ratio: float
    dominant_image_ratio: float
    evidence: tuple[dict[str, Any], ...]

    def to_metadata(self) -> dict[str, Any]:
        value = asdict(self)
        value["semantic_hints"] = list(self.semantic_hints)
        value["text_origins"] = list(self.text_origins)
        value["origin_admission"] = dict(self.origin_admission)
        value["uncertainty_reasons"] = list(self.uncertainty_reasons)
        value["evidence"] = [dict(item) for item in self.evidence]
        return value


@dataclass(frozen=True)
class DocumentProfileV2:
    schema_version: int
    profile_version: str
    shadow_only: bool
    page_count: int
    layout_distribution: dict[str, float]
    semantic_hints: tuple[str, ...]
    text_origin_distribution: dict[str, float]
    ocr_trust_distribution: dict[str, float]
    uncertain_page_count: int
    pages: tuple[PageProfileV2, ...]
    evidence: tuple[dict[str, Any], ...]

    def to_metadata(self) -> dict[str, Any]:
        value = asdict(self)
        value["semantic_hints"] = list(self.semantic_hints)
        value["pages"] = [page.to_metadata() for page in self.pages]
        value["evidence"] = [dict(item) for item in self.evidence]
        return value


def _compact(value: str) -> str:
    return _SPACE_RE.sub("", value or "")


def _page_text(canonical: CanonicalDocument, page_no: int) -> str:
    page_nodes = [
        node
        for node in canonical.provenance_nodes
        if node.page_no == page_no and node.node_type != "image"
    ]
    if not page_nodes and page_no == 1:
        page_nodes = [
            node
            for node in canonical.provenance_nodes
            if node.page_no is None and node.node_type != "image"
        ]
    return "\n".join(node.canonical_text for node in page_nodes)


def _text_quality(text: str) -> tuple[float, list[dict[str, Any]]]:
    compact = _compact(text)
    if not compact:
        return 0.0, [
            {
                "signal": "ocr_text_quality",
                "value": 0.0,
                "contribution": 0.0,
                "reason": "empty_text",
            }
        ]
    valid_ratio = len(_VALID_TEXT_RE.findall(compact)) / len(compact)
    punctuation_ratio = min(len(_PUNCTUATION_RE.findall(compact)) / max(len(compact), 1), 0.1)
    cjk_chars = _CJK_RE.findall(compact)
    cjk_ratio = len(cjk_chars) / len(compact)
    function_char_ratio = (
        sum(char in _CJK_FUNCTION_CHARS for char in cjk_chars)
        / max(len(cjk_chars), 1)
    )
    replacement_ratio = compact.count("\ufffd") / len(compact)
    diversity = len(set(compact)) / len(compact)
    repetition_penalty = 0.25 * (
        1.0 - min(diversity / 0.08, 1.0)
    )
    fragmented_cjk = (
        len(cjk_chars) >= 80
        and cjk_ratio >= 0.7
        and punctuation_ratio < 0.005
        and function_char_ratio < 0.06
    )
    cjk_fragmentation_penalty = 0.65 if fragmented_cjk else 0.0
    score = (
        0.5 * valid_ratio
        + 0.2 * min(diversity / 0.35, 1.0)
        + 0.15 * min(punctuation_ratio / 0.03, 1.0)
        + 0.15 * min(cjk_ratio / 0.5, 1.0)
        - 0.5 * replacement_ratio
        - repetition_penalty
        - cjk_fragmentation_penalty
    )
    score = round(min(max(score, 0.0), 1.0), 6)
    return score, [
        {
            "signal": "valid_character_ratio",
            "value": round(valid_ratio, 6),
            "contribution": round(0.5 * valid_ratio, 6),
        },
        {
            "signal": "character_diversity",
            "value": round(diversity, 6),
            "contribution": round(0.2 * min(diversity / 0.35, 1.0), 6),
        },
        {
            "signal": "cjk_ratio",
            "value": round(cjk_ratio, 6),
            "contribution": round(0.15 * min(cjk_ratio / 0.5, 1.0), 6),
        },
        {
            "signal": "replacement_character_ratio",
            "value": round(replacement_ratio, 6),
            "contribution": round(-0.5 * replacement_ratio, 6),
        },
        {
            "signal": "low_diversity_repetition_penalty",
            "value": round(repetition_penalty, 6),
            "contribution": round(-repetition_penalty, 6),
        },
        {
            "signal": "cjk_function_character_ratio",
            "value": round(function_char_ratio, 6),
            "contribution": 0.0,
        },
        {
            "signal": "unpunctuated_cjk_fragmentation_penalty",
            "value": fragmented_cjk,
            "contribution": -cjk_fragmentation_penalty,
        },
    ]


def _layout_page_map(visual_layout: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    return {
        int(page["page_no"]): page
        for page in (visual_layout or {}).get("pages") or []
        if isinstance(page, dict) and page.get("page_no") is not None
    }


def _layout_kind(page: PageProfile, *, visual_item_count: int) -> str:
    if page.page_kind == "table_dense":
        return "table_dense"
    if page.page_kind == "slide":
        return "slide_like"
    if page.page_kind == "scanned_prose":
        return "scanned_text"
    if page.native_text_chars > 0 and (
        visual_item_count
        or page.dominant_image_ratio >= 0.65
        or page.page_primary_visual_count > 0
    ):
        return "mixed_layout"
    if page.page_kind == "visual_dominant" or visual_item_count:
        return "visual_dominant"
    if page.canonical_text_chars > 0:
        return "text_dominant"
    return "unknown"


def _semantic_hints(
    canonical: CanonicalDocument,
    page: PageProfile,
    *,
    visual_item_count: int,
    unsegmented: bool,
) -> tuple[str, ...]:
    hints: set[str] = set()
    if page.page_kind == "scanned_prose":
        hints.add("scanned")
    if page.page_kind == "table_dense":
        hints.add("table")
    if page.page_kind == "slide":
        hints.add("presentation")
    if page.code_block_count:
        hints.add("code")
    if visual_item_count > 1:
        hints.add("multi_visual")
    elif visual_item_count == 1:
        hints.add("single_visual")
    if unsegmented:
        hints.add("unsegmented_visual")
    asset_text = " ".join(
        " ".join(
            (
                asset.caption or "",
                asset.vlm_description or "",
                str((asset.metadata or {}).get("semantic_hint") or ""),
            )
        )
        for asset in canonical.assets.values()
        if asset.page_no == page.page_no
    ).lower()
    if any(term in asset_text for term in _CALLIGRAPHY_TERMS):
        hints.add("calligraphy")
    if any(term in asset_text for term in _PAINTING_TERMS):
        hints.add("painting")
    return tuple(sorted(hints))


def _raw_ocr_score(
    page: PageProfile,
    *,
    text_quality: float,
    visual_item_count: int,
    unsegmented: bool,
) -> tuple[float | None, list[dict[str, Any]]]:
    has_explicit_ocr_role = bool(
        {"visual_ocr_candidate", "scanned_body_ocr"}.intersection(
            page.semantic_roles
        )
    )
    is_ocr_candidate = (
        page.native_text_chars == 0
        and page.canonical_text_chars > 0
        and (
            page.page_kind in {"scanned_prose", "visual_dominant"}
            or visual_item_count > 0
            or has_explicit_ocr_role
        )
    )
    if not is_ocr_candidate:
        return None, [
            {
                "signal": "ocr_candidate",
                "value": False,
                "contribution": 0.0,
                "reason": "native_or_non_visual_text_path",
            }
        ]
    visual_ocr_candidate = "visual_ocr_candidate" in set(page.semantic_roles)
    scanned_ocr_candidate = "scanned_body_ocr" in set(page.semantic_roles)
    scanned_prior = (
        page.page_kind == "scanned_prose"
        and not visual_ocr_candidate
        and text_quality >= 0.3
    ) or (scanned_ocr_candidate and text_quality >= 0.3)
    base = 0.52 if scanned_prior else 0.24
    contributions = [
        {
            "signal": "page_kind_prior",
            "value": page.page_kind,
            "contribution": base,
        },
        {
            "signal": "text_quality",
            "value": text_quality,
            "contribution": round(0.25 * text_quality, 6),
        },
    ]
    score = base + 0.25 * text_quality
    continuity = min(page.canonical_text_chars / 300, 1.0)
    continuity_weight = 0.16 if scanned_prior else 0.06
    score += continuity_weight * continuity
    contributions.append(
        {
            "signal": "text_continuity",
            "value": round(continuity, 6),
            "contribution": round(continuity_weight * continuity, 6),
        }
    )
    area_contribution = 0.08 * min(page.parser_text_area_ratio / 0.6, 1.0)
    score += area_contribution
    contributions.append(
        {
            "signal": "parser_text_area_ratio",
            "value": page.parser_text_area_ratio,
            "contribution": round(area_contribution, 6),
        }
    )
    if unsegmented:
        score -= 0.08
        contributions.append(
            {
                "signal": "unsegmented_visual",
                "value": True,
                "contribution": -0.08,
            }
        )
    if visual_item_count > 1:
        score -= 0.04
        contributions.append(
            {
                "signal": "multi_visual_page",
                "value": visual_item_count,
                "contribution": -0.04,
            }
        )
    return round(min(max(score, 0.0), 1.0), 6), contributions


def _trust_band(score: float | None) -> str:
    if score is None:
        return "not_applicable"
    if score >= 0.75:
        return "trusted"
    if score >= 0.60:
        return "auxiliary"
    if score >= 0.40:
        return "uncertain"
    return "shadow"


def _origins_and_admission(
    canonical: CanonicalDocument,
    page: PageProfile,
    *,
    band: str,
) -> tuple[tuple[str, ...], dict[str, str]]:
    origins: set[str] = set()
    if page.native_text_chars > 0:
        origins.add("native_text")
    if page.canonical_text_chars > 0:
        if band == "not_applicable":
            origins.add("parser_text")
        else:
            origins.add("visual_ocr")
    page_assets = [
        asset for asset in canonical.assets.values() if asset.page_no == page.page_no
    ]
    if any(asset.caption for asset in page_assets):
        origins.add("caption")
    if any(asset.vlm_description for asset in page_assets):
        origins.add("vlm_description")
    admission = {
        origin: "normal"
        for origin in origins
        if origin != "visual_ocr"
    }
    if "visual_ocr" in origins:
        admission["visual_ocr"] = {
            "trusted": "normal",
            "auxiliary": "observe",
            "uncertain": "shadow_auxiliary",
            "shadow": "shadow_auxiliary",
        }.get(band, "shadow_auxiliary")
    return tuple(sorted(origins)), admission


def profile_canonical_document_v2(
    canonical: CanonicalDocument,
    profile_v1: DocumentProfile,
    *,
    visual_layout: dict[str, Any] | None = None,
) -> DocumentProfileV2:
    layout_pages = _layout_page_map(visual_layout)
    drafts: list[dict[str, Any]] = []
    for page in profile_v1.pages:
        layout = layout_pages.get(page.page_no) or {}
        items = layout.get("visual_items") or []
        visual_item_count = len(items)
        unsegmented = any(
            item.get("status") == "unsegmented_page_visual" for item in items
        )
        ambiguous_count = sum(
            relation.get("status") == "ambiguous"
            for relation in layout.get("text_relations") or []
        )
        text_quality, quality_evidence = _text_quality(
            _page_text(canonical, page.page_no)
        )
        raw_score, score_evidence = _raw_ocr_score(
            page,
            text_quality=text_quality,
            visual_item_count=visual_item_count,
            unsegmented=unsegmented,
        )
        drafts.append(
            {
                "page": page,
                "layout_kind": _layout_kind(
                    page,
                    visual_item_count=visual_item_count,
                ),
                "semantic_hints": _semantic_hints(
                    canonical,
                    page,
                    visual_item_count=visual_item_count,
                    unsegmented=unsegmented,
                ),
                "visual_item_count": visual_item_count,
                "unsegmented": unsegmented,
                "ambiguous_count": ambiguous_count,
                "raw_score": raw_score,
                "score": raw_score,
                "evidence": [*quality_evidence, *score_evidence],
            }
        )
    for index, draft in enumerate(drafts):
        raw_score = draft["raw_score"]
        if raw_score is None or not 0.35 <= raw_score <= 0.70:
            continue
        neighbor_scores = [
            drafts[neighbor]["raw_score"]
            for neighbor in (index - 1, index + 1)
            if 0 <= neighbor < len(drafts)
            and drafts[neighbor]["raw_score"] is not None
        ]
        if not neighbor_scores:
            continue
        neighbor_prior = sum(neighbor_scores) / len(neighbor_scores)
        smoothed = round(0.85 * raw_score + 0.15 * neighbor_prior, 6)
        draft["score"] = smoothed
        draft["evidence"].append(
            {
                "signal": "adjacent_page_weak_prior",
                "value": round(neighbor_prior, 6),
                "contribution": round(smoothed - raw_score, 6),
            }
        )
    pages: list[PageProfileV2] = []
    for draft in drafts:
        page = draft["page"]
        score = draft["score"]
        band = _trust_band(score)
        origins, admission = _origins_and_admission(
            canonical,
            page,
            band=band,
        )
        uncertain = (
            band == "uncertain"
            or draft["ambiguous_count"] > 0
            or draft["unsegmented"]
        )
        uncertainty_reasons: list[str] = []
        if band == "uncertain":
            uncertainty_reasons.append("ocr_score_in_uncertain_interval")
        if draft["ambiguous_count"]:
            uncertainty_reasons.append("ambiguous_visual_text_relation")
        if draft["unsegmented"]:
            uncertainty_reasons.append("unsegmented_page_visual")
        evidence = [
            {
                "source": "document_profile_v1",
                "signal": "page_kind",
                "value": page.page_kind,
                "contribution": 0.0,
            },
            {
                "source": "document_visual_layout",
                "signal": "visual_item_count",
                "value": draft["visual_item_count"],
                "contribution": 0.0,
            },
            *(
                {
                    "source": "heuristic_features",
                    **item,
                }
                for item in draft["evidence"]
            ),
        ]
        pages.append(
            PageProfileV2(
                page_no=page.page_no,
                layout_kind=draft["layout_kind"],
                semantic_hints=draft["semantic_hints"],
                text_origins=origins,
                origin_admission=admission,
                ocr_trust_score=score,
                ocr_trust_band=band,
                uncertain=uncertain,
                uncertainty_reasons=tuple(uncertainty_reasons),
                visual_item_count=draft["visual_item_count"],
                unsegmented_visual=draft["unsegmented"],
                ambiguous_text_count=draft["ambiguous_count"],
                native_text_chars=page.native_text_chars,
                canonical_text_chars=page.canonical_text_chars,
                parser_text_area_ratio=page.parser_text_area_ratio,
                dominant_image_ratio=page.dominant_image_ratio,
                evidence=tuple(evidence),
            )
        )

    def distribution(values: list[str]) -> dict[str, float]:
        if not pages:
            return {}
        counts = Counter(values)
        return {
            key: round(value / len(pages), 6)
            for key, value in sorted(counts.items())
        }

    all_origins = [
        origin for page in pages for origin in page.text_origins
    ]
    origin_counts = Counter(all_origins)
    origin_distribution = {
        key: round(value / max(len(pages), 1), 6)
        for key, value in sorted(origin_counts.items())
    }
    semantic_hints = tuple(
        sorted({hint for page in pages for hint in page.semantic_hints})
    )
    return DocumentProfileV2(
        schema_version=2,
        profile_version=PROFILE_VERSION,
        shadow_only=True,
        page_count=len(pages),
        layout_distribution=distribution(
            [page.layout_kind for page in pages]
        ),
        semantic_hints=semantic_hints,
        text_origin_distribution=origin_distribution,
        ocr_trust_distribution=distribution(
            [page.ocr_trust_band for page in pages]
        ),
        uncertain_page_count=sum(page.uncertain for page in pages),
        pages=tuple(pages),
        evidence=(
            {
                "source": "document_profile_v2",
                "signal": "shadow_only",
                "value": True,
            },
            {
                "source": "document_profile_v2",
                "signal": "reliable_channels_never_shadowed",
                "value": ["native_text", "caption", "vlm_description"],
            },
        ),
    )


__all__ = [
    "DocumentProfileV2",
    "PROFILE_VERSION",
    "PageProfileV2",
    "profile_canonical_document_v2",
]
