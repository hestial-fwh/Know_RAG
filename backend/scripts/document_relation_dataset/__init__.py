"""Deterministic Phase 0 document-relation dataset generator."""

from .generator import DATASET_TYPES, SCENARIOS, generate_dataset
from .report import build_coverage_report

__all__ = ["DATASET_TYPES", "SCENARIOS", "build_coverage_report", "generate_dataset"]

