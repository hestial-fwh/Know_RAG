from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BlockSpec:
    block_id: str
    block_type: str
    bbox: tuple[int, int, int, int]
    text: str = ""
    role: str = ""
    native_pdf_text: bool = False
    parser_output: bool = True
    asset_kind: str = ""
    asset_path: str = ""
    caption: str = ""
    expected_semantic_role: str = ""
    expected_index_policy: str = "normal"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["bbox"] = list(self.bbox)
        return value


@dataclass(frozen=True)
class PageSpec:
    case_id: str
    page_no: int
    page_kind: str
    chunk_policy_id: str
    preserve_page_visual: bool
    blocks: tuple[BlockSpec, ...]
    page_width: int = 612
    page_height: int = 792
    tags: tuple[str, ...] = ()
    expected_known_gaps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        image_blocks = [block for block in self.blocks if block.block_type == "image"]
        dominant_ratio = max(
            (
                ((block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1]))
                / (self.page_width * self.page_height)
                for block in image_blocks
            ),
            default=0.0,
        )
        return {
            "case_id": self.case_id,
            "page_no": self.page_no,
            "page_kind": self.page_kind,
            "chunk_policy_id": self.chunk_policy_id,
            "preserve_page_visual": self.preserve_page_visual,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "dominant_image_ratio": round(dominant_ratio, 6),
            "blocks": [block.to_dict() for block in self.blocks],
            "tags": list(self.tags),
            "expected_known_gaps": list(self.expected_known_gaps),
        }


@dataclass(frozen=True)
class DocumentSpec:
    document_id: str
    document_type: str
    primary_kind: str
    pages: tuple[PageSpec, ...]
