from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Iterable


RELATION_STATUSES = {"bound", "ambiguous", "unmatched", "rejected", "overridden"}
GROUP_STATUSES = {"bound", "ambiguous", "rejected"}
RELATION_TYPES = {
    "caption_of",
    "footnote_of",
    "explicit_reference",
    "relative_reference",
    "shared_context_of",
    "member_of_group",
}


def _ordered_strings(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values or () if str(value)))


def stable_relation_id(
    relation_type: str,
    source_node_ids: Iterable[str],
    target_asset_ids: Iterable[str] = (),
    target_group_ids: Iterable[str] = (),
    *,
    occurrence: dict[str, Any] | None = None,
) -> str:
    payload = {
        "relation_type": relation_type,
        "source_node_ids": list(_ordered_strings(source_node_ids)),
        "target_asset_ids": list(_ordered_strings(target_asset_ids)),
        "target_group_ids": list(_ordered_strings(target_group_ids)),
        "occurrence": occurrence or {},
    }
    digest = sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return f"rel-{digest}"


@dataclass
class CanonicalRelationEdge:
    relation_id: str
    relation_type: str
    source_node_ids: tuple[str, ...] = ()
    target_asset_ids: tuple[str, ...] = ()
    target_group_ids: tuple[str, ...] = ()
    status: str = "unmatched"
    binding_method: str = ""
    binding_score: float | None = None
    score_kind: str = "heuristic_rule_score"
    page_no: int | None = None
    section_path: str = ""
    evidence: tuple[dict[str, Any], ...] = ()
    candidate_targets: tuple[dict[str, Any], ...] = ()
    ambiguity_reasons: tuple[str, ...] = ()
    algorithm_version: str = "document-relation-graph-v1"

    def __post_init__(self) -> None:
        self.source_node_ids = _ordered_strings(self.source_node_ids)
        self.target_asset_ids = _ordered_strings(self.target_asset_ids)
        self.target_group_ids = _ordered_strings(self.target_group_ids)
        self.ambiguity_reasons = _ordered_strings(self.ambiguity_reasons)
        self.evidence = tuple(dict(item) for item in self.evidence)
        self.candidate_targets = tuple(dict(item) for item in self.candidate_targets)
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(f"unsupported relation_type: {self.relation_type}")
        if self.status not in RELATION_STATUSES:
            raise ValueError(f"unsupported relation status: {self.status}")
        if not self.relation_id:
            raise ValueError("relation_id is required")
        if not self.binding_method or not self.score_kind or not self.algorithm_version:
            raise ValueError("automatic relation method, score_kind and algorithm_version are required")
        if not self.evidence:
            raise ValueError("automatic relation evidence is required")
        lowered_score_kind = self.score_kind.lower()
        if "confidence" in lowered_score_kind or "probability" in lowered_score_kind:
            raise ValueError("heuristic scores must not be named confidence or probability")

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "relation_type": self.relation_type,
            "source_node_ids": list(self.source_node_ids),
            "target_asset_ids": list(self.target_asset_ids),
            "target_group_ids": list(self.target_group_ids),
            "status": self.status,
            "binding_method": self.binding_method,
            "binding_score": self.binding_score,
            "score_kind": self.score_kind,
            "page_no": self.page_no,
            "section_path": self.section_path,
            "evidence": [dict(item) for item in self.evidence],
            "candidate_targets": [dict(item) for item in self.candidate_targets],
            "ambiguity_reasons": list(self.ambiguity_reasons),
            "algorithm_version": self.algorithm_version,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CanonicalRelationEdge":
        return cls(
            relation_id=str(payload.get("relation_id") or ""),
            relation_type=str(payload.get("relation_type") or ""),
            source_node_ids=tuple(payload.get("source_node_ids") or ()),
            target_asset_ids=tuple(payload.get("target_asset_ids") or ()),
            target_group_ids=tuple(payload.get("target_group_ids") or ()),
            status=str(payload.get("status") or "unmatched"),
            binding_method=str(payload.get("binding_method") or ""),
            binding_score=payload.get("binding_score"),
            score_kind=str(payload.get("score_kind") or ""),
            page_no=payload.get("page_no"),
            section_path=str(payload.get("section_path") or ""),
            evidence=tuple(payload.get("evidence") or ()),
            candidate_targets=tuple(payload.get("candidate_targets") or ()),
            ambiguity_reasons=tuple(payload.get("ambiguity_reasons") or ()),
            algorithm_version=str(payload.get("algorithm_version") or ""),
        )


@dataclass
class CanonicalAssetGroup:
    group_id: str
    group_type: str
    member_asset_ids: tuple[str, ...]
    shared_context_node_ids: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None
    section_path: str = ""
    grouping_method: str = ""
    grouping_score: float | None = None
    score_kind: str = "heuristic_rule_score"
    status: str = "ambiguous"
    evidence: tuple[dict[str, Any], ...] = ()
    ambiguity_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.member_asset_ids = _ordered_strings(self.member_asset_ids)
        self.shared_context_node_ids = _ordered_strings(self.shared_context_node_ids)
        self.ambiguity_reasons = _ordered_strings(self.ambiguity_reasons)
        self.evidence = tuple(dict(item) for item in self.evidence)
        if self.status not in GROUP_STATUSES:
            raise ValueError(f"unsupported group status: {self.status}")
        if not self.group_id or not self.grouping_method or not self.score_kind:
            raise ValueError("group id, method and score_kind are required")
        if not self.member_asset_ids or not self.evidence:
            raise ValueError("group members and evidence are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "group_type": self.group_type,
            "member_asset_ids": list(self.member_asset_ids),
            "shared_context_node_ids": list(self.shared_context_node_ids),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_path": self.section_path,
            "grouping_method": self.grouping_method,
            "grouping_score": self.grouping_score,
            "score_kind": self.score_kind,
            "status": self.status,
            "evidence": [dict(item) for item in self.evidence],
            "ambiguity_reasons": list(self.ambiguity_reasons),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CanonicalAssetGroup":
        return cls(
            group_id=str(payload.get("group_id") or ""),
            group_type=str(payload.get("group_type") or ""),
            member_asset_ids=tuple(payload.get("member_asset_ids") or ()),
            shared_context_node_ids=tuple(payload.get("shared_context_node_ids") or ()),
            page_start=payload.get("page_start"),
            page_end=payload.get("page_end"),
            section_path=str(payload.get("section_path") or ""),
            grouping_method=str(payload.get("grouping_method") or ""),
            grouping_score=payload.get("grouping_score"),
            score_kind=str(payload.get("score_kind") or ""),
            status=str(payload.get("status") or "ambiguous"),
            evidence=tuple(payload.get("evidence") or ()),
            ambiguity_reasons=tuple(payload.get("ambiguity_reasons") or ()),
        )


@dataclass
class CanonicalRelationGraph:
    edges: list[CanonicalRelationEdge] = field(default_factory=list)
    groups: list[CanonicalAssetGroup] = field(default_factory=list)
    schema_version: int = 1
    algorithm_version: str = "document-relation-graph-v1"
    coordinate_space: str = "normalized_canonical_markdown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "algorithm_version": self.algorithm_version,
            "coordinate_space": self.coordinate_space,
            "edges": [edge.to_dict() for edge in self.edges],
            "groups": [group.to_dict() for group in self.groups],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "CanonicalRelationGraph":
        value = payload or {}
        return cls(
            edges=[CanonicalRelationEdge.from_dict(item) for item in value.get("edges") or ()],
            groups=[CanonicalAssetGroup.from_dict(item) for item in value.get("groups") or ()],
            schema_version=int(value.get("schema_version") or 1),
            algorithm_version=str(
                value.get("algorithm_version") or "document-relation-graph-v1"
            ),
            coordinate_space=str(
                value.get("coordinate_space") or "normalized_canonical_markdown"
            ),
        )


__all__ = [
    "CanonicalAssetGroup",
    "CanonicalRelationEdge",
    "CanonicalRelationGraph",
    "stable_relation_id",
]
