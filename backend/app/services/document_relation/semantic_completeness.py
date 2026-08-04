"""Semantic completeness diagnostics for image-text binding.

Checks go beyond structural validity (no orphans, valid parent refs)
to flag semantic issues: image-only parents with adjacent unbound text,
multi-work text mixing in one parent, VisualItem binding coverage gaps,
and text-image splits across different parents.

All checks are diagnostic only — they never block chunk creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALGORITHM_VERSION = "semantic-completeness-v1"


@dataclass
class SemanticCompletenessIssue:
    check: str
    severity: str  # "warning" | "info"
    page_no: int | None
    parent_index: int | None
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "page_no": self.page_no,
            "parent_index": self.parent_index,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class SemanticCompletenessReport:
    issues: list[SemanticCompletenessIssue] = field(default_factory=list)
    algorithm_version: str = ALGORITHM_VERSION
    pages_checked: int = 0

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "info")

    def to_metadata(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.algorithm_version,
            "pages_checked": self.pages_checked,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "issues": [i.to_metadata() for i in self.issues],
        }


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------


def _page_no_for_child(child: Any) -> int | None:
    """Extract page number from child metadata."""
    metadata = getattr(child, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return None
    for key in ("visual_page_no", "page_no", "chunk_policy_page_no"):
        try:
            value = int(metadata.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return None


def check_image_only_parents_with_nearby_unbound(
    plan: Any,
    visual_layout: dict[str, Any],
) -> list[SemanticCompletenessIssue]:
    """Flag parents where all children are images but there is adjacent
    unbound text on the same page."""
    issues: list[SemanticCompletenessIssue] = []

    # collect unbound text relations per page
    unbound_per_page: dict[int, int] = {}
    for page in visual_layout.get("pages") or []:
        page_no = page.get("page_no")
        if page_no is None:
            continue
        unbound_count = 0
        for relation in page.get("text_relations") or []:
            if relation.get("status") == "unbound":
                unbound_count += 1
        if unbound_count:
            unbound_per_page[int(page_no)] = unbound_count

    # group children by parent
    children_by_parent: dict[int, list[Any]] = {}
    for child in plan.children:
        parent_idx = getattr(child, "parent_index", None)
        if parent_idx is not None:
            children_by_parent.setdefault(parent_idx, []).append(child)

    for parent_idx, siblings in children_by_parent.items():
        types = {getattr(c, "chunk_type", "") for c in siblings}
        if types != {"image"}:
            continue  # not an image-only parent

        # find a page for this parent
        page_nos = {_page_no_for_child(c) for c in siblings} - {None}
        for page_no in page_nos:
            if page_no in unbound_per_page:
                issues.append(
                    SemanticCompletenessIssue(
                        check="image_only_parent_with_nearby_unbound",
                        severity="warning",
                        page_no=page_no,
                        parent_index=parent_idx,
                        detail=(
                            f"Image-only parent {parent_idx} on page {page_no} "
                            f"has {unbound_per_page[page_no]} unbound text(s) "
                            "on the same page"
                        ),
                        evidence={
                            "image_count": len(siblings),
                            "unbound_text_count": unbound_per_page[page_no],
                        },
                    )
                )
    return issues


def check_visual_item_binding_coverage(
    visual_layout: dict[str, Any],
) -> list[SemanticCompletenessIssue]:
    """Compute per-page ratio of bound text nodes to total text nodes
    near visual items."""
    issues: list[SemanticCompletenessIssue] = []

    for page in visual_layout.get("pages") or []:
        page_no = page.get("page_no")
        if page_no is None:
            continue
        relations = page.get("text_relations") or []
        total = len(relations)
        if total == 0:
            continue
        bound = sum(1 for r in relations if r.get("status") == "bound")
        coverage = bound / total if total > 0 else 0.0
        if coverage < 0.5:
            issues.append(
                SemanticCompletenessIssue(
                    check="low_visual_item_binding_coverage",
                    severity="warning",
                    page_no=int(page_no),
                    parent_index=None,
                    detail=(
                        f"Page {page_no}: {bound}/{total} text nodes bound "
                        f"to visual items (coverage={coverage:.1%})"
                    ),
                    evidence={
                        "bound_count": bound,
                        "total_near_visual_text": total,
                        "coverage": round(coverage, 4),
                    },
                )
            )
    return issues


def check_multi_work_mixing_in_parent(
    plan: Any,
) -> list[SemanticCompletenessIssue]:
    """Detect when text from different section paths appears in the same
    parent — a possible sign that two works' descriptions were merged."""
    issues: list[SemanticCompletenessIssue] = []

    children_by_parent: dict[int, list[Any]] = {}
    for child in plan.children:
        parent_idx = getattr(child, "parent_index", None)
        if parent_idx is not None:
            children_by_parent.setdefault(parent_idx, []).append(child)

    for parent_idx, siblings in children_by_parent.items():
        sections: set[str] = set()
        for child in siblings:
            metadata = getattr(child, "metadata", None) or {}
            if not isinstance(metadata, dict):
                continue
            section = str(metadata.get("section_path") or "").strip()
            if section:
                sections.add(section)
        if len(sections) > 1:
            issues.append(
                SemanticCompletenessIssue(
                    check="multi_work_mixing_in_parent",
                    severity="warning",
                    page_no=_page_no_for_child(siblings[0]) if siblings else None,
                    parent_index=parent_idx,
                    detail=(
                        f"Parent {parent_idx} mixes {len(sections)} distinct "
                        "section paths — possible multi-work merging"
                    ),
                    evidence={
                        "section_count": len(sections),
                        "child_count": len(siblings),
                    },
                )
            )
    return issues


def compute_semantic_completeness(
    plan: Any,
    visual_layout: dict[str, Any],
    *,
    v2_enabled: bool = False,
) -> SemanticCompletenessReport:
    """Run all semantic completeness checks.

    Returns a :class:`SemanticCompletenessReport`.  Does not block chunk
    creation — these are diagnostic metrics only.
    """
    report = SemanticCompletenessReport()
    report.pages_checked = len(visual_layout.get("pages") or [])
    report.issues.extend(
        check_image_only_parents_with_nearby_unbound(plan, visual_layout)
    )
    report.issues.extend(
        check_visual_item_binding_coverage(visual_layout)
    )
    report.issues.extend(
        check_multi_work_mixing_in_parent(plan)
    )
    return report


__all__ = [
    "SemanticCompletenessIssue",
    "SemanticCompletenessReport",
    "check_image_only_parents_with_nearby_unbound",
    "check_multi_work_mixing_in_parent",
    "check_visual_item_binding_coverage",
    "compute_semantic_completeness",
]
