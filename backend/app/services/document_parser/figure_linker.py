"""Link explicit prose figure references to reliable Canonical image assets.

P2 operates only in normalized Canonical Markdown coordinates. It deliberately
does not map references to child chunks or persist ORM relations; those are P3
and P4 responsibilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.services.document_parser.canonical_document import (
    CanonicalAsset,
    CanonicalSourceNode,
    find_image_asset_refs,
)
from app.services.document_parser.image_relation_resolver import normalize_figure_token


_FIGURE_LABEL_PATTERN = r"(?:图|fig(?:ures?|s)?\.?)"
_FIGURE_TOKEN_PATTERN = (
    r"(?:[Ss]\s*[-.]?\s*)?(?:[0-9]+|[一二三四五六七八九十百]+)"
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
_RANGE_ENDPOINT_PATTERN = (
    r"(?:[Ss]\s*[-.]?\s*)?(?:[0-9]+|[一二三四五六七八九十百]+)"
    r"(?:\s*[（(]\s*[A-Za-z0-9]\s*[）)]|[A-Za-z])?"
)
_LABELED_FIGURE_RANGE_RE = re.compile(
    rf"{_FIGURE_LABEL_PATTERN}\s*(?P<first>{_RANGE_ENDPOINT_PATTERN})"
    rf"\s*(?:-|–|—|‑|~|～|至|到|to)\s*"
    rf"(?:{_FIGURE_LABEL_PATTERN}\s*)?(?P<last>{_RANGE_ENDPOINT_PATTERN})",
    re.IGNORECASE,
)
_PLURAL_FIGURE_RANGE_RE = re.compile(
    rf"(?:figs|figures)\.?\s*(?P<first>{_RANGE_ENDPOINT_PATTERN})"
    rf"\s*(?:-|–|—|‑|~|～|to)\s*(?P<last>{_RANGE_ENDPOINT_PATTERN})",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(?<!`)`[^`\n]+`(?!`)")
_ZH_PREFIX_CUE_RE = re.compile(
    r"(?:如|见|参见|详见|参考|根据|由)[^。；;！？!?\n]{0,24}$"
)
_ZH_SUFFIX_CUE_RE = re.compile(r"^\s*(?:所示|可见|中|给出|显示|表明|说明|展示|列出|比较|对比|为|是)")
_EN_PREFIX_CUE_RE = re.compile(
    r"(?:as\s+shown\s+in|shown\s+in|see|refer(?:ring)?\s+to)\s*$",
    re.IGNORECASE,
)
_EN_SUFFIX_CUE_RE = re.compile(r"^\s*(?:shows?|illustrates?|depicts?|is\s+a|presents?|represents?)\b", re.IGNORECASE)
_NON_PROSE_ROLES = {"caption", "image_caption", "figure_caption", "footnote", "code", "code_block"}


@dataclass(frozen=True)
class FigureReference:
    figure_id: str
    raw_text: str
    start: int
    end: int
    section: str = ""
    reference_type: str = "explicit"
    source_node_id: str = ""
    page_no: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    source_order: int | None = None


@dataclass(frozen=True)
class FigureCandidateEvidence:
    asset_id: str
    figure_id_exact: bool
    p1_binding_score: float
    caption_method: str
    page_match: bool | None
    section_match: bool
    source_order_distance: int | None
    canonical_char_distance: int | None
    rule_score: float

    def to_metadata(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "figure_id_exact": self.figure_id_exact,
            "p1_binding_score": self.p1_binding_score,
            "caption_method": self.caption_method,
            "page_match": self.page_match,
            "section_match": self.section_match,
            "source_order_distance": self.source_order_distance,
            "canonical_char_distance": self.canonical_char_distance,
            "rule_score": self.rule_score,
        }


@dataclass(frozen=True)
class FigureLink:
    reference: FigureReference
    status: str
    asset_ids: tuple[str, ...] = ()
    candidate_asset_ids: tuple[str, ...] = ()
    binding_method: str = ""
    binding_score: float = 0.0
    score_kind: str = "heuristic_rule_score"
    ambiguity_reason: str = ""
    evidence: tuple[FigureCandidateEvidence, ...] = ()

    def to_metadata(self) -> dict:
        return {
            "version": 1,
            "figure_id": self.reference.figure_id,
            "reference_type": self.reference.reference_type,
            "raw_text": self.reference.raw_text,
            "start": self.reference.start,
            "end": self.reference.end,
            "section": self.reference.section,
            "status": self.status,
            "asset_ids": list(self.asset_ids),
            "candidate_asset_ids": list(self.candidate_asset_ids),
            "binding_method": self.binding_method,
            "binding_score": self.binding_score,
            "score_kind": self.score_kind,
            "ambiguity_reason": self.ambiguity_reason,
            "evidence": [item.to_metadata() for item in self.evidence],
            "source_node_id": self.reference.source_node_id,
            "page_no": self.reference.page_no,
            "bbox": list(self.reference.bbox) if self.reference.bbox else None,
            "source_order": self.reference.source_order,
        }


@dataclass
class FigureLinkResult:
    links: list[FigureLink] = field(default_factory=list)
    coordinate_space: str = "normalized_canonical_markdown"
    version: int = 1

    @property
    def references(self) -> list[FigureReference]:
        return [link.reference for link in self.links]

    def to_metadata(self) -> dict:
        return {
            "version": self.version,
            "coordinate_space": self.coordinate_space,
            "links": [link.to_metadata() for link in self.links],
        }


def _protected_spans(markdown: str) -> list[tuple[int, int]]:
    spans = [(start, end) for _, _, start, end in find_image_asset_refs(markdown)]
    spans.extend((match.start(), match.end()) for match in _INLINE_CODE_RE.finditer(markdown))
    fence_start: int | None = None
    fence_marker = ""
    offset = 0
    for line in markdown.splitlines(keepends=True):
        match = _FENCE_RE.match(line)
        if fence_start is None and match:
            fence_start = offset
            fence_marker = match.group(1)
        elif (
            fence_start is not None
            and match
            and match.group(1)[0] == fence_marker[0]
            and len(match.group(1)) >= len(fence_marker)
        ):
            spans.append((fence_start, offset + len(line)))
            fence_start = None
            fence_marker = ""
        offset += len(line)
    if fence_start is not None:
        spans.append((fence_start, len(markdown)))
    return sorted(spans)


def _inside_spans(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _heading_positions(markdown: str) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    path: list[str] = []
    fence = ""
    offset = 0
    for line in markdown.splitlines(keepends=True):
        fence_match = _FENCE_RE.match(line)
        if fence:
            if (
                fence_match
                and fence_match.group(1)[0] == fence[0]
                and len(fence_match.group(1)) >= len(fence)
            ):
                fence = ""
            offset += len(line)
            continue
        if fence_match:
            fence = fence_match.group(1)
            offset += len(line)
            continue
        heading = _HEADING_RE.match(line.rstrip("\r\n"))
        if heading:
            level = len(heading.group(1))
            path = path[: level - 1]
            path.append(heading.group(2).strip())
            positions.append((offset, " > ".join(path)))
        offset += len(line)
    return positions


def _section_at(position: int, headings: list[tuple[int, str]]) -> str:
    section = ""
    for offset, value in headings:
        if offset > position:
            break
        section = value
    return section


def _node_for_span(
    start: int,
    end: int,
    provenance_nodes: list[CanonicalSourceNode],
) -> CanonicalSourceNode | None:
    matches = [
        node
        for node in provenance_nodes
        if node.normalized_start is not None
        and node.normalized_end is not None
        and node.normalized_start <= start
        and node.normalized_end >= end
    ]
    if not matches:
        return None
    return min(
        matches,
        key=lambda node: (
            (node.normalized_end or 0) - (node.normalized_start or 0),
            node.source_order,
        ),
    )


def _has_explicit_cue(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 32) : start].rsplit("\n", 1)[-1]
    after = text[end : min(len(text), end + 16)].split("\n", 1)[0]
    return bool(
        _ZH_PREFIX_CUE_RE.search(before)
        or _ZH_SUFFIX_CUE_RE.search(after)
        or _EN_PREFIX_CUE_RE.search(before)
        or _EN_SUFFIX_CUE_RE.search(after)
    )


def extract_explicit_figure_references(
    markdown: str,
    *,
    provenance_nodes: list[CanonicalSourceNode] | None = None,
) -> list[FigureReference]:
    """Extract explicit prose references, excluding image markers and code."""
    text = str(markdown or "")
    protected = _protected_spans(text)
    headings = _heading_positions(text)
    references: list[FigureReference] = []
    nodes = list(provenance_nodes or [])
    range_spans: list[tuple[int, int]] = []
    range_matches = sorted(
        [
            *list(_LABELED_FIGURE_RANGE_RE.finditer(text)),
            *list(_PLURAL_FIGURE_RANGE_RE.finditer(text)),
        ],
        key=lambda match: (match.start(), match.end()),
    )
    for match in range_matches:
        if _inside_spans(match.start(), match.end(), protected):
            continue
        if _inside_spans(match.start(), match.end(), range_spans):
            continue
        if not _has_explicit_cue(text, match.start(), match.end()):
            continue
        node = _node_for_span(match.start(), match.end(), nodes)
        if node and node.semantic_role in _NON_PROSE_ROLES:
            continue
        range_spans.append((match.start(), match.end()))
        for endpoint in ("first", "last"):
            start = match.start(endpoint)
            end = match.end(endpoint)
            references.append(
                FigureReference(
                    figure_id=normalize_figure_token(match.group(endpoint)),
                    raw_text=match.group(endpoint),
                    start=start,
                    end=end,
                    section=(
                        node.section_path
                        if node and node.section_path
                        else _section_at(start, headings)
                    ),
                    reference_type="explicit_range",
                    source_node_id=node.node_id if node else "",
                    page_no=node.page_no if node else None,
                    bbox=node.bbox if node else None,
                    source_order=node.source_order if node else None,
                )
            )
    for group in _FIGURE_GROUP_RE.finditer(text):
        if _inside_spans(group.start(), group.end(), protected):
            continue
        if _inside_spans(group.start(), group.end(), range_spans):
            continue
        if not _has_explicit_cue(text, group.start(), group.end()):
            continue
        node = _node_for_span(group.start(), group.end(), nodes)
        if node and node.semantic_role in _NON_PROSE_ROLES:
            continue
        body_start = group.start("body")
        for token in _FIGURE_TOKEN_RE.finditer(group.group("body")):
            start = body_start + token.start()
            end = body_start + token.end()
            references.append(
                FigureReference(
                    figure_id=normalize_figure_token(token.group(0)),
                    raw_text=token.group(0),
                    start=start,
                    end=end,
                    section=(node.section_path if node and node.section_path else _section_at(start, headings)),
                    source_node_id=node.node_id if node else "",
                    page_no=node.page_no if node else None,
                    bbox=node.bbox if node else None,
                    source_order=node.source_order if node else None,
                )
            )
    return sorted(references, key=lambda reference: (reference.start, reference.end))


def _asset_marker_positions(markdown: str) -> dict[str, int]:
    positions: dict[str, int] = {}
    for asset_id, _, start, end in find_image_asset_refs(markdown):
        positions.setdefault(asset_id, (start + end) // 2)
    return positions


def _candidate_evidence(
    reference: FigureReference,
    asset: CanonicalAsset,
    *,
    asset_section: str,
    marker_position: int | None,
) -> FigureCandidateEvidence:
    page_match = (
        reference.page_no == asset.page_no
        if reference.page_no is not None and asset.page_no is not None
        else None
    )
    section_match = bool(
        reference.section
        and asset_section
        and reference.section == asset_section
    )
    order_distance = (
        abs(asset.source_block_order - reference.source_order)
        if reference.source_order is not None
        else None
    )
    char_distance = (
        abs(marker_position - reference.start)
        if marker_position is not None
        else None
    )
    proximity = (
        max(0.0, 1.0 - min(char_distance, 12000) / 12000)
        if char_distance is not None
        else 0.0
    )
    rule_score = 0.48 + 0.28 * max(0.0, min(1.0, asset.binding_score))
    if section_match:
        rule_score += 0.10
    if page_match is True:
        rule_score += 0.10
    elif page_match is False:
        rule_score -= 0.10
    rule_score += 0.04 * proximity
    return FigureCandidateEvidence(
        asset_id=asset.asset_id,
        figure_id_exact=reference.figure_id in asset.figure_ids,
        p1_binding_score=asset.binding_score,
        caption_method=asset.caption_method,
        page_match=page_match,
        section_match=section_match,
        source_order_distance=order_distance,
        canonical_char_distance=char_distance,
        rule_score=round(max(0.0, min(0.99, rule_score)), 4),
    )


def link_figure_references(
    normalized_markdown: str,
    assets: dict[str, CanonicalAsset],
    *,
    provenance_nodes: list[CanonicalSourceNode] | None = None,
) -> FigureLinkResult:
    """Resolve explicit references without forcing repeated figure numbers."""
    text = str(normalized_markdown or "")
    headings = _heading_positions(text)
    marker_positions = _asset_marker_positions(text)
    asset_sections = {
        asset_id: _section_at(position, headings)
        for asset_id, position in marker_positions.items()
    }
    result = FigureLinkResult()
    for reference in extract_explicit_figure_references(
        text,
        provenance_nodes=provenance_nodes,
    ):
        candidates = [
            asset
            for asset in assets.values()
            if asset.deterministic_binding_allowed
            and reference.figure_id in asset.figure_ids
        ]
        evidence_by_asset = {
            asset.asset_id: _candidate_evidence(
                reference,
                asset,
                asset_section=asset_sections.get(asset.asset_id, asset.context_header),
                marker_position=marker_positions.get(asset.asset_id),
            )
            for asset in candidates
        }
        candidates.sort(
            key=lambda asset: (
                -evidence_by_asset[asset.asset_id].rule_score,
                evidence_by_asset[asset.asset_id].canonical_char_distance
                if evidence_by_asset[asset.asset_id].canonical_char_distance is not None
                else len(text) + asset.source_block_order,
                asset.source_block_order,
                asset.asset_id,
            )
        )
        evidence = tuple(evidence_by_asset[asset.asset_id] for asset in candidates)
        candidate_ids = tuple(item.asset_id for item in evidence)
        if not candidates:
            result.links.append(
                FigureLink(
                    reference=reference,
                    status="unmatched",
                    ambiguity_reason="no_reliable_figure_asset",
                    evidence=evidence,
                )
            )
            continue
        if len(candidates) == 1:
            result.links.append(
                FigureLink(
                    reference=reference,
                    status="bound",
                    asset_ids=(candidates[0].asset_id,),
                    candidate_asset_ids=candidate_ids,
                    binding_method="figure_id_exact",
                    binding_score=evidence[0].rule_score,
                    evidence=evidence,
                )
            )
            continue
        same_page = [item for item in evidence if item.page_match is True]
        has_comparable_pages = any(item.page_match is not None for item in evidence)
        decision_pool = (
            same_page
            if same_page
            else ([] if has_comparable_pages else list(evidence))
        )
        if len(same_page) == 1:
            selected = same_page[0]
            result.links.append(
                FigureLink(
                    reference=reference,
                    status="bound",
                    asset_ids=(selected.asset_id,),
                    candidate_asset_ids=candidate_ids,
                    binding_method="figure_id_page_exact",
                    binding_score=selected.rule_score,
                    evidence=evidence,
                )
            )
            continue
        same_section = [item for item in decision_pool if item.section_match]
        if len(same_section) == 1:
            selected = same_section[0]
            result.links.append(
                FigureLink(
                    reference=reference,
                    status="bound",
                    asset_ids=(selected.asset_id,),
                    candidate_asset_ids=candidate_ids,
                    binding_method="figure_id_page_section_exact" if same_page else "figure_id_section_exact",
                    binding_score=selected.rule_score,
                    evidence=evidence,
                )
            )
            continue
        result.links.append(
            FigureLink(
                reference=reference,
                status="ambiguous",
                candidate_asset_ids=candidate_ids,
                ambiguity_reason="repeated_figure_id_not_uniquely_resolved",
                evidence=evidence,
            )
        )
    return result


__all__ = [
    "FigureLink",
    "FigureLinkResult",
    "FigureReference",
    "extract_explicit_figure_references",
    "link_figure_references",
]
