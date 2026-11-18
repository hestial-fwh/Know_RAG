"""P5家族内规则路由；支持外部意图 hint，无 hint 时保持原有规则行为。"""

import re
from dataclasses import dataclass

from app.schemas.retrieval_strategy import EventStrategy, FusionSettings, TieredStrategy

INTENT_LABELS = frozenset(
    {
        "aggregation",
        "exploration",
        "overview",
        "relationship",
        "event",
        "fusion",
        "definition",
        "general",
    }
)

AGGREGATION_INTENT = re.compile(r"所有|全部|总计|总数|多少|计数|how many|\btotal\b", re.I)
EXPLORATION_INTENT = re.compile(r"深入|探索|追问|多角度|explore|investigate", re.I)
OVERVIEW_INTENT = re.compile(r"全库|整体|主要主题|共性|总体趋势|概览|overview|common themes", re.I)
RELATION_INTENT = re.compile(
    r"关系|合作|关联|比较|对比|影响|路径|relation|compare|related", re.I,
)
EVENT_INTENT = re.compile(
    r"事件|签约|签订|举办|会议|参会|比赛|参与者|时间线|发生|共建|timeline|event", re.I,
)
DEFINITION_INTENT = re.compile(r"含义|定义|什么意思|是什么|概念|what is|meaning of", re.I)
# 这些词单独命中时经常把别的意图抢走；由 intent resolver 决定是否交给 Jev。
WEAK_TRIGGER = re.compile(r"是什么时候|联系方式是什么|分别是什么|如果.*发生.*吗", re.I)
DEFINITION_PRECISE = re.compile(r"\d|[\"“”「」]|\b[A-Z]{2,}\b")


@dataclass(frozen=True)
class RuleSignals:
    """规则层能给出的全部信号，不只最终 winner。"""

    matched: frozenset[str]
    winner: str
    weak: bool
    short: bool


def classify_rule_signals(query: str) -> RuleSignals:
    """按原优先级返回 winner，同时保留所有命中的意图家族。"""
    query_text = query.strip()
    if len(query_text) < 4:
        return RuleSignals(frozenset(), "general", False, True)

    matched: list[str] = []
    if AGGREGATION_INTENT.search(query):
        matched.append("aggregation")
    if EXPLORATION_INTENT.search(query):
        matched.append("exploration")
    if OVERVIEW_INTENT.search(query):
        matched.append("overview")
    relation_intent = bool(RELATION_INTENT.search(query))
    event_intent = bool(EVENT_INTENT.search(query))
    if relation_intent:
        matched.append("relationship")
    if event_intent:
        matched.append("event")
    if DEFINITION_INTENT.search(query):
        matched.append("definition")

    if "aggregation" in matched:
        winner = "aggregation"
    elif "exploration" in matched:
        winner = "exploration"
    elif "overview" in matched:
        winner = "overview"
    elif relation_intent and event_intent:
        winner = "fusion"
    elif relation_intent:
        winner = "relationship"
    elif event_intent:
        winner = "event"
    elif "definition" in matched:
        winner = "definition"
    else:
        winner = "general"
    return RuleSignals(frozenset(matched), winner, bool(WEAK_TRIGGER.search(query)), False)


def route_auto(query: str, strategy: TieredStrategy, capabilities: dict):
    """只选择已就绪路线；家族 A 禁用事件，家族 B 禁用关系与社区。

    ``capabilities["intent_hint"]`` 存在且合法时，直接采用外部意图结论；
    否则完全走原有正则优先级，输出与改造前一致。
    """
    query_text = query.strip()
    capabilities = capabilities or {}
    scope = capabilities.get("family_scope")
    scope = scope if scope in {"a", "b"} else "a"
    allow_relations = scope == "a"
    allow_events = scope == "b"
    allow_global = scope == "a"
    relations = bool(capabilities.get("relations"))
    events = bool(capabilities.get("events"))
    entity_matches = int(capabilities.get("entity_matches") or 0)

    signals = classify_rule_signals(query_text)
    # 有 hint 时不再跑正则分类；关系/事件布尔值由 hint 决定，后续策略和家族降级复用原逻辑。
    hint = capabilities.get("intent_hint")
    hint = hint if isinstance(hint, str) and hint in INTENT_LABELS else None
    intent = hint or signals.winner
    source = capabilities.get("intent_source") or ("jev" if hint else "rules")

    tier, method = "hybrid", None
    reason = "意图不明确，采用混合检索"
    fusion = FusionSettings()
    family_strategy = None
    relation_intent = intent in {"relationship", "fusion"}
    event_intent = intent in {"event", "fusion"}

    if signals.short and hint is None:
        reason = "查询过短，信息不足以进入图扩展，采用混合检索"
    elif intent == "aggregation":
        reason = "全量问题需要范围完整性与确定性聚合，返回混合候选并标记覆盖不足"
    elif intent == "exploration":
        if allow_global and capabilities.get("global") and relations and strategy.budget.max_candidates >= 3:
            tier, method, reason = "graph", "drift", "社区与关系可用，按共享预算探索子问题"
        elif not allow_global:
            reason = "事件型知识库未启用社区索引，采用混合检索"
        else:
            reason = "探索所需社区、关系或候选预算不足，采用混合检索"
    elif intent == "overview":
        if allow_global and capabilities.get("global") and relations:
            tier, reason = "fusion", "主题综合且社区与关系索引可用，采用融合检索并附加背景"
            fusion = FusionSettings(include_global=True)
        elif allow_global and capabilities.get("global"):
            tier, method, reason = "graph", "global", "主题综合且社区报告可用"
        elif not allow_global:
            reason = "事件型知识库未启用社区索引，采用混合检索"
        else:
            reason = "社区报告未就绪，采用混合检索"
    elif intent in {"relationship", "event", "fusion"}:
        relation_ready = allow_relations and relations and entity_matches > 0
        event_ready = allow_events and events
        coverage_ok = not capabilities.get("coverage_incomplete")
        if intent == "fusion":
            if relation_ready and event_ready and coverage_ok:
                tier, reason = "fusion", "问题同时涉及关系与事件，关系/事件索引均可用"
                fusion = FusionSettings(event_policy="independent")
            elif relation_ready and coverage_ok:
                tier, method, reason = "graph", "local", "当前家族仅关系通道可用，按关系邻域扩展"
            elif event_ready and coverage_ok:
                family_strategy = EventStrategy(
                    family="event_graph", fallback_policy=strategy.fallback_policy,
                    budget=strategy.budget,
                )
                reason = "当前家族仅事件通道可用，使用事件—实体关联"
            elif not allow_relations and not allow_events:
                reason = "关系与事件索引均不可用，采用混合检索"
            else:
                reason = "关系与事件均未就绪或覆盖不足，采用混合检索"
        elif intent == "relationship":
            if relation_ready and coverage_ok:
                tier, method, reason = "graph", "local", "问题涉及关系并链接到当前有效实体"
            elif not allow_relations:
                reason = "事件型知识库未启用关系索引，采用混合检索"
            else:
                reason = "缺少有效实体链接、关系索引或覆盖不足，采用混合检索"
        else:
            if event_ready and coverage_ok:
                family_strategy = EventStrategy(
                    family="event_graph", fallback_policy=strategy.fallback_policy,
                    budget=strategy.budget,
                )
                reason = "问题涉及事件且事件—实体索引可用"
            elif not allow_events:
                reason = "关系型知识库未启用事件索引，采用混合检索"
            else:
                reason = "事件索引未就绪，采用混合检索"
    elif intent == "definition":
        if not DEFINITION_PRECISE.search(query):
            tier, reason = "semantic", "概念释义问题采用语义检索"
        else:
            reason = "释义问题含编号或精确术语，保留关键词通道"

    if hint:
        reason = f"Jev 判定为 {intent}；{reason}"

    if family_strategy is None:
        family_strategy = TieredStrategy(
            tier=tier, graph_method=method,
            fusion=fusion,
            fallback_policy=strategy.fallback_policy, budget=strategy.budget,
        )
    return family_strategy, {
        "provider": "know_rag_rules_v2",
        "family_scope": scope,
        "intent": intent,
        "intent_source": source,
        "intent_confidence": capabilities.get("intent_confidence"),
        "intent_margin": capabilities.get("intent_margin"),
        "intent_signals": {
            "matched": sorted(signals.matched),
            "weak": signals.weak,
            "short": signals.short,
        },
        "intent_latency_ms": capabilities.get("intent_latency_ms"),
        "intent_model": capabilities.get("intent_model"),
        "intent_fallback_reason": capabilities.get("intent_fallback_reason"),
        "intent_shadow": bool(capabilities.get("intent_shadow")),
        "intent_cache_hit": bool(capabilities.get("intent_cache_hit")),
        "reason": reason,
        "family": family_strategy.family,
        "capabilities": capabilities.copy(),
        "model_calls": int(capabilities.get("intent_model_calls") or 0),
        "chosen_strategy": family_strategy.model_dump(exclude_none=True),
        "aggregation_supported": False,
        "coverage_incomplete": intent == "aggregation",
    }