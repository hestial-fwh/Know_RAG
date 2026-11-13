"""P5家族内规则路由，意图不明确时采用混合检索，并记录可审计理由。"""

import re

from app.schemas.retrieval_strategy import EventStrategy, FusionSettings, TieredStrategy

RELATION_INTENT = re.compile(
    r"关系|合作|关联|比较|对比|影响|路径|relation|compare|related", re.I,
)
EVENT_INTENT = re.compile(
    r"事件|签约|签订|举办|会议|参会|比赛|参与者|时间线|发生|共建|timeline|event", re.I,
)


def route_auto(query: str, strategy: TieredStrategy, capabilities: dict):
    """只选择已就绪路线；事件与关系同时命中时进入 fusion，覆盖不足时负向回 hybrid。"""
    query_text = query.strip()
    capabilities = capabilities or {}
    relations = bool(capabilities.get("relations"))
    events = bool(capabilities.get("events"))
    entity_matches = int(capabilities.get("entity_matches") or 0)
    tier, method, intent = "hybrid", None, "general"
    reason = "意图不明确，采用混合检索"
    fusion = FusionSettings()
    family_strategy = None

    if len(query_text) < 4:
        reason = "查询过短，信息不足以进入图扩展，采用混合检索"
    elif re.search(r"所有|全部|总计|总数|多少|计数|how many|\btotal\b", query, re.I):
        intent, reason = "aggregation", "全量问题需要范围完整性与确定性聚合，返回混合候选并标记覆盖不足"
    elif re.search(r"深入|探索|追问|多角度|explore|investigate", query, re.I):
        intent = "exploration"
        if capabilities.get("global") and relations and strategy.budget.max_candidates >= 3:
            tier, method, reason = "graph", "drift", "社区与关系可用，按共享预算探索子问题"
        else:
            reason = "探索所需社区、关系或候选预算不足，采用混合检索"
    elif re.search(r"全库|整体|主要主题|共性|总体趋势|概览|overview|common themes", query, re.I):
        intent = "overview"
        if capabilities.get("global") and relations:
            tier, reason = "fusion", "主题综合且社区与关系索引可用，采用融合检索并附加背景"
            fusion = FusionSettings(include_global=True)
        elif capabilities.get("global"):
            tier, method, reason = "graph", "global", "主题综合且社区报告可用"
        else:
            reason = "社区报告未就绪，采用混合检索"
    elif RELATION_INTENT.search(query) or EVENT_INTENT.search(query):
        relation_intent = bool(RELATION_INTENT.search(query))
        event_intent = bool(EVENT_INTENT.search(query))
        relation_ready = relations and entity_matches > 0
        coverage_ok = not capabilities.get("coverage_incomplete")
        if relation_intent and event_intent:
            intent = "fusion"
            if relations and events and relation_ready and coverage_ok:
                tier, reason = "fusion", "问题同时涉及关系与事件，关系/事件索引均可用"
                fusion = FusionSettings(event_policy="independent")
            elif relation_ready and coverage_ok:
                tier, method, reason = "graph", "local", "事件索引未就绪，退回关系邻域扩展"
            elif events and coverage_ok:
                family_strategy = EventStrategy(
                    family="event_graph", fallback_policy=strategy.fallback_policy,
                    budget=strategy.budget,
                )
                reason = "关系链接不足，仅使用事件—实体关联"
            else:
                reason = "关系与事件均未就绪或覆盖不足，采用混合检索"
        elif relation_intent:
            intent = "relationship"
            if relation_ready and coverage_ok:
                tier, method, reason = "graph", "local", "问题涉及关系并链接到当前有效实体"
            else:
                reason = "缺少有效实体链接、关系索引或覆盖不足，采用混合检索"
        else:
            intent = "event"
            if events and coverage_ok:
                family_strategy = EventStrategy(
                    family="event_graph", fallback_policy=strategy.fallback_policy,
                    budget=strategy.budget,
                )
                reason = "问题涉及事件且事件—实体索引可用"
            else:
                reason = "事件索引未就绪，采用混合检索"
    elif re.search(r"含义|定义|什么意思|是什么|概念|what is|meaning of", query, re.I):
        intent = "definition"
        if not re.search(r"\d|[\"“”「」]|\b[A-Z]{2,}\b", query):
            tier, reason = "semantic", "概念释义问题采用语义检索"
        else:
            reason = "释义问题含编号或精确术语，保留关键词通道"

    if family_strategy is None:
        family_strategy = TieredStrategy(
            tier=tier, graph_method=method,
            fusion=fusion,
            fallback_policy=strategy.fallback_policy, budget=strategy.budget,
        )
    return family_strategy, {
        "provider": "know_rag_rules_v2", "intent": intent, "reason": reason,
        "family": family_strategy.family, "capabilities": capabilities.copy(), "model_calls": 0,
        "chosen_strategy": family_strategy.model_dump(exclude_none=True),
        "aggregation_supported": False, "coverage_incomplete": intent == "aggregation",
    }