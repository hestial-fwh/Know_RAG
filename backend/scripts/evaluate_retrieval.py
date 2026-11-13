"""同题/同预算检索评测，读取人工标注JSONL，记录证据指标、实际路线、耗时与调用数。

在项目环境运行：python scripts/evaluate_retrieval.py --cases cases.jsonl --output result.json
登录令牌通过KNOW_RAG_EVAL_TOKEN环境变量提供，结果文件仅保存指标与诊断。
每行包含id、knowledge_base_id、query、relevant_chunk_ids，可另加required_chunk_ids与no_answer。

默认对比 hybrid、local（基础+关系）、event（基础+事件）、fusion（基础+关系+事件）、
fusion_global（fusion+背景）与 auto；fusion_rel 可用 --strategies 显式加入，用于隔离事件通道。
事件/社区未建索引时，对应通道会被跳过，结果里会保留 channel_status，不能据此判断该通道质量。
"""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from urllib.parse import urlsplit

import httpx

# 与现有诊断脚本一致，直接运行scripts路径时也能导入同一检出的app。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas.retrieval_strategy import AssociationBudget


# 同题对照的固定策略表；fusion_* 只改组合方式，预算由 AssociationBudget 统一固定。
STRATEGIES = {
    "semantic": {"tier": "semantic"},
    "hybrid": {"tier": "hybrid"},
    "local": {"tier": "graph", "graph_method": "local"},
    "global": {"tier": "graph", "graph_method": "global"},
    "drift": {"tier": "graph", "graph_method": "drift"},
    "event": {"family": "event_graph"},
    "fusion": {"tier": "fusion"},
    "fusion_rel": {"tier": "fusion", "fusion": {"event_policy": "off"}},
    "fusion_global": {"tier": "fusion", "fusion": {"include_global": True}},
    "auto": {"tier": "auto"},
}


def evidence_metrics(returned_ids, relevant_ids, required_ids=()):
    """去重后按实际返回名次计算二元相关nDCG与完整证据集合覆盖。"""
    returned = list(dict.fromkeys(returned_ids))
    relevant, required = set(relevant_ids), set(required_ids)
    hits = set(returned) & relevant
    dcg = sum(1 / math.log2(rank + 2) for rank, item in enumerate(returned) if item in relevant)
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(len(relevant), len(returned))))
    return {"precision": len(hits) / len(returned) if returned else 0.0,
        "recall": len(hits) / len(relevant) if relevant else None,
        "ndcg": dcg / ideal if ideal else 0.0,
        "required_evidence_complete": required <= set(returned) if required else None}


def summarize(rows):
    """错误请求仍进入总样本数与质量分母，避免只平均成功请求产生虚高收益。"""
    summary = {}
    for name in dict.fromkeys(row["strategy"] for row in rows):
        group = [row for row in rows if row["strategy"] == name]
        timings = sorted(row["elapsed_ms"] for row in group)
        metrics, context_metrics = {}, {}
        for metric in ("precision", "recall", "ndcg", "required_evidence_complete"):
            values = [row["metrics"][metric] for row in group if row["metrics"][metric] is not None]
            metrics[metric] = sum(values) / len(values) if values else None
            # 上下文裁剪后的指标单独统计，避免把“召回得到但没进上下文”算成可用证据。
            context_values = [(row.get("context_metrics") or {}).get(metric) for row in group
                              if (row.get("context_metrics") or {}).get(metric) is not None]
            context_metrics[metric] = sum(context_values) / len(context_values) if context_values else None
        channels, degraded, coverage_incomplete = {}, 0, 0
        for row in group:
            info = row.get("retrieval") or {}
            degraded += bool(info.get("degraded"))
            fusion = info.get("fusion") or {}
            coverage_incomplete += bool(fusion.get("coverage_incomplete"))
            # 汇总实际执行通道，避免事件/社区未就绪时把“跳过”误读成“无贡献”。
            for channel in fusion.get("executed") or info.get("channels") or []:
                channels[channel] = channels.get(channel, 0) + 1
        summary[name] = {"cases": len(group), "successful": sum(row["success"] for row in group),
            "metrics": metrics, "context_metrics": context_metrics,
            "p50_ms": timings[math.ceil(.5 * len(timings)) - 1],
            "p95_ms": timings[math.ceil(.95 * len(timings)) - 1],
            "model_calls": sum(row.get("model_calls") or 0 for row in group),
            "unknown_cost_cases": sum(row.get("model_calls") is None for row in group),
            "channel_executions": channels, "degraded_cases": degraded,
            "coverage_incomplete_cases": coverage_incomplete}
    return summary


def main():
    parser = argparse.ArgumentParser(description="人工标注证据的同题检索评测")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--strategies", default="hybrid,local,event,fusion,fusion_global,auto")
    parser.add_argument("--top-k", type=int, default=5, choices=range(1, 101))
    parser.add_argument("--context-chars", type=int, default=8000)
    args = parser.parse_args()
    if urlsplit(args.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("评测入口须为本机API")
    token = os.environ.get("KNOW_RAG_EVAL_TOKEN", "")
    if not token:
        parser.error("请通过KNOW_RAG_EVAL_TOKEN提供已有登录令牌")
    names = args.strategies.split(",")
    if not set(names) <= STRATEGIES.keys():
        parser.error("strategies包含未知策略")
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases:
        parser.error("标注文件不能为空")
    for case in cases:
        if not all(case.get(key) for key in ("id", "knowledge_base_id", "query")):
            parser.error("每条标注须包含id、knowledge_base_id和query")
        if not case.get("relevant_chunk_ids") and not case.get("no_answer"):
            parser.error("每条标注须有relevant_chunk_ids或显式no_answer=true")
        if any(not isinstance(case.get(key, []), list) or any(not isinstance(item, str) or not item for item in case.get(key, []))
               for key in ("relevant_chunk_ids", "required_chunk_ids")):
            parser.error("证据ID必须为非空字符串列表")
    budget = AssociationBudget().model_dump()
    rows = []
    with httpx.Client(base_url=args.base_url, headers={"Authorization": "Bearer " + token},
                      timeout=190, follow_redirects=False, trust_env=False) as client:
        for case in cases:
            for name in names:
                strategy = {"family": "tiered", **STRATEGIES[name], "fallback_policy": "error", "budget": budget}
                started = perf_counter()
                data, success, failure = {}, False, ""
                try:
                    result = client.post(f'/api/v1/knowledge-bases/{case["knowledge_base_id"]}/llm-context', json={
                        "query_text": case["query"], "strategy": strategy, "top_k": args.top_k,
                        "return_mode": "debug", "max_context_chars": args.context_chars,
                        "include_content": True, "rerank_enabled": False,
                    })
                    result.raise_for_status()
                    payload = result.json()
                    success = payload.get("success", False)
                    data = payload.get("data") or {}
                    failure = "" if success else payload.get("message", "查询失败")
                except (httpx.HTTPError, ValueError) as exc:
                    failure = type(exc).__name__
                # debug同时提供原始召回名次和裁剪后Context，分别计算，避免分组顺序扭曲nDCG。
                raw_results = data.get("retrieval_results", {}).get("results", [])
                ids = list(dict.fromkeys(chunk["chunk_id"] for chunk in raw_results))
                context_ids = list(dict.fromkeys(chunk["chunk_id"] for chunk in data.get("chunks", [])))
                metrics = evidence_metrics(ids, case.get("relevant_chunk_ids", []), case.get("required_chunk_ids", []))
                info = data.get("meta", {}).get("retrieval", {})
                rows.append({"case_id": case["id"], "strategy": name, "success": success,
                    "error": failure, "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                    "returned_ids": ids, "context_ids": context_ids, "metrics": metrics, "retrieval": info,
                    "context_metrics": evidence_metrics(context_ids, case.get("relevant_chunk_ids", []), case.get("required_chunk_ids", [])),
                    "no_answer_with_candidates": bool(ids) if case.get("no_answer") else None,
                    "model_calls": (info.get("drift") or info.get("global") or {}).get("model_calls", 0) if success else None})
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "top_k": args.top_k,
        "max_context_chars": args.context_chars, "budget": budget, "summary": summarize(rows), "rows": rows}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
