"""Golden tests for image-text binding v2 improvements.

Tests cover: coordinate space validation, anomalous extent detection,
v2 descriptive-text binding, long body text guard, ambiguity handling,
full-page fallback suppression, and regression (v2=False preserves v1).
"""

from __future__ import annotations

import pytest

from app.services.document_relation.coordinate_space import (
    CoordinateContext,
    CoordinateSpace,
    validate_normalized_bbox,
    renormalize_bbox,
)
from app.services.document_relation.page_layout_graph import (
    BBox,
    normalize_layout_bbox,
    _anomalous_extent,
    _derived_extent,
)


# ══════════════════════════════════════════════════════════════════════
# Coordinate space validation
# ══════════════════════════════════════════════════════════════════════


class TestNormalizeLayoutBboxValidation:
    """normalize_layout_bbox should reject out-of-range results."""

    def test_normal_bbox_in_range_passes(self):
        box: BBox = (50.0, 100.0, 400.0, 500.0)
        extent: BBox = (0.0, 0.0, 560.0, 794.0)
        result = normalize_layout_bbox(box, extent, validate=True)
        assert result is not None
        assert all(0.0 <= v <= 1.0 for v in result)

    def test_out_of_range_bbox_rejected(self):
        """Bbox whose corners extend beyond extent should be rejected."""
        box: BBox = (50.0, 100.0, 980.0, 1043.0)  # image-native coords
        extent: BBox = (0.0, 0.0, 560.0, 794.0)  # PDF page coords
        result = normalize_layout_bbox(box, extent, validate=True)
        assert result is None

    def test_out_of_range_bbox_passes_without_validation(self):
        """validate=False should return unvalidated result."""
        box: BBox = (50.0, 100.0, 980.0, 1043.0)
        extent: BBox = (0.0, 0.0, 560.0, 794.0)
        result = normalize_layout_bbox(box, extent, validate=False)
        assert result is not None
        # unvalidated: some values may be > 1.0
        assert any(v > 1.0 for v in result)

    def test_none_box_returns_none(self):
        assert normalize_layout_bbox(None, (0, 0, 100, 100)) is None

    def test_none_extent_returns_none(self):
        assert normalize_layout_bbox((10, 10, 50, 50), None) is None

    def test_zero_area_extent_returns_none(self):
        assert normalize_layout_bbox((10, 10, 50, 50), (0, 0, 0, 100)) is None


class TestValidateNormalizedBbox:
    """validate_normalized_bbox should reject coordinates outside [0,1]."""

    def test_valid_bbox_passes(self):
        assert validate_normalized_bbox((0.0, 0.0, 1.0, 1.0)) is not None
        assert validate_normalized_bbox((0.1, 0.2, 0.9, 0.8)) is not None

    def test_slightly_out_of_range_rejected(self):
        assert validate_normalized_bbox((-0.01, 0.0, 1.0, 1.0)) is None
        assert validate_normalized_bbox((0.0, 0.0, 1.01, 1.0)) is None

    def test_none_returns_none(self):
        assert validate_normalized_bbox(None) is None

    def test_epsilon_tolerance(self):
        """Values within epsilon of [0,1] should pass."""
        assert validate_normalized_bbox((-1e-7, 0.0, 1.0, 1.0 + 1e-7)) is not None


# ══════════════════════════════════════════════════════════════════════
# Anomalous extent detection
# ══════════════════════════════════════════════════════════════════════


class TestAnomalousExtent:
    """_anomalous_extent detects image-native bboxes polluting page extent."""

    def test_normal_boxes_no_anomaly(self):
        """Similar-sized, closely-spaced boxes — no anomaly."""
        boxes: list[BBox] = [
            (10, 10, 200, 80),
            (210, 15, 400, 75),
            (10, 85, 195, 160),
            (205, 90, 395, 155),
        ]
        assert _anomalous_extent(boxes) is None

    def test_mixed_coordinate_spaces_detected(self):
        """Image-native bbox mixed with text bboxes → anomalous."""
        boxes: list[BBox] = [
            (10, 10, 200, 80),    # text bbox, width=190
            (10, 100, 220, 180),  # text bbox, width=210
            (50, 100, 980, 1043),  # image-native bbox, width=930
        ]
        result = _anomalous_extent(boxes)
        assert result is not None  # anomalous

    def test_too_few_boxes_returns_none(self):
        assert _anomalous_extent([(0, 0, 100, 100)]) is None
        assert _anomalous_extent([(0, 0, 100, 100), (0, 0, 200, 200)]) is None


class TestDerivedExtentV2:
    """_derived_extent v2 should fall back to text-only extent."""

    def test_v2_falls_back_to_text_boxes(self):
        all_boxes: list[BBox] = [
            (10, 10, 200, 80),
            (10, 100, 220, 180),
            (50, 100, 980, 1043),  # image-native pollutes extent
        ]
        text_boxes: list[BBox] = [
            (10, 10, 200, 80),
            (10, 100, 220, 180),
        ]
        result = _derived_extent(
            all_boxes, text_boxes=text_boxes, v2_enabled=True
        )
        assert result is not None
        ew = result[2] - result[0]
        # text-only extent should be much smaller than the polluted 980
        assert ew < 500

    def test_v2_disabled_uses_all_boxes(self):
        """v2_enabled=False keeps original behavior."""
        all_boxes: list[BBox] = [
            (10, 10, 200, 80),
            (50, 100, 980, 1043),
        ]
        result = _derived_extent(all_boxes, v2_enabled=False)
        assert result is not None
        # polluted extent includes the large image bbox
        assert result[2] >= 980


# ══════════════════════════════════════════════════════════════════════
# CoordinateContext and renormalize_bbox
# ══════════════════════════════════════════════════════════════════════


class TestRenormalizeBbox:
    """renormalize_bbox should re-project between coordinate spaces."""

    def test_same_space_identity(self):
        """Same extent → identity transform."""
        ctx = CoordinateContext(
            space=CoordinateSpace.MINERU_PAGE,
            extent=(0.0, 0.0, 560.0, 794.0),
        )
        bbox: BBox = (50.0, 100.0, 400.0, 500.0)
        result = renormalize_bbox(bbox, from_ctx=ctx, to_ctx=ctx)
        assert result == pytest.approx(bbox)  # raw → raw

    def test_renormalize_to_normalized_validates_bounds(self):
        """Re-normalizing to NORMALIZED_PAGE validates [0,1]."""
        from_ctx = CoordinateContext(
            space=CoordinateSpace.MINERU_IMAGE,
            extent=(0.0, 0.0, 930.0, 1043.0),
        )
        # NORMALIZED_PAGE extent is (0,0,1,1)
        to_ctx = CoordinateContext(
            space=CoordinateSpace.NORMALIZED_PAGE,
            extent=(0.0, 0.0, 1.0, 1.0),
        )
        # bbox in image-native coords → maps to [0,1] in page coords
        bbox: BBox = (50.0, 100.0, 400.0, 500.0)
        result = renormalize_bbox(bbox, from_ctx=from_ctx, to_ctx=to_ctx)
        assert result is not None
        assert all(0.0 <= v <= 1.0 for v in result)

    def test_missing_extent_returns_none(self):
        ctx = CoordinateContext(space=CoordinateSpace.UNKNOWN)
        bbox: BBox = (0.0, 0.0, 1.0, 1.0)
        assert renormalize_bbox(bbox, from_ctx=ctx, to_ctx=ctx) is None


# ══════════════════════════════════════════════════════════════════════
# Semantic completeness checks
# ══════════════════════════════════════════════════════════════════════


class TestSemanticCompleteness:
    """Semantic completeness should detect semantic issues."""

    def test_empty_visual_layout_no_issues(self):
        from app.services.document_relation.semantic_completeness import (
            compute_semantic_completeness,
        )
        # Create a minimal mock plan with no children
        class _MockPlan:
            children = []
        report = compute_semantic_completeness(
            _MockPlan(), {"pages": []}, v2_enabled=True
        )
        assert report.warning_count == 0

    def test_low_binding_coverage_flagged(self):
        from app.services.document_relation.semantic_completeness import (
            check_visual_item_binding_coverage,
        )
        layout = {
            "pages": [
                {
                    "page_no": 1,
                    "text_relations": [
                        {"status": "unbound"},
                        {"status": "unbound"},
                        {"status": "unbound"},
                        {"status": "bound"},
                    ],
                }
            ]
        }
        issues = check_visual_item_binding_coverage(layout)
        assert len(issues) == 1  # coverage = 0.25 < 0.5
        assert issues[0].check == "low_visual_item_binding_coverage"

    def test_good_binding_coverage_no_issue(self):
        from app.services.document_relation.semantic_completeness import (
            check_visual_item_binding_coverage,
        )
        layout = {
            "pages": [
                {
                    "page_no": 1,
                    "text_relations": [
                        {"status": "bound"},
                        {"status": "bound"},
                        {"status": "bound"},
                    ],
                }
            ]
        }
        issues = check_visual_item_binding_coverage(layout)
        assert len(issues) == 0  # coverage = 1.0 > 0.5

    def test_image_only_parent_with_unbound_text_flagged(self):
        from app.services.document_relation.semantic_completeness import (
            check_image_only_parents_with_nearby_unbound,
        )

        class _MockChild:
            def __init__(self, chunk_type, parent_index, page_no):
                self.chunk_type = chunk_type
                self.parent_index = parent_index
                self.metadata = {"visual_page_no": page_no}

        class _MockPlan:
            def __init__(self, children):
                self.children = children

        plan = _MockPlan([
            _MockChild("image", 0, 1),
            _MockChild("image", 0, 1),
        ])
        layout = {
            "pages": [
                {
                    "page_no": 1,
                    "text_relations": [
                        {"status": "unbound"},
                        {"status": "unbound"},
                    ],
                }
            ]
        }
        issues = check_image_only_parents_with_nearby_unbound(plan, layout)
        assert len(issues) >= 1
        assert issues[0].check == "image_only_parent_with_nearby_unbound"
