"""评测工具的指标口径回归，避免错误请求或重复结果抬高分数。"""

from app.schemas.retrieval_strategy import TieredStrategy
from scripts.evaluate_retrieval import STRATEGIES, evidence_metrics, summarize


def test_evidence_metric_deduplicates_and_checks_complete_chain():
    result = evidence_metrics(["a", "a", "noise", "b"], ["a", "b"], ["a", "b"])
    assert result["precision"] == 2 / 3 and result["recall"] == 1
    assert 0 < result["ndcg"] < 1 and result["required_evidence_complete"]


def test_missing_required_source_is_not_a_complete_chain():
    result = evidence_metrics(["a"], ["a", "b"], ["a", "b"])
    assert result["recall"] == .5 and not result["required_evidence_complete"]


def test_fusion_comparison_routes_validate_and_keep_event_policy_isolated():
    for name in ("fusion", "fusion_rel", "fusion_global"):
        strategy = TieredStrategy.model_validate({"family": "tiered", **STRATEGIES[name]})
        assert strategy.tier == "fusion" and strategy.graph_method is None
    assert TieredStrategy.model_validate({"family": "tiered", **STRATEGIES["fusion"]}).fusion.event_policy == "independent"
    assert TieredStrategy.model_validate({"family": "tiered", **STRATEGIES["fusion_rel"]}).fusion.event_policy == "off"
    assert TieredStrategy.model_validate({"family": "tiered", **STRATEGIES["fusion_global"]}).fusion.include_global


def test_errors_are_included_in_quality_denominator():
    rows = [{"strategy": "auto", "success": True, "elapsed_ms": 10,
             "metrics": evidence_metrics(["a"], ["a"]), "model_calls": 0},
            {"strategy": "auto", "success": False, "elapsed_ms": 20,
             "metrics": evidence_metrics([], ["a"]), "model_calls": 0}]
    result = summarize(rows)["auto"]
    assert result["successful"] == 1 and result["cases"] == 2
    assert result["metrics"]["recall"] == .5 and result["p95_ms"] == 20
    assert result["channel_executions"] == {} and result["degraded_cases"] == 0


def test_summary_surfaces_executed_channels_and_degradation():
    rows = [
        {"strategy": "fusion", "success": True, "elapsed_ms": 10, "metrics": evidence_metrics(["a"], ["a"]),
         "model_calls": 0, "retrieval": {"degraded": False, "fusion": {
             "executed": ["vector", "keyword", "relation"], "coverage_incomplete": True}}},
        {"strategy": "fusion", "success": True, "elapsed_ms": 20, "metrics": evidence_metrics(["b"], ["b"]),
         "model_calls": 0, "retrieval": {"degraded": True, "fusion": {
             "executed": ["vector", "keyword"], "coverage_incomplete": False}}},
    ]
    result = summarize(rows)["fusion"]
    assert result["channel_executions"] == {"vector": 2, "keyword": 2, "relation": 1}
    assert result["degraded_cases"] == 1 and result["coverage_incomplete_cases"] == 1
