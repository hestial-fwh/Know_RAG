from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class ParseEvalRun(Base):
    """A low-coupling evaluation run for parsed document quality."""

    __tablename__ = "parse_eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(64), nullable=False, default="no_gt")
    template_id: Mapped[str] = mapped_column(String(128), nullable=False, default="generic_no_gt")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="completed")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")
    recommendation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    score_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    options_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParseEvalItem(Base):
    """A page/block/chunk/media level issue or evidence item."""

    __tablename__ = "parse_eval_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("parse_eval_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, default="chunk")
    target_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    page_no: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    issue_level: Mapped[str] = mapped_column(String(50), nullable=False, default="info")
    issue_type: Mapped[str] = mapped_column(String(100), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
