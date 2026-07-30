from __future__ import annotations

from collections import Counter
from typing import Any

from app.services.chunker.canonical_parent_child import (
    CanonicalChunkPlan,
    CanonicalChildDraft,
    CanonicalParentDraft,
)


POLICY_VERSION = "dcp3.1-parent-policy-v1"


def _page_no(child: CanonicalChildDraft) -> int | None:
    metadata = child.metadata or {}
    for key in ("visual_item_page_no", "page_no", "chunk_policy_page_no", "visual_page_no"):
        try:
            value = int(metadata.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return None


def _visual_maps(
    visual_layout: dict[str, Any],
) -> tuple[dict[int, set[str]], dict[str, str], dict[str, str]]:
    items_by_page: dict[int, set[str]] = {}
    asset_to_item: dict[str, str] = {}
    node_to_item: dict[str, str] = {}
    for page in visual_layout.get("pages") or []:
        if not isinstance(page, dict):
            continue
        try:
            page_no = int(page.get("page_no") or 0)
        except (TypeError, ValueError):
            continue
        for item in page.get("visual_items") or []:
            item_id = str(item.get("visual_item_id") or "")
            if not item_id:
                continue
            items_by_page.setdefault(page_no, set()).add(item_id)
            for asset_id in item.get("asset_ids") or []:
                asset_to_item[str(asset_id)] = item_id
        for relation in page.get("text_relations") or []:
            item_ids = [
                str(value)
                for value in relation.get("visual_item_ids") or []
                if value
            ]
            if relation.get("status") != "bound" or len(item_ids) != 1:
                continue
            node_id = str(relation.get("source_node_id") or "")
            if node_id:
                node_to_item[node_id] = item_ids[0]
    return items_by_page, asset_to_item, node_to_item


def _child_visual_item(
    child: CanonicalChildDraft,
    *,
    asset_to_item: dict[str, str],
    node_to_item: dict[str, str],
) -> str:
    metadata = child.metadata or {}
    asset_id = str(metadata.get("asset_id") or "")
    if asset_id and asset_id in asset_to_item:
        return asset_to_item[asset_id]
    item_ids = {
        node_to_item[str(node_id)]
        for node_id in metadata.get("source_node_ids") or []
        if str(node_id) in node_to_item
    }
    return next(iter(item_ids)) if len(item_ids) == 1 else ""


def _combined_content(group: list[CanonicalChildDraft]) -> str:
    return "\n\n".join(
        child.content.strip()
        for child in sorted(
            group,
            key=lambda item: (item.start, item.end, item.source_child_seq),
        )
        if child.content.strip()
    )


def apply_visual_item_parent_policy(
    plan: CanonicalChunkPlan,
    visual_layout: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    """Rebuild parents only where one page contains multiple VisualItems.

    Legacy visual-section parents may absorb several artworks or bind pure image
    pages across page boundaries. DCP-3.1 gives each resolved VisualItem an
    authoritative parent and leaves ambiguous/unbound text standalone.
    """

    summary = {
        "algorithm_version": POLICY_VERSION,
        "enabled": bool(enabled),
        "applied": False,
        "affected_pages": [],
        "visual_item_parent_count": 0,
        "page_overview_parent_count": 0,
        "ambiguous_standalone_count": 0,
    }
    if not enabled:
        return summary
    items_by_page, asset_to_item, node_to_item = _visual_maps(visual_layout)
    affected_pages = {
        page_no for page_no, item_ids in items_by_page.items() if len(item_ids) > 1
    }
    if not affected_pages:
        return summary

    old_parents = {parent.parent_index: parent for parent in plan.parents}
    old_counts = Counter(
        child.parent_index
        for child in plan.children
        if child.parent_index is not None
    )
    groups: dict[tuple[Any, ...], list[CanonicalChildDraft]] = {}
    standalone: list[CanonicalChildDraft] = []

    for child in plan.children:
        page_no = _page_no(child)
        if page_no not in affected_pages:
            if child.parent_index is None:
                standalone.append(child)
            else:
                groups.setdefault(("legacy", child.parent_index), []).append(child)
            continue
        item_id = _child_visual_item(
            child,
            asset_to_item=asset_to_item,
            node_to_item=node_to_item,
        )
        if item_id:
            groups.setdefault(("visual_item", page_no, item_id), []).append(child)
            continue
        child.metadata["parent_assignment_state"] = "visual_relation_ambiguous"
        child.metadata["parent_policy"] = {
            "policy_id": "parent-page-overview-v1",
            "strategy": "page_overview_ambiguous",
            "algorithm_version": POLICY_VERSION,
            "materialize": True,
        }
        child.flags.pop("standalone_root_child", None)
        groups.setdefault(("page_overview", page_no), []).append(child)

    parents: list[CanonicalParentDraft] = []
    for key, group in sorted(
        groups.items(),
        key=lambda item: min(child.start for child in item[1]),
    ):
        kind = key[0]
        parent_index = len(parents)
        group = sorted(
            group,
            key=lambda child: (child.start, child.end, child.source_child_seq),
        )
        if kind == "legacy":
            old_index = int(key[1])
            old_parent = old_parents.get(old_index)
            unchanged = (
                old_parent is not None and len(group) == old_counts.get(old_index, 0)
            )
            content = old_parent.content if unchanged else _combined_content(group)
            context_header = (
                old_parent.context_header
                if old_parent is not None
                else next((child.context_header for child in group if child.context_header), "")
            )
            metadata = dict(old_parent.metadata or {}) if old_parent is not None else {}
            metadata["children_count_planned"] = len(group)
            if not unchanged:
                metadata["parent_materialization_rule"] = (
                    "dcp3.1_legacy_parent_after_visual_item_extraction"
                )
                metadata["dcp31_remainder_parent"] = True
                metadata["parent_policy"] = {
                    "policy_id": "parent-dcp31-remainder-v1",
                    "strategy": "legacy_remainder",
                    "algorithm_version": POLICY_VERSION,
                    "materialize": True,
                }
        elif kind == "visual_item":
            page_no = int(key[1])
            item_id = str(key[2])
            content = _combined_content(group)
            context_header = next(
                (child.context_header for child in group if child.context_header),
                "",
            )
            metadata = {
                "canonical_markdown": True,
                "chunk_role": "parent",
                "children_count_planned": len(group),
                "visual_item_id": item_id,
                "visual_item_page_no": page_no,
                "visual_item_scoped_parent": True,
                "parent_materialization_rule": "dcp3.1_visual_item_boundary",
                "parent_policy": {
                    "policy_id": "parent-visual-item-v1",
                    "strategy": "visual_item",
                    "algorithm_version": POLICY_VERSION,
                },
                "runtime_postprocess_policy": "structure_first",
            }
            summary["visual_item_parent_count"] += 1
        else:
            page_no = int(key[1])
            content = _combined_content(group)
            context_header = next(
                (child.context_header for child in group if child.context_header),
                f"第 {page_no} 页内容",
            )
            metadata = {
                "canonical_markdown": True,
                "chunk_role": "parent",
                "children_count_planned": len(group),
                "visual_item_page_no": page_no,
                "visual_page_scoped_parent": True,
                "dcp31_page_overview_parent": True,
                "parent_materialization_rule": "dcp3.1_page_overview_ambiguous",
                "parent_policy": {
                    "policy_id": "parent-page-overview-v1",
                    "strategy": "page_overview_ambiguous",
                    "algorithm_version": POLICY_VERSION,
                    "materialize": True,
                },
                "runtime_postprocess_policy": "structure_first",
            }
            summary["page_overview_parent_count"] += 1

        for child in group:
            child.parent_index = parent_index
            child.metadata["parent_materialized"] = True
            child.flags["parent_materialized"] = True
            if kind == "visual_item":
                child.metadata["visual_item_scoped_parent"] = True
                child.metadata["parent_materialization_rule"] = (
                    "dcp3.1_visual_item_boundary"
                )
                child.metadata["parent_policy"] = dict(metadata["parent_policy"])
                child.flags["visual_item_scoped_parent"] = True
            elif kind == "page_overview":
                child.metadata["visual_page_scoped_parent"] = True
                child.metadata["dcp31_page_overview_parent"] = True
                child.metadata["parent_materialization_rule"] = (
                    "dcp3.1_page_overview_ambiguous"
                )
                child.metadata["parent_policy"] = dict(metadata["parent_policy"])
                child.flags["visual_page_scoped_parent"] = True
                child.flags["dcp31_page_overview_parent"] = True
        parents.append(
            CanonicalParentDraft(
                parent_index=parent_index,
                content=content,
                context_header=context_header,
                start=min(child.start for child in group),
                end=max(child.end for child in group),
                metadata=metadata,
            )
        )

    plan.parents = parents
    summary.update(
        {
            "applied": True,
            "affected_pages": sorted(affected_pages),
            "ambiguous_standalone_count": sum(
                child.metadata.get("parent_assignment_state")
                == "visual_relation_ambiguous"
                for child in standalone
            ),
        }
    )
    return summary


__all__ = ["POLICY_VERSION", "apply_visual_item_parent_policy"]
