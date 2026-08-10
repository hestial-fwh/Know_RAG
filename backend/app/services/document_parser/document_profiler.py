from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable

from app.services.document_parser.canonical_document import (
    CanonicalAsset,
    CanonicalDocument,
    CanonicalSourceNode,
)


PROFILE_VERSION = "document-profile-v1"
_SPACE_RE = re.compile(r"\s+")
_CODE_FENCE_RE = re.compile(r"(?m)^\s*(```|~~~)")
_MARKDOWN_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")
_SENTENCE_PUNCTUATION_RE = re.compile(r"[。！？!?；;.]")
_HEADING_ROLES = {"heading", "title"}
_CODE_ROLES = {"code", "code_block", "fenced_code", "pre", "source_code"}


@dataclass(frozen=True)
class PageProfile:
    page_no: int
    page_kind: str
    native_text_chars: int
    canonical_text_chars: int
    dominant_image_ratio: float
    text_node_count: int
    image_node_count: int
    table_node_count: int
    table_area_ratio: float
    table_text_ratio: float
    parser_text_area_ratio: float
    heading_count: int
    code_block_count: int
    page_primary_visual_count: int
    semantic_roles: tuple[str, ...]
    visual_status: str
    ocr_layout: str
    heuristic_score: float
    evidence: tuple[dict[str, Any], ...]

    def to_metadata(self) -> dict[str, Any]:
        value = asdict(self)
        value["semantic_roles"] = list(self.semantic_roles)
        value["evidence"] = [dict(item) for item in self.evidence]
        return value


@dataclass(frozen=True)
class DocumentProfile:
    schema_version: int
    profile_version: str
    primary_kind: str
    secondary_kinds: tuple[str, ...]
    page_count: int
    native_text_page_ratio: float
    visual_dominant_page_ratio: float
    scanned_page_ratio: float
    table_dense_page_ratio: float
    heading_density: float
    parser_quality_status: str
    pages: tuple[PageProfile, ...]
    evidence: tuple[dict[str, Any], ...]

    def to_metadata(self) -> dict[str, Any]:
        value = asdict(self)
        value["secondary_kinds"] = list(self.secondary_kinds)
        value["pages"] = [page.to_metadata() for page in self.pages]
        value["evidence"] = [dict(item) for item in self.evidence]
        return value


def _compact_chars(value: str) -> int:
    return len(_SPACE_RE.sub("", value or ""))


def _page_visual_facts(canonical: CanonicalDocument) -> dict[int, dict[str, Any]]:
    profile = canonical.metadata.get("page_visual_profile")
    if not isinstance(profile, dict):
        return {}
    facts: dict[int, dict[str, Any]] = {}
    for item in profile.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        try:
            page_no = int(item.get("page_no"))
        except (TypeError, ValueError):
            continue
        if page_no > 0:
            facts[page_no] = item
    return facts


def _asset_is_page_primary(asset: CanonicalAsset) -> bool:
    metadata = asset.metadata or {}
    return (
        metadata.get("visual_role") == "page_primary_visual"
        or metadata.get("semantic_role") == "page_primary_visual"
        or metadata.get("role") == "page_primary_visual"
    )


def _node_area_ratio(
    nodes: Iterable[CanonicalSourceNode],
    *,
    page_width: float,
    page_height: float,
) -> float:
    page_area = page_width * page_height
    if page_area <= 0.0:
        return 0.0
    area = 0.0
    for node in nodes:
        if not node.bbox:
            continue
        try:
            width = max(0.0, float(node.bbox[2]) - float(node.bbox[0]))
            height = max(0.0, float(node.bbox[3]) - float(node.bbox[1]))
        except (TypeError, ValueError):
            continue
        area += width * height
    return round(min(1.0, area / page_area), 6)


def _looks_like_continuous_prose(text: str) -> bool:
    compact_chars = _compact_chars(text)
    if compact_chars < 60:
        return False
    tokens = [token for token in _SPACE_RE.split(text.strip()) if token]
    return bool(
        _SENTENCE_PUNCTUATION_RE.search(text)
        or len(tokens) >= 10
        or compact_chars >= 120
    )


def _page_kind(
    *,
    table_dense: bool,
    image_count: int,
    primary_visual_count: int,
    dominant_image_ratio: float,
    canonical_text_chars: int,
    heading_count: int,
    non_heading_text_count: int,
    continuous_prose: bool,
    native_text_chars: int,
    has_code: bool,
    processing_mode: str = "visual_art",
) -> tuple[str, tuple[str, ...], float]:
    """Classify a page into one of six kinds.

    In ``"standard"`` mode the classifier trusts MinerU's signals
    exclusively: ``visual_dominant`` is only triggered by an explicit
    ``page_primary_visual`` marker (with a ``>= 300`` char safety guard
    for legacy documents).  pdfplumber's ``dominant_image_ratio`` and
    ``native_text_chars`` are NOT consulted.  Best for patents, papers,
    reports, and most born-digital PDFs.

    In ``"visual_art"`` mode (default, backward-compatible) the
    classifier uses the 255ca0d-aligned heuristic:
    ``visual_dominant = primary_visual_count OR dominant_image_ratio >= 0.65``.
    The only escape is ``continuous_prose + native_text_chars == 0``
    → ``scanned_prose``.  There are NO patent/text-page escapes.
    Best for calligraphy, art books, and visual collections.
    """
    reasons: list[str] = []
    if table_dense:
        reasons.extend(("table_density_threshold_met", "table_boundary_is_structural"))
        return "table_dense", tuple(reasons), 1.0

    if processing_mode == "standard":
        # Standard mode trusts MinerU exclusively.  pdfplumber is never
        # invoked at parse time (parser_service skips the enricher), so
        # primary_visual_count is reliably 0 for newly parsed documents.
        #
        # The guard below protects against two edge cases:
        # 1. Legacy documents parsed before the enricher was gated by
        #    processing_mode — their stored canonical data may still
        #    carry spurious page_primary_visual tags.
        # 2. Future MinerU versions that might emit their own
        #    page_primary_visual signal for genuine full-page figures.
        if primary_visual_count:
            reasons.append("page_primary_visual_present")
            if canonical_text_chars >= 300:
                reasons.append("substantial_text_overrides_primary_visual")
                # fall through → native_text
            else:
                reasons.append("visual_content_dominates_page")
                return "visual_dominant", tuple(reasons), 1.0
    else:
        # visual_art mode: 255ca0d-aligned heuristic.
        # dominant_image_ratio >= 0.65 triggers visual_dominant.
        # Only escape: continuous_prose + zero native_text → scanned_prose.
        # No patent/document escape branches — those belong in standard mode.
        visual_dominant = bool(
            primary_visual_count or dominant_image_ratio >= 0.65
        )
        if visual_dominant:
            reasons.append(
                "page_primary_visual_present"
                if primary_visual_count
                else "dominant_embedded_image"
            )
            if continuous_prose and native_text_chars == 0:
                reasons.append("continuous_parser_prose_without_native_text")
                return "scanned_prose", tuple(reasons), 1.0
            reasons.append("visual_content_dominates_page")
            return "visual_dominant", tuple(reasons), 1.0

    if (
        image_count
        and heading_count
        and non_heading_text_count
        and 0 < canonical_text_chars <= 100
    ):
        reasons.extend(
            (
                "short_heading_body_layout",
                "supporting_figure_present",
                "page_sized_content_group",
            )
        )
        return "slide", tuple(reasons), 0.8
    if canonical_text_chars:
        reasons.append("canonical_text_present")
        if has_code:
            reasons.append("code_structure_present")
        return "native_text", tuple(reasons), 0.8
    reasons.append("insufficient_page_evidence")
    return "unknown", tuple(reasons), 0.0


def _profile_category(page: PageProfile) -> str:
    if page.page_kind == "native_text" and page.code_block_count:
        return "code_or_technical"
    return {
        "native_text": "structured_text",
        "scanned_prose": "scanned_prose",
        "visual_dominant": "visual_collection",
        "table_dense": "table_dense",
        "slide": "presentation",
    }.get(page.page_kind, "unknown")


def profile_canonical_document(
    canonical: CanonicalDocument,
    *,
    provenance_nodes: Iterable[CanonicalSourceNode] | None = None,
    processing_mode: str = "visual_art",
) -> DocumentProfile:
    """Build a deterministic, read-only document and page profile.

    The result is an explainable shadow decision. It does not mutate canonical
    content, chunks, assets, or index-admission metadata.

    ``processing_mode`` controls both the parse-time enrichment and the
    page classification heuristic:

    - ``"standard"`` — pdfplumber is NOT invoked (neither for profiling nor
      for page visual enrichment).  ``_page_kind`` trusts MinerU's
      ``canonical_text_chars`` exclusively; ``visual_dominant`` only
      triggers on explicit ``page_primary_visual`` markers (with a
      ``>= 300`` char safety guard for legacy documents).  Best for
      patents, papers, reports, and most born-digital PDFs.
    - ``"visual_art"`` — 255ca0d-aligned heuristic: pdfplumber runs
      normally; ``visual_dominant`` is triggered by ``primary_visual_count``
      OR ``dominant_image_ratio >= 0.65`` with a single ``scanned_prose``
      escape.  No patent/text-page escape branches.  Best for calligraphy,
      art books, and visual collections.
    """
    nodes = list(
        provenance_nodes
        if provenance_nodes is not None
        else canonical.provenance_nodes
    )
    visual_facts = _page_visual_facts(canonical)
    nodes_by_page: dict[int, list[CanonicalSourceNode]] = {}
    assets_by_page: dict[int, list[CanonicalAsset]] = {}
    unpaged_nodes: list[CanonicalSourceNode] = []
    unpaged_assets: list[CanonicalAsset] = []
    page_numbers = set(visual_facts)
    for node in nodes:
        if node.page_no is None or int(node.page_no) <= 0:
            unpaged_nodes.append(node)
            continue
        page_no = int(node.page_no)
        nodes_by_page.setdefault(page_no, []).append(node)
        page_numbers.add(page_no)
    for asset in canonical.assets.values():
        if asset.page_no is None or int(asset.page_no) <= 0:
            unpaged_assets.append(asset)
            continue
        page_no = int(asset.page_no)
        assets_by_page.setdefault(page_no, []).append(asset)
        page_numbers.add(page_no)
    logical_pages: set[int] = set()
    if not page_numbers and (
        unpaged_nodes or unpaged_assets or canonical.markdown_content.strip()
    ):
        # Page-less formats still need a stable policy scope. Treat the whole
        # canonical document as one logical page without mutating provenance.
        page_numbers.add(1)
        nodes_by_page[1] = unpaged_nodes
        assets_by_page[1] = unpaged_assets
        logical_pages.add(1)

    pages: list[PageProfile] = []
    for page_no in sorted(page_numbers):
        page_nodes = nodes_by_page.get(page_no, [])
        page_assets = assets_by_page.get(page_no, [])
        roles = {
            node.semantic_role.strip().lower()
            for node in page_nodes
            if node.semantic_role.strip()
        }
        node_types = Counter(node.node_type.strip().lower() for node in page_nodes)
        page_text_nodes = [
            node
            for node in page_nodes
            if node.node_type.strip().lower() == "text"
        ]
        table_nodes = [
            node
            for node in page_nodes
            if node.node_type.strip().lower() == "table"
        ]
        page_text = "\n".join(node.canonical_text for node in page_text_nodes)
        canonical_text_chars = sum(
            _compact_chars(node.canonical_text)
            for node in page_nodes
            if node.node_type.strip().lower() != "image"
        )
        if page_no in logical_pages and not canonical_text_chars:
            canonical_text_chars = _compact_chars(canonical.markdown_content)
        image_count = max(node_types["image"], len(page_assets))
        primary_visual_count = sum(_asset_is_page_primary(asset) for asset in page_assets)
        fact = visual_facts.get(page_no) or {}
        try:
            native_text_chars = (
                int(fact.get("native_text_chars") or 0)
                if fact
                else canonical_text_chars
            )
            dominant_image_ratio = float(fact.get("dominant_image_ratio") or 0.0)
            page_width = float(fact.get("page_width") or 0.0)
            page_height = float(fact.get("page_height") or 0.0)
        except (TypeError, ValueError):
            native_text_chars = 0
            dominant_image_ratio = 0.0
            page_width = 0.0
            page_height = 0.0
        heading_count = sum(
            (
                node.semantic_role.strip().lower() in _HEADING_ROLES
                or bool(_MARKDOWN_HEADING_RE.match(node.canonical_text))
            )
            for node in page_text_nodes
        )
        if page_no in logical_pages:
            heading_count = max(
                heading_count,
                len(_MARKDOWN_HEADING_RE.findall(canonical.markdown_content)),
            )
        code_block_count = sum(
            node.semantic_role.strip().lower() in _CODE_ROLES
            for node in page_text_nodes
        )
        fence_marker_count = len(
            _CODE_FENCE_RE.findall(
                canonical.markdown_content if page_no in logical_pages else page_text
            )
        )
        fenced_code_count = (fence_marker_count + 1) // 2
        code_block_count = max(code_block_count, fenced_code_count)
        table_text_chars = sum(_compact_chars(node.canonical_text) for node in table_nodes)
        table_text_ratio = round(
            table_text_chars / max(canonical_text_chars, 1),
            6,
        )
        table_area_ratio = _node_area_ratio(
            table_nodes,
            page_width=page_width,
            page_height=page_height,
        )
        parser_text_area_ratio = _node_area_ratio(
            page_text_nodes,
            page_width=page_width,
            page_height=page_height,
        )
        table_dense = bool(
            table_nodes
            and (table_area_ratio >= 0.35 or table_text_ratio >= 0.60)
        )
        continuous_prose = _looks_like_continuous_prose(page_text)
        page_kind, rule_reasons, score = _page_kind(
            table_dense=table_dense,
            image_count=image_count,
            primary_visual_count=primary_visual_count,
            dominant_image_ratio=dominant_image_ratio,
            canonical_text_chars=canonical_text_chars,
            heading_count=heading_count,
            non_heading_text_count=max(len(page_text_nodes) - heading_count, 0),
            continuous_prose=continuous_prose,
            native_text_chars=native_text_chars,
            has_code=code_block_count > 0,
            processing_mode=processing_mode,
        )
        if page_kind == "scanned_prose":
            ocr_layout = "continuous_prose"
        elif (
            page_kind == "visual_dominant"
            and native_text_chars == 0
            and 30 <= canonical_text_chars <= 400
            and 0.0 < parser_text_area_ratio <= 0.35
        ):
            ocr_layout = "visual_core_text"
        elif page_kind == "visual_dominant" and native_text_chars == 0:
            ocr_layout = "visual_candidate"
        else:
            ocr_layout = "not_detected"
        evidence: list[dict[str, Any]] = [
            {
                "source": "canonical_provenance",
                "signal": reason,
                "value": True,
            }
            for reason in rule_reasons
        ]
        if fact:
            evidence.append(
                {
                    "source": "pdf_page_visual_profile",
                    "signal": "visual_decision",
                    "value": str(fact.get("decision") or "normal"),
                }
            )
        if page_no in logical_pages:
            evidence.append(
                {
                    "source": "canonical_provenance",
                    "signal": "logical_page_fallback",
                    "value": True,
                }
            )
        pages.append(
            PageProfile(
                page_no=page_no,
                page_kind=page_kind,
                native_text_chars=native_text_chars,
                canonical_text_chars=canonical_text_chars,
                dominant_image_ratio=round(dominant_image_ratio, 6),
                text_node_count=node_types["text"],
                image_node_count=image_count,
                table_node_count=node_types["table"],
                table_area_ratio=table_area_ratio,
                table_text_ratio=table_text_ratio,
                parser_text_area_ratio=parser_text_area_ratio,
                heading_count=heading_count,
                code_block_count=code_block_count,
                page_primary_visual_count=primary_visual_count,
                semantic_roles=tuple(sorted(roles)),
                visual_status=str(fact.get("decision") or "not_profiled"),
                ocr_layout=ocr_layout,
                heuristic_score=score,
                evidence=tuple(evidence),
            )
        )

    categories = [_profile_category(page) for page in pages]
    category_counts = Counter(categories)
    non_unknown = sorted(category for category in category_counts if category != "unknown")
    if not pages:
        primary_kind = "unknown"
    elif len(non_unknown) == 1 and category_counts.get("unknown", 0) == 0:
        primary_kind = non_unknown[0]
    elif len(non_unknown) == 0:
        primary_kind = "unknown"
    else:
        primary_kind = "mixed"
    secondary_kinds = tuple(
        category
        for category, _ in sorted(
            category_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if category != primary_kind
    )
    page_count = len(pages)

    def ratio(predicate) -> float:
        if not page_count:
            return 0.0
        return round(sum(predicate(page) for page in pages) / page_count, 6)

    evidence = (
        {
            "source": "page_profiles",
            "signal": "page_kind_counts",
            "value": dict(sorted(Counter(page.page_kind for page in pages).items())),
        },
        {
            "source": "page_profiles",
            "signal": "category_counts",
            "value": dict(sorted(category_counts.items())),
        },
    )
    return DocumentProfile(
        schema_version=1,
        profile_version=PROFILE_VERSION,
        primary_kind=primary_kind,
        secondary_kinds=secondary_kinds,
        page_count=page_count,
        native_text_page_ratio=ratio(lambda page: page.native_text_chars > 0),
        visual_dominant_page_ratio=ratio(
            lambda page: page.page_kind == "visual_dominant"
        ),
        scanned_page_ratio=ratio(lambda page: page.page_kind == "scanned_prose"),
        table_dense_page_ratio=ratio(lambda page: page.page_kind == "table_dense"),
        heading_density=round(
            sum(page.heading_count for page in pages) / max(page_count, 1),
            6,
        ),
        parser_quality_status="profiled" if pages else "insufficient_evidence",
        pages=tuple(pages),
        evidence=evidence,
    )


__all__ = [
    "DocumentProfile",
    "PROFILE_VERSION",
    "PageProfile",
    "profile_canonical_document",
]
