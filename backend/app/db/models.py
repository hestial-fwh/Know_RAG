from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, BigInteger, UniqueConstraint, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    embedding_model_id: Mapped[str] = mapped_column(String(128), default="")
    summary_model_id: Mapped[str] = mapped_column(String(128), default="")
    # 旧库保持原行为；仅创建服务显式写入 v2 默认策略，避免升级时改变检索路线。
    retrieval_profile: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=lambda: {"version": "legacy_v1"},
    )
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )


class RetrievalConfig(Base):
    __tablename__ = "retrieval_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    embedding_top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    score_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    use_vector: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    use_keyword: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rerank_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fusion_strategy: Mapped[str] = mapped_column(String(50), nullable=False, default="rrf")
    rrf_k: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    rrf_vector_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    rrf_keyword_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    include_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ModelConfig(Base):
    __tablename__ = "model_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="ollama")
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    api_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    dimension: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class VectorStoreConfig(Base):
    __tablename__ = "vector_store_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="postgres")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChunkerConfig(Base):
    __tablename__ = "chunker_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False, default="auto")
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    separators: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enable_parent_child: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parent_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    child_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=384)
    child_chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    request_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    # 按知识库和原文件内容去重；旧记录缺失指纹时不把所有空值误判为同一文件。
    __table_args__ = (
        Index(
            "uq_documents_kb_file_hash", "knowledge_base_id", "file_hash", unique=True,
            postgresql_where=text("file_hash <> ''"), sqlite_where=text("file_hash <> ''"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parse_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="file")
    channel: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enable_status: Mapped[str] = mapped_column(String(50), nullable=False, default="enabled")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    canonical_artifact: Mapped["DocumentCanonicalArtifact | None"] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        uselist=False,
    )


class DocumentCanonicalArtifact(Base):
    """Current versioned Canonical IR and deterministic provenance ledger."""

    __tablename__ = "document_canonical_artifacts"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parse_revision: Mapped[str] = mapped_column(String(36), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False, default="p2.5-v1")
    source_format: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    canonical_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    assets_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    figure_links_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    relation_graph_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    stats_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    document: Mapped[Document] = relationship(back_populates="canonical_artifact")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_id: Mapped[str | None] = mapped_column(String(36), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ready")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    start_at: Mapped[int | None] = mapped_column(Integer)
    end_at: Mapped[int | None] = mapped_column(Integer)
    pre_chunk_id: Mapped[str | None] = mapped_column(String(36), index=True)
    next_chunk_id: Mapped[str | None] = mapped_column(String(36), index=True)
    chunk_type: Mapped[str] = mapped_column(String(50), nullable=False, default="text")
    parent_chunk_id: Mapped[str | None] = mapped_column(String(36), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    image_info: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    relation_chunks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    indirect_relation_chunks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_header: Mapped[str] = mapped_column(Text, nullable=False, default="")
    flags: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
    embedding: Mapped["ChunkEmbedding"] = relationship(
        back_populates="chunk",
        cascade="all, delete-orphan",
        uselist=False,
    )


class KnowledgeIndexJob(Base):
    """与来源事务一起写入的 outbox；lease_token 阻止过期 worker 发布。"""

    __tablename__ = "knowledge_index_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeIndexManifest(Base):
    """发布的输入清单和覆盖率；不等同于图/事件检索就绪。"""

    __tablename__ = "index_manifests"
    id: Mapped[str] = mapped_column(ForeignKey("knowledge_index_jobs.id", ondelete="CASCADE"), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    coverage_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeEntity(Base):
    """同名不直接合并：身份键同时包含类型和有来源的 scope。"""

    __tablename__ = "knowledge_entities"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "identity_key", name="uq_entity_identity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    identity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(512), nullable=False)


class KnowledgeEvidence(Base):
    """提及 span 属于准入投影，不能冒充 Canonical 原始偏移。"""

    __tablename__ = "knowledge_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(ForeignKey("index_manifests.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    parse_revision: Mapped[str] = mapped_column(String(36), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[int] = mapped_column(Integer, nullable=False)
    end_at: Mapped[int] = mapped_column(Integer, nullable=False)
    source_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class EntityAlias(Base):
    """别名必须有提及证据，查询时再次检查版本与准入。"""

    __tablename__ = "entity_aliases"
    __table_args__ = (UniqueConstraint("entity_id", "alias", "evidence_id", name="uq_entity_alias_evidence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("knowledge_evidence.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)


class KnowledgeRelation(Base):
    """有支持引文的语义关系候选；保留否定、时间与条件，不从共现生成事实。"""
    __tablename__ = "knowledge_relations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(ForeignKey("index_manifests.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True)
    object_id: Mapped[str] = mapped_column(ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True)
    predicate: Mapped[str] = mapped_column(String(255), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[int] = mapped_column(Integer, nullable=False)
    end_at: Mapped[int] = mapped_column(Integer, nullable=False)
    qualifiers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class KnowledgeEvent(Base):
    """完整事件检索表示；生成的 text 与原文 quote 分开保存。"""
    __tablename__ = "knowledge_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(ForeignKey("index_manifests.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[int] = mapped_column(Integer, nullable=False)
    end_at: Mapped[int] = mapped_column(Integer, nullable=False)
    qualifiers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class EventEntity(Base):
    """事件参与者必须有同一切片、同一发布版本的实体证据。"""
    __tablename__ = "event_entities"
    event_id: Mapped[str] = mapped_column(ForeignKey("knowledge_events.id", ondelete="CASCADE"), primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("knowledge_entities.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("knowledge_evidence.id", ondelete="CASCADE"), nullable=False)


class KnowledgeGraphJob(Base):
    """KB级图revision任务；与文档级抽取分开，拥有独立租约和输入清单。"""
    __tablename__ = "knowledge_graph_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    coverage_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GraphCommunity(Base):
    """版本化社区成员及支持来源；不使用社区ID冒充原文chunkID。"""
    __tablename__ = "graph_communities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    graph_revision: Mapped[str] = mapped_column(ForeignKey("knowledge_graph_jobs.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    community_key: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    members_json: Mapped[list] = mapped_column(JSON, nullable=False)
    sources_json: Mapped[list] = mapped_column(JSON, nullable=False)
    provider_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class CommunityReport(Base):
    """生成摘要和支持引文分离；每次读取必须核验整个图输入版本。"""
    __tablename__ = "community_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    community_id: Mapped[str] = mapped_column(ForeignKey("graph_communities.id", ondelete="CASCADE"), nullable=False, unique=True)
    graph_revision: Mapped[str] = mapped_column(ForeignKey("knowledge_graph_jobs.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    findings_json: Mapped[list] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ParseTask(Base):
    __tablename__ = "parse_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False, default="parse")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    current_stage: Mapped[str] = mapped_column(String(100), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ParseTaskEvent(Base):
    __tablename__ = "parse_task_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("parse_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_id: Mapped[str | None] = mapped_column(String(36), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="chunk")
    content: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    chunk: Mapped[Chunk] = relationship(back_populates="embedding")


class McpClient(Base):
    """External MCP client credential for signed access to the MCP server."""

    __tablename__ = "mcp_clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    client_secret_encrypted: Mapped[str] = mapped_column(String(512), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
