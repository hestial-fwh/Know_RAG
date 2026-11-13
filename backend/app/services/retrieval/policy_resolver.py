"""纯策略解析：请求 > 知识库 > 默认；不访问索引或触发构建。"""

from dataclasses import dataclass

from pydantic import TypeAdapter

from app.schemas.retrieval_strategy import RetrievalProfile, RetrievalStrategy, TieredStrategy


@dataclass
class RetrievalPlan:
    payload: dict
    requested: dict
    executed: dict
    degraded: bool = False
    reason: str = ""
    legacy: bool = False
    routing: dict | None = None

    def diagnostics(self) -> dict:
        result = {
            "contract": "legacy_v1" if self.legacy else "new_profile_v2",
            "requested_strategy": self.requested,
            "executed_strategy": self.executed,
            "degraded": self.degraded,
            "degradation_reason": self.reason or None,
        }
        if self.routing is not None:
            result["routing"] = self.routing
        return result


def resolve_policy(payload: dict, profile: dict | None = None, capabilities: dict | None = None) -> RetrievalPlan:
    """解析选择并拒绝冲突；尚未实现的图策略只能显式回退或失败。"""
    normalized = {key: value for key, value in payload.items() if value is not None}
    kb_profile = RetrievalProfile.model_validate(profile or {"version": "legacy_v1"})
    raw = normalized.get("strategy")
    if raw is None and kb_profile.version == "legacy_v1":
        # 旧 MCP 曾无条件发送 hybrid；该字符串历史上未参与执行，必须保持此行为。
        return RetrievalPlan(normalized, {"family": "legacy"}, {"family": "legacy"}, legacy=True)

    strategy = (
        TypeAdapter(RetrievalStrategy).validate_python(raw)
        if raw is not None else kb_profile.strategy
    )
    requested = strategy.model_dump(exclude_none=True)
    routing = None
    if isinstance(strategy, TieredStrategy) and strategy.tier == "auto":
        from app.services.retrieval.auto_router import route_auto

        if any(key in normalized for key in ("retrieval_mode", "use_vector", "use_keyword", "disable_vector_match", "disable_keywords_match")):
            raise ValueError("自动策略不能同时指定底层召回开关，请只传strategy")
        strategy, routing = route_auto(str(normalized.get("query_text") or normalized.get("query") or ""), strategy, capabilities or {})
    executed = strategy.model_dump(exclude_none=True)
    # 四条 v2 路线共用基础召回预算；降级时也保留请求预算，不再只给关联模式使用。
    normalized["_candidate_k"] = strategy.budget.candidate_k
    alias = normalized.get("retrieval_mode")
    expected_alias = (
        "vector" if isinstance(strategy, TieredStrategy) and strategy.tier == "semantic"
        else "hybrid" if isinstance(strategy, TieredStrategy) and strategy.tier == "hybrid"
        else None
    )
    if alias is not None and alias != expected_alias:
        raise ValueError("retrieval_mode 与新策略冲突；请只传 strategy")

    semantic = isinstance(strategy, TieredStrategy) and strategy.tier == "semantic"
    expected = {
        "use_vector": True, "use_keyword": not semantic,
        "disable_vector_match": False, "disable_keywords_match": semantic,
    }
    for key, value in expected.items():
        if key in normalized and normalized[key] != value:
            raise ValueError(f"{key} 与检索策略冲突")
    normalized.update(expected)

    if isinstance(strategy, TieredStrategy) and strategy.tier == "fusion":
        # fusion 只组合当前就绪通道；基础通道已在上面强制开启。
        ready = capabilities or {}
        kinds = [kind for kind in ("relations", "events") if ready.get(kind)]
        if strategy.fusion.event_policy == "off":
            kinds = [kind for kind in kinds if kind != "events"]
        if not kinds:
            reason = "关系/事件索引均未就绪或事件通道已关闭，已回退混合检索"
            if strategy.fallback_policy == "error":
                raise ValueError("fusion 需要 relations 或 events 索引")
            executed = TieredStrategy(budget=strategy.budget).model_dump(exclude_none=True)
            return RetrievalPlan(normalized, requested, executed, True, reason, routing=routing)
        normalized["_association_kinds"] = kinds
        normalized["_association_budget"] = strategy.budget.model_dump()
        normalized["_fusion_settings"] = strategy.fusion.model_dump()
        if strategy.fusion.include_global and ready.get("global"):
            normalized["_global_background"] = True
        return RetrievalPlan(normalized, requested, executed, routing=routing)

    unavailable = strategy.family == "event_graph" or (
        isinstance(strategy, TieredStrategy) and strategy.tier == "graph"
    )
    if unavailable:
        if strategy.family == "tiered" and strategy.graph_method in {"global", "drift"}:
            method = strategy.graph_method
            ready = (capabilities or {}).get("global") and (method == "global" or (capabilities or {}).get("relations"))
            if ready:
                normalized[f"_{method}_search"] = True
                normalized[f"_{method}_budget"] = strategy.budget.model_dump()
                return RetrievalPlan(normalized, requested, executed, routing=routing)
            reason = "社区报告未就绪" if method == "global" else "DRIFT需要有效社区报告和Local关系索引"
            if strategy.fallback_policy == "error":
                raise ValueError(reason)
            executed = TieredStrategy(budget=strategy.budget).model_dump(exclude_none=True)
            return RetrievalPlan(normalized, requested, executed, True, reason, routing=routing)
        kind = "events" if strategy.family == "event_graph" else "relations"
        if (capabilities or {}).get(kind):
            normalized["_association_kind"] = kind
            normalized["_association_budget"] = strategy.budget.model_dump()
            return RetrievalPlan(normalized, requested, executed, routing=routing)
        reason = "事件索引未就绪" if kind == "events" else "Local 关系索引未就绪"
        if strategy.fallback_policy == "error":
            raise ValueError(reason)
        executed = TieredStrategy(budget=strategy.budget).model_dump(exclude_none=True)
        return RetrievalPlan(normalized, requested, executed, True, reason, routing=routing)
    return RetrievalPlan(normalized, requested, executed, routing=routing)
