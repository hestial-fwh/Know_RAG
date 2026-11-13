"""双模式检索的显式策略契约；旧请求不根据 retrieval_mode 自动升级。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssociationBudget(BaseModel):
    """candidate_k 为每路基础召回上限；其余为全查询共用的关联扩展预算。"""
    model_config = ConfigDict(extra="forbid")
    candidate_k: int = Field(default=20, ge=1, le=100)
    max_candidates: int = Field(default=50, ge=1, le=100)
    max_hops: int = Field(default=2, ge=1, le=2)
    max_fanout: int = Field(default=5, ge=1, le=10)
    max_seeds: int = Field(default=8, ge=1, le=20)
    max_reports: int = Field(default=4, ge=1, le=12)
    max_model_calls: int = Field(default=5, ge=2, le=13)
    max_summary_chars: int = Field(default=6000, ge=500, le=20000)
    # DRIFT各轮共用预算，searches同时限制Embedding/BM25查询次数。
    max_rounds: int = Field(default=2, ge=1, le=3)
    max_searches: int = Field(default=4, ge=1, le=8)
    timeout_seconds: int = Field(default=120, ge=1, le=180)
    max_context_chars: int = Field(default=12000, ge=500, le=40000)
    # fusion 按通道配额；结构通道总候选与超时单独约束，避免与旧关联预算混用。
    relation_quota: int = Field(default=30, ge=1, le=100)
    event_quota: int = Field(default=20, ge=0, le=100)
    max_structure_candidates: int = Field(default=50, ge=1, le=100)
    structure_timeout_ms: int = Field(default=800, ge=50, le=5000)
    # Global 背景专用预算，不复用 DRIFT/Global 主路径的报告与模型调用上限。
    global_timeout_seconds: int = Field(default=45, ge=1, le=180)
    max_global_model_calls: int = Field(default=2, ge=0, le=5)
    max_global_reports: int = Field(default=2, ge=0, le=12)


class CommunitySettings(BaseModel):
    """离线社区预算；官方provider与本地算法分别标识。"""
    model_config = ConfigDict(extra="forbid")
    provider: Literal["native", "microsoft_graphrag_2_7_0"] = "native"
    max_communities: int = Field(default=16, ge=1, le=64)
    max_members: int = Field(default=50, ge=2, le=200)
    max_source_chars: int = Field(default=10000, ge=1000, le=30000)
    level: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def validate_native_level(self):
        if self.provider == "native" and self.level != 0:
            raise ValueError("本地社区分区仅支持level=0；层次level需要官方provider")
        return self


class LocalIndexModel(BaseModel):
    """知识库独立抽取模型，执行端沿用本机Ollama地址门禁。"""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: Literal["ollama"] = "ollama"
    model_name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(default="http://host.docker.internal:11434", max_length=2048)
    think: bool | None = None
    reference_mode: Literal["indices", "names"] = "indices"


class FusionSettings(BaseModel):
    """fusion 只组合已就绪通道；事件默认独立计分，避免被主排名淹没。"""
    model_config = ConfigDict(extra="forbid")
    rank_mode: Literal["rrf", "feature"] = "rrf"
    event_policy: Literal["independent", "fused", "off"] = "independent"
    include_global: bool = False
    include_event_timeline: bool = False
    event_reserved_slots: int = Field(default=2, ge=0, le=3)
    relation_reserved_slots: int = Field(default=2, ge=0, le=3)
    event_relevance_threshold: float = Field(default=0.5, ge=0, le=1)
    graph_feature_weight: float = Field(default=0.15, ge=0, le=1)
    event_feature_weight: float = Field(default=0.25, ge=0, le=1)


class TieredStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal["tiered"] = "tiered"
    tier: Literal["semantic", "hybrid", "graph", "fusion", "auto"] = "hybrid"
    graph_method: Literal["local", "global", "drift"] | None = None
    fusion: FusionSettings = Field(default_factory=FusionSettings)
    fallback_policy: Literal["hybrid", "error"] = "hybrid"
    budget: AssociationBudget = Field(default_factory=AssociationBudget)

    @model_validator(mode="after")
    def validate_graph_method(self):
        if self.tier not in {"graph", "fusion"} and self.graph_method is not None:
            raise ValueError("只有 graph/fusion 级别允许 graph_method")
        if self.tier == "graph" and self.graph_method is None:
            self.graph_method = "local"
        if self.tier == "fusion" and self.graph_method is not None:
            raise ValueError("fusion 不接受 graph_method")
        if self.graph_method == "drift" and self.budget.max_candidates < 3:
            raise ValueError("DRIFT的max_candidates至少为3，供两路基础召回和关系扩展共用")
        if self.tier == "fusion" and self.fusion.event_policy != "off" and self.budget.event_quota <= 0:
            raise ValueError("fusion 事件通道开启时 event_quota 必须大于 0")
        if self.fusion.include_global or self.fusion.include_event_timeline:
            if self.tier not in {"fusion", "auto"}:
                raise ValueError("include_global/include_event_timeline 只对 fusion/auto 生效")
        return self


class EventStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal["event_graph"]
    fallback_policy: Literal["hybrid", "error"] = "hybrid"
    budget: AssociationBudget = Field(default_factory=AssociationBudget)


RetrievalStrategy = Annotated[TieredStrategy | EventStrategy, Field(discriminator="family")]


class RetrievalProfile(BaseModel):
    """知识库显式迁移标记；旧库留在 legacy，新建库采用混合检索。"""

    model_config = ConfigDict(extra="forbid")

    version: Literal["legacy_v1", "new_profile_v2"] = "new_profile_v2"
    strategy: RetrievalStrategy = Field(default_factory=TieredStrategy)
    # 默认不构建，避免混合检索产生额外模型成本。
    build_indexes: list[Literal["entities", "relations", "events", "communities"]] = Field(default_factory=list)
    community: CommunitySettings = Field(default_factory=CommunitySettings)
    index_model: LocalIndexModel | None = None

    @model_validator(mode="after")
    def validate_community_dependencies(self):
        if "communities" in self.build_indexes and "relations" not in self.build_indexes:
            raise ValueError("社区索引需要同时开启relations索引")
        return self
