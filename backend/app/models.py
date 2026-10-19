from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Index, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.app.db import Base

def utcnow():
    return datetime.now(timezone.utc)

class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_input: Mapped[str] = mapped_column(String(200), nullable=False)
    focus: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    limitations_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    research_plan_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    observations_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    calculations_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    hypotheses_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    audit_log_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_clusters_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False, server_default="{}")
    progress_stage: Mapped[str] = mapped_column(String(40), default="queued", nullable=False, server_default="queued")
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    stages_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False, server_default="[]")
    config_snapshot_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False, server_default="{}")
    sources: Mapped[list["SourceRecord"]] = relationship(back_populates="run", cascade="all, delete-orphan")

class SourceRecord(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), default="web", nullable=False)
    publication_date: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    content_type: Mapped[str] = mapped_column(String(150), default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    fetch_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, server_default="pending")
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, server_default="0")
    relevance_reasons_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False, server_default="[]")
    independence_group: Mapped[str] = mapped_column(String(255), default="", nullable=False, server_default="")
    is_scanned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ocr_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    run: Mapped["ResearchRun"] = relationship(back_populates="sources")

Index("ix_sources_run_hash", SourceRecord.run_id, SourceRecord.content_hash)
