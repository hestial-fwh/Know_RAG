from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, BigInteger, func
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
    rerank_model_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
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
