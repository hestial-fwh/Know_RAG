from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LayoutBlock:
    block_id: str
    block_type: str
    bbox: tuple[int, int, int, int] | None
    text: str = ""
    caption: str = ""
    section_path: str = ""
    role: str = ""
    asset_path: str = ""
    source_order: int = 0
    page_no: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["bbox"] = list(self.bbox) if self.bbox else None
        return value


@dataclass(frozen=True)
class GoldRelation:
    relation_id: str
    relation_type: str
    status: str
    auto_bind_allowed: bool
    binding_method: str
    source_node_ids: tuple[str, ...] = ()
    target_asset_ids: tuple[str, ...] = ()
    target_group_ids: tuple[str, ...] = ()
    candidate_targets: tuple[str, ...] = ()
    binding_score: float | None = None
    score_kind: str = "heuristic_rule_score"
    evidence: tuple[dict[str, Any], ...] = ()
    ambiguity_reasons: tuple[str, ...] = ()
    algorithm_version: str = "phase0-gold-v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in (
            "source_node_ids",
            "target_asset_ids",
            "target_group_ids",
            "candidate_targets",
            "evidence",
            "ambiguity_reasons",
        ):
            value[key] = list(value[key])
        return value


@dataclass(frozen=True)
class PageSpec:
    case_id: str
    document_type: str
    scenario: str
    page_no: int
    page_width: int
    page_height: int
    blocks: tuple[LayoutBlock, ...]
    gold_relations: tuple[GoldRelation, ...]
    tags: tuple[str, ...] = ()
    parser_noise: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "document_type": self.document_type,
            "scenario": self.scenario,
            "page_no": self.page_no,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "blocks": [block.to_dict() for block in self.blocks],
            "gold_relations": [relation.to_dict() for relation in self.gold_relations],
            "tags": list(self.tags),
            "parser_noise": self.parser_noise,
        }

