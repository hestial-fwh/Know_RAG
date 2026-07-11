"""Runtime adapter for WeKnora's two-pass parent-child splitter.

The actual splitting algorithm lives in :mod:`weknora_splitter`, a Python port
of WeKnora's Go chunker.  This adapter only converts Know_RAG's ``ChunkerConfig``
and ``ParsedChunk`` models to/from the WeKnora-compatible runtime structures.

Core behavior copied from WeKnora ``SplitParentChild``:
1. Split the whole document into large parent candidates.
2. Re-split each parent candidate with the child config.
3. Materialize a parent only when real subdivision happened.
4. Child sequence numbers are global across the document.
5. Merge parent/child breadcrumbs without duplicating the seam heading.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.chunker.heading_utils import normalize_context_header
from app.services.chunker.models import ChunkerConfig, ParsedChunk
from app.services.chunker.weknora_splitter import (
    SplitterConfig as WeKnoraSplitterConfig,
    build_parent_child_configs as build_weknora_parent_child_configs,
    merge_breadcrumbs as weknora_merge_breadcrumbs,
    split_parent_child as weknora_split_parent_child,
)


@dataclass
class ParentChildSplitResult:
    parents: list[ParsedChunk]
    children: list[ParsedChunk]


def _base_config(cfg: ChunkerConfig) -> WeKnoraSplitterConfig:
    return WeKnoraSplitterConfig(
        chunk_size=max(cfg.chunk_size or 512, 1),
        chunk_overlap=max(cfg.chunk_overlap or 0, 0),
        separators=list(cfg.separators or ["\n\n", "\n", "。"]),
        strategy=(cfg.strategy or "auto").lower(),
        token_limit=max(cfg.token_limit or 0, 0),
        languages=list(cfg.languages or []),
    )


def merge_breadcrumbs(parent: str, child: str) -> str:
    return normalize_context_header(weknora_merge_breadcrumbs(parent or "", child or ""))


def split_parent_child_result(text: str, cfg: ChunkerConfig) -> ParentChildSplitResult:
    if not text:
        return ParentChildSplitResult(parents=[], children=[])

    base_cfg = _base_config(cfg)
    parent_cfg, child_cfg = build_weknora_parent_child_configs(
        base_cfg,
        parent_chunk_size=max(cfg.parent_chunk_size or 4096, 1),
        child_chunk_size=max(cfg.child_chunk_size or 384, 1),
    )
    result = weknora_split_parent_child(text, parent_cfg, child_cfg)

    parents: list[ParsedChunk] = []
    for parent_index, parent in enumerate(result.parents):
        parents.append(
            ParsedChunk(
                content=parent.content,
                context_header=normalize_context_header(parent.context_header),
                seq=parent_index,
                start=parent.start,
                end=parent.end,
                metadata={
                    "role": "parent",
                    "seq": parent_index,
                    "strategy": cfg.strategy,
                    "token_limit": cfg.token_limit,
                    "languages": list(cfg.languages or []),
                    "separators": list(cfg.separators or []),
                    "weknora_parent_child": True,
                    "weknora_runtime_port": True,
                },
                chunk_type="parent_text",
            )
        )

    children: list[ParsedChunk] = []
    for child in result.children:
        parent_index = child.parent_index if child.parent_index >= 0 else None
        children.append(
            ParsedChunk(
                content=child.content,
                context_header=normalize_context_header(child.context_header),
                seq=child.seq,
                start=child.start,
                end=child.end,
                metadata={
                    "role": "child",
                    "parent_seq": parent_index,
                    "seq": child.seq,
                    "strategy": cfg.strategy,
                    "token_limit": cfg.token_limit,
                    "languages": list(cfg.languages or []),
                    "separators": list(cfg.separators or []),
                    "weknora_parent_child": True,
                    "weknora_runtime_port": True,
                    "parent_materialized": parent_index is not None,
                },
                chunk_type="text",
                parent_index=parent_index,
            )
        )

    return ParentChildSplitResult(parents=parents, children=children)


def split_parent_child(text: str, cfg: ChunkerConfig) -> list[ParsedChunk]:
    """Backward-compatible flat output used by ``split_document``."""
    result = split_parent_child_result(text, cfg)
    return [*result.parents, *result.children]
